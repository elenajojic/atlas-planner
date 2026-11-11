"""Every remaining semester at once.

Decides which term each remaining course goes in:
  * only in seasons it has historically run (offering confidence from past terms)
  * prereqs in an earlier term, coreqs same term or earlier
  * every requirement group satisfied, preferring courses that double count
  * MCAT courses finished before your MCAT term
  * workload balanced across semesters (minimizes the heaviest term)
  * gateway courses early (critical path: courses that unlock the most later ones)
  * close to when students like you usually take each course
  * brutal combos kept out of the same term
  * optional spring/summer terms; optional study abroad term (or find the best one)
Then scores the application story and builds heatmap data.
"""
from __future__ import annotations

import os

import copy
from typing import Optional

from ortools.sat.python import cp_model

from .grades import GradeModel, gpa_projection
from .models import is_main_term, term_key, term_name, terms_between
from .requirements import done_courses, double_counts, resolve
from .scoring import difficulty_score, workload_index

S = 100


def semester_index(term: str, first_fall_year: int = 25) -> float:
    """FA25 = 1, WN26 = 2, FA26 = 3 ... spring/summer sit between."""
    y, s = term_key(term)
    y -= 2000
    if term[:2] == "FA":
        return 2 * (y - first_fall_year) + 1
    if term[:2] == "WN":
        return 2 * (y - first_fall_year - 1) + 2
    return 2 * (y - first_fall_year - 1) + 2.5


def offering_confidence(catalog) -> dict[str, dict[str, float]]:
    years: dict[str, set] = {"FA": set(), "WN": set(), "SP": set(), "SU": set()}
    for c in catalog:
        for t in c.offered_history:
            years[t[:2]].add(t[2:])
    out = {}
    for c in catalog:
        out[c.code] = {}
        for s in years:
            denom = len(years[s]) or 1
            out[c.code][s] = round(sum(1 for t in c.offered_history if t[:2] == s) / denom, 2)
    return out


def _descendants(code: str, children: dict[str, set], seen=None) -> set:
    seen = seen if seen is not None else set()
    for ch in children.get(code, ()):
        if ch not in seen:
            seen.add(ch)
            _descendants(ch, children, seen)
    return seen


