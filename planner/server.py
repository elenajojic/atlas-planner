"""Atlas Planner: an MCP server for Claude Desktop."""
from __future__ import annotations

import copy
import os
import json
import shutil
from typing import Optional

import yaml

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as FastMCP
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP

from . import friends, render
from .data_source import PKG_ROOT, AtlasSource, get_source, user_home
from .grades import GradeModel, gpa_projection
from .longterm import find_best_abroad, offering_confidence, plan_semesters, what_if
from .requirements import done_courses, resolve, summary
from .scoring import course_quality, difficulty_score, judge_instructor, workload_index
from .week import optimize_week

INSTRUCTIONS = """Atlas Planner builds University of Michigan schedules from Atlas data plus the user's profile.

FIRST: call setup_status. If setup isn't done, run the guided setup ONE step at a time, in this order,
asking the user and saving answers with update_profile as you go:
  1. Atlas login (atlas_login) when the source is 'atlas'.
  2. Career goals, in their words. Turn them into goals with tags and weights.
  3. Majors / minors / premed: search with list_programs(query=...). Save the exact Atlas plan names to
     `atlas_plans` (major first, max 3) and to `programs` (plus 'Premed (template)' for premed).
     Then call refresh_atlas_data (takes a few minutes the first time; tell the user).
  4. Call get_progress and walk them through what's done and what's left.
  5. What to optimize for (instructors, grades, light workload, free mornings, double counting...).
     Map their answer onto `weights` (0 to 5) and save their own words in `optimize_for`.
  6. Preferred class window, hard earliest start, credit range.
  7. Fixed commitments. If a calendar connector is available, read recurring weekly events and offer
     to add them (with commute buffers). Ask about protected time (volunteering, gym, MCAT study).
  8. Long term: graduation term, MCAT term, spring/summer, study abroad.
Then set setup_done: true.

AFTER optimize_schedule: show the user a visual week grid for the top option using the returned
`grid` data (day, start_min, end_min, label, kind, where), with a scorecard (credits, workload index,
predicted term GPA, BCPM, heaviest day, lunch, protected time). The tool also writes an HTML view
(view_file) the user can open. Present options with trade offs. Predicted grades are ranges with a
confidence level: always say so. Never register for classes. If data_status says 'sample', tell the
user the results use fake data. Atlas data is for the user's personal planning only.

DECISION LAYER: after plan_all_semesters, lead with its `alerts` (critical first, then heads_up, then
opportunity), each with its options, before listing courses. For any "why is X in Y" / "what if I drop X" /
"why is this term heavy" question, call explain_plan (fast) instead of re-planning. When the user is
weighing graduation dates, workload or summers, offer compare_paths and give its recommendation and
tradeoffs. Before registration, offer registration_strategy (order to register, backups, fallbacks)."""

mcp = FastMCP("atlas-planner", instructions=INSTRUCTIONS)


def _tool(fn):
    """Register a tool that returns readable errors instead of a generic failure."""
    import functools
    import traceback

    @functools.wraps(fn)
    def wrapper(*a, **k):
        try:
            return fn(*a, **k)
        except Exception as e:
            tb = traceback.extract_tb(e.__traceback__)[-1]
            return json.dumps({"error": f"{e.__class__.__name__}: {e}",
                               "where": f"{tb.filename.rsplit('/', 1)[-1]}:{tb.lineno} in {tb.name}",
                               "hint": "If it says the session expired, run atlas_login again."}, indent=1)
    TOOLS.append(wrapper)
    return mcp.tool()(wrapper)
TOOLS: list = []          # (name, wrapped fn) for re-registering in the hosted app


def _profile_path():
    return user_home() / "profile.yaml"


def _last_week():
    return user_home() / "last_week.json"


def _profile() -> dict:
    if not _profile_path().exists():
        shutil.copy(PKG_ROOT / "profile.example.yaml", _profile_path())
    return yaml.safe_load(_profile_path().read_text()) or {}


def _save(p: dict) -> None:
    if _profile_path().exists():
        shutil.copy(_profile_path(), _profile_path().with_suffix(".yaml.bak"))
    _profile_path().write_text(yaml.safe_dump(p, sort_keys=False, allow_unicode=True))


