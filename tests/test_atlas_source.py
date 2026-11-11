"""Offline test of AtlasSource parsing, using fixtures shaped like real Atlas responses."""
import json, os, sys, tempfile, html
sys.path.insert(0, sys.argv[1]); sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
home = tempfile.mkdtemp(); os.environ["ATLAS_PLANNER_HOME"] = home
from planner import data_source as ds
from planner.requirements import resolve, done_courses, summary

open(f"{home}/profile.yaml", "w").write("""name: Test
programs: [Biomedical Engineering BSE, AI BSE BioE, Business Admin BSE BioE, Premed (template)]
atlas_plans:
  - {plan: Biomedical Engineering BSE, type: MAJ}
  - {plan: AI BSE BioE, type: MIN}
  - {plan: Business Admin BSE BioE, type: MIN}
my_grades: {CHEM 130: A-}
""")
open(f"{home}/atlas_session.json", "w").write('{"cookies":[]}')

from fake_atlas import fake, calls  # noqa: E402

ds.AtlasSource._fetch = fake
ds.AtlasSource.MIN_SECONDS_BETWEEN_REQUESTS = 0
src = ds.AtlasSource()
assert src.uniqname() == "ejojic"
hist = src.student_history()
h = {x.course: x for x in hist}
assert h["MATH 115"].grade is None and h["MATH 115"].term == "FA26", h["MATH 115"]
assert h["CHEM 130"].grade == "A-" and h["CHEM 210"].term == "SU26" if "CHEM 210" in h else True
assert "EECS 183" in h and h["EECS 183"].term == "FA25"
post = [c for c in calls if c[0] == "POST"][0][2]
assert post == {"emplid": "ejojic", "acad_career": "UENG", "acad_prog1": "00007", "acad_plan1": "0880BSE",
                "acad_prog2": "00007", "acad_plan2": "AIBSEBE", "acad_prog3": "00007", "acad_plan3": "BABSEBE"}, post
src.catalog()  # warm, as refresh_atlas_data would
prof = src._profile()
groups = resolve(prof, src, done_courses(hist))
S = {g["group"]: g for g in summary(groups)}
names = list(S)
for n in names: print(f'{S[n]["program"][:22]:22} | rem {S[n]["remaining"]} | {n[:70]} | {S[n]["options_left"][:6]} {S[n].get("note","")}')
chem = next(g for g in groups if "CHEM 125" in g["options"]); assert chem["need"] == 3 and chem["remaining"] == 0
ai = next(g for g in groups if "EECS 245" in g["options"]); assert ai["options"] == ["EECS 245", "MATH 214", "MATH 217", "MATH 417", "MATH 419"]
lvl = next(g for g in groups if "300-Level" in g["group"]); assert "BIOMEDE 350" in lvl["options"] and "BIOMEDE 241" not in lvl["options"]
assert not any("Cumulative GPA" in g["group"] for g in groups)
assert any(g["program"] == "Premed (template)" for g in groups)
assert src.next_term() == "WN27"
offs = src.offerings("")
c = offs[0]; print("OFFER", c.code, [(s.section_id, s.component, [(m.day, m.start, m.campus, m.location) for m in s.meetings], s.instructor) for s in c.sections], c.prereqs)
assert c.sections[0].meetings[0].day == 0 and c.sections[0].meetings[0].campus == "North" and c.sections[0].meetings[0].start == 630
assert c.prereqs == [["MATH 115"], ["ENGR 101", "EECS 183"]]
st = src.course_stats(c.code); print(st); assert st.workload_pct == 61 and st.median_grade == "B+" and st.n_responses == 812
ins = src.instructor_stats("Clark,Steven E"); print(ins); assert ins.clarity_pct == 82.0
print("cohort", list(src.cohort()["typical_paths"]["courses"].items())[:2])
print("skipped:", src.skipped_subjects)
print("requests:", len(calls), "ALL OK")

# ---- full tool run through the MCP server functions
os.environ["ATLAS_PLANNER_SOURCE"] = "atlas"
from planner import server
for name, fn in [("get_progress", lambda: server.get_progress()),
                 ("course_report", lambda: server.course_report("BIOMEDE 211")),
                 ("optimize_schedule", lambda: server.optimize_schedule()),
                 ("plan_all_semesters", lambda: server.plan_all_semesters())]:
    try:
        out = fn()
        assert '"error"' not in str(out)[:20], out
        print(f"== {name}: OK", str(out)[:300].replace("\n", " "))
    except Exception as e:
        import traceback; traceback.print_exc(); print(f"== {name}: FAIL {e}")
