"""One term: pick courses and sections.

Step 1  Expand every course into "bundles" (one section of each component: LEC + DIS + LAB).
        Drop bundles that clash with themselves, your commitments (+ buffers), or your hard
        earliest start.
Step 2  CP-SAT picks courses and bundles with:
          * no overlaps, and enough time to walk between buildings (Michigan time aware),
            with a fixed gap (default 30 min) for North <-> Central
          * credit limits, prereqs (earlier term) and coreqs (same term ok), must/avoid
          * rewards: requirement progress, courses that double count, goal fit, instructor
            quality (scaled by your teaching style match), course quality, predicted grade
          * penalties: workload over your cap, brutal combos, early/late classes, big classes
            (if you prefer small ones)
Step 3  Re-rank the top candidates on whole week things: lunch every day, dead time between
        classes, protected time actually fitting, heaviest day vs average day, free days.
Step 4  For the winners: predicted grades + GPA/BCPM projection, seat risk and backup sections,
        day by day load, flagged combos, and grid data for the visual.
"""
from __future__ import annotations

import os

import itertools
from dataclasses import asdict, dataclass, field
from typing import Optional

from ortools.sat.python import cp_model

from .buildings import transit_minutes
from .grades import GradeModel, gpa_projection
from .models import DAY_NAMES, Course, Meeting, Section, fmt_time, parse_days, parse_time
from .requirements import done_courses, double_counts, resolve
from .scoring import course_quality, difficulty_score, judge_instructor, workload_index

SCALE = 100


# ------------------------------------------------------------------ helpers
def commitment_meetings(items: list[dict]) -> list[tuple[str, Meeting]]:
    out = []
    for it in items or []:
        buf = int(it.get("buffer_min", 0))
        for d in parse_days(it["days"]):
            out.append((it.get("name", "busy"), Meeting(d, parse_time(it["start"]) - buf, parse_time(it["end"]) + buf,
                                                        it.get("campus", "any"), it.get("location", ""))))
    return out


def clash(a: Meeting, b: Meeting, cross_gap: int, mich_time: int) -> bool:
    if a.day != b.day:
        return False
    if a.start < b.end and b.start < a.end:
        return True
    first, second = (a, b) if a.end <= b.start else (b, a)
    gap = second.start - first.end
    need = transit_minutes(first.location, first.campus, second.location, second.campus, cross_gap)
    if need >= cross_gap:            # different campus: listed gap must be >= cross_gap
        return gap < cross_gap
    return gap + mich_time < need    # same campus: Michigan time gives you extra minutes


@dataclass
class Bundle:
    course: Course
    sections: tuple[Section, ...]

    @property
    def meetings(self) -> list[Meeting]:
        return [m for s in self.sections for m in s.meetings]

    @property
    def label(self) -> str:
        return " + ".join(f"{s.component} {s.section_id}" for s in self.sections)

    def lecture_instructor(self) -> Optional[str]:
        for s in self.sections:
            if s.instructor:
                return s.instructor
        return None


MAX_BUNDLES_PER_COURSE = int(os.environ.get("MAX_BUNDLES_PER_COURSE", "12"))
MAX_COMBOS_SCANNED = 20000


def _usable(s: Section, blocked, hard_start, allow_full) -> bool:
    if any(m.start < hard_start for m in s.meetings):
        return False
    if any(m.overlaps(bm) for m in s.meetings for _, bm in blocked):
        return False
    return allow_full or s.seats_open is None or s.seats_open > 0


def _midday(s: Section) -> float:
    return sum(abs((m.start + m.end) / 2 - 12.5 * 60) for m in s.meetings) / max(1, len(s.meetings))


PER_COMPONENT = int(os.environ.get("SECTIONS_PER_COMPONENT", "5"))


EXAM_COMPONENTS = {"MID", "EXM", "EXAM", "FIN", "FNL"}   # exam slots (a few evenings a term), not weekly classes


def _weekly(course) -> list:
    return [s for s in course.sections if s.component.upper() not in EXAM_COMPONENTS]


