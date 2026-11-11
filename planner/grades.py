"""Your predicted grades, GPA projections, and teaching style match.

Predicted grade for course X
  1. Start from what students usually get: the instructor's mean GPA in X if Atlas has it,
     otherwise X's course mean.
  2. Look at every course you've finished. For each one, how far above or below that course's
     average did you land? (your delta)
  3. Average those deltas, weighting courses that are more like X more heavily (same subject,
     overlapping topics, similar workload). Shrink toward 0 when there's little evidence.
  4. If you have enough history, check whether you do relatively better or worse on heavy
     courses, and adjust for X's workload.
  5. The range widens when evidence is thin. Confidence = low / medium / high.

BCPM = biology, chemistry, physics, math (the AMCAS science GPA). Which subjects count is
configurable in your profile because AMCAS classifies by course content.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from .models import GRADE_POINTS, Course, HistoryEntry, gp_to_letter
from .scoring import _slope, course_mean_gpa

DEFAULT_BCPM = ["BIOLOGY", "CHEM", "PHYSICS", "MATH", "STATS", "BIOLCHEM", "MCDB"]


@dataclass
class GradePrediction:
    course: str
    gpa: float
    low: float
    high: float
    letter: str
    range: str
    confidence: str
    evidence_weight: float
    based_on: list[str]
    baseline: float

    def as_dict(self):
        return {"course": self.course, "predicted": self.letter, "range": self.range, "gpa": round(self.gpa, 2),
                "confidence": self.confidence, "typical_for_course": round(self.baseline, 2),
                "most_similar_past_courses": self.based_on}


def _similarity(a: Course, b: Course, wl_a: float, wl_b: float) -> float:
    ta, tb = set(a.tags) - {"abroad_ok"}, set(b.tags) - {"abroad_ok"}
    jac = len(ta & tb) / len(ta | tb) if ta | tb else 0
    return 0.15 + 0.5 * (a.subject == b.subject) + 0.35 * jac + 0.25 * (1 - abs(wl_a - wl_b) / 100)


class GradeModel:
    def __init__(self, source, history: list[HistoryEntry]):
        self.src = source
        self.cat = {c.code: c for c in source.catalog()}
        self.graded = [h for h in history if h.grade and h.grade in GRADE_POINTS and h.course in self.cat]
        self.points = []   # (course, delta, workload_pct)
        for h in self.graded:
            st = source.course_stats(h.course)
            base = course_mean_gpa(st)
            if base is None:
                continue
            self.points.append((self.cat[h.course], GRADE_POINTS[h.grade] - base, st.workload_pct or 55))
        n = len(self.points)
        if n >= 4:
            self.wl_slope = _slope([p[2] for p in self.points], [p[1] for p in self.points]) * n / (n + 4)
            self.wl_mean = sum(p[2] for p in self.points) / n
        else:
            self.wl_slope, self.wl_mean = 0.0, 55.0

    def predict(self, code: str, instructor: Optional[str] = None) -> Optional[GradePrediction]:
        c = self.cat.get(code)
        if not c:
            return None
        st = self.src.course_stats(code)
        base = course_mean_gpa(st)
        if base is None:
            return None
        if instructor:
            inst = self.src.instructor_stats(instructor)
            base = inst.mean_gpa_given.get(code, base)
        wl = st.workload_pct or 55
        weighted = [(_similarity(c, pc, wl, pwl), d, pc.code) for pc, d, pwl in self.points]
        sw = sum(w for w, _, _ in weighted)
        delta = sum(w * d for w, d, _ in weighted) / (sw + 2.0) if weighted else 0.0
        delta += self.wl_slope * (wl - self.wl_mean)
        gpa = max(0.0, min(4.0, base + delta))
        spread = 0.18 + 0.55 / math.sqrt(1 + sw)
        lo, hi = max(0.0, gpa - spread), min(4.0, gpa + spread)
        conf = "low" if sw < 2 else "medium" if sw < 4.5 else "high"
        top = [code_ for _, _, code_ in sorted(weighted, reverse=True)[:3]]
        lo_l, hi_l = gp_to_letter(lo), gp_to_letter(hi)
        return GradePrediction(code, gpa, lo, hi, gp_to_letter(gpa), lo_l if lo_l == hi_l else f"{lo_l} to {hi_l}",
                               conf, round(sw, 2), top, base)

    def teaching_style(self) -> dict:
        """Do you do relatively better with high clarity instructors? Returns a weight multiplier."""
        xs, ys = [], []
        hist_by_course = {h.course: h for h in self.graded}
        for pc, d, _ in self.points:
            h = hist_by_course.get(pc.code)
            if h and h.instructor:
                cl = self.src.instructor_stats(h.instructor).clarity_pct
                if cl is not None:
                    xs.append(cl)
                    ys.append(d)
        if len(xs) < 4:
            return {"multiplier": 1.0, "finding": "Not enough graded courses with instructor data yet.", "n": len(xs)}
        s = _slope(xs, ys) * len(xs) / (len(xs) + 4)
        mult = max(0.6, min(1.8, 1 + s * 25))
        if s > 0.004:
            msg = "You tend to beat the average by more with instructors rated clear, so instructor clarity counts extra for you."
        elif s < -0.004:
            msg = "Instructor clarity hasn't made much difference to your results, so it counts a bit less for you."
        else:
            msg = "No clear pattern between instructor clarity and your results yet."
        return {"multiplier": round(mult, 2), "finding": msg, "n": len(xs), "slope_per_clarity_pt": round(s, 4)}


def gpa_projection(history: list[HistoryEntry], planned: list[tuple[str, float, float]],
                   bcpm_subjects: Optional[list[str]] = None) -> dict:
    """planned = [(course, credits, predicted_gpa)]"""
    subj = set(bcpm_subjects or DEFAULT_BCPM)

    def agg(rows):
        cr = sum(c for _, c, _ in rows)
        return round(sum(c * g for _, c, g in rows) / cr, 2) if cr else None

    past = [(h.course, h.credits, GRADE_POINTS[h.grade]) for h in history if h.grade in GRADE_POINTS]
    is_b = lambda code: code.split()[0] in subj  # noqa: E731
    return {
        "term_gpa": agg(planned),
        "cumulative_now": agg(past),
        "cumulative_after": agg(past + planned),
        "bcpm_now": agg([r for r in past if is_b(r[0])]),
        "bcpm_after": agg([r for r in past + planned if is_b(r[0])]),
        "bcpm_subjects": sorted(subj),
    }
