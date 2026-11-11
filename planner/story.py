"""Application story score: a rough read of how your 4 year course list will look to
med schools and grad programs. It's a heuristic to start conversations, not a prediction."""
from __future__ import annotations


def application_story(plan: dict, profile: dict, catalog: dict) -> dict:
    notes, score = [], 0
    planned = [c["course"] for pt in plan["terms"] for c in pt["courses"]]
    goal_tags = {t.lower() for g in profile.get("goals", []) for t in g.get("tags", [])}

    if plan["mcat"]["term"]:
        if plan["mcat"]["ready"]:
            score += 30
            notes.append("All MCAT prereqs finish before your MCAT term.")
        else:
            score += 10
            notes.append(f"Still taking {', '.join(plan['mcat']['courses_after_mcat'])} after your MCAT term.")
    else:
        score += 15
        notes.append("No MCAT term set yet.")

    bcpm = plan["gpa_projection"].get("bcpm_after")
    if bcpm is not None:
        pts = max(0, min(25, (bcpm - 3.2) / 0.6 * 25))
        score += pts
        notes.append(f"Projected BCPM science GPA {bcpm} (from predicted grades; ranges apply).")

    upper = [c for c in planned if catalog.get(c) and catalog[c].level >= 300]
    if upper:
        fit = sum(1 for c in upper if goal_tags & {t.lower() for t in catalog[c].tags}) / len(upper)
        score += 20 * fit
        notes.append(f"{fit:.0%} of your upper level courses line up with your stated goals.")

    main = [pt for pt in plan["terms"] if pt["courses"] and not pt["abroad"]]
    if len(main) >= 4:
        half = len(main) // 2
        early = sum(p["avg_difficulty"] for p in main[:half]) / half
        late = sum(p["avg_difficulty"] for p in main[half:]) / (len(main) - half)
        if late >= early - 3:
            score += 10
            notes.append("Rigor holds up or rises in later years.")
        else:
            notes.append("Later semesters get noticeably lighter; schools like to see sustained rigor.")

    tags = {t for c in planned if c in catalog for t in catalog[c].tags}
    if {"ethics", "medicine", "humanities"} & tags:
        score += 7.5
        notes.append("Includes humanities or ethics coursework.")
    if {"design", "robotics", "prosthetics"} & tags:
        score += 7.5
        notes.append("Includes hands on design or robotics work, which pairs well with your research.")
    return {"score": round(min(100, score)), "notes": notes,
            "disclaimer": "Heuristic only. Talk to UM Health Professions advising about your specific path."}