def _section_combos(course, blocked=(), hard_start=0, allow_full=True, first_only=False):
    """Valid section combinations, kept small: ignore exam slots, drop sections that can't work on their own,
    merge sections meeting at identical times, keep the best few per component, and use Atlas's own valid
    combos when known (ignoring ids that aren't real timetable sections, like "999")."""
    weekly = _weekly(course)
    real = {s.section_id for s in weekly}
    ok = {s.section_id: s for s in weekly if _usable(s, blocked, hard_start, allow_full)}
    exam_ids = {s.section_id for s in course.sections} - real
    if course.clusters:
        seen, any_cluster = set(), False
        for cl in course.clusters:
            ids = [x for x in cl if x in real and x not in exam_ids]
            if not ids:
                continue
            any_cluster = True
            if all(x in ok for x in ids):
                combo = tuple(ok[x] for x in ids)
                pat = tuple(sorted((m.day, m.start, m.end) for s in combo for m in s.meetings))
                if pat not in seen:
                    seen.add(pat)
                    yield combo
                    if len(seen) >= MAX_BUNDLES_PER_COURSE * 4:
                        return
        if any_cluster and seen:
            return
        if any_cluster and not seen:
            return          # Atlas's combos exist but none is usable for you
    by_comp: dict[str, dict] = {}
    for s in ok.values():
        pat = tuple(sorted((m.day, m.start, m.end) for m in s.meetings))
        comp = by_comp.setdefault(s.component, {})
        prev = comp.get(pat)
        if prev is None or (prev.instructor in (None, "Staff") and s.instructor not in (None, "Staff")):
            comp[pat] = s
    if not by_comp or len(by_comp) < len({s.component for s in weekly}):
        return          # some required component has no usable section
    lists = [sorted(c.values(), key=_midday)[:PER_COMPONENT] for c in by_comp.values()]
    yield from itertools.islice(itertools.product(*lists), MAX_COMBOS_SCANNED)


def explain_no_fit(course, blocked, hard_start, allow_full=False) -> dict:
    """Why a course has no usable section combination (for diagnosing)."""
    secs = _weekly(course)
    early = [s.section_id for s in secs if any(m.start < hard_start for m in s.meetings)]
    clash_c = [s.section_id for s in secs if any(m.overlaps(bm) for m in s.meetings for _, bm in blocked)]
    full = [s.section_id for s in secs if s.seats_open is not None and s.seats_open <= 0]
    ok = {s.section_id for s in secs if _usable(s, blocked, hard_start, allow_full)}
    comps = {}
    for s in secs:
        comps.setdefault(s.component, [0, 0])
        comps[s.component][0] += 1
        comps[s.component][1] += s.section_id in ok
    real = {s.section_id for s in secs}
    cl_ok = sum(bool([x for x in cl if x in real]) and all(x in ok for x in cl if x in real) for cl in course.clusters)
    missing_ids = sorted({x for cl in course.clusters for x in cl} - real)[:8]
    return {"sections": len(secs), "too_early": len(early), "clash_commitment": len(clash_c), "full": len(full),
            "usable_by_component": {k: f"{v[1]}/{v[0]}" for k, v in comps.items()},
            "atlas_combos": len(course.clusters), "atlas_combos_usable": cl_ok, "combo_ids_not_in_sections": missing_ids}


def build_bundles(course, blocked, hard_start, cross_gap, mich_time, allow_full=False,
                  max_bundles: Optional[int] = None) -> list[Bundle]:
    """Usable section combos for one course. Combos meeting at exactly the same times are equivalent for
    the schedule, so only one per time pattern is kept (the one with open seats, then a named instructor)."""
    by_pattern: dict = {}
    for combo in _section_combos(course, blocked, hard_start, allow_full):
        ms = [m for s in combo for m in s.meetings]
        if any(clash(a, b, cross_gap, mich_time) for a, b in itertools.combinations(ms, 2)):
            continue
        full = any(s.seats_open is not None and s.seats_open <= 0 for s in combo)
        pattern = tuple(sorted((m.day, m.start, m.end, m.location or m.campus) for m in ms))
        rank = (full, not any(s.instructor and s.instructor != "Staff" for s in combo))
        if pattern not in by_pattern or rank < by_pattern[pattern][0]:
            by_pattern[pattern] = (rank, combo)
    if not by_pattern and not allow_full:
        # every usable section shows 0 open seats (often true before seats are released): plan anyway
        return build_bundles(course, blocked, hard_start, cross_gap, mich_time, True, max_bundles)
    out = [Bundle(course, combo) for _, combo in by_pattern.values()]
    cap = max_bundles or MAX_BUNDLES_PER_COURSE
    if len(out) > cap:   # keep the ones closest to midday (the usual preferred window)
        out.sort(key=lambda b: sum(abs((m.start + m.end) / 2 - 12.5 * 60) for m in b.meetings) / max(1, len(b.meetings)))
        out = out[:cap]
    return out


