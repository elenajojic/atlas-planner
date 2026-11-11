"""Turning raw numbers into comparable scores.

Instructor cross comparison
  Atlas clarity (0 to 100) and an optional second source (1 to 5 rating, rescaled to 0 to 100)
  are blended, each weighted by how many responses it rests on. If they disagree by more than
  DISAGREE_POINTS the instructor is flagged.

Instructor grading
  Compares the mean GPA in an instructor's sections to the course mean, and fits a trend over
  their recent terms (are their grades or clarity scores rising or falling?).

Workload index (per course)
  credits/4 * (0.5 + workload_pct/100). A 4 credit course where 50% of students say "heavier than
  usual" = 1.0, a brutal one at 92% = 1.42. A term's index is the sum: ~4.0 normal, 5.0+ heavy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .models import GRADE_POINTS, CourseStats, InstructorStats

DISAGREE_POINTS = 20
NEUTRAL = 60.0


def _confidence(n: Optional[int], k: int = 30) -> float:
    return 0.25 if not n else n / (n + k)


def _slope(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 3:
        return 0.0
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return 0.0 if den == 0 else sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


@dataclass
class InstructorVerdict:
    name: str
    score: float
    atlas: Optional[float]
    external: Optional[float]
    grading_vs_course: Optional[float] = None   # + = grades higher than course average
    gpa_trend_per_term: Optional[float] = None
    clarity_trend_per_term: Optional[float] = None
    flags: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.name}: {self.score:.0f}/100"]
        if self.atlas is not None:
            parts.append(f"Atlas clarity {self.atlas:.0f}")
        if self.external is not None:
            parts.append(f"2nd source {self.external:.0f}")
        if self.grading_vs_course is not None:
            parts.append(f"grades {self.grading_vs_course:+.2f} vs course avg")
        if self.flags:
            parts.append("; ".join(self.flags))
        return " | ".join(parts)


def judge_instructor(s: InstructorStats, course: Optional[str] = None,
                     course_stats: Optional[CourseStats] = None) -> InstructorVerdict:
    atlas = s.clarity_pct
    ext = (s.ext_rating - 1) / 4 * 100 if s.ext_rating is not None else None
    flags = []
    if atlas is None and ext is None:
        v = InstructorVerdict(s.name, NEUTRAL, None, None, flags=["no rating data"])
    else:
        wa = _confidence(s.n_responses) if atlas is not None else 0
        we = _confidence(s.ext_n) * 0.7 if ext is not None else 0
        score = ((atlas or 0) * wa + (ext or 0) * we) / (wa + we)
        if atlas is not None and ext is not None and abs(atlas - ext) > DISAGREE_POINTS:
            hi = "Atlas" if atlas > ext else "2nd source"
            flags.append(f"sources disagree by {abs(atlas - ext):.0f} pts ({hi} higher)")
        if s.ext_difficulty is not None and s.ext_difficulty >= 4.3:
            flags.append(f"rated very difficult ({s.ext_difficulty}/5)")
        if (s.n_responses or 0) < 40 and (s.ext_n or 0) < 20:
            flags.append("small sample")
        score = NEUTRAL + (score - NEUTRAL) * min(1.0, wa + we)
        v = InstructorVerdict(s.name, score, atlas, ext, flags=flags)

    if course and course_stats and course_stats.mean_gpa is not None and course in s.mean_gpa_given:
        v.grading_vs_course = round(s.mean_gpa_given[course] - course_stats.mean_gpa, 2)
        if v.grading_vs_course <= -0.2:
            v.flags.append("grades harder than the course average")
        elif v.grading_vs_course >= 0.2:
            v.flags.append("grades easier than the course average")
    hist = [h for h in s.history if not course or h.get("course") == course]
    if len(hist) >= 3:
        xs = list(range(len(hist)))
        v.gpa_trend_per_term = round(_slope(xs, [h["mean_gpa"] for h in hist]), 3)
        v.clarity_trend_per_term = round(_slope(xs, [h["clarity_pct"] for h in hist]), 2)
        if v.clarity_trend_per_term >= 1.5:
            v.flags.append("clarity improving")
        elif v.clarity_trend_per_term <= -1.5:
            v.flags.append("clarity declining")
        if v.gpa_trend_per_term <= -0.03:
            v.flags.append("grades trending down")
    return v


def workload_index(credits: float, stats: CourseStats) -> float:
    pct = stats.workload_pct if stats.workload_pct is not None else 55.0
    return credits / 4 * (0.5 + pct / 100)


def course_quality(stats: CourseStats) -> float:
    vals = [v for v in (stats.understanding_pct, stats.expectations_pct, stats.desire_pct) if v is not None]
    return sum(vals) / len(vals) if vals else NEUTRAL


def course_mean_gpa(stats: CourseStats) -> Optional[float]:
    if stats.mean_gpa is not None:
        return stats.mean_gpa
    g = GRADE_POINTS.get((stats.median_grade or "").strip())
    return None if g is None else g - 0.15   # mean usually sits a bit under the median


def difficulty_score(stats: CourseStats) -> float:
    """0 to 100, higher = harder. Blends workload with how low grades run."""
    wl = stats.workload_pct if stats.workload_pct is not None else 55
    gpa = course_mean_gpa(stats) or 3.3
    return round(0.6 * wl + 0.4 * max(0, min(100, (3.9 - gpa) / 1.2 * 100)), 1)
