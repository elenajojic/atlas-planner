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
user the results use fake data. Atlas data is for the user's personal planning only."""

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


@_tool
def optimize_schedule(term: str = "", n_plans: int = 4, extra_busy: Optional[list[dict]] = None,
                      overrides: Optional[dict] = None, open_view: bool = False,
                      follow_semester_plan: bool = True) -> str:
    """Build and rank full weekly schedules for one term (default: next term).

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
    return _j(r)


@_tool
def plan_all_semesters(overrides: Optional[dict] = None, study_abroad_term: str = "",
                       extra_busy: Optional[list[dict]] = None, open_view: bool = False) -> str:
    """Plan every remaining semester: offering history, prereq order, double counting, critical path, MCAT
    timing, students like you, balanced workload, optional spring/summer. Next term is checked against real
    section times so it matches optimize_schedule. Includes GPA/BCPM projection, application story score
    and heatmap. overrides may include {"plan":{...}, "must_take":[], "avoid":[]}."""
    src, prof = get_source(), _profile()
    r, wk = _aligned(src, prof, "", 3, extra_busy, overrides, abroad_term=study_abroad_term or None)
    _write_week(src, wk, False)
    if r.get("ok"):
        r["view_file"] = render.write_view("four_year_plan", render.heatmap_html(r, "Atlas Planner: semester plan"), open_view)
    return _j(r)


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
    r.pop("new_plan", None)
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