def plan_semesters(source, profile: dict, overrides: Optional[dict] = None, abroad_term: Optional[str] = None,
                   time_limit: float = 8.0) -> dict:
    ov = overrides or {}
    P = {**profile.get("plan", {}), **ov.get("plan", {})}
    W = {**profile.get("weights", {}), **ov.get("weights", {})}
    history = source.student_history()
    done = done_courses(history, profile.get("completed_extra")) - set(ov.get("not_done", []))
    avoid = set(profile.get("avoid", [])) | set(ov.get("avoid", []))
    must = set(profile.get("must_take", [])) | set(ov.get("must_take", []))
    prof = copy.deepcopy(profile)
    if "programs" in ov:
        prof["programs"] = ov["programs"]
    groups = resolve(prof, source, done)
    dc = double_counts(groups)

    terms = terms_between(P.get("start_term", source.next_term()), P.get("graduation_term", "WN29"),
                          P.get("use_spring_summer", False))
    if abroad_term and abroad_term not in terms:
        abroad_term = None
    tidx = {t: i for i, t in enumerate(terms)}
    catalog = {c.code: c for c in source.catalog()}
    conf = offering_confidence(catalog.values())
    min_conf = float(P.get("offering_confidence_min", 0.6))
    cohort = source.cohort()
    typical = cohort.get("typical_paths", {}).get("courses", {})
    combos = cohort.get("combos", [])
    mcat_term = P.get("mcat_term")
    mcat_courses = {o for g in groups if g.get("mcat") for o in g["options"]}

    cand = sorted({o for g in groups if g["remaining"] > 0 for o in g["options"]} | must)
    # pull in prereqs/coreqs needed by candidates (transitively)
    changed = True
    while changed:
        changed = False
        for code in list(cand):
            c = catalog.get(code)
            if not c:
                continue
            for ors in c.prereqs + c.coreqs:
                if not any(o in done for o in ors) and not any(o in cand for o in ors):
                    first = next((o for o in ors if o in catalog), None)
                    if first:
                        cand.append(first)
                        changed = True
    cand = [c for c in cand if c in catalog and c not in done and c not in avoid]

    children: dict[str, set] = {}
    for code in cand:
        for ors in catalog[code].prereqs:
            for o in ors:
                children.setdefault(o, set()).add(code)
    unlock = {code: len(_descendants(code, children) & set(cand)) for code in cand}

    stats = {code: source.course_stats(code) for code in cand}
    wl = {code: workload_index(catalog[code].credits, stats[code]) for code in cand}
    diff = {code: difficulty_score(stats[code]) for code in cand}
    goals = prof.get("goals", [])
    gfit = {}
    for code in cand:
        text = " ".join(catalog[code].tags + [catalog[code].title.lower()])
        gfit[code] = sum(g.get("weight", 1) for g in goals if any(t.lower() in text for t in g.get("tags", [])))

    # Next term has real section times: only place courses that have a section fitting your week.
    from .models import parse_time
    from .week import build_bundles, commitment_meetings
    next_t = source.next_term()
    fits_next: Optional[set] = None
    blocked_next = []
    try:
        prefs = prof.get("preferences", {})
        blocked = commitment_meetings(prof.get("commitments", [])) + commitment_meetings(ov.get("extra_busy", []))
        fits_next = set()
        for c in source.offerings(next_t):
            if build_bundles(c, blocked, parse_time(prefs.get("hard_earliest_start", "08:00")),
                             int(prefs.get("cross_campus_gap", 30)), int(prefs.get("michigan_time_min", 10)),
                             prefs.get("allow_full_sections", False)):
                fits_next.add(c.code)
            else:
                blocked_next.append(c.code)
    except Exception:
        fits_next = None

    m = cp_model.CpModel()
    y = {}
    unavailable = []
    for code in cand:
        any_ok = False
        for t in terms:
            ok = conf[code].get(t[:2], 0) >= min_conf
            if t == next_t and fits_next is not None:
                ok = code in fits_next
            if code in ov.get("avoid_in_term", {}).get(t, []):
                ok = False
            if abroad_term and t == abroad_term and "abroad_ok" not in catalog[code].tags:
                ok = False
            if ok:
                y[code, t] = m.NewBoolVar(f"y_{code}_{t}")
                any_ok = True
        if not any_ok:
            unavailable.append(code)
    taken = {code: sum(y[code, t] for t in terms if (code, t) in y) for code in cand}
    for code in cand:
        m.Add(taken[code] <= 1)
    for code in must:
        if code in taken:
            m.Add(taken[code] == 1)

    def before(code_opts, t, inclusive):
        lim = tidx[t] + (1 if inclusive else 0)
        return [y[o, t2] for o in code_opts for t2 in terms[:lim] if (o, t2) in y]

    for (code, t), var in list(y.items()):
        c = catalog[code]
        for ors in c.prereqs:
            if any(o in done for o in ors):
                continue
            m.Add(sum(before(ors, t, False)) >= var)
        for ors in c.coreqs:
            if any(o in done for o in ors):
                continue
            m.Add(sum(before(ors, t, True)) >= var)

    slack = {}
    for gi, g in enumerate(groups):
        if g["remaining"] <= 0:
            continue
        s_ = m.NewIntVar(0, g["remaining"], f"slack_{gi}")
        m.Add(sum(taken[o] for o in g["options"] if o in taken) + s_ >= g["remaining"])
        slack[g["group"]] = s_

    cred = {code: int(round(catalog[code].credits * 2)) for code in cand}
    wl_t, short = {}, []
    hi_main = int(P.get("max_credits_main", 17) * 2)
    lo_main = int(P.get("min_credits_main", 14) * 2)
    for t in terms:
        cr = sum(cred[c] * y[c, t] for c in cand if (c, t) in y)
        if t == abroad_term:
            m.Add(cr <= int(P.get("abroad_max_credits", 16) * 2))
        elif is_main_term(t):
            m.Add(cr <= hi_main)
            sh = m.NewIntVar(0, lo_main, f"short_{t}")
            m.Add(cr + sh >= lo_main)
            short.append(sh)
        else:
            m.Add(cr <= int(P.get("spring_summer_max_credits", 8) * 2))
        wl_t[t] = sum(int(wl[c] * S) * y[c, t] for c in cand if (c, t) in y)
    peak = m.NewIntVar(0, 100 * S, "peak")
    for t in terms:
        if is_main_term(t) and t != abroad_term:
            m.Add(peak >= wl_t[t])
        elif not is_main_term(t):
            m.Add(peak >= 2 * wl_t[t])   # half length term: same work in half the weeks

    pen = [10000 * s_ for s_ in slack.values()]
    pen.append(int(W.get("workload_balance", 4) * 6) * peak)
    pen += [int(5 * S / 100) * sh for sh in short]   # gentle push toward full time terms
    pen += [3 * S * taken[c] for c in cand]            # fewer total courses => double counting wins
    if mcat_term:
        for (code, t), var in y.items():
            if code in mcat_courses and term_key(t) >= term_key(mcat_term):
                pen.append(int(W.get("mcat", 5) * 20 * S) * var)
    for (code, t), var in y.items():
        late = tidx[t]
        pen.append(int(W.get("critical_path", 3) * unlock[code] * late * S / 4) * var)
        typ = typical.get(code, {}).get("typical_term_index")
        if typ:
            pen.append(int(W.get("typical_path", 1) * abs(semester_index(t) - typ) * S / 2) * var)
        if not is_main_term(t):
            pen.append(int(2 * S) * var)  # small cost for using spring/summer
    for cb in combos:
        a, b = cb["courses"]
        for t in terms:
            if (a, t) in y and (b, t) in y:
                z = m.NewBoolVar(f"cb_{a}_{b}_{t}")
                m.Add(y[a, t] + y[b, t] <= 1 + z)
                pen.append(int(W.get("combos", 3) * cb["penalty"] * S) * z)
    reward = [int(W.get("goal_fit", 3) * gfit[c] * S / 2) * taken[c] for c in cand]
    reward += [int(W.get("double_counting", 3) * max(0, len(dc.get(c, [])) - 1) * 3 * S) * taken[c] for c in cand]
    m.Maximize(sum(reward) - sum(pen))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = time_limit
    solver.parameters.num_workers = int(os.environ.get("SOLVER_WORKERS", "8"))
    if os.environ.get("SOLVER_MAX_MB"):
        solver.parameters.max_memory_in_mb = int(os.environ["SOLVER_MAX_MB"])
    st = solver.Solve(m)
    if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        return {"ok": False, "error": "No multi term plan found. Try enabling spring/summer or lowering minimum credits."}

    gm = GradeModel(source, history)
    plan_terms, all_planned = [], []
    for t in terms:
        rows, planned = [], []
        for code in cand:
            if (code, t) in y and solver.Value(y[code, t]):
                c = catalog[code]
                p = gm.predict(code)
                if p:
                    planned.append((code, c.credits, p.gpa))
                typ = typical.get(code, {})
                rows.append({"course": code, "title": c.title, "credits": c.credits,
                             "offering_confidence": conf[code].get(t[:2], 0),
                             "offered_last_5_years": f"{sum(1 for h in c.offered_history if h[:2] == t[:2])} of 5 {term_name(t).split()[0]} terms",
                             "predicted_grade": p.range if p else None, "difficulty_0_100": diff[code],
                             "workload_index": round(wl[code], 2), "unlocks": unlock[code],
                             "counts_toward": dc.get(code, []),
                             "students_like_you_take_it": (f"semester {typ['typical_term_index']}" if typ else None),
                             "you_take_it": f"semester {semester_index(t):g}",
                             "mcat_course": code in mcat_courses})
        all_planned += planned
        cr = sum(r["credits"] for r in rows)
        w = sum(r["workload_index"] for r in rows)
        plan_terms.append({"term": t, "name": term_name(t), "abroad": t == abroad_term, "courses": rows,
                           "credits": cr, "workload_index": round(w, 2),
                           "avg_difficulty": round(sum(r["difficulty_0_100"] for r in rows) / len(rows), 1) if rows else 0,
                           "predicted_term_gpa": gpa_projection([], planned)["term_gpa"],
                           "combos": [f"{' + '.join(cb['courses'])}: {cb['note']}" for cb in combos
                                      if all(any(r["course"] == x for r in rows) for x in cb["courses"])]})

    unmet = [f"{g}: short {solver.Value(s_)}" for g, s_ in slack.items() if solver.Value(s_) > 0]
    late_mcat = [r["course"] for pt in plan_terms for r in pt["courses"]
                 if mcat_term and r["mcat_course"] and term_key(pt["term"]) >= term_key(mcat_term)]
    gpa = gpa_projection(history, all_planned, prof.get("bcpm_subjects"))
    main = [pt for pt in plan_terms if is_main_term(pt["term"]) and not pt["abroad"]]
    result = {
        "ok": True, "objective": solver.ObjectiveValue() / S,
        "terms": plan_terms,
        "unmet_requirements": unmet,
        "not_offered_often_enough": unavailable,
        "next_term_no_section_fits": sorted(set(blocked_next) & set(cand)),
        "mcat": {"term": mcat_term, "ready": not late_mcat, "courses_after_mcat": late_mcat},
        "gpa_projection": gpa,
        "heaviest_term": max(main, key=lambda p: p["workload_index"])["term"] if main else None,
        "workload_spread": [round(p["workload_index"], 2) for p in main],
        "double_counters": {c: v for c, v in dc.items() if len(v) > 1 and c in cand},
        "critical_path": sorted(((c, unlock[c]) for c in cand if unlock[c] > 0), key=lambda t: -t[1])[:6],
        "heatmap": [{"term": p["term"], "workload_index": p["workload_index"], "avg_difficulty": p["avg_difficulty"],
                     "predicted_gpa": p["predicted_term_gpa"], "credits": p["credits"], "abroad": p["abroad"]}
                    for p in plan_terms],
    }
    from .story import application_story
    result["application_story"] = application_story(result, prof, catalog)
    return result


