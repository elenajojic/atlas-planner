"""Core data types shared by every part of the planner."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

DAY_CODES = {"M": 0, "Tu": 1, "W": 2, "Th": 3, "F": 4, "Sa": 5, "Su": 6}
DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

GRADE_POINTS = {"A+": 4.0, "A": 4.0, "A-": 3.7, "B+": 3.3, "B": 3.0, "B-": 2.7,
                "C+": 2.3, "C": 2.0, "C-": 1.7, "D+": 1.3, "D": 1.0, "D-": 0.7, "E": 0.0}


def gp_to_letter(gp: float) -> str:
    best = min(((abs(v - gp), k) for k, v in GRADE_POINTS.items() if k != "A+"), key=lambda t: t[0])
    return best[1]


def parse_days(s: str) -> list[int]:
    """'MW' -> [0,2], 'TuTh' -> [1,3], 'MTuWThF' -> [0..4]."""
    out, i = [], 0
    while i < len(s):
        for code in ("Tu", "Th", "Sa", "Su", "M", "W", "F"):
            if s.startswith(code, i):
                out.append(DAY_CODES[code])
                i += len(code)
                break
        else:
            raise ValueError(f"Can't parse day string {s!r}")
    return out


def parse_time(t: str) -> int:
    h, m = t.split(":")
    return int(h) * 60 + int(m)


def fmt_time(m: int) -> str:
    h, mm = divmod(m, 60)
    suffix = "am" if h < 12 else "pm"
    return f"{h % 12 or 12}:{mm:02d}{suffix}"


# ---- terms: "FA26", "WN27", "SP27", "SU27"
SEASONS = {"WN": 0, "SP": 1, "SU": 2, "FA": 3}
SEASON_NAMES = {"WN": "Winter", "SP": "Spring", "SU": "Summer", "FA": "Fall"}


def term_key(code: str) -> tuple[int, int]:
    return (2000 + int(code[2:]), SEASONS[code[:2]])


def term_name(code: str) -> str:
    return f"{SEASON_NAMES[code[:2]]} 20{code[2:]}"


def next_term(code: str) -> str:
    order = ["WN", "SP", "SU", "FA"]
    s, y = code[:2], int(code[2:])
    i = order.index(s)
    return f"{order[i + 1]}{y:02d}" if i < 3 else f"WN{y + 1:02d}"


def terms_between(start: str, end: str, include_spring_summer: bool) -> list[str]:
    out, t = [], start
    while term_key(t) <= term_key(end):
        if include_spring_summer or is_main_term(t):
            out.append(t)
        t = next_term(t)
    return out


def is_main_term(code: str) -> bool:
    return code[:2] in ("FA", "WN")


@dataclass(frozen=True)
class Meeting:
    day: int
    start: int
    end: int
    campus: str = "Central"
    location: str = ""     # building code, e.g. "CHEM", "GGBL"

    def overlaps(self, other: "Meeting", buffer: int = 0) -> bool:
        return self.day == other.day and self.start < other.end + buffer and other.start < self.end + buffer


@dataclass
class Section:
    section_id: str
    component: str          # LEC / DIS / LAB / SEM
    meetings: list[Meeting]
    instructor: Optional[str] = None
    seats_open: Optional[int] = None
    capacity: Optional[int] = None


@dataclass
class Course:
    code: str
    title: str
    credits: float
    sections: list[Section]
    prereqs: list[list[str]] = field(default_factory=list)   # must be done in an EARLIER term (AND of ORs)
    coreqs: list[list[str]] = field(default_factory=list)    # earlier OR same term
    tags: list[str] = field(default_factory=list)
    description: str = ""
    offered_history: list[str] = field(default_factory=list)  # past term codes it ran
    class_size: Optional[int] = None
    format: Optional[str] = None            # "large lecture", "seminar", "lab heavy"...
    fill_days: Optional[float] = None       # median days after registration opens until full
    level: int = 0

    @property
    def subject(self) -> str:
        return self.code.split()[0]

    def __post_init__(self):
        if not self.level:
            try:
                self.level = int("".join(ch for ch in self.code.split()[-1] if ch.isdigit())[:3])
            except ValueError:
                self.level = 0


@dataclass
class CourseStats:
    workload_pct: Optional[float] = None
    median_grade: Optional[str] = None
    mean_gpa: Optional[float] = None
    gpa_sd: Optional[float] = None
    desire_pct: Optional[float] = None
    understanding_pct: Optional[float] = None
    expectations_pct: Optional[float] = None
    n_responses: Optional[int] = None


@dataclass
class InstructorStats:
    name: str
    clarity_pct: Optional[float] = None
    n_responses: Optional[int] = None
    mean_gpa_given: dict = field(default_factory=dict)   # course code -> mean gpa in their sections
    history: list = field(default_factory=list)          # [{"term","course","mean_gpa","clarity_pct"}]
    ext_rating: Optional[float] = None
    ext_difficulty: Optional[float] = None
    ext_n: Optional[int] = None
    ext_source: Optional[str] = None


@dataclass
class HistoryEntry:
    course: str
    term: str
    grade: Optional[str]          # None = in progress
    credits: float
    instructor: Optional[str] = None
