"""Resolve majors/minors/premed into requirement groups, and what's left to do."""
from __future__ import annotations

from .models import HistoryEntry


def done_courses(history: list[HistoryEntry], extra_completed: list[str] | None = None) -> set[str]:
    """Finished or in progress (in progress counts as done for planning future terms)."""
    out = {h.course for h in history if h.grade not in ("E", "W")}
    return out | set(extra_completed or [])


def resolve(profile: dict, source, done: set[str]) -> list[dict]:
    groups = []
    for prog in profile.get("programs", []):
        try:
            req = source.requirements(prog)
        except KeyError:
            continue
        for g in req["groups"]:
            groups.append({**g, "program": prog})
    for g in profile.get("extra_requirements", []):
        groups.append({**g, "program": "custom"})
    equiv = equivalents(groups) - done
    for g in groups:
        g["done"] = sorted(o for o in g["options"] if o in done)
        if "atlas_status" not in g:      # template/custom lists: trust what the official audit accepted
            via = sorted(o for o in g["options"] if o in equiv)
            if via:
                g["done_by_equivalent"] = via
                g["done"] = sorted(set(g["done"]) | set(via))
        g["remaining"] = max(0, g["need"] - len(g["done"]))
        if g.get("atlas_satisfied") or g.get("untracked"):
            # Atlas's audit already says it's complete, or it's a credit rule we can't map to courses
            # (shown in get_progress, not planned automatically).
            g["remaining"] = 0
    return groups


def equivalents(groups: list[dict]) -> set[str]:
    """Courses the official audit treats as satisfied even though you took something else
    (AP/transfer/honors versions): every option of an audit line that Atlas marks complete."""
    out: set[str] = set()
    for g in groups:
        if g.get("atlas_satisfied") and len(g.get("options", [])) <= 6:
            out |= set(g["options"])
    return out


def double_counts(groups: list[dict]) -> dict[str, list[str]]:
    """course -> list of open groups it can satisfy."""
    out: dict[str, list[str]] = {}
    for g in groups:
        if g["remaining"] <= 0:
            continue
        for o in g["options"]:
            out.setdefault(o, []).append(g["group"])
    return out


def summary(groups: list[dict]) -> list[dict]:
    out = []
    for g in groups:
        row = {"program": g["program"], "group": g["group"], "need": g["need"], "done": g["done"],
               "remaining": g["remaining"], "options_left": [o for o in g["options"] if o not in g["done"]][:25]}
        if "atlas_status" in g:
            row["atlas_status"] = {"C": "complete", "NC": "not complete", "IP": "in progress"}.get(
                g["atlas_status"], g["atlas_status"])
            row["credits"] = g.get("credits")
        if g.get("done_by_equivalent"):
            row["counted_via_equivalent_credit"] = g["done_by_equivalent"]
        if g.get("untracked") and g.get("atlas_status") != "C":
            row["note"] = "Credit rule. Not auto planned: pick courses for it with Claude."
        out.append(row)
    return out
