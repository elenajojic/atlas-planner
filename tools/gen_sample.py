"""Generates the bundled SAMPLE data. Every course stat, instructor and grade here is fake.

Run: python tools/gen_sample.py
"""
import json
import random
from pathlib import Path

random.seed(11)
OUT = Path(__file__).resolve().parent.parent / "data" / "sample"
OUT.mkdir(parents=True, exist_ok=True)

PAST = [f"{s}{y:02d}" for y in range(21, 27) for s in ("WN", "SP", "SU", "FA")]
PAST = [t for t in PAST if not (t.startswith("WN21") or t in ("SP21", "SU21"))][:20]  # FA21..SU26


def history(pattern: str, miss=0):
    seasons = {"F": "FA", "W": "WN", "S": "SP", "U": "SU"}
    want = {seasons[ch] for ch in pattern}
    terms = [t for t in PAST if t[:2] in want]
    for _ in range(miss):
        terms.remove(random.choice(terms))
    return terms


NORTH = ["GGBL", "EECS", "DOW", "LBME", "BBB", "FXB", "CHRYS"]
CENTRAL = ["CHEM", "BSB", "MH", "AH", "DENN", "WEIS", "WH", "EH", "RAND"]

# code, title, credits, prereqs, coreqs, tags, pattern, miss, campus, size, format, fill_days,
# workload%, mean_gpa, gpa_sd, desire, understanding, expectations, typical_term_index, share_of_bme_premed
C = [
 ("ENGR 100", "Introduction to Engineering", 4, [], [], ["design"], "FW", 0, "N", 120, "project", 6, 60, 3.6, .4, 55, 75, 70, 1, .99),
 ("ENGR 101", "Intro to Computers and Programming", 4, [], [], ["computing"], "FWS", 0, "N", 400, "large lecture", 8, 72, 3.4, .6, 45, 82, 76, 1, .95),
 ("MATH 115", "Calculus I", 4, [], [], ["math"], "FWSU", 0, "C", 30, "small section", 10, 55, 3.2, .7, 30, 70, 72, 1, .6),
 ("MATH 116", "Calculus II", 4, [["MATH 115"]], [], ["math"], "FWSU", 0, "C", 30, "small section", 9, 68, 3.0, .7, 25, 72, 70, 2, .9),
 ("CHEM 130", "General Chemistry", 3, [], [], ["premed", "chemistry"], "FWS", 0, "C", 300, "large lecture", 7, 62, 3.1, .7, 30, 70, 68, 1, .95),
 ("CHEM 125", "General Chemistry Lab I", 1, [], [["CHEM 130"]], ["premed", "chemistry"], "FWS", 0, "C", 24, "lab", 5, 40, 3.6, .4, 20, 55, 66, 1, .95),
 ("CHEM 126", "General Chemistry Lab II", 1, [["CHEM 125"]], [], ["premed", "chemistry"], "FWS", 0, "C", 24, "lab", 5, 42, 3.6, .4, 20, 55, 66, 2, .9),
 ("PHYSICS 140", "General Physics I", 4, [], [["MATH 115"]], ["premed", "physics"], "FWS", 0, "C", 250, "large lecture", 6, 70, 3.0, .7, 25, 70, 68, 2, .95),
 ("PHYSICS 141", "Elementary Laboratory I", 1, [], [["PHYSICS 140"]], ["premed", "physics"], "FWS", 0, "C", 24, "lab", 4, 35, 3.6, .4, 20, 50, 60, 2, .95),
 ("ENGLISH 125", "Writing and Academic Inquiry", 4, [], [], ["writing"], "FWSU", 0, "C", 18, "seminar", 3, 45, 3.7, .3, 35, 70, 78, 2, .7),
 ("BIOMEDE 211", "Circuits and Systems for Biomedical Engineering", 4, [["MATH 116"]], [], ["bme", "electronics"], "FW", 0, "N", 110, "lecture + lab", 9, 70, 3.4, .5, 50, 78, 72, 2, .9),
 ("MATH 215", "Multivariable Calculus", 4, [["MATH 116"]], [], ["math"], "FWSU", 0, "C", 30, "small section", 9, 62, 3.2, .6, 25, 72, 72, 3, .9),
 ("BIOLOGY 171", "Introductory Biology: Ecology and Evolution", 4, [], [], ["premed", "biology"], "FWS", 0, "C", 350, "large lecture", 10, 50, 3.3, .6, 35, 70, 72, 3, .8),
 ("EECS 280", "Programming and Intro Data Structures", 4, [["ENGR 101"]], [], ["ai", "computing"], "FWS", 0, "N", 600, "large lecture", 4, 85, 3.3, .6, 60, 88, 78, 3, .5),
 ("BIOLOGY 172", "Introductory Biology: Molecular, Cellular, and Developmental", 4, [["CHEM 130"]], [], ["premed", "biology"], "FWS", 0, "C", 400, "large lecture", 5, 62, 3.2, .6, 48, 78, 70, 4, .9),
 ("BIOLOGY 173", "Introductory Biology Laboratory", 2, [], [["BIOLOGY 171", "BIOLOGY 172"]], ["premed", "biology"], "FW", 0, "C", 24, "lab", 2, 55, 3.6, .4, 30, 60, 65, 4, .85),
 ("CHEM 210", "Structure and Reactivity I", 4, [["CHEM 130"]], [], ["premed", "chemistry"], "FWS", 0, "C", 300, "large lecture", 3, 80, 3.0, .8, 40, 82, 72, 4, .95),
 ("CHEM 211", "Investigations in Chemistry", 1, [["CHEM 125"]], [["CHEM 210"]], ["premed", "chemistry"], "FW", 0, "C", 24, "lab", 3, 45, 3.6, .4, 25, 55, 68, 4, .9),
 ("CHEM 215", "Structure and Reactivity II", 3, [["CHEM 210"]], [], ["premed", "chemistry"], "FWS", 0, "C", 250, "large lecture", 3, 84, 2.9, .8, 35, 80, 70, 5, .9),
 ("CHEM 216", "Synthesis and Characterization", 2, [["CHEM 211"]], [["CHEM 215"]], ["premed", "chemistry"], "FW", 1, "C", 24, "lab", 3, 60, 3.4, .5, 25, 60, 66, 5, .85),
 ("CHEM 351", "Fundamentals of Biochemistry", 4, [["CHEM 215"], ["BIOLOGY 172"]], [], ["premed", "biochemistry"], "FW", 0, "C", 200, "large lecture", 6, 72, 3.2, .7, 45, 80, 72, 6, .4),
 ("MCDB 310", "Introductory Biochemistry", 4, [["CHEM 210"], ["BIOLOGY 172"]], [], ["premed", "biochemistry", "biology"], "FW", 0, "C", 150, "lecture", 8, 68, 3.3, .6, 50, 80, 75, 6, .35),
 ("BIOLCHEM 415", "Introductory Biochemistry", 3, [["CHEM 215"]], [], ["premed", "biochemistry"], "F", 1, "Med", 120, "lecture", 9, 66, 3.4, .6, 45, 78, 74, 7, .25),
 ("PHYSICS 240", "General Physics II", 4, [["PHYSICS 140"], ["MATH 116"]], [], ["premed", "physics"], "FWS", 0, "N", 250, "large lecture", 6, 70, 3.0, .7, 28, 70, 66, 4, .9),
 ("PHYSICS 241", "Elementary Laboratory II", 1, [["PHYSICS 141"]], [["PHYSICS 240"]], ["premed", "physics"], "FWS", 0, "N", 24, "lab", 5, 35, 3.6, .4, 20, 50, 60, 4, .9),
 ("PSYCH 111", "Introduction to Psychology", 4, [], [], ["premed", "psychology", "abroad_ok"], "FWSU", 0, "C", 400, "large lecture", 7, 30, 3.6, .4, 65, 80, 82, 5, .8),
 ("SOC 100", "Principles of Sociology", 4, [], [], ["premed", "sociology", "abroad_ok"], "FW", 0, "C", 250, "large lecture", 9, 28, 3.6, .4, 58, 74, 79, 5, .7),
 ("STATS 250", "Introduction to Statistics and Data Analysis", 4, [], [], ["premed", "statistics", "ai"], "FWSU", 0, "C", 500, "large lecture", 8, 40, 3.5, .5, 35, 72, 85, 4, .75),
 ("ENGLISH 225", "Academic Argumentation", 4, [["ENGLISH 125"]], [], ["writing", "abroad_ok"], "FWSU", 0, "C", 18, "seminar", 4, 48, 3.7, .3, 40, 72, 78, 5, .5),
 ("MATH 216", "Introduction to Differential Equations", 4, [["MATH 116"]], [], ["math"], "FWSU", 0, "C", 30, "small section", 8, 64, 3.1, .6, 22, 70, 70, 4, .9),
 ("BIOMEDE 221", "Biophysical Chemistry and Thermodynamics", 4, [["CHEM 130"], ["MATH 116"]], [], ["bme", "chemistry"], "W", 0, "N", 120, "lecture", 8, 68, 3.3, .5, 52, 75, 69, 4, .9),
 ("BIOMEDE 231", "Introduction to Biomechanics", 4, [["PHYSICS 140"], ["MATH 116"]], [], ["bme", "biomechanics", "prosthetics", "design"], "F", 0, "N", 120, "lecture", 9, 58, 3.6, .4, 70, 84, 80, 5, .9),
 ("BIOMEDE 241", "Statistics and Probability in BME", 4, [["MATH 216"], ["PHYSICS 240"]], [], ["bme", "statistics", "ai"], "W", 0, "N", 110, "lecture", 9, 66, 3.3, .5, 40, 74, 72, 6, .9),
 ("BIOMEDE 350", "Biomedical Design", 4, [["BIOMEDE 211"]], [], ["bme", "design", "prosthetics"], "F", 0, "N", 80, "project", 7, 70, 3.6, .3, 72, 82, 70, 5, .9),
 ("BIOMEDE 419", "Quantitative Physiology", 3, [["BIOLOGY 172"], ["MATH 216"]], [], ["bme", "physiology", "premed", "medicine"], "W", 0, "N", 90, "lecture", 8, 64, 3.4, .5, 66, 82, 74, 6, .6),
 ("BIOMEDE 450", "Biomedical Design Capstone", 4, [["BIOMEDE 350"]], [], ["bme", "design", "prosthetics", "robotics"], "W", 0, "N", 70, "project", 10, 78, 3.7, .3, 75, 85, 70, 8, .9),
 ("BIOMEDE 458", "Biomedical Instrumentation and Design", 4, [["BIOMEDE 211"], ["BIOMEDE 241"]], [], ["bme", "electronics", "design"], "F", 0, "N", 60, "lab heavy", 6, 82, 3.4, .5, 60, 84, 70, 7, .4),
 ("BIOMEDE 476", "Biomechanics and Bioinspired Design", 4, [["BIOMEDE 231"]], [], ["bme", "biomechanics", "robotics", "soft robotics", "prosthetics"], "F", 1, "N", 50, "seminar", 5, 70, 3.6, .3, 80, 86, 76, 7, .35),
 ("EECS 281", "Data Structures and Algorithms", 4, [["EECS 280"]], [], ["ai", "computing"], "FWS", 0, "N", 700, "large lecture", 2, 92, 3.2, .7, 60, 88, 74, 4, .4),
 ("EECS 445", "Introduction to Machine Learning", 4, [["EECS 281"]], [], ["ai", "computing"], "FW", 0, "N", 400, "large lecture", 2, 85, 3.4, .6, 78, 86, 72, 6, .3),
 ("EECS 492", "Introduction to Artificial Intelligence", 4, [["EECS 281"]], [], ["ai", "computing"], "FW", 1, "N", 250, "lecture", 4, 70, 3.5, .5, 70, 80, 72, 6, .25),
 ("ROB 311", "How to Build Robots and Make Them Move", 4, [["ENGR 100"]], [], ["robotics", "design", "controls", "soft robotics"], "FW", 1, "N", 60, "project", 3, 80, 3.6, .4, 85, 85, 70, 5, .2),
 ("HISTART 101", "Great Monuments: Pyramids to Present", 4, [], [], ["humanities", "abroad_ok"], "FW", 0, "C", 200, "large lecture", 12, 25, 3.7, .3, 55, 70, 80, 5, .3),
 ("PHIL 160", "Moral Principles and Problems", 4, [], [], ["humanities", "ethics", "medicine", "abroad_ok"], "FW", 0, "C", 150, "lecture", 10, 35, 3.6, .3, 60, 76, 78, 5, .35),
 ("ANTHRCUL 101", "Introduction to Anthropology", 4, [], [], ["humanities", "abroad_ok", "medicine"], "FWSU", 0, "C", 300, "large lecture", 14, 25, 3.6, .4, 50, 70, 78, 5, .3),
]

