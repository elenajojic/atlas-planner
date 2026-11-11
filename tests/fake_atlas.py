"""A fake Atlas shaped like the real responses (mapped Nov 2026)."""
import html
import json
import os

WHATIF = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "whatif_fixture.json")))
calls = []

def browse(sub):
    rows = {"BIOMEDE": [("221", "Biophysical Chemistry", 4), ("241", "Statistics", 4), ("350", "Biotransport", 4), ("458", "Design", 3), ("211", "Circuits", 4)],
            "MATH": [("116", "Calculus II", 4), ("215", "Calc III", 4), ("214", "Lin Alg", 4)],
            "EECS": [("280", "Prog & Data Struct", 4), ("245", "Math for ML", 4)], "CHEM": [("130", "Gen Chem", 3)]}.get(sub, [])
    return {"results": [{"course_code_spaced": f"{sub} {n}", "title": t, "max_credits": c, "min_credits": c, "end_term_code": None,
                         "offered_terms": [{"term": "2570"}, {"term": "2620"}]} for n, t, c in rows], "count": len(rows)}

def page(code):
    cached = {"eval_summary": {"num_responses": 812, "responses": {"q891": 61, "q4": 40, "q1631": 85, "q1633": 70}},
              "grades": {"grades": [{"grade": "A", "percentage": 0.3}, {"grade": "B+", "percentage": 0.4}, {"grade": "C", "percentage": 0.3}],
                         "median_grade": ["B+", "B"]}, "student_levels": [{"name": "Sophomore", "value": 0.6}, {"name": "Junior", "value": 0.3}]}
    props = [{"description": "Covers biomechanics and data structures.", "enforced_prereqs": "MATH 115 and (ENGR 101 or EECS 183)"}]
    s = lambda i, v: f'<script type="application/json" id="{i}">{html.escape(json.dumps(v))}</script>'
    return {"url": "x", "text": s("current-term-data", "2620") + s("course-props-data", props) + s("cached-course-data", cached)}

def sections(code):
    return {"selected_term": "2620", "terms": ["2610", "2620"], "offered_classes": [
        {"term": "2620", "section": "001", "section_type": "LEC", "open_seat_count": 10, "enrollment_capacity": 200,
         "instructors": [{"name": "Clark,Steven E"}],
         "meetings": [{"days": [1, 3, 5], "time": {"start": "10:30:00", "end": "11:30:00", "is_tba": False}, "facility_description": "1500 EECS"}]},
        {"term": "2620", "section": "011", "section_type": "DIS", "open_seat_count": 0, "enrollment_capacity": 25, "instructors": [],
         "meetings": [{"days": [4], "time": {"start": "08:30:00", "end": "10:00:00", "is_tba": False}, "facility_description": "2234 USB"}]}]}

def fake(self, path, kind="json", method="GET", body=None):
    calls.append((method, path, body))
    if path == "/my-dashboard/": return {"url": "", "text": '<degree-progress username="ejojic"></degree-progress>'}
    if path.startswith("/api/plan-options/"):
        code = {"Biomedical": "0880BSE", "AI": "AIBSEBE", "Business": "BABSEBE"}[next(k for k in ["Biomedical", "AI", "Business"] if k in path)]
        return {"results": [{"code": code, "program__code": "00007", "career__code": "UENG", "description": "x"}]}
    if path == "/api/what-if-report-search/": return json.loads(json.dumps(WHATIF))
    if path == "/api/completed-courses.json": return [{"course_code": "EECS183", "term": "2560"}, {"course_code": "MATH186", "term": "2570"}]
    if path.startswith("/api/courses/browse/"):
        sub = path.split("subject=")[1]
        if sub == "TO":
            import requests; raise requests.HTTPError("500 Server Error")
        return browse(sub)
    if path.startswith("/api/section-table-data/"): return sections(path)
    if path.endswith("course-instructors.json"):
        return {"instructor_data": [{"full_name": "Clark, Steven", "terms_taught": 5, "evaluations": [{"id": "199", "value": 0.82, "displayable": True}]}]}
    if path.startswith("/courses/"): return page(path)
    raise AssertionError(path)