def _conflict_pairs(bundles: dict, cross_gap: int, mich: int):
    """Pairs of bundles (different courses) that clash, found via 30-minute time buckets
    instead of comparing everything with everything."""
    buckets: dict = {}
    for code, bl in bundles.items():
        for i, b in enumerate(bl):
            for m in b.meetings:
                lo, hi = (m.start - cross_gap) // 30, (m.end + cross_gap) // 30
                for slot in range(lo, hi + 1):
                    buckets.setdefault((m.day, slot), []).append((code, i))
    seen = set()
    for items in buckets.values():
        for (c1, i1), (c2, i2) in itertools.combinations(items, 2):
            if c1 == c2:
                continue
            key = ((c1, i1), (c2, i2)) if (c1, i1) < (c2, i2) else ((c2, i2), (c1, i1))
            if key in seen:
                continue
            seen.add(key)
            if any(clash(a, b, cross_gap, mich) for a in bundles[c1][i1].meetings for b in bundles[c2][i2].meetings):
                yield key


def seat_risk(course: Course, b: Bundle) -> str:
    fill = course.fill_days if course.fill_days is not None else 7
    tight = any(s.capacity and s.seats_open is not None and s.seats_open / s.capacity < 0.1 for s in b.sections)
    if fill <= 3 or tight:
        return "high"
    if fill <= 7:
        return "medium"
    return "low"


# ------------------------------------------------------------------ week level evaluation
def _by_day(ms: list[Meeting]) -> dict[int, list[tuple[int, int]]]:
    d: dict[int, list[tuple[int, int]]] = {}
    for m in ms:
        d.setdefault(m.day, []).append((m.start, m.end))
    for k in d:
        d[k].sort()
    return d


def dead_time_hours(ms: list[Meeting]) -> float:
    total = 0
    for iv in _by_day(ms).values():
        for (_, e1), (s2, _) in zip(iv, iv[1:]):
            if 30 <= s2 - e1 <= 120:
                total += s2 - e1
    return total / 60


def lunch_ok(all_busy: list[Meeting], class_days: set[int], window=(660, 840), minutes=30) -> list[str]:
    """Days (with classes) that have no free `minutes` inside the lunch window."""
    bad = []
    by = _by_day(all_busy)
    for d in sorted(class_days):
        cur, found = window[0], False
        for s, e in by.get(d, []) + [(window[1], window[1])]:
            s2 = min(max(s, window[0]), window[1])
            if s2 - cur >= minutes:
                found = True
                break
            cur = max(cur, min(e, window[1]))
        if not found:
            bad.append(DAY_NAMES[d])
    return bad


def protected_time_fit(all_busy: list[Meeting], items: list[dict]) -> list[dict]:
    free: list[tuple[int, int, int]] = []
    by = _by_day(all_busy)
    for d in range(7):
        cur = 7 * 60
        for s, e in by.get(d, []) + [(22 * 60, 22 * 60)]:
            if s > cur:
                free.append((d, cur, s))
            cur = max(cur, e)
    results = []
    for it in sorted(items or [], key=lambda x: -x.get("min_block_hours", 1)):
        need = it["hours_per_week"] * 60
        blk = int(it.get("min_block_hours", 1) * 60)
        w0, w1 = (parse_time(t) for t in it.get("window", ["07:00", "22:00"]))
        got, new_free = 0, []
        for d, s, e in free:
            s2, e2 = max(s, w0), min(e, w1)
            if got < need and e2 - s2 >= blk:
                take = min(e2 - s2, need - got)
                got += take
                if s2 > s:
                    new_free.append((d, s, s2))
                if s2 + take < e:
                    new_free.append((d, s2 + take, e))
            else:
                new_free.append((d, s, e))
        free = new_free
        results.append({"name": it["name"], "needed_h": it["hours_per_week"], "found_h": round(got / 60, 1),
                        "fits": got >= need})
    return results