INST_POOL = [f"Sample Instructor {chr(65 + i)}" for i in range(26)] + [f"Sample Instructor A{chr(65 + i)}" for i in range(26)] + [f"Sample Instructor B{chr(65 + i)}" for i in range(26)]
instructors = {}
course_insts = {}
k = 0
for row in C:
    code = row[0]
    n = 2 if row[9] >= 200 else 1
    course_insts[code] = INST_POOL[k:k + n]
    k += n
    for name in course_insts[code]:
        instructors.setdefault(name, {"clarity_pct": random.randint(52, 92), "n_responses": random.randint(60, 600),
                                      "mean_gpa_given": {}, "history": []})
        base = row[13] + random.uniform(-.3, .3)
        instructors[name]["mean_gpa_given"][code] = round(base, 2)
        drift = random.uniform(-.06, .06)
        clar = instructors[name]["clarity_pct"]
        for i, t in enumerate([t for t in PAST if t[:2] in ("FA", "WN")][-6:]):
            instructors[name]["history"].append({"term": t, "course": code,
                                                 "mean_gpa": round(base - drift * (5 - i), 2),
                                                 "clarity_pct": max(30, min(98, round(clar - drift * 60 * (5 - i))))})

SLOTS = {"MWF": [("09:00", "10:00"), ("10:00", "11:00"), ("11:00", "12:00"), ("12:00", "13:00"), ("13:00", "14:00"), ("14:00", "15:00")],
         "TuTh": [("08:30", "10:00"), ("10:00", "11:30"), ("11:30", "13:00"), ("13:00", "14:30"), ("16:00", "17:30")],
         "MW": [("10:30", "12:00"), ("12:00", "13:30"), ("13:30", "15:00"), ("15:00", "16:30"), ("16:30", "18:00")]}