def find_best_abroad(source, profile: dict, overrides: Optional[dict] = None) -> dict:
    base = plan_semesters(source, profile, overrides, time_limit=6)
    if not base.get("ok"):
        return {"ok": False, "error": base.get("error")}
    P = {**profile.get("plan", {}), **(overrides or {}).get("plan", {})}
    terms = terms_between(P.get("start_term", source.next_term()), P.get("graduation_term", "WN29"), False)
    out = []
    for t in terms[1:-1]:
        r = plan_semesters(source, profile, overrides, abroad_term=t, time_limit=5)
        if not r.get("ok"):
            out.append({"term": t, "feasible": False})
            continue
        out.append({"term": t, "name": term_name(t), "feasible": True,
                    "cost_vs_no_abroad": round(base["objective"] - r["objective"], 1),
                    "unmet_requirements": r["unmet_requirements"], "mcat_ready": r["mcat"]["ready"],
                    "heaviest_term_load": max(r["workload_spread"]) if r["workload_spread"] else None,
                    "courses_abroad": [c["course"] for pt in r["terms"] if pt["abroad"] for c in pt["courses"]]})
    ok = [o for o in out if o["feasible"] and not o["unmet_requirements"]]
    ok.sort(key=lambda o: (not o["mcat_ready"], o["cost_vs_no_abroad"]))
    return {"ok": True, "ranking": ok + [o for o in out if o not in ok],
            "best": ok[0]["term"] if ok else None,
            "note": "Cost is how much the overall plan gets worse (heavier peaks, later gateways, etc). Lower is better."}


