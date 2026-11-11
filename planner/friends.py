"""Friends mode: compare plans without any server.

export_plan writes a small JSON file with ONLY course codes, sections and meeting times.
No grades, predictions, history, or profile. You send the file to a friend however you like;
they import it on their side.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .data_source import user_home

DAYS = ["Mon", "Tue", "Wed", "Thu", "Fri"]


def _shared_dir() -> Path:
    p = user_home() / "shared"
    p.mkdir(exist_ok=True)
    return p


def export_plan(week_plan: dict, display_name: str) -> dict:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", display_name)[:40] or "me"
    payload = {
        "atlas_planner_share": 1,
        "name": display_name,
        "term": week_plan["term"],
        "courses": [{"course": c["course"], "sections": c["sections"]} for c in week_plan["courses"]],
        "meetings": [{k: g[k] for k in ("day", "start_min", "end_min", "label", "kind")}
                     for g in week_plan["grid"] if g["kind"] != "commitment"],
    }
    path = _shared_dir() / f"{safe}_{week_plan['term']}.json"
    path.write_text(json.dumps(payload, indent=1))
    return {"file": str(path), "contains": "course codes, section numbers and class times only"}


def compare(mine: dict, theirs: dict, min_free_block: int = 45) -> dict:
    if theirs.get("atlas_planner_share") != 1:
        return {"error": "That doesn't look like an Atlas Planner share file."}
    my_c = {c["course"]: c["sections"] for c in mine["courses"]}
    th_c = {c["course"]: c["sections"] for c in theirs["courses"]}
    shared = sorted(set(my_c) & set(th_c))
    same_section = [c for c in shared if my_c[c] == th_c[c]]

    def busy(meetings):
        d = {x: [] for x in DAYS}
        for mm in meetings:
            if mm["day"] in d:
                d[mm["day"]].append((mm["start_min"], mm["end_min"]))
        return d

    mine_busy = busy([g for g in mine["grid"]])
    th_busy = busy(theirs["meetings"])
    common = []
    for day in DAYS:
        iv = sorted(mine_busy[day] + th_busy[day])
        cur = 8 * 60
        for s, e in iv + [(20 * 60, 20 * 60)]:
            if s - cur >= min_free_block:
                common.append({"day": day, "from": f"{cur // 60}:{cur % 60:02d}", "to": f"{s // 60}:{s % 60:02d}",
                               "minutes": s - cur})
            cur = max(cur, e)
    return {"friend": theirs.get("name"), "term": theirs.get("term"), "shared_courses": shared,
            "same_sections": same_section,
            "could_match_sections": [c for c in shared if c not in same_section],
            "common_free_time": common}