ONE_DAY = ["M", "Tu", "W", "Th", "F"]


def meet(days, s, e, campus):
    camp = {"N": "North", "C": "Central", "Med": "Medical"}[campus]
    bld = random.choice(NORTH if campus == "N" else CENTRAL)
    return {"days": days, "start": s, "end": e, "campus": camp, "location": bld}


def sections_for(row):
    code, credits, campus, size, fmt = row[0], row[2], row[8], row[9], row[10]
    secs = []
    if fmt == "lab":
        for i in range(3):
            d = random.choice(ONE_DAY)
            st = random.choice(["08:00", "09:00", "13:00", "14:00", "15:00"])
            h = 4 if credits >= 2 else 3
            e = f"{int(st[:2]) + h:02d}:{st[3:]}"
            secs.append({"section_id": f"0{10 + i}", "component": "LAB", "instructor": None,
                         "seats_open": random.randint(0, 6), "capacity": 24, "meetings": [meet(d, st, e, campus)]})
        return secs
    for i, name in enumerate(course_insts[code]):
        pat = random.choice(list(SLOTS))
        s, e = random.choice(SLOTS[pat])
        secs.append({"section_id": f"{i + 1}00", "component": "LEC", "instructor": name,
                     "seats_open": random.randint(0, 60), "capacity": size // len(course_insts[code]),
                     "meetings": [meet(pat, s, e, campus)]})
    if size >= 100:
        for j in range(3):
            d = random.choice(ONE_DAY)
            st = random.choice(["09:00", "10:00", "11:00", "12:00", "15:00", "16:00"])
            secs.append({"section_id": f"1{j + 1:02d}", "component": "DIS", "instructor": None,
                         "seats_open": random.randint(0, 25), "capacity": 25,
                         "meetings": [meet(d, st, f"{int(st[:2]) + 1:02d}:00", campus)]})
    if fmt in ("lab heavy", "lecture + lab"):
        d = random.choice(ONE_DAY)
        secs.append({"section_id": "201", "component": "LAB", "instructor": None, "seats_open": 8, "capacity": 20,
                     "meetings": [meet(d, "13:00", "16:00", campus)]})
    return secs