def what_if(source, profile: dict, changes: dict) -> dict:
    """changes: drop / fail / add_program / remove_program / must_take / mcat_term /
    use_spring_summer / graduation_term / study_abroad_term"""
    base = plan_semesters(source, profile, time_limit=6)
    ov: dict = {"plan": {}, "avoid": list(changes.get("drop", [])), "not_done": list(changes.get("fail", [])),
                "must_take": list(changes.get("must_take", [])) + list(changes.get("fail", []))}
    progs = list(profile.get("programs", []))
    for p in changes.get("add_program", []):
        if p not in progs:
            progs.append(p)
    progs = [p for p in progs if p not in changes.get("remove_program", [])]
    ov["programs"] = progs
    for k in ("mcat_term", "use_spring_summer", "graduation_term"):
        if k in changes:
            ov["plan"][k] = changes[k]
    new = plan_semesters(source, profile, ov, abroad_term=changes.get("study_abroad_term"), time_limit=6)

    def courses_by_term(r):
        return {pt["term"]: [c["course"] for c in pt["courses"]] for pt in r.get("terms", [])}

    diff = []
    a, b = courses_by_term(base), courses_by_term(new)
    for t in sorted(set(a) | set(b), key=term_key):
        add, rem = sorted(set(b.get(t, [])) - set(a.get(t, []))), sorted(set(a.get(t, [])) - set(b.get(t, [])))
        if add or rem:
            diff.append({"term": t, "added": add, "removed": rem})
    summ = lambda r: {  # noqa: E731
        "unmet": r.get("unmet_requirements"), "mcat_ready": r.get("mcat", {}).get("ready"),
        "heaviest_load": max(r["workload_spread"]) if r.get("workload_spread") else None,
        "bcpm_after": r.get("gpa_projection", {}).get("bcpm_after"),
        "story_score": r.get("application_story", {}).get("score")}
    return {"changes": changes, "before": summ(base), "after": summ(new), "term_changes": diff,
            "new_plan": new}