def _merge(a: dict, b: dict) -> dict:
    out = copy.deepcopy(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _j(x) -> str:
    return json.dumps(x, indent=1, default=str)


# ------------------------------------------------------------------ status + setup
@_tool
def data_status() -> str:
    """Which data source is active (sample or live Atlas), login state, and where the user's files live."""
    src = get_source()
    info = {"source": src.name, "label": src.label, "your_folder": str(user_home()), "profile": str(_profile_path())}
    if isinstance(src, AtlasSource):
        info["atlas_logged_in"] = src.logged_in()
    return _j(info)


@_tool
def atlas_login() -> str:
    """Open a browser window so the user signs into Atlas with Weblogin + Duo. Session is saved on this computer only."""
    return AtlasSource().login()


@_tool
def setup_status() -> str:
    """Checklist of the guided setup: which steps are done and what to ask next."""
    p, src = _profile(), get_source()
    steps = [
        ("login", src.name == "sample" or (isinstance(src, AtlasSource) and src.logged_in()), "Log into Atlas (atlas_login)."),
        ("goals", bool(p.get("goals")) and p.get("name") != "Your Name", "Ask about career goals."),
        ("programs", bool(p.get("programs")), "Pick majors/minors/premed from list_programs."),
        ("progress_reviewed", bool(p.get("progress_reviewed")), "Show get_progress and confirm it looks right."),
        ("optimize_for", bool(p.get("optimize_for")), "Ask what to optimize for and set weights."),
        ("class_window", bool(p.get("preferences", {}).get("class_window")), "Ask preferred class window and credit range."),
        ("commitments", p.get("commitments_reviewed", False), "Review commitments (check calendar) and protected time."),
        ("long_term", p.get("long_term_reviewed", False), "Ask graduation term, MCAT term, spring/summer, study abroad."),
    ]
    todo = [{"step": s, "ask": a} for s, ok, a in steps if not ok]
    if p.get("setup_done"):
        return _j({"setup_done": True, "source": src.name, "done": [s for s, ok, _ in steps if ok],
                   "optional_still_open": todo, "next": []})
    return _j({"setup_done": False, "source": src.name,
               "done": [s for s, ok, _ in steps if ok], "next": todo[:1], "remaining": todo})


@_tool
def list_programs(query: str = "") -> str:
    """Majors, minors and tracks. With a query (live Atlas), searches Atlas's plan list, e.g. 'Biomedical'.
    Engineering minors have one plan per major: a BME student's AI minor is 'AI BSE BioE'."""
    src = get_source()
    if query and isinstance(src, AtlasSource):
        return _j({"matches": src.search_programs(query),
                   "how_to_save": "update_profile({'atlas_plans': [{'plan': <plan_name>, 'type': 'MAJ'|'MIN'}, ...], "
                                  "'programs': [<same plan names>, 'Premed (template)']}). Max 3 Atlas plans, major first."})
    return _j(src.programs())


@_tool
def refresh_atlas_data(term: str = "") -> str:
    """Prefetch your audit, the catalog, section times and evaluations for the courses you still need.
    Slow the first time (requests are spaced out to be polite to Atlas), then cached for a week.
    Run after login, after changing majors/minors, and before the first optimize_schedule."""
    src = get_source()
    if not isinstance(src, AtlasSource):
        return _j({"source": src.name, "note": "Using sample data; nothing to refresh."})
    return _j(src.warm(term))


@_tool
def get_progress() -> str:
    """What the user has completed (from their Atlas academic history) and what's left for each program."""
    src, p = get_source(), _profile()
    hist = src.student_history()
    done = done_courses(hist, p.get("completed_extra"))
    groups = resolve(p, src, done)
    gpa = gpa_projection(hist, [], p.get("bcpm_subjects"))
    atlas_gpa = src.audit_gpa() if isinstance(src, AtlasSource) else None
    return _j({"history": [h.__dict__ for h in hist],
               "gpa_now": gpa["cumulative_now"] if gpa["cumulative_now"] is not None else atlas_gpa,
               "gpa_source": "your my_grades" if gpa["cumulative_now"] is not None else ("Atlas audit" if atlas_gpa else None),
               "bcpm_now": gpa["bcpm_now"],
               "grades_note": None if not isinstance(src, AtlasSource) else
               "Atlas shows courses but not letter grades ('CR' = completed). Add grades under my_grades in the "
               "profile to get BCPM and grade-based predictions.",
               "in_progress": [h.course for h in hist if not h.grade],
               "requirements": summary(groups),
               "note": "In progress courses count as done when planning future terms."})


@_tool
def get_profile() -> str:
    """The user's planning profile (YAML)."""
    _profile()
    return f"# file: {_profile_path()}\n" + _profile_path().read_text()


@_tool
def update_profile(changes: dict) -> str:
    """Merge changes into the profile (nested dicts merge, lists are replaced). Example:
    {"name": "Elena", "programs": ["BME BSE", "AI minor"], "preferences": {"class_window": ["10:00","17:00"]},
     "weights": {"instructor_quality": 5}, "optimize_for": ["great teachers", "balanced workload"], "setup_done": true}"""
    p = _merge(_profile(), changes)
    _save(p)
    return f"Saved. Changed keys: {', '.join(changes)}"


@_tool
def save_profile(profile_yaml: str) -> str:
    """Replace the whole profile with this YAML document."""
    data = yaml.safe_load(profile_yaml)
    if not isinstance(data, dict) or "preferences" not in data or "weights" not in data:
        return "Not saved: needs at least 'preferences' and 'weights'."
    _save(data)
    return f"Saved to {_profile_path()} (previous version kept as profile.yaml.bak)."


@_tool
def import_external_ratings(csv_text: str) -> str:
    """Save a second source of instructor ratings the user collected themselves.
    CSV: instructor,rating,difficulty,num_ratings,source (rating and difficulty 1 to 5)."""
    lines = csv_text.strip().splitlines()
    if not lines or not lines[0].lower().startswith("instructor"):
        return "First line must be: instructor,rating,difficulty,num_ratings,source"
    (user_home() / "external_ratings.csv").write_text(csv_text.strip() + "\n")
    return f"Saved {len(lines) - 1} instructors."


# ------------------------------------------------------------------ research
@_tool
def search_offerings(query: str = "", term: str = "") -> str:
    """Courses offered in a term (default: next term) matching a code, word, or tag. Empty query lists all."""
    src = get_source()
    q, rows = query.lower(), []
    for c in src.offerings(term):
        if q in " ".join([c.code, c.title, c.description, *c.tags]).lower():
            st = src.course_stats(c.code)
            rows.append({"course": c.code, "title": c.title, "credits": c.credits, "workload_pct": st.workload_pct,
                         "median_grade": st.median_grade, "difficulty_0_100": difficulty_score(st),
                         "class_size": c.class_size, "format": c.format})
    out = {"term": term or src.next_term(), "count": len(rows), "courses": rows}
    if q:
        offered_now = {r["course"] for r in rows}
        conf = offering_confidence(src.catalog())
        other = [{"course": c.code, "title": c.title, "usually_offered": [s for s, v in conf[c.code].items() if v >= 0.6]}
                 for c in src.catalog() if c.code not in offered_now
                 and q in " ".join([c.code, c.title, c.description, *c.tags]).lower()]
        if other:
            out["not_offered_this_term"] = other
    return _j(out)


@_tool
def course_report(course: str) -> str:
    """Deep dive on one course: Atlas stats, difficulty, offering history and confidence, typical timing for
    similar students, and every instructor cross compared (2nd source, grading vs course average, trends)
    with the user's predicted grade under each."""
    src = get_source()
    cat = {c.code.lower(): c for c in src.catalog()}
    c = cat.get(course.lower())
    if not c:
        return f"{course} not found."
    if hasattr(src, "enrich"):
        src.enrich(c.code)
    st = src.course_stats(c.code)
    gm = GradeModel(src, src.student_history())
    nxt = {x.code: x for x in src.offerings("")}
    insts = sorted({s.instructor for s in (nxt[c.code].sections if c.code in nxt else []) if s.instructor})
    teachers = []
    for n in insts:
        v = judge_instructor(src.instructor_stats(n), c.code, st)
        p = gm.predict(c.code, n)
        teachers.append({"name": n, "score": round(v.score), "atlas_clarity": v.atlas, "second_source": v.external,
                         "grading_vs_course_avg": v.grading_vs_course, "gpa_trend_per_term": v.gpa_trend_per_term,
                         "clarity_trend_per_term": v.clarity_trend_per_term, "flags": v.flags,
                         "your_predicted_grade": p.as_dict() if p else None})
    teachers.sort(key=lambda t: -t["score"])
    typ = src.cohort().get("typical_paths", {}).get("courses", {}).get(c.code)
    p = gm.predict(c.code)
    return _j({"course": c.code, "title": c.title, "credits": c.credits, "prereqs": c.prereqs, "coreqs": c.coreqs,
               "prereq_text_from_atlas": getattr(c, "prereq_text", None),
               "atlas": st.__dict__, "difficulty_0_100": difficulty_score(st),
               "course_quality_0_100": round(course_quality(st)), "workload_index": round(workload_index(c.credits, st), 2),
               "class_size": c.class_size, "format": c.format, "fills_in_days": c.fill_days,
               "offering_confidence": offering_confidence(src.catalog())[c.code], "offered_history": c.offered_history,
               "students_like_you": typ, "your_predicted_grade_any_instructor": p.as_dict() if p else None,
               "instructors_next_term": teachers})


@_tool
def compare_courses(courses: list[str]) -> str:
    """Side by side: difficulty, workload, average grade, YOUR predicted grade, best instructor, offering pattern."""
    src = get_source()
    cat = {c.code.lower(): c for c in src.catalog()}
    nxt = {x.code: x for x in src.offerings("")}
    gm = GradeModel(src, src.student_history())
    conf = offering_confidence(src.catalog())
    out = []
    for code in courses:
        c = cat.get(code.lower())
        if not c:
            out.append({"course": code, "error": "not found"})
            continue
        st = src.course_stats(c.code)
        best = None
        if c.code in nxt:
            vs = [judge_instructor(src.instructor_stats(s.instructor), c.code, st) for s in nxt[c.code].sections if s.instructor]
            best = max(vs, key=lambda v: v.score).summary() if vs else None
        p = gm.predict(c.code)
        out.append({"course": c.code, "credits": c.credits, "difficulty_0_100": difficulty_score(st),
                    "workload_pct": st.workload_pct, "mean_gpa": st.mean_gpa, "median_grade": st.median_grade,
                    "your_predicted": p.range if p else None, "confidence": p.confidence if p else None,
                    "best_instructor_next_term": best, "offering_confidence": conf[c.code],
                    "class_size": c.class_size, "format": c.format})
    return _j(out)


# ------------------------------------------------------------------ planning
def _aligned(src, prof, term, n_plans, extra_busy, overrides, abroad_term=None):
    """Semester plan <-> week loop, so both always agree.
    If the semester plan's courses for the next term can't all fit real sections together,
    push the ones that don't fit to another term and re-plan (up to 4 rounds)."""
    t = term or src.next_term()
    ov = dict(overrides or {})
    pushed: list[str] = []
    lt, wk, planned = {}, {"term": t, "plans": []}, []
    max_rounds = int(os.environ.get("ALIGN_ROUNDS", "4"))
    for i in range(max_rounds):
        lt_ov = {**ov, "extra_busy": extra_busy or [],
                 "avoid_in_term": {**ov.get("avoid_in_term", {}), t: list(pushed)}}
        lt = plan_semesters(src, prof, lt_ov, abroad_term=abroad_term, time_limit=5)
        planned = [c["course"] for pt in lt.get("terms", []) if pt["term"] == t for c in pt["courses"]]
        wk = optimize_week(src, prof, term, n_plans, extra_busy, {**ov, "prefer": planned})
        if not wk["plans"]:
            break
        missing = [c for c in planned if c not in {x["course"] for x in wk["plans"][0]["courses"]}]
        if not missing or i == max_rounds - 1:
            break
        pushed += missing
    note = f"Matched to the semester plan for {t}: {', '.join(planned)}."
    if pushed:
        note += f" Moved {', '.join(pushed)} out of {t} because their sections don't fit together with your week."
    wk["semester_plan_note"] = note
    lt["next_term_note"] = note
    return lt, wk


def _write_week(src, r, open_view):
    r["source"], r["source_label"] = src.name, src.label
    _last_week().write_text(json.dumps(r, default=str))
    if r["plans"]:
        r["view_file"] = render.write_view(f"week_{r['term']}", render.week_html(r, "Atlas Planner: week options"), open_view)


def _brief_plan(r: dict) -> dict:
    if not r.get("ok"):
        return r
    out = {k: r.get(k) for k in ("ok", "plan_quality", "does_it_all_fit", "unmet_requirements", "unmet_details", "program_priority",
                                 "mcat", "gpa_projection", "heaviest_term", "next_term_note",
                                 "next_term_no_section_fits", "not_offered_often_enough")}
    out["terms"] = [{"term": t["term"], "name": t["name"], "credits": t["credits"],
                     "workload_index": t["workload_index"], "predicted_term_gpa": t["predicted_term_gpa"],
                     "courses": [{"course": c["course"], "title": c["title"], "credits": c["credits"],
                                  "predicted_grade": c["predicted_grade"], "difficulty_0_100": c["difficulty_0_100"],
                                  "counts_toward": [x[:60] for x in c["counts_toward"]] or "prerequisite only",
                                  **({"mcat_course": True} if c["mcat_course"] else {})} for c in t["courses"]]}
                    for t in r["terms"]]
    ins = r.get("insights") or {}
    out["alerts"] = [{k: a[k] for k in ("level", "title", "detail", "options") if k in a} for a in ins.get("alerts", [])]
    out["assumptions"] = ins.get("assumptions", [])
    out["application_story"] = r.get("application_story")
    out["more"] = 'For "why" questions call explain_plan. Call again with detail="full" for offering history, typical timing, critical path and double counting.'
    return out


def _brief_week(r: dict) -> dict:
    out = {k: r.get(k) for k in ("term", "semester_plan_note", "warnings", "source_label") if r.get(k)}
    out["plans"] = []
    for p in r.get("plans", []):
        out["plans"].append({
            "rank": p["rank"], "score": p["score"], "credits": p["credits"], "workload_index": p["workload_index"],
            "predicted_term_gpa": p["gpa"]["term_gpa"], "bcpm_after": p["gpa"].get("bcpm_after"),
            "heaviest_day": p["heaviest_day"], "gap_hours_between_classes": p["dead_time_h"],
            "days_without_lunch": p["lunch_missing_days"], "free_weekdays": p["free_weekdays"],
            "protected_time": [f"{x['name']}: {'fits' if x['fits'] else 'does NOT fit'}" for x in p["protected"]],
            "warnings": p["warnings"],
            "courses": [{"course": c["course"], "title": c["title"], "credits": c["credits"], "sections": c["sections"],
                         "times": c["times"], "instructor": c["instructor"],
                         "predicted_grade": (f"{c['predicted_grade']['range']} ({c['predicted_grade']['confidence']} confidence)"
                                             if c["predicted_grade"] else None),
                         "backup_sections": c["backup_sections"], "counts_toward": [x[:60] for x in c["counts_toward"]]}
                        for c in p["courses"]],
            "grid": [[g["day"], g["start_min"], g["end_min"], g["label"], g["kind"], g["where"]] for g in p["grid"]]})
    out["grid_columns"] = ["day", "start_min", "end_min", "label", "kind", "where"]
    out["more"] = 'Call again with detail="full" for Atlas stats, seat risk, requirement progress and goal fit.'
    return out


@_tool
def optimize_schedule(term: str = "", n_plans: int = 3, extra_busy: Optional[list[dict]] = None,
                      overrides: Optional[dict] = None, open_view: bool = False,
                      follow_semester_plan: bool = True, detail: str = "brief") -> str:
    """Build and rank full weekly schedules for one term (default: next term).

    detail: "brief" (default, fast to read) or "full" (every stat).

    follow_semester_plan: keep this term consistent with the multi semester plan (default). Set False
      to let the week optimizer choose freely.
    extra_busy: recurring weekly events to avoid, e.g. from the user's calendar:
      [{"name":"Shift","days":"MW","start":"17:00","end":"20:00","buffer_min":15,"campus":"Central"}]
      Days use M Tu W Th F Sa Su.
    overrides: one off tweaks, e.g. {"must_take":["CHEM 210"],"avoid":["EECS 281"],
      "preferences":{"class_window":["10:00","16:00"],"max_credits":16},"weights":{"instructor_quality":5}}
    open_view: also open the HTML view in the user's browser.
    Returns plans with a `grid` for drawing the week, plus `view_file`."""
    src, prof = get_source(), _profile()
    if follow_semester_plan:
        _, r = _aligned(src, prof, term, n_plans, extra_busy, overrides)
    else:
        r = optimize_week(src, prof, term, n_plans, extra_busy, overrides)
    _write_week(src, r, open_view)
    return _j(r if detail == "full" else _brief_week(r))


@_tool
def plan_all_semesters(overrides: Optional[dict] = None, study_abroad_term: str = "",
                       extra_busy: Optional[list[dict]] = None, open_view: bool = False,
                       detail: str = "brief") -> str:
    """Plan every remaining semester: offering history, prereq order, double counting, critical path, MCAT
    timing, students like you, balanced workload, optional spring/summer. Next term is checked against real
    section times so it matches optimize_schedule. Includes GPA/BCPM projection, application story score
    and heatmap. overrides may include {"plan":{...}, "must_take":[], "avoid":[]}.
    detail: "brief" (default) or "full". Always tell the user what `does_it_all_fit` says."""
    src, prof = get_source(), _profile()
    r, wk = _aligned(src, prof, "", 3, extra_busy, overrides, abroad_term=study_abroad_term or None)
    _write_week(src, wk, False)
    _save_plan(r)
    if r.get("ok"):
        r["view_file"] = render.write_view("four_year_plan", render.heatmap_html(r, "Atlas Planner: semester plan"), open_view)
    return _j(r if detail == "full" else _brief_plan(r))


@_tool
def find_study_abroad_term() -> str:
    """Try going abroad each eligible semester and rank them by how little it disrupts the plan."""
    return _j(find_best_abroad(get_source(), _profile()))


@_tool
def what_if_scenario(changes: dict) -> str:
    """Re-plan with a hypothetical and show what changes. Keys: drop [courses], fail [courses],
    add_program [names], remove_program [names], must_take [courses], mcat_term "SP28",
    use_spring_summer true/false, graduation_term "WN29", study_abroad_term "FA27"."""
    r = what_if(get_source(), _profile(), changes)
    new = r.pop("new_plan", None) or {}
    r["new_plan_by_term"] = {t["term"]: [c["course"] for c in t["courses"]] for t in new.get("terms", [])}
    r["does_it_all_fit_after"] = new.get("does_it_all_fit")
    return _j(r)


# ------------------------------------------------------------------ decision layer
def _last_plan():
    return user_home() / "last_plan.json"


def _save_plan(r: dict) -> None:
    if not r.get("ok"):
        return
    keep = {"saved_from": "plan_all_semesters",
            "terms": [{"term": t["term"], "name": t["name"], "credits": t["credits"],
                       "workload_index": t["workload_index"], "predicted_term_gpa": t["predicted_term_gpa"],
                       "courses": [{k: c.get(k) for k in ("course", "title", "credits", "predicted_grade",
                                                          "difficulty_0_100", "workload_index", "counts_toward",
                                                          "offered_last_5_years", "mcat_course")}
                                   for c in t["courses"]]} for t in r["terms"]],
            "insights": r.get("insights", {}), "does_it_all_fit": r.get("does_it_all_fit"),
            "gpa_projection": r.get("gpa_projection"), "mcat": r.get("mcat")}
    _last_plan().write_text(json.dumps(keep, default=str))


@_tool
def explain_plan(item: str = "") -> str:
    """Explain the last semester plan. item = a course code ("CHEM 215") for why it's in that term,
    what it needs, what it unlocks, how much room it has to move, what replaces it if dropped;
    a term ("WN27") for why that term looks the way it does; or empty for the alerts and assumptions.
    Fast: reads the saved plan, no re-solving. Use this for every "why" question about the plan."""
    if not _last_plan().exists():
        return "No saved plan yet. Run plan_all_semesters first."
    r = json.loads(_last_plan().read_text())
    ins = r.get("insights", {})
    facts = ins.get("course_facts", {})
    q = item.strip().upper().replace("-", " ")
    if not q:
        return _j({"alerts": ins.get("alerts", []), "assumptions": ins.get("assumptions", []),
                   "terms": {t["term"]: [c["course"] for c in t["courses"]] for t in r["terms"]}})
    for t in r["terms"]:
        if q in (t["term"], t["name"].upper()):
            rows = sorted(t["courses"], key=lambda c: -(c["difficulty_0_100"] or 0))
            all_wl = sorted((x["workload_index"] for x in r["terms"] if x["courses"]), reverse=True)
            return _j({"term": t["name"], "credits": t["credits"], "workload_index": t["workload_index"],
                       "workload_rank": f"{all_wl.index(t['workload_index']) + 1} heaviest of {len(all_wl)}",
                       "predicted_term_gpa": t["predicted_term_gpa"],
                       "courses": [{"course": c["course"], "why_here": _why(facts.get(c["course"], {}), c),
                                    "difficulty_0_100": c["difficulty_0_100"]} for c in rows],
                       "alerts": [a for a in ins.get("alerts", []) if t["name"] in a.get("title", "")]})
    code = q if q in facts else next((c for c in facts if c.replace(" ", "") == q.replace(" ", "")), None)
    if not code:
        return f"{item} isn't in the saved plan. Planned courses: {', '.join(sorted(facts))}"
    f = facts[code]
    row = next(c for t in r["terms"] for c in t["courses"] if c["course"] == code)
    return _j({"course": code, "title": row.get("title"), "why_here": _why(f, row), **f,
               "predicted_grade": row.get("predicted_grade"), "difficulty_0_100": row.get("difficulty_0_100"),
               "offered_last_5_years": row.get("offered_last_5_years"),
               "alerts": [a for a in ins.get("alerts", []) if a.get("course") == code]})


def _why(f: dict, row: dict) -> str:
    parts = []
    if row.get("counts_toward"):
        parts.append("counts toward " + "; ".join(x[:70] for x in row["counts_toward"][:3]))
    else:
        parts.append("a prerequisite for later courses")
    if f.get("needs_first_in_plan"):
        parts.append("comes after " + ", ".join(f["needs_first_in_plan"]))
    if f.get("directly_unlocks"):
        parts.append("needed before " + ", ".join(f["directly_unlocks"][:4]))
    if f.get("slack_terms") == 0:
        parts.append("no later term works without delaying something")
    elif f.get("can_move_to"):
        parts.append("could move to " + ", ".join(f["can_move_to"][:2]))
    if row.get("mcat_course"):
        parts.append("on the MCAT, so placed before your test")
    return "; ".join(parts) + "."


@_tool
def compare_paths(paths: str = "fastest,balanced,gpa_first", earliest_graduation: str = "",
                  latest_graduation: str = "") -> str:
    """Build up to three alternative full plans side by side, with tradeoffs and a recommendation:
    fastest (earliest graduation, summers, high credit cap), balanced (the user's own settings),
    gpa_first (latest acceptable graduation, lighter terms, grades weighted highest).
    earliest_graduation / latest_graduation like "FA28" / "WN29" (else profile plan keys, else graduation_term).
    The user's completed_extra credits are respected in every path.
    Slow (about a minute); say so before calling."""
    from . import insights
    src, prof = get_source(), _profile()
    P = prof.get("plan", {})
    grad = P.get("graduation_term", "WN29")
    early = earliest_graduation or P.get("earliest_graduation_term", grad)
    late = latest_graduation or P.get("latest_graduation_term", grad)
    cap_pref = float(prof.get("preferences", {}).get("max_workload_index", 4.5))
    defs = {
        "fastest": {"plan": {"graduation_term": early, "use_spring_summer": True,
                             "max_credits_main": max(18, P.get("max_credits_main", 17)),
                             "spring_summer_max_credits": 8}},
        "balanced": {},
        "gpa_first": {"plan": {"graduation_term": late, "use_spring_summer": True,
                               "max_credits_main": min(16, P.get("max_credits_main", 17)),
                               "spring_summer_max_credits": 4},
                      "weights": {"grades": 5, "workload": 4}},
    }
    out = []
    for name in [x.strip() for x in paths.split(",") if x.strip() in defs]:
        r = plan_semesters(src, prof, defs[name], time_limit=float(os.environ.get("PATH_TIME_LIMIT", "1.5")))
        thr = (r.get("insights") or {}).get("heavy_term_threshold", cap_pref)
        summ = insights.path_summary(name, r, thr)
        summ["settings"] = defs[name].get("plan") or "your saved settings"
        summ["top_alerts"] = [a["title"] for a in (r.get("insights") or {}).get("alerts", [])
                              if a["level"] != "opportunity"][:3]
        out.append(summ)
    ok = [p for p in out if p.get("ok") and p["fits_everything"]]
    rec = None
    if ok:
        best = max(ok, key=lambda p: ((p["predicted_gpa"] or 0) - 0.05 * p["heavy_terms"]
                                      - 0.02 * p["summer_terms_used"], p["path"] == "balanced"))
        rec = (f"{best['path']}: fits everything by {best['graduation']} with "
               f"{best['heavy_terms']} heavy term(s), predicted GPA about {best['predicted_gpa']}.")
    tradeoffs = []
    by = {p["path"]: p for p in out if p.get("ok")}
    if "fastest" in by and "gpa_first" in by:
        f, g = by["fastest"], by["gpa_first"]
        tradeoffs.append(f"Fastest finishes {f['graduation']} vs {g['graduation']}, costs "
                         f"{f['heavy_terms'] - g['heavy_terms']} more heavy term(s) and "
                         f"{f['summer_terms_used'] - g['summer_terms_used']} more summer term(s).")
    return _j({"paths": out, "tradeoffs": tradeoffs, "recommendation": rec or "No path fits everything; see left_out.",
               "note": "Predicted GPAs are course averages, not your own grades. Nothing is saved; say which path "
                       "to keep and I'll save its settings."})


@_tool
def registration_strategy(term: str = "") -> str:
    """Registration game plan for next term (or `term`): the order to register in (Critical, Priority,
    Flexible), why, backup sections, and what to do if a class is full (move it and what that does to
    graduation, or swap it for an alternative that counts the same). Never registers for anything."""
    from . import insights
    src, prof = get_source(), _profile()
    lt, wk = _aligned(src, prof, term, 1, None, None)
    _write_week(src, wk, False)
    _save_plan(lt)
    r = insights.registration_plan(wk, (lt.get("insights") or {}).get("course_facts", {}), lt)
    r["semester_plan_note"] = wk.get("semester_plan_note")
    return _j(r)


# ------------------------------------------------------------------ friends
@_tool
def export_plan_for_friend(option: int = 1, display_name: str = "me") -> str:
    """Write a share file of one option from the last optimize_schedule run. Contains ONLY course codes,
    sections and times. No grades or profile. The user sends the file to their friend themselves."""
    if not _last_week().exists():
        return "Run optimize_schedule first."
    r = json.loads(_last_week().read_text())
    plan = next((p for p in r["plans"] if p["rank"] == option), None)
    if not plan:
        return f"No option {option} in the last run."
    return _j(friends.export_plan(plan, display_name))


@_tool
def compare_with_friend(share_file_json: str, option: int = 1) -> str:
    """Compare one of the user's options (last run) with a friend's share file contents (paste the JSON)."""
    if not _last_week().exists():
        return "Run optimize_schedule first."
    r = json.loads(_last_week().read_text())
    plan = next((p for p in r["plans"] if p["rank"] == option), None)
    if not plan:
        return f"No option {option} in the last run."
    try:
        theirs = json.loads(share_file_json)
    except json.JSONDecodeError:
        return "That isn't valid JSON."
    return _j(friends.compare(plan, theirs))


def main():
    mcp.run()


if __name__ == "__main__":
    main()