courses, stats, typical = [], {}, {}
for row in C:
    (code, title, cr, pre, co, tags, pat, miss, campus, size, fmt, fill, wl, mg, sd, des, und, exp, typ, share) = row
    hist = history(pat, miss)
    c = {"code": code, "title": title, "credits": cr, "prereqs": pre, "coreqs": co, "tags": tags, "description": "",
         "offered_history": hist, "class_size": size, "format": fmt, "fill_days": fill,
         "terms": {}}
    if "W" in pat:
        c["terms"]["WN27"] = sections_for(row)
    courses.append(c)
    letter = min(((abs(v - mg), kk) for kk, v in {"A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7}.items()))[1]
    stats[code] = {"workload_pct": wl, "median_grade": letter, "mean_gpa": mg, "gpa_sd": sd, "desire_pct": des,
                   "understanding_pct": und, "expectations_pct": exp, "n_responses": size * 3}
    typical[code] = {"typical_term_index": typ, "share_of_similar_students": share}

json.dump({"label": "SAMPLE DATA (fake courses, stats and instructors)", "next_term": "WN27", "courses": courses},
          open(OUT / "offerings.json", "w"), indent=1)
json.dump({"courses": stats, "instructors": instructors}, open(OUT / "atlas_stats.json", "w"), indent=1)

combos = [
    {"courses": ["CHEM 210", "PHYSICS 240"], "penalty": 3, "note": "Both exam heavy, students taking them together report much higher stress"},
    {"courses": ["CHEM 215", "BIOMEDE 241"], "penalty": 3, "note": "Exams cluster mid semester"},
    {"courses": ["EECS 281", "CHEM 215"], "penalty": 4, "note": "Two of the heaviest courses on campus"},
    {"courses": ["EECS 281", "CHEM 210"], "penalty": 3, "note": "Projects due during orgo exam weeks"},
    {"courses": ["EECS 445", "BIOMEDE 450"], "penalty": 3, "note": "Capstone plus ML projects"},
]
json.dump({"combos": combos, "typical_paths": {"cohort": "BME majors on a premed track", "courses": typical}},
          open(OUT / "cohort.json", "w"), indent=1)

programs = {
  "BME BSE": {"source": "sample", "groups": [
    {"group": "BME core", "need": 6, "options": ["BIOMEDE 211", "BIOMEDE 221", "BIOMEDE 231", "BIOMEDE 241", "BIOMEDE 350", "BIOMEDE 450"]},
    {"group": "BME math", "need": 2, "options": ["MATH 215", "MATH 216"]},
    {"group": "BME biology", "need": 1, "options": ["BIOLOGY 171", "BIOLOGY 172"]},
    {"group": "BME organic chemistry", "need": 1, "options": ["CHEM 210"]},
    {"group": "BME physiology", "need": 1, "options": ["BIOMEDE 419", "MCDB 310"]},
    {"group": "BME physics", "need": 2, "options": ["PHYSICS 240", "PHYSICS 241"]},
    {"group": "BME advanced electives", "need": 2, "options": ["BIOMEDE 458", "BIOMEDE 476", "EECS 445", "ROB 311"]},
    {"group": "Humanities", "need": 2, "options": ["HISTART 101", "PHIL 160", "ANTHRCUL 101", "ENGLISH 225"]}]},
  "AI minor": {"source": "sample", "groups": [
    {"group": "AI minor foundations", "need": 1, "options": ["EECS 281"]},
    {"group": "AI minor statistics", "need": 1, "options": ["STATS 250", "BIOMEDE 241"]},
    {"group": "AI minor core", "need": 2, "options": ["EECS 445", "EECS 492", "ROB 311"]}]},
  "Premed (template)": {"source": "template, confirm with UM Health Professions advising", "groups": [
    {"group": "Premed biology", "need": 3, "options": ["BIOLOGY 171", "BIOLOGY 172", "BIOLOGY 173"], "mcat": True},
    {"group": "Premed general chemistry", "need": 3, "options": ["CHEM 130", "CHEM 125", "CHEM 126"], "mcat": True},
    {"group": "Premed organic chemistry", "need": 4, "options": ["CHEM 210", "CHEM 211", "CHEM 215", "CHEM 216"], "mcat": True},
    {"group": "Premed biochemistry", "need": 1, "options": ["CHEM 351", "MCDB 310", "BIOLCHEM 415"], "mcat": True},
    {"group": "Premed physics", "need": 4, "options": ["PHYSICS 140", "PHYSICS 141", "PHYSICS 240", "PHYSICS 241"], "mcat": True},
    {"group": "Premed psych and soc", "need": 2, "options": ["PSYCH 111", "SOC 100"], "mcat": True},
    {"group": "Premed statistics", "need": 1, "options": ["STATS 250", "BIOMEDE 241"]},
    {"group": "Premed English", "need": 2, "options": ["ENGLISH 125", "ENGLISH 225"]}]},
}
json.dump(programs, open(OUT / "programs.json", "w"), indent=1)

hist = [
 ("ENGR 100", "FA25", "A-"), ("ENGR 101", "FA25", "A"), ("MATH 115", "FA25", "A-"), ("CHEM 130", "FA25", "B+"), ("CHEM 125", "FA25", "A"),
 ("MATH 116", "WN26", "B+"), ("PHYSICS 140", "WN26", "B"), ("PHYSICS 141", "WN26", "A-"), ("ENGLISH 125", "WN26", "A"),
 ("BIOMEDE 211", "WN26", "A-"), ("CHEM 126", "WN26", "A"),
 ("MATH 215", "FA26", None), ("BIOLOGY 171", "FA26", None), ("EECS 280", "FA26", None),
]
cred = {r[0]: r[2] for r in C}
student = [{"course": c, "term": t, "grade": g, "credits": cred[c],
            "instructor": course_insts[c][0]} for c, t, g in hist]
json.dump({"label": "SAMPLE student history (fake grades)", "history": student}, open(OUT / "student_history.json", "w"), indent=1)
print("sample data written to", OUT)
