"""Walking times between UMich buildings.

Coordinates are approximate (good to ~50 m), which is plenty for walk estimates.
Unknown buildings fall back to campus level rules.
North <-> Central (or Medical) always needs the cross campus gap (default 30 min, the bus).
"""
from __future__ import annotations

import math

BUILDINGS = {
    # Central
    "CHEM": (42.2785, -83.7366, "Central"), "BSB": (42.2781, -83.7354, "Central"),
    "MH": (42.2768, -83.7398, "Central"), "AH": (42.2766, -83.7394, "Central"),
    "DENN": (42.2795, -83.7368, "Central"), "WEIS": (42.2788, -83.7397, "Central"),
    "WH": (42.2754, -83.7372, "Central"), "EH": (42.2770, -83.7350, "Central"),
    "RAND": (42.2793, -83.7354, "Central"), "LSA": (42.2772, -83.7422, "Central"),
    # Medical
    "MSRB": (42.2830, -83.7310, "Medical"), "MED": (42.2840, -83.7300, "Medical"),
    # North
    "GGBL": (42.2933, -83.7136, "North"), "EECS": (42.2925, -83.7144, "North"),
    "DOW": (42.2930, -83.7157, "North"), "LBME": (42.2885, -83.7131, "North"),
    "BBB": (42.2928, -83.7164, "North"), "FXB": (42.2936, -83.7121, "North"),
    "CHRYS": (42.2909, -83.7170, "North"),
}

WALK_M_PER_MIN = 80      # ~3 mph
DETOUR = 1.3             # paths aren't straight lines


def _meters(a, b) -> float:
    lat1, lon1, lat2, lon2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def campus_of(location: str, campus: str) -> str:
    return BUILDINGS.get(location, (0, 0, campus))[2] if location else campus


def transit_minutes(loc_a: str, campus_a: str, loc_b: str, campus_b: str, cross_campus_min: int = 30) -> int:
    """Minutes needed to get from one class to the next."""
    ca, cb = campus_of(loc_a, campus_a), campus_of(loc_b, campus_b)
    if "any" in (ca, cb):
        return 0
    if ca != cb:
        return cross_campus_min
    if loc_a in BUILDINGS and loc_b in BUILDINGS:
        if loc_a == loc_b:
            return 2
        return math.ceil(_meters(BUILDINGS[loc_a], BUILDINGS[loc_b]) * DETOUR / WALK_M_PER_MIN) + 2
    return 10  # same campus, unknown buildings
