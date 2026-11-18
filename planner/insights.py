"""The decision layer: explain every planned course, flag graduation risks, compare paths, plan registration.

Everything here reads a finished semester plan; nothing re-solves, so it is fast.
"""
from __future__ import annotations

from typing import Optional

from .models import is_main_term, next_term, term_key, term_name


# ---------------------------------------------------------------- per course facts
def _deps(catalog, planned: dict, done: set) -> dict[str, set]:
    """course -> planned courses it strictly needs first (only when one planned option can satisfy that prereq)."""
    need: dict[str, set] = {c: set() for c in planned}
    for code in planned:
        c = catalog.get(code)
        if not c:
            continue
        for ors in c.prereqs:
            if any(o in done for o in ors):
                continue
            opts = [o for o in ors if o in planned]
            if len(opts) == 1:
                need[code].add(opts[0])
    return need


def course_facts(plan: dict, catalog, conf: dict, min_conf: float, terms: list[str], done: set,
                 groups: list[dict], dc: dict) -> dict:
    planned = {r["course"]: pt["term"] for pt in plan["terms"] for r in pt["courses"]}
    idx = {t: i for i, t in enumerate(terms)}
    need = _deps(catalog, planned, done)
    children: dict[str, set] = {c: set() for c in planned}
    for c, ps in need.items():
        for p in ps:
            children.setdefault(p, set()).add(c)

    def offered(code, t):
        return conf.get(code, {}).get(t[:2], 0) >= min_conf

    latest: dict[str, Optional[str]] = {}

    def L(code, stack=()):
        if code in latest:
            return latest[code]
        if code in stack:
            return terms[-1]
        bound = len(terms)
        for ch in children.get(code, ()):
            lc = L(ch, stack + (code,))
            bound = min(bound, idx[lc] if lc in idx else -1)
        best = None
        for t in terms:
            if idx[t] < bound and offered(code, t):
                best = t
        latest[code] = best
        return best

    def descendants(code, seen=None):
        seen = set() if seen is None else seen
        for ch in children.get(code, ()):
            if ch not in seen:
                seen.add(ch)
                descendants(ch, seen)
        return seen

    facts = {}
    for code, t in planned.items():
        c = catalog.get(code)
        lt = L(code)
        later_ok = [x for x in terms if lt and idx[t] < idx[x] <= idx[lt] and offered(code, x)]
        prereq_terms = [planned[p] for p in need[code]]
        earliest = None
        for x in terms:
            if offered(code, x) and all(idx[x] > idx[pt] for pt in prereq_terms):
                earliest = x
                break
        replace = []
        for g in groups:
            if g.get("remaining", 0) > 0 and code in g["options"]:
                alts = [o for o in g["options"] if o != code and o not in done and o in catalog][:4]
                replace.append({"requirement": g["group"][:80], "program": g["program"], "alternatives": alts})
        seasons = sorted(s for s in ("FA", "WN", "SP", "SU") if conf.get(code, {}).get(s, 0) >= min_conf)
        facts[code] = {
            "term": t, "term_name": term_name(t),
            "earliest_possible": earliest, "latest_possible": lt,
            "slack_terms": len(later_ok),
            "can_move_to": later_ok[:3],
            "needs_first_in_plan": sorted(need[code]),
            "prereqs_from_atlas": c.prereqs if c else [],
            "directly_unlocks": sorted(children.get(code, ())),
            "unlocks_total": len(descendants(code)),
            "counts_toward": dc.get(code, []),
            "if_dropped": replace,
            "usually_offered": [{"FA": "Fall", "WN": "Winter", "SP": "Spring", "SU": "Summer"}[s] for s in seasons],
        }
    return facts


# ---------------------------------------------------------------- alerts
def _calendar_after(term: str, include_ss: bool, n: int) -> list[str]:
    out, t = [], term
    while len(out) < n:
        t = next_term(t)
        if include_ss or is_main_term(t):
            out.append(t)
    return out