def day_loads(bl: list[Bundle], wl_pct: dict[str, float]) -> dict[str, float]:
    """Hours in class per day, scaled up by how heavy each course is (a rough 'effort hours')."""
    out = {d: 0.0 for d in DAY_NAMES[:5]}
    for b in bl:
        k = 1 + (wl_pct.get(b.course.code, 55) / 100)
        for m in b.meetings:
            if m.day < 5:
                out[DAY_NAMES[m.day]] += (m.end - m.start) / 60 * k
    return {d: round(v, 1) for d, v in out.items()}


# ------------------------------------------------------------------ result
@dataclass
class WeekPlan:
    rank: int
    score: float
    term: str
    courses: list[dict]
    credits: float
    workload_index: float
    gpa: dict
    requirement_progress: list[str]
    double_counted: dict
    goal_fit: dict
    day_load: dict
    heaviest_day: str
    dead_time_h: float
    lunch_missing_days: list[str]
    protected: list[dict]
    free_weekdays: list[str]
    combos: list[str]
    grid: list[dict]
    warnings: list[str] = field(default_factory=list)


def optimize_week(source, profile: dict, term: str = "", n_plans: int = 4,
                  extra_busy: Optional[list[dict]] = None, overrides: Optional[dict] = None) -> dict:
    ov = overrides or {}
    term = term or source.next_term()
    prefs = {**profile.get("preferences", {}), **ov.get("preferences", {})}
    W = {**profile.get("weights", {}), **ov.get("weights", {})}
    history = source.student_history()
    done = done_courses(history, profile.get("completed_extra"))
    must = set(profile.get("must_take", [])) | set(ov.get("must_take", []))
    avoid = set(profile.get("avoid", [])) | set(ov.get("avoid", []))
    prefer = set(ov.get("prefer", []))   # e.g. the semester plan's courses for this term
    cross_gap = int(prefs.get("cross_campus_gap", 30))
    mich = int(prefs.get("michigan_time_min", 10))
    win = prefs.get("class_window", ["09:00", "18:00"])
    soft_start, soft_end = parse_time(win[0]), parse_time(win[1])
    hard_start = parse_time(prefs.get("hard_earliest_start", "08:00"))
    lunch_win = tuple(parse_time(t) for t in prefs.get("lunch_window", ["11:00", "14:00"]))
    lunch_min = int(prefs.get("lunch_minutes", 30))

    blocked = commitment_meetings(profile.get("commitments", [])) + commitment_meetings(extra_busy or [])
    offered = [c for c in source.offerings(term) if c.code not in done and c.code not in avoid]
    groups = resolve(profile, source, done)
    dc = double_counts(groups)
    # Keep the solver small: the semester plan's courses, must takes, and the courses that count toward the
    # most open requirements. (Considering every eligible course made the free server run out of memory.)
    max_courses = int(os.environ.get("WEEK_MAX_COURSES", "22"))
    if len(offered) > max_courses:
        keep = [c for c in offered if c.code in prefer or c.code in must]
        rest = sorted((c for c in offered if c not in keep), key=lambda c: (-len(dc.get(c.code, [])), c.code))
        offered = keep + rest[:max(0, max_courses - len(keep))]
    gm = GradeModel(source, history)
    style = gm.teaching_style()
    cohort = source.cohort()
    combos = cohort.get("combos", [])

    goals = profile.get("goals", [])
    cinfo, bundles = {}, {}
    for c in offered:
        st = source.course_stats(c.code)
        text = " ".join(c.tags + [c.title.lower(), c.description.lower()])
        gfit = {g["name"]: g.get("weight", 1) if any(t.lower() in text for t in g.get("tags", [])) else 0 for g in goals}
        cinfo[c.code] = dict(stats=st, wl=workload_index(c.credits, st), quality=course_quality(st),
                             diff=difficulty_score(st), goal=gfit, course=c)
        bl = build_bundles(c, blocked, hard_start, cross_gap, mich, prefs.get("allow_full_sections", False))
        if bl:
            bundles[c.code] = bl

    vcache: dict = {}

    def verdict(name, code):
        key = (name, code)
        if key not in vcache:
            vcache[key] = judge_instructor(source.instructor_stats(name), code, source.course_stats(code))
        return vcache[key]

    pred_cache: dict = {}

    def pred(code, inst):
        key = (code, inst)
        if key not in pred_cache:
            pred_cache[key] = gm.predict(code, inst)
        return pred_cache[key]

    iq_w = W.get("instructor_quality", 3) * style["multiplier"]

    def bundle_value(b: Bundle) -> float:
        inst = b.lecture_instructor()
        iq = verdict(inst, b.course.code).score if inst else 60
        p = pred(b.course.code, inst)
        g = (p.gpa - 3.3) * 10 if p else 0
        early = sum(max(0, soft_start - m.start) for m in b.meetings) / 60
        late = sum(max(0, m.end - soft_end) for m in b.meetings) / 60
        return iq_w * (iq - 60) / 10 + W.get("grades", 2) * g / 2 - W.get("time_of_day", 2) * (early + late) * 1.5

    def course_value(code: str) -> float:
        ci = cinfo[code]
        c = ci["course"]
        size_pen = 0.0
        if prefs.get("prefer_small_classes") and c.class_size:
            size_pen = min(3.0, c.class_size / 150)
        bonus = 40.0 if code in prefer else 0.0
        return (bonus + W.get("goal_fit", 3) * sum(ci["goal"].values())
                + W.get("double_counting", 3) * max(0, len(dc.get(code, [])) - 1) * 2
                + (ci["quality"] - 60) / 10 - W.get("class_size", 1) * size_pen)

    m = cp_model.CpModel()
    take, x = {}, {}
    for code, bl in bundles.items():
        take[code] = m.NewBoolVar(f"take_{code}")
        for i in range(len(bl)):
            x[code, i] = m.NewBoolVar(f"x_{code}_{i}")
        m.Add(sum(x[code, i] for i in range(len(bl))) == take[code])

    warnings = []
    if style["n"] >= 4 and abs(style["multiplier"] - 1.0) >= 0.1:
        warnings.append(f"Teaching style match: {style['finding']}")
    for code in must:
        if code in take:
            m.Add(take[code] == 1)
        else:
            warnings.append(f"{code} is a must take but no section fits (or it isn't offered in {term}).")

    for code in take:
        c = cinfo[code]["course"]
        for ors in c.prereqs:
            if not any(o in done for o in ors):
                m.Add(take[code] == 0)
        for ors in c.coreqs:
            if any(o in done for o in ors):
                continue
            conc = [take[o] for o in ors if o in take]
            if conc:
                m.Add(sum(conc) >= take[code])
            else:
                m.Add(take[code] == 0)

    keys = [(code, i) for code, bl in bundles.items() for i in range(len(bl))]
    for (c1, i1), (c2, i2) in _conflict_pairs(bundles, cross_gap, mich):
        m.Add(x[c1, i1] + x[c2, i2] <= 1)

    cred = {code: int(round(cinfo[code]["course"].credits * 2)) for code in take}
    tot = sum(cred[c] * take[c] for c in take)
    m.Add(tot >= int(prefs.get("min_credits", 12) * 2))
    m.Add(tot <= int(prefs.get("max_credits", 18) * 2))

    prog = []
    for gi, g in enumerate(groups):
        mem = [take[o] for o in g["options"] if o in take]
        if g["remaining"] > 0 and mem:
            p = m.NewIntVar(0, g["remaining"], f"prog_{gi}")
            m.Add(p <= sum(mem))
            prog.append(p)

    wl_total = sum(int(cinfo[c]["wl"] * SCALE) * take[c] for c in take)
    excess = m.NewIntVar(0, 100 * SCALE, "excess")
    m.Add(excess >= wl_total - int(float(prefs.get("max_workload_index", 4.5)) * SCALE))

    combo_terms = []
    for cb in combos:
        a, b = cb["courses"]
        if a in take and b in take:
            both = m.NewBoolVar(f"combo_{a}_{b}")
            m.AddBoolAnd([take[a], take[b]]).OnlyEnforceIf(both)
            m.Add(take[a] + take[b] <= 1 + both)
            combo_terms.append(int(W.get("combos", 3) * cb["penalty"] * SCALE) * both)

    obj = [int(W.get("requirement_progress", 5) * 4 * SCALE) * p for p in prog]
    obj += [int(course_value(c) * SCALE) * take[c] for c in take]
    obj += [int(bundle_value(bundles[c][i]) * SCALE) * x[c, i] for (c, i) in keys]
    obj.append(-int(W.get("workload_balance", 4) * 4) * excess)
    m.Maximize(sum(obj) - sum(combo_terms))

    solver = cp_model.CpSolver()
    budget = float(os.environ.get("WEEK_BUDGET_SECONDS", "40"))   # total for all the alternatives
    rounds = min(max(30, n_plans * 8), int(os.environ.get("WEEK_MAX_ROUNDS", str(n_plans * 3))))
    solver.parameters.num_workers = int(os.environ.get("SOLVER_WORKERS", "8"))
    if os.environ.get("SOLVER_MAX_MB"):
        solver.parameters.max_memory_in_mb = int(os.environ["SOLVER_MAX_MB"])
    import time as _t
    t_end = _t.time() + budget
    cands = []
    for k in range(rounds):
        left = t_end - _t.time()
        if left < 1 and cands:
            break
        solver.parameters.max_time_in_seconds = max(1.0, min(8.0 if k == 0 else 3.0, left))
        st = solver.Solve(m)
        if st not in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            break
        chosen = [(c, i) for (c, i) in keys if solver.Value(x[c, i])]
        cands.append((solver.ObjectiveValue() / SCALE, chosen))
        on = [c for c in take if solver.Value(take[c])]
        off = [c for c in take if not solver.Value(take[c])]
        m.Add(sum(take[c] for c in on) - sum(take[c] for c in off) <= len(on) - 1)
    if not cands:
        return {"term": term, "plans": [], "warnings": warnings + [
            "No schedule fits. Loosen credits, class window, must_take, or commitments."]}

    wl_pct = {c: (cinfo[c]["stats"].workload_pct or 55) for c in cinfo}
    ranked = []
    for base, chosen in cands:
        bl = [bundles[c][i] for c, i in chosen]
        cms = [mm for b in bl for mm in b.meetings]
        busy = cms + [bm for _, bm in blocked]
        days = {mm.day for mm in cms}
        dead = dead_time_hours(cms)
        prot = protected_time_fit(busy, profile.get("protected_time", []))
        miss = sum(max(0, p["needed_h"] - p["found_h"]) for p in prot)
        lunch_bad = lunch_ok(busy, {d for d in days if d < 5}, lunch_win, lunch_min) if prefs.get("guarantee_lunch", True) else []
        loads = day_loads(bl, wl_pct)
        vals = [v for v in loads.values() if v > 0]
        spike = (max(vals) - sum(vals) / len(vals)) if vals else 0
        free_wd = [DAY_NAMES[d] for d in range(5) if d not in days]
        free_bonus = min(len(free_wd), int(prefs.get("free_days_wanted", 0))) * 8
        score = (base - W.get("compact_days", 2) * dead * 1.5 - W.get("protected_time", 4) * miss * 2
                 - 25 * len(lunch_bad) - W.get("heavy_day", 2) * spike * 1.5 + free_bonus)
        ranked.append((score, chosen, bl, dead, prot, lunch_bad, loads, free_wd))
    ranked.sort(key=lambda r: -r[0])
    top_score = ranked[0][0] or 1

    plans = []
    for rank, (score, chosen, bl, dead, prot, lunch_bad, loads, free_wd) in enumerate(ranked[:n_plans], 1):
        taken = {b.course.code for b in bl}
        rows, planned_gpa, grid = [], [], []
        other_ms = lambda code: [mm for b in bl if b.course.code != code for mm in b.meetings]  # noqa: E731
        for b in sorted(bl, key=lambda b: b.course.code):
            ci, c = cinfo[b.course.code], b.course
            inst = b.lecture_instructor()
            p = pred(c.code, inst)
            if p:
                planned_gpa.append((c.code, c.credits, p.gpa))
            risk = seat_risk(c, b)
            backups = []
            if risk != "low":
                rest = other_ms(c.code) + [bm for _, bm in blocked]
                for alt in bundles[c.code]:
                    if alt.sections != b.sections and not any(clash(a, o, cross_gap, mich) for a in alt.meetings for o in rest):
                        backups.append(alt.label)
                    if len(backups) == 2:
                        break
            st = ci["stats"]
            rows.append({
                "course": c.code, "title": c.title, "credits": c.credits, "sections": b.label,
                "times": "; ".join(f"{s.component} " + ", ".join(
                    f"{DAY_NAMES[mm.day]} {fmt_time(mm.start)} to {fmt_time(mm.end)} @ {mm.location or mm.campus}"
                    for mm in s.meetings) for s in b.sections),
                "instructor": verdict(inst, c.code).summary() if inst else None,
                "predicted_grade": p.as_dict() if p else None,
                "atlas": {"workload_pct": st.workload_pct, "median_grade": st.median_grade, "mean_gpa": st.mean_gpa,
                          "desire_pct": st.desire_pct, "understanding_pct": st.understanding_pct},
                "difficulty_0_100": ci["diff"], "workload_index": round(ci["wl"], 2),
                "class_size": c.class_size, "format": c.format,
                "seat_risk": risk, "backup_sections": backups,
                "counts_toward": dc.get(c.code, []),
            })
            for s in b.sections:
                for mm in s.meetings:
                    grid.append({"day": DAY_NAMES[mm.day], "start": fmt_time(mm.start), "end": fmt_time(mm.end),
                                 "start_min": mm.start, "end_min": mm.end, "label": c.code,
                                 "kind": s.component, "where": mm.location or mm.campus, "campus": mm.campus})
        for name, bm in blocked:
            grid.append({"day": DAY_NAMES[bm.day], "start": fmt_time(bm.start), "end": fmt_time(bm.end),
                         "start_min": bm.start, "end_min": bm.end, "label": name, "kind": "commitment",
                         "where": bm.location or "", "campus": bm.campus})
        req = []
        for g in groups:
            if g["remaining"] > 0:
                n = sum(1 for o in g["options"] if o in taken)
                if n:
                    req.append(f"{g['group']}: {min(n, g['remaining'])} of {g['remaining']} remaining")
        flagged = [f"{' + '.join(cb['courses'])}: {cb['note']}" for cb in combos if set(cb["courses"]) <= taken]
        wl = sum(cinfo[b.course.code]["wl"] for b in bl)
        w = list(warnings)
        if wl > float(prefs.get("max_workload_index", 4.5)):
            w.append(f"Workload index {wl:.2f} is over your cap of {prefs.get('max_workload_index', 4.5)}.")
        for pt in prot:
            if not pt["fits"]:
                w.append(f"Only {pt['found_h']}h of the {pt['needed_h']}h you wanted for {pt['name']}.")
        if lunch_bad:
            w.append(f"No {lunch_min} min lunch window on: {', '.join(lunch_bad)}.")
        missing = sorted(prefer - taken)
        extra = sorted(taken - prefer) if prefer else []
        if missing:
            w.append(f"Differs from your semester plan: leaves out {', '.join(missing)}"
                     + (f", adds {', '.join(extra)}" if extra else "") + ".")
        goal_tot = {g["name"]: sum(1 for b in bl if cinfo[b.course.code]["goal"].get(g["name"])) for g in goals}
        heaviest = max(loads, key=loads.get) if any(loads.values()) else ""
        plans.append(WeekPlan(rank, round(100 * score / top_score, 1) if top_score > 0 else round(score, 1), term,
                              rows, sum(b.course.credits for b in bl), round(wl, 2),
                              gpa_projection(history, planned_gpa, profile.get("bcpm_subjects")),
                              req, {c: dc[c] for c in taken if len(dc.get(c, [])) > 1}, goal_tot, loads, heaviest,
                              round(dead, 1), lunch_bad, prot, free_wd, flagged, grid, w))
    return {"term": term, "teaching_style": style, "plans": [asdict(p) for p in plans]}