def alerts(plan: dict, facts: dict, conf: dict, min_conf: float, terms: list[str], P: dict, prof: dict,
           atlas_courses: set, wl_cap: float) -> list[dict]:
    out = []
    include_ss = bool(P.get("use_spring_summer"))
    grad = terms[-1] if terms else P.get("graduation_term")
    cap = plan.get("does_it_all_fit") or {}

    # critical
    for prog, d in (cap.get("left_out_by_program") or {}).items():
        out.append({"level": "critical", "title": f"{prog}: not everything fits by {term_name(grad)}",
                    "detail": "Left out: " + "; ".join(r[:60] for r in d["requirements"]),
                    "options": ["Add spring/summer terms", "Graduate a term later", "Drop this program",
                                "Run compare_paths to see each option"]})
    late = (plan.get("mcat") or {}).get("courses_after_mcat") or []
    if late:
        out.append({"level": "critical", "title": f"MCAT courses land after your MCAT ({plan['mcat']['term']})",
                    "detail": ", ".join(late), "options": ["Move them earlier with must_take", "Push the MCAT a term"]})

    # heads up: bottlenecks (no room to move)
    bottlenecks = [(c, f) for c, f in facts.items() if f["slack_terms"] == 0 and f["unlocks_total"] > 0]
    bottlenecks.sort(key=lambda x: (term_key(x[1]["term"]), -x[1]["unlocks_total"]))
    for code, f in bottlenecks[:5]:
        nxt = next((t for t in _calendar_after(f["term"], include_ss, 6)
                    if conf.get(code, {}).get(t[:2], 0) >= min_conf), None)
        slip = None
        if nxt:   # fall/winter terms lost, so summers don't inflate the estimate
            lost = max(1, len([t for t in _calendar_after(f["term"], False, 8) if term_key(t) <= term_key(nxt)]))
            slip = _calendar_after(grad, False, lost)[-1]
        out.append({"level": "heads_up", "course": code,
                    "title": f"{code} in {f['term_name']} has no room to move",
                    "detail": (f"It leads to {f['unlocks_total']} later course(s) in your plan"
                               + (f" ({', '.join(f['directly_unlocks'][:3])})" if f["directly_unlocks"] else "")
                               + (f". Next time it runs is {term_name(nxt)}" if nxt else "")
                               + (f", which could push graduation to about {term_name(slip)}." if slip else ".")),
                    "options": ["Register for it first", "Keep a backup section ready"]})

    # heads up: heavy terms
    for pt in plan["terms"]:
        hard = [r["course"] for r in pt["courses"] if r["difficulty_0_100"] >= 60]
        if pt["workload_index"] > wl_cap or len(hard) >= 3:
            out.append({"level": "heads_up", "title": f"{pt['name']} is heavy",
                        "detail": f"Workload {pt['workload_index']} (heavy above {wl_cap})"
                                  + (f"; {len(hard)} hard courses together: {', '.join(hard)}" if len(hard) >= 2 else ""),
                        "options": ["Move one hard course to a lighter term", "Use a summer term"]})

    # heads up: credit the plan assumes but Atlas doesn't show
    unverified = sorted(set(prof.get("completed_extra", [])) - atlas_courses)
    if unverified:
        out.append({"level": "heads_up", "title": "Plan counts credit Atlas doesn't show yet",
                    "detail": ", ".join(unverified) + ". If any of these isn't posted, the plan changes.",
                    "options": ["Check your transcript / ask your advisor to post transfer, IB or AP credit"]})
    if "Premed (template)" in prof.get("programs", []):
        out.append({"level": "heads_up", "title": "Premed list is a general template",
                    "detail": "Medical schools differ, and some don't accept IB/AP credit for science or English.",
                    "options": ["Confirm with UM Health Professions advising"]})

    # opportunities
    main = [pt for pt in plan["terms"] if is_main_term(pt["term"]) and pt["courses"]]
    if main:
        avg = sum(p["workload_index"] for p in main) / len(main)
        light = [p for p in main if p["workload_index"] < avg - 0.7]
        for p in light[:2]:
            out.append({"level": "opportunity", "title": f"{p['name']} is light",
                        "detail": f"Workload {p['workload_index']} vs your average {avg:.2f}. Room for a credit rule "
                                  "course (humanities, business elective) or research.",
                        "options": ["Ask Claude which course could go here"]})
    if cap.get("fits"):
        used = sum(pt["credits"] for pt in plan["terms"])
        spare = (cap.get("credit_room_before_graduation") or 0) - used - (cap.get("credit_rules_total") or 0)
        if spare >= float(P.get("min_credits_main", 14)):
            out.append({"level": "opportunity", "title": "You may be able to graduate a term earlier",
                        "detail": f"About {spare:g} spare credits of room before {term_name(grad)}.",
                        "options": ["Run compare_paths to see the fastest path"]})
    order = {"critical": 0, "heads_up": 1, "opportunity": 2}
    return sorted(out, key=lambda a: order[a["level"]])


def assumptions(prof: dict, P: dict, atlas_courses: set) -> list[str]:
    a = [f"Graduating {term_name(P.get('graduation_term', 'WN29'))}, "
         + ("with" if P.get("use_spring_summer") else "without") + " spring/summer terms.",
         f"Up to {P.get('max_credits_main', 17)} credits in fall/winter"
         + (f", {P.get('spring_summer_max_credits', 8)} in spring/summer." if P.get("use_spring_summer") else "."),
         f"A course is only planned in a season it ran in at least {int(float(P.get('offering_confidence_min', 0.6)) * 100)}% of recent years.",
         "Predicted grades are course averages until your letter grades are added, so treat them as rough.",
         "Requirements come from Atlas's unofficial What If audit; your official audit wins."]
    extra = sorted(set(prof.get("completed_extra", [])))
    if extra:
        a.append("Counts as done (you told me): " + ", ".join(extra) + ".")
    if P.get("mcat_term"):
        a.append(f"MCAT in {term_name(P['mcat_term'])}: MCAT courses are placed before it.")
    return a


# ---------------------------------------------------------------- paths
def path_summary(name: str, plan: dict, wl_cap: float) -> dict:
    if not plan.get("ok"):
        return {"path": name, "ok": False, "error": plan.get("error")}
    terms = [pt for pt in plan["terms"] if pt["courses"]]
    main = [pt for pt in terms if is_main_term(pt["term"])]
    summers = [pt for pt in terms if not is_main_term(pt["term"])]
    cap = plan.get("does_it_all_fit") or {}
    used = sum(pt["credits"] for pt in terms)
    return {
        "path": name, "ok": True,
        "graduation": term_name(terms[-1]["term"]) if terms else None,
        "fits_everything": bool(cap.get("fits")),
        "left_out": {k: v["requirements"] for k, v in (cap.get("left_out_by_program") or {}).items()},
        "avg_credits_fall_winter": round(sum(p["credits"] for p in main) / len(main), 1) if main else 0,
        "summer_terms_used": len(summers),
        "heavy_terms": sum(1 for p in terms if p["workload_index"] > wl_cap or
                           sum(r["difficulty_0_100"] >= 60 for r in p["courses"]) >= 3),
        "heaviest_workload": max((p["workload_index"] for p in terms), default=0),
        "predicted_gpa": (plan.get("gpa_projection") or {}).get("cumulative_after"),
        "predicted_bcpm": (plan.get("gpa_projection") or {}).get("bcpm_after"),
        "mcat_ready": (plan.get("mcat") or {}).get("ready"),
        "spare_credit_room": round((cap.get("credit_room_before_graduation") or 0) - used
                                   - (cap.get("credit_rules_total") or 0), 1),
        "by_term": {pt["term"]: [r["course"] for r in pt["courses"]] for pt in terms},
    }


# ---------------------------------------------------------------- registration
def registration_plan(week: dict, facts: dict, plan: dict) -> dict:
    if not week.get("plans"):
        return {"ok": False, "error": "No schedule fits next term.", "warnings": week.get("warnings")}
    top = week["plans"][0]
    rows = []
    for c in top["courses"]:
        f = facts.get(c["course"], {})
        risk = c.get("seat_risk", "unknown")
        backups = c.get("backup_sections") or []
        if f.get("slack_terms") == 0 and f.get("unlocks_total", 0) > 0:
            tier, why = 1, f"Critical: bottleneck, leads to {f['unlocks_total']} later course(s); no later term works."
        elif risk == "high" or (risk == "medium" and (not backups or f.get("slack_terms", 1) == 0)):
            tier, why = 2, "Priority: " + ("seats usually go fast" if risk == "high"
                                           else "fills within a week and has no easy backup")
        else:
            tier, why = 3, "Flexible: " + (f"can move to {term_name(f['can_move_to'][0])}" if f.get("can_move_to")
                                           else "other sections available")
        fallback = []
        if backups:
            fallback.append(f"If {c['sections']} is full: take {backups[0]}"
                            + (f", then {backups[1]}" if len(backups) > 1 else "") + ".")
        if f.get("can_move_to"):
            fallback.append(f"If no section works: move it to {term_name(f['can_move_to'][0])} (graduation unchanged).")
        else:
            for r in f.get("if_dropped", [])[:1]:
                if r["alternatives"]:
                    fallback.append(f"If no section works: swap in {' or '.join(r['alternatives'][:2])} "
                                    f"(also counts for {r['requirement'][:50]}).")
            if not fallback or len(fallback) == (1 if backups else 0):
                fallback.append("If no section works: ask Claude to re-plan around it before registration closes.")
        rows.append({"tier": tier, "course": c["course"], "sections": c["sections"], "times": c["times"],
                     "why": why, "seat_risk": risk, "fallback": fallback})
    rows.sort(key=lambda r: (r["tier"], r["course"]))
    labels = {1: "Register first", 2: "Register right after", 3: "Flexible"}
    for i, r in enumerate(rows, 1):
        r["order"] = i
        r["group"] = labels[r["tier"]]
    return {"ok": True, "term": week.get("term"), "credits": top.get("credits"), "order": rows,
            "note": "Register in this order. Have the backups open in another tab. Atlas seat counts change fast."}
