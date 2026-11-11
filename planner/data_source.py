"""Where data comes from.

  * SampleSource: bundled fake data, for testing.
  * AtlasSource:  live Atlas, using the user's OWN logged in session.

Each user's session, grades and cache live in their own folder (ATLAS_PLANNER_HOME,
default ~/.atlas-planner). Nothing is uploaded or shared between users.
"""
from __future__ import annotations

import csv
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from .models import (Course, CourseStats, HistoryEntry, InstructorStats, Meeting, Section,
                     parse_days, parse_time)

PKG_ROOT = Path(__file__).resolve().parent.parent
SAMPLE_DIR = PKG_ROOT / "data" / "sample"


# Hosted mode: who the current request belongs to (set by remote_app for each tool call).
import contextvars  # noqa: E402

CURRENT_USER: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar("atlas_user", default=None)
CURRENT_COOKIES: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar("atlas_cookies", default=None)
PERSONAL_PREFIXES = ("me_", "whatif_")


def is_hosted() -> bool:
    return CURRENT_USER.get() is not None


def user_home() -> Path:
    uid = CURRENT_USER.get()
    if uid:
        p = Path(os.environ.get("ATLAS_PLANNER_WORKDIR", "/tmp/atlas-planner")) / "users" / uid
    else:
        p = Path(os.environ.get("ATLAS_PLANNER_HOME", Path.home() / ".atlas-planner"))
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sections_from_json(raw: list[dict]) -> list[Section]:
    out = []
    for s in raw:
        meetings = []
        for m in s["meetings"]:
            for day in parse_days(m["days"]):
                meetings.append(Meeting(day, parse_time(m["start"]), parse_time(m["end"]),
                                        m.get("campus", "Central"), m.get("location", "")))
        out.append(Section(s["section_id"], s["component"], meetings, s.get("instructor"),
                           s.get("seats_open"), s.get("capacity")))
    return out


def _course_from_json(d: dict, term: Optional[str]) -> Course:
    secs = _sections_from_json(d.get("terms", {}).get(term, [])) if term else []
    return Course(d["code"], d["title"], float(d["credits"]), secs, d.get("prereqs", []), d.get("coreqs", []),
                  d.get("tags", []), d.get("description", ""), d.get("offered_history", []),
                  d.get("class_size"), d.get("format"), d.get("fill_days"))


def load_external_ratings(path: Path) -> dict[str, dict]:
    """Optional second opinion on instructors, in a CSV the user fills in themselves:
    instructor,rating,difficulty,num_ratings,source"""
    if not path or not path.exists():
        return {}
    out = {}
    with path.open() as f:
        for row in csv.DictReader(f):
            out[row["instructor"].strip()] = {
                "ext_rating": float(row["rating"]) if row.get("rating") else None,
                "ext_difficulty": float(row["difficulty"]) if row.get("difficulty") else None,
                "ext_n": int(row["num_ratings"]) if row.get("num_ratings") else None,
                "ext_source": row.get("source") or "manual",
            }
    return out


class DataSource:
    name = "base"
    label = ""

    def next_term(self) -> str: ...
    def catalog(self) -> list[Course]: ...                      # every course, with offering history
    def offerings(self, term: str) -> list[Course]: ...         # courses with sections in that term
    def course_stats(self, code: str) -> CourseStats: ...
    def instructor_stats(self, name: str) -> InstructorStats: ...
    def programs(self) -> list[str]: ...
    def requirements(self, program: str) -> dict: ...
    def student_history(self) -> list[HistoryEntry]: ...
    def cohort(self) -> dict: ...                               # combos + typical paths


class SampleSource(DataSource):
    name = "sample"

    def __init__(self, ext_ratings_path: Optional[Path] = None):
        self._off = json.loads((SAMPLE_DIR / "offerings.json").read_text())
        self._stats = json.loads((SAMPLE_DIR / "atlas_stats.json").read_text())
        self._programs = json.loads((SAMPLE_DIR / "programs.json").read_text())
        self._hist = json.loads((SAMPLE_DIR / "student_history.json").read_text())
        self._cohort = json.loads((SAMPLE_DIR / "cohort.json").read_text())
        self._ext = load_external_ratings(ext_ratings_path or SAMPLE_DIR / "external_ratings.csv")
        self.label = self._off["label"]

    def next_term(self) -> str:
        return self._off["next_term"]

    def catalog(self) -> list[Course]:
        return [_course_from_json(c, None) for c in self._off["courses"]]

    def offerings(self, term: str) -> list[Course]:
        term = term or self.next_term()
        return [_course_from_json(c, term) for c in self._off["courses"] if term in c.get("terms", {})]

    def course_stats(self, code: str) -> CourseStats:
        return CourseStats(**self._stats["courses"].get(code, {}))

    def instructor_stats(self, name: str) -> InstructorStats:
        base = self._stats["instructors"].get(name, {})
        return InstructorStats(name=name, **base, **self._ext.get(name, {}))

    def programs(self) -> list[str]:
        return list(self._programs)

    def requirements(self, program: str) -> dict:
        return self._programs[program]

    def student_history(self) -> list[HistoryEntry]:
        return [HistoryEntry(**h) for h in self._hist["history"]]

    def cohort(self) -> dict:
        return self._cohort


# How long each kind of Atlas data stays fresh. Course info barely changes within a term; seats do.
_DAY = 24 * 3600
TTL_BY_PREFIX = [("me_", 1 * _DAY), ("whatif_", 1 * _DAY), ("sections_", 2 * _DAY),
                 ("browse_", 45 * _DAY), ("course_", 45 * _DAY), ("instr_", 45 * _DAY), ("plan_", 120 * _DAY)]


def ttl_for(key: str, default: float) -> float:
    for prefix, ttl in TTL_BY_PREFIX:
        if key.startswith(prefix):
            return ttl
    return default


class _Cache:
    """Files on your computer (local), or the shared/encrypted store (hosted)."""

    def __init__(self, folder: Path, ttl_hours: float):
        self.folder = folder
        self.folder.mkdir(parents=True, exist_ok=True)
        self.ttl = ttl_hours * 3600

    @staticmethod
    def _safe(key: str) -> str:
        return "".join(ch if ch.isalnum() or ch in "-_." else "_" for ch in key)

    def _path(self, key: str) -> Path:
        return self.folder / (self._safe(key) + ".json")

    @staticmethod
    def _ns(key: str) -> str:
        return f"user:{CURRENT_USER.get()}" if key.startswith(PERSONAL_PREFIXES) else "shared"

    def get(self, key: str):
        if is_hosted():
            from .store import get_store
            return get_store().get(self._ns(key), "cache:" + self._safe(key))
        p = self._path(key)
        if p.exists() and time.time() - p.stat().st_mtime < ttl_for(key, self.ttl):
            return json.loads(p.read_text())
        return None

    def put(self, key: str, value) -> None:
        if is_hosted():
            from .store import get_store
            get_store().put(self._ns(key), "cache:" + self._safe(key), value, ttl=ttl_for(key, self.ttl))
            return
        self._path(key).write_text(json.dumps(value))

    def keys(self, prefix: str) -> list[str]:
        """Cached keys (in their safe form) starting with prefix."""
        if is_hosted():
            from .store import get_store
            ns = self._ns(prefix)
            return [k[len("cache:"):] for k in get_store().keys(ns, "cache:" + self._safe(prefix))]
        return [f.stem for f in self.folder.glob(self._safe(prefix) + "*.json")]

    def get_safe(self, safe_key: str):
        if is_hosted():
            from .store import get_store
            return get_store().get(self._ns(safe_key), "cache:" + safe_key)
        p = self.folder / (safe_key + ".json")
        return json.loads(p.read_text()) if p.exists() else None


NORTH_BUILDINGS = {"GGBL", "EECS", "DOW", "LBME", "BBB", "FXB", "CHRYS", "IOE", "COOL", "EWRE", "ERB", "SRB",
                   "NCRB", "ARCH", "MOORE", "LEIN", "STAMPS", "DC", "NAME", "PIER", "LURIE", "FMCRB", "GFL", "WALGRN",
                   "BEYSTER", "ELB", "NAMEB", "MEC", "WILLARD", "CSRB", "BIRB", "HH", "NCAB", "BLAU", "FORD"}
MEDICAL_BUILDINGS = {"MSRB", "MED", "MS1", "MS2", "NIB", "UH", "TAUB", "BSRB", "SPH1", "SPH2", "THOMPSON", "CVC"}

# Atlas term codes: Fall 2025 = 2560, Winter 2026 = 2570, Spring 2026 = 2580, Spring/Summer 2026 = 2590,
# Summer 2026 = 2600, Fall 2026 = 2610 ... (+50 per academic year).
_TERM_OFFSETS = {0: ("FA", 0), 10: ("WN", 1), 20: ("SP", 1), 30: ("SU", 1), 40: ("SU", 1)}


def atlas_to_term(code) -> str:
    k, r = divmod(int(code) - 2560, 50)
    season, add = _TERM_OFFSETS[r]
    return f"{season}{(2025 + k + add) % 100:02d}"


def term_to_atlas(term: str) -> str:
    season, yy = term[:2], 2000 + int(term[2:])
    r = {"FA": 0, "WN": 10, "SP": 20, "SU": 40}[season]
    year0 = yy if season == "FA" else yy - 1
    return str(2560 + 50 * (year0 - 2025) + r)


_NOT_SUBJECTS = {"OR", "AND", "AT", "OF", "IN", "TO", "THE", "RQ", "FROM", "LEVEL", "MIN", "MAX"}
_CODE = re.compile(r"\b([A-Z][A-Z&]{1,9})\s?(\d{3}[A-Z]?)\b")


def extract_codes(text: str) -> list[str]:
    """'MATH 116, 215, 216 or EECS 203' -> [MATH 116, MATH 215, MATH 216, EECS 203].
    A bare number takes the subject of the code before it."""
    out, subject = [], None
    toks = re.finditer(r"([A-Z][A-Z&]{1,9})\s?(\d{3}[A-Z]?)|(?<![\w.])(\d{3}[A-Z]?)\b", text or "")
    for m in toks:
        if m.group(1) and m.group(1) in _NOT_SUBJECTS:
            if subject:
                code = f"{subject} {m.group(2)}"
                if code not in out:
                    out.append(code)
            continue
        if m.group(1):
            subject = m.group(1)
            code = f"{subject} {m.group(2)}"
        elif subject:
            code = f"{subject} {m.group(3)}"
        else:
            continue
        if code not in out:
            out.append(code)
    return out


def parse_prereqs(text: str) -> list[list[str]]:
    """Enforced prereq text -> AND of ORs. Rough but conservative: each top level 'and' part is one group."""
    text = (text or "").strip()
    if not text:
        return []
    parts = re.split(r"\band\b|;", text, flags=re.I)
    groups = [extract_codes(p.upper()) for p in parts]
    return [g for g in groups if g]


def _name_key(name: str) -> str:
    """'Clark,Steven E' and 'Clark, Steven' -> 'clark,s'."""
    name = (name or "").lower().replace(" ", "")
    if "," not in name:
        return name
    last, first = name.split(",", 1)
    return f"{last},{first[:1]}"


def _grade_stats(grades: list[dict]):
    from .models import GRADE_POINTS
    pts = [(GRADE_POINTS[g["grade"]], g.get("percentage") or 0) for g in grades if g.get("grade") in GRADE_POINTS]
    w = sum(p for _, p in pts)
    if w <= 0:
        return None, None
    mean = sum(v * p for v, p in pts) / w
    sd = (sum(p * (v - mean) ** 2 for v, p in pts) / w) ** 0.5
    return round(mean, 2), round(sd, 2)


class AtlasSource(DataSource):
    """Live Atlas with the user's own session.

    Login: `login()` opens a real browser window. The user signs in with Weblogin + Duo and we
    save the session to their own folder (file mode 600). We never see or store passwords.
    Politeness: everything is cached for a week and requests are spaced out.

    Endpoints (mapped Nov 2026 from Atlas's own pages):
      dashboard HTML                      -> uniqname
      /api/completed-courses.json         -> U-M courses taken (no grades)
      /api/plan-search/?q=                -> majors/minors
      /api/plan-options/?plan=&type=      -> plan + program codes
      POST /api/what-if-report-search/    -> unofficial audit for up to 3 plans (requirements, courses used)
      /api/courses/browse/?subject=       -> catalog + offering history per subject
      /courses/<CODE>/                    -> redirects to current term; HTML has eval summary, grades, prereqs
      /courses/<CODE>/<term>/course-instructors.json -> instructor clarity/respect/preparedness per course
      /api/section-table-data/<CODE>/     -> sections, times, rooms, seats for the next term
    """
    name = "atlas"
    label = "Atlas (live)"
    BASE = "https://atlas.ai.umich.edu"
    MIN_SECONDS_BETWEEN_REQUESTS = 0.4   # at most ~2.5 requests a second to Atlas, even with workers
    WORKERS = 4
    DEFAULT_SUBJECTS: list[str] = []   # subjects come from your audit + premed template + must/watch lists
    PREMED_TEMPLATE = "Premed (template)"

    def __init__(self):
        home = user_home()
        self.home = home
        self.state_path = home / "atlas_session.json"
        self.cache = _Cache(home / "cache", ttl_hours=24 * 7)
        self.ext = load_external_ratings(home / "external_ratings.csv")
        self._last = 0.0
        self._throttle = threading.Lock()
        self._session = None
        self._instr: dict[str, dict] = {}   # name key -> {"clarity": [(course, pct)], "full_name": str}

    # ---------------------------------------------------------------- plumbing
    def logged_in(self) -> bool:
        if is_hosted():
            return bool(CURRENT_COOKIES.get())
        return self.state_path.exists()

    def login(self, timeout_s: int = 300) -> str:
        if is_hosted():
            return ("You're using the hosted connector. To log in again: Claude Settings > Connectors > "
                    "Atlas Planner > Disconnect, then Connect, and paste a fresh Atlas cookie.")
        return self._local_login(timeout_s)

    def _local_login(self, timeout_s: int = 300) -> str:
        """Open a real browser for Weblogin/Okta + Duo. Prefers your installed Google Chrome (Okta often
        stalls in Playwright's bundled test browser), with its own profile in ~/.atlas-planner/browser."""
        from playwright.sync_api import sync_playwright

        profile_dir = str(self.home / "browser")
        opts = dict(headless=False, ignore_default_args=["--enable-automation"],
                    args=["--disable-blink-features=AutomationControlled"], no_viewport=True)
        with sync_playwright() as p:
            ctx = None
            for channel in ("chrome", "msedge", None):
                try:
                    ctx = p.chromium.launch_persistent_context(profile_dir, channel=channel, **opts) if channel \
                        else p.chromium.launch_persistent_context(profile_dir, **opts)
                    break
                except Exception:
                    continue
            if ctx is None:
                return "Couldn't open a browser. Install Google Chrome, or run: python -m playwright install chromium"
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(self.BASE + "/accounts/login/")
            deadline = time.time() + timeout_s
            while time.time() < deadline:
                try:
                    url = page.url
                except Exception:
                    break   # window closed by the user
                if url.startswith(self.BASE) and "/accounts/login" not in url:
                    if any(c["name"] == "sessionid" for c in ctx.cookies(self.BASE)):
                        ctx.storage_state(path=str(self.state_path))
                        os.chmod(self.state_path, 0o600)
                        ctx.close()
                        for f in self.cache.folder.glob("*.json"):
                            if f.name.startswith(("me_", "whatif_")):
                                f.unlink()
                        return "Logged in. Session saved on this computer only."
                time.sleep(1)
            try:
                ctx.close()
            except Exception:
                pass
            return "Login didn't finish (timed out or the window was closed). Run atlas_login again."

    def _http(self):
        if not self.logged_in():
            raise RuntimeError("Not logged into Atlas yet. Run the `atlas_login` tool first.")
        if self._session is None:
            import requests
            s = requests.Session()
            if is_hosted():
                for name, value in (CURRENT_COOKIES.get() or {}).items():
                    s.cookies.set(name, value, domain="atlas.ai.umich.edu", path="/")
            else:
                state = json.loads(self.state_path.read_text())
                for c in state.get("cookies", []):
                    if "umich.edu" in (c.get("domain") or ""):
                        s.cookies.set(c["name"], c["value"], domain=c["domain"], path=c.get("path", "/"))
            s.headers.update({"User-Agent": "atlas-planner (personal schedule planning)",
                              "Referer": self.BASE + "/my-dashboard/"})
            self._session = s
        return self._session

    def _fetch(self, path: str, *, kind: str = "json", method: str = "GET", body: Optional[dict] = None):
        s = self._http()
        with self._throttle:
            wait = self.MIN_SECONDS_BETWEEN_REQUESTS - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
        url = path if path.startswith("http") else self.BASE + path
        if method == "POST":
            r = s.post(url, json=body, timeout=60,
                       headers={"X-CSRFTOKEN": s.cookies.get("csrftoken", ""), "Accept": "application/json"})
        else:
            r = s.get(url, timeout=30, headers={"Accept": "application/json" if kind == "json" else "text/html"})
        with self._throttle:
            self.n_requests = getattr(self, "n_requests", 0) + 1
            n = self.n_requests
        hook = getattr(self, "on_request", None)
        if hook:
            hook(n)
        if r.status_code in (401, 403) or "weblogin" in r.url or "/accounts/login" in r.url:
            raise RuntimeError("Atlas session expired. " + (
                "Reconnect: Claude Settings > Connectors > Atlas Planner > Disconnect, then Connect."
                if is_hosted() else "Run `atlas_login` again."))
        if r.status_code == 404:
            return None
        r.raise_for_status()
        return {"url": r.url, "text": r.text} if kind == "html" else r.json()

    def _cached(self, key: str, fn):
        v = self.cache.get(key)
        if v is None:
            v = fn()
            if v is not None:
                self.cache.put(key, v)
        return v

    @staticmethod
    def _compact(code: str) -> str:
        return code.replace(" ", "").replace("&", "%26")

    @staticmethod
    def _json_script(html: str, sid: str):
        m = re.search(r'<script[^>]*id="%s"[^>]*>(.*?)</script>' % re.escape(sid), html, re.S)
        if not m:
            return None
        import html as _h
        try:
            return json.loads(_h.unescape(m.group(1)))
        except json.JSONDecodeError:
            return None

    def _profile(self) -> dict:
        import yaml
        p = self.home / "profile.yaml"
        return (yaml.safe_load(p.read_text()) or {}) if p.exists() else {}

    # ---------------------------------------------------------------- the student
    def uniqname(self) -> str:
        def get():
            page = self._fetch("/my-dashboard/", kind="html")
            m = re.search(r'username="([a-z0-9]+)"', page["text"])
            if not m:
                raise RuntimeError("Couldn't find your uniqname on the Atlas dashboard.")
            return {"u": m.group(1)}
        return self._cached("me_uniqname", get)["u"]

    def student_history(self) -> list[HistoryEntry]:
        """Courses from the What If audit (includes transfer/AP and in progress), else completed-courses.
        Atlas doesn't expose grades; the user can add their own under `my_grades` in the profile."""
        prof = self._profile()
        my_grades = {k.upper(): v for k, v in (prof.get("my_grades") or {}).items()}
        seen: dict[str, HistoryEntry] = {}
        report = self._whatif(prof) if prof.get("atlas_plans") else None
        if report:
            for b in report.get("requirement_bundles", []):
                for g in b["requirement_groups"]:
                    for r in g["requirements"]:
                        for l in r["requirement_lines"]:
                            for c in l.get("courses_used", []):
                                code = re.sub(r"\s+", " ", c["course_code"]).upper()
                                term = atlas_to_term(c["term_code"]) if c.get("term_code") else ""
                                grade = None if c.get("in_progress") else my_grades.get(code, "CR")
                                seen.setdefault(code, HistoryEntry(code, term, grade, float(c.get("units_taken") or 0)))
        done = self._cached("me_completed", lambda: self._fetch("/api/completed-courses.json")) or []
        for c in done:
            code = " ".join(re.match(r"([A-Z&]+)(\d+\w*)", c["course_code"]).groups())
            if code not in seen:
                seen[code] = HistoryEntry(code, atlas_to_term(c["term"]), my_grades.get(code, "CR"), 0.0)
        cat_idx = self._catalog_if_ready() or {}
        for code, h in seen.items():
            if h.credits == 0:
                cat = cat_idx.get(code)
                h.credits = cat.credits if cat else 3.0
        return sorted(seen.values(), key=lambda h: (h.term[2:], h.term[:2], h.course))

    def audit_gpa(self) -> Optional[float]:
        """Cumulative GPA as Atlas's audit reports it (the 'Cumulative GPA' / 'Overall GPA' line)."""
        rep = self._whatif(self._profile())
        if not rep:
            rep = self._cached("me_degree", lambda: self._fetch(f"/api/{self.uniqname()}/degree-summary.json"))
        for b in (rep or {}).get("requirement_bundles", []):
            for g in b["requirement_groups"]:
                for r in g["requirements"]:
                    for l in r["requirement_lines"]:
                        if l.get("type") == "GPA" and re.search(r"cumulative|overall", l["name"], re.I):
                            return l.get("numerator")
        return None

    # ---------------------------------------------------------------- programs + requirements
    def search_programs(self, query: str) -> list[dict]:
        from urllib.parse import quote
        r = self._fetch(f"/api/plan-search/?q={quote(query)}") or {}
        rows = r.get("results", r) if isinstance(r, dict) else r
        return [{"plan_name": x["plan_name"], "type": x["type"], "description": x["description"],
                 "careers": x["careers"]} for x in rows]

    def programs(self) -> list[str]:
        prof = self._profile()
        return [p["plan"] for p in prof.get("atlas_plans", [])] + [self.PREMED_TEMPLATE]

    def _plan_codes(self, plan: str, typ: str, career: Optional[str]) -> dict:
        from urllib.parse import urlencode
        q = {"plan": plan, "type": typ}
        if career:
            q["career"] = career
        res = self._cached("plan_" + plan + typ, lambda: self._fetch("/api/plan-options/?" + urlencode(q)))
        opts = (res or {}).get("results", [])
        if not opts:
            raise KeyError(f"Atlas has no plan called {plan!r}. Use list_programs(query=...) to search.")
        o = opts[0]
        return {"code": o["code"], "prog": o["program__code"], "career": o["career__code"],
                "description": o["description"]}

    def _whatif(self, prof: dict):
        plans = prof.get("atlas_plans", [])[:3]
        if not plans:
            return None
        key = "whatif_" + "_".join(p["plan"] for p in plans)

        def run():
            codes, career = [], None
            for p in plans:
                c = self._plan_codes(p["plan"], p.get("type", "MAJ"), career)
                career = career or c["career"]
                codes.append(c)
            body = {"emplid": self.uniqname(), "acad_career": career}
            for i, c in enumerate(codes, 1):
                body[f"acad_prog{i}"], body[f"acad_plan{i}"] = c["prog"], c["code"]
            rep = self._fetch("/api/what-if-report-search/", method="POST", body=body)
            rep["_plan_codes"] = {p["plan"]: c["code"] for p, c in zip(plans, codes)}
            return rep
        return self._cached(key, run)

    def _line_to_group(self, line: dict, group_name: str, req_name: str) -> Optional[dict]:
        name = line["name"]
        opts = [] if re.search(r"\d00-Level", name, re.I) else extract_codes(name)
        status = line.get("status")
        if line.get("type") == "GPA":
            return None
        den = float(line.get("denominator") or 1)
        if opts:
            need = int(den) if ("&" in name and den <= len(opts)) else 1
        else:
            m = re.search(r"\b([A-Z]{2,9})\s+(\d)00-Level", name)
            need = max(1, round((den - float(line.get("numerator") or 0)) / 3)) if line.get("type") == "CREDIT" else 1
            cat_idx = self._catalog_if_ready()
            if m and cat_idx is not None:
                lvl = int(m.group(2)) * 100
                opts = [c.code for c in cat_idx.values() if c.subject == m.group(1) and c.level >= lvl]
        return {"group": f"{req_name}: {name[:90]}", "need": need, "options": opts,
                "atlas_status": status, "atlas_satisfied": status == "C",
                "credits": {"have": line.get("numerator"), "in_progress": line.get("numerator_in_progress"),
                            "need": line.get("denominator"), "kind": line.get("type")},
                "untracked": not opts}

    def requirements(self, program: str) -> dict:
        if program == self.PREMED_TEMPLATE:
            return json.loads((SAMPLE_DIR / "programs.json").read_text())[program]
        prof = self._profile()
        rep = self._whatif(prof)
        if not rep:
            raise KeyError(program)
        code = rep.get("_plan_codes", {}).get(program)
        if code is None:
            raise KeyError(program)
        first_plan = prof["atlas_plans"][0]["plan"]
        groups = []
        for b in rep["requirement_bundles"]:
            for g in b["requirement_groups"]:
                pid = g.get("plan_id") or ""
                if not (pid == code or (pid == "" and program == first_plan)):
                    continue
                for r in g["requirements"]:
                    for l in r["requirement_lines"]:
                        grp = self._line_to_group(l, g["name"].title(), r["name"])
                        if grp:
                            groups.append(grp)
        return {"source": "Atlas What If audit (unofficial)", "report_date": rep.get("report_date"), "groups": groups}

    # ---------------------------------------------------------------- catalog + sections
    def _subjects(self) -> list[str]:
        prof = self._profile()
        subs = list(self.DEFAULT_SUBJECTS) + list(prof.get("extra_subjects", []))
        for code in prof.get("must_take", []) + prof.get("watch_courses", []):
            subs.append(code.split()[0])
        if self.PREMED_TEMPLATE in prof.get("programs", []):
            for g in self.requirements(self.PREMED_TEMPLATE)["groups"]:
                subs += [o.split()[0] for o in g["options"]]
        rep = self._whatif(prof) if prof.get("atlas_plans") else None
        if rep:
            for b in rep["requirement_bundles"]:
                for g in b["requirement_groups"]:
                    for r in g["requirements"]:
                        for l in r["requirement_lines"]:
                            subs += [c.split()[0] for c in extract_codes(l["name"])]
        return sorted(set(subs))

    def _catalog_if_ready(self) -> Optional[dict[str, Course]]:
        """The catalog, but only if it's already downloaded (never triggers the slow first download)."""
        if hasattr(self, "_cat"):
            return self._cat
        if all(self.cache.get(f"browse_{s}") is not None for s in self._subjects()):
            return self._catalog_index()
        return None

    def _catalog_index(self) -> dict[str, Course]:
        if not hasattr(self, "_cat"):
            self._cat = {c.code: c for c in self.catalog()}
        return self._cat

    def catalog(self) -> list[Course]:
        if hasattr(self, "_cat"):
            return list(self._cat.values())
        out = {}
        self.skipped_subjects = []

        def one(sub):
            try:
                return sub, self._cached(f"browse_{sub}", lambda: self._browse_subject(sub)) or {}, None
            except RuntimeError:
                raise                      # login problems should surface
            except Exception as e:         # one bad subject (Atlas 500 etc.) shouldn't break planning
                return sub, {}, e

        results = self._pmap(one, self._subjects())
        for sub, data, err in results:
            if err is not None:
                self.skipped_subjects.append(f"{sub}: {err.__class__.__name__}")
                continue
            for c in data.get("results", []):
                if c.get("end_term_code"):
                    continue
                code = c["course_code_spaced"]
                hist = [atlas_to_term(t["term"]) for t in c.get("offered_terms", [])]
                out[code] = Course(code, c["title"], float(c.get("max_credits") or c.get("min_credits") or 3),
                                   [], [], [], [], "", hist)
        self._cat = out
        return list(out.values())

    def _browse_subject(self, sub: str) -> dict:
        """All courses in one subject, 100 per page."""
        from urllib.parse import quote
        rows, page = [], 1
        while page <= 20:
            d = self._fetch(f"/api/courses/browse/?page={page}&page_size=100&sort=alpha&subject={quote(sub)}")
            if not d:
                break
            rows += d.get("results", [])
            if not d.get("next"):
                break
            page += 1
        return {"results": rows}

    def _course_page(self, code: str) -> Optional[dict]:
        def get():
            page = self._fetch(f"/courses/{self._compact(code)}/", kind="html")
            if not page:
                return None
            html = page["text"]
            term = self._json_script(html, "current-term-data")
            return {"term": term, "props": (self._json_script(html, "course-props-data") or [])[-1:],
                    "cached": self._json_script(html, "cached-course-data") or {}}
        return self._cached("course_" + code, get)

    def enrich(self, code: str) -> Optional[Course]:
        """Add prereqs and description from the course page (cached)."""
        c = self._catalog_index().get(code)
        if not c:
            return None
        if c.description:
            return c
        page = self._course_page(code)
        if page and page["props"]:
            pr = page["props"][0]
            c.prereqs = parse_prereqs(pr.get("enforced_prereqs") or "")
            c.description = pr.get("description") or ""
            c.tags = [c.description.lower()[:600]]
        return c

    def next_term(self) -> str:
        p = self._profile().get("plan", {}).get("next_term")
        if p:
            return p
        page = self._course_page("ENGLISH 125") or self._course_page("MATH 115")
        return atlas_to_term(page["term"])

    def _sections(self, code: str):
        return self._cached("sections_" + code,
                            lambda: self._fetch(f"/api/section-table-data/{code.replace(' ', '%20').replace('&', '%26')}/"))

    def _load_instructors(self, code: str, term_code: str):
        data = self._cached(f"instr_{code}", lambda: self._fetch(
            f"/courses/{self._compact(code)}/{term_code}/course-instructors.json")) or {}
        for i in data.get("instructor_data", []):
            k = _name_key(i["full_name"])
            ev = {e["id"]: e["value"] for e in i.get("evaluations", []) if e.get("displayable")}
            rec = self._instr.setdefault(k, {"full_name": i["full_name"], "by_course": {}, "terms": 0})
            if "199" in ev:
                rec["by_course"][code] = round(ev["199"] * 100, 1)
            rec["terms"] = max(rec["terms"], i.get("terms_taught") or 0)

    def candidate_codes(self) -> list[str]:
        """Courses worth pulling section times for: requirement options, must/watch lists, premed template."""
        prof = self._profile()
        codes = set(prof.get("must_take", [])) | set(prof.get("watch_courses", []))
        for prog in self.programs():
            try:
                for g in self.requirements(prog)["groups"]:
                    if not g.get("atlas_satisfied"):
                        codes |= set(g["options"])
            except KeyError:
                pass
        cat = self._catalog_index()
        return sorted(c for c in codes if c in cat)

    def offerings(self, term: str) -> list[Course]:
        term = term or self.next_term()
        tcode = term_to_atlas(term)
        out = []
        for code in self.candidate_codes():
            c = self._catalog_index()[code]
            if term not in c.offered_history:
                continue
            try:
                data = self._sections(code)
            except RuntimeError:
                raise
            except Exception:
                continue
            if not data or tcode not in data.get("terms", []):
                continue
            if data.get("selected_term") != tcode:
                data = self._cached(f"sections_{code}_{tcode}", lambda: self._fetch(
                    f"/api/section-table-data/{code.replace(' ', '%20')}/?term={tcode}")) or {}
            secs = []
            for s in data.get("offered_classes", []):
                if s.get("term") != tcode:
                    continue
                meetings = []
                for m in s.get("meetings", []):
                    t = m.get("time") or {}
                    if t.get("is_tba") or not t.get("start"):
                        continue
                    bldg = (m.get("facility_description") or "").split()[-1:] or [""]
                    bldg = bldg[0].upper()
                    campus = "North" if bldg in NORTH_BUILDINGS else "Medical" if bldg in MEDICAL_BUILDINGS else "Central"
                    for d in m.get("days", []):
                        meetings.append(Meeting((int(d) - 1) % 7, parse_time(t["start"][:5]), parse_time(t["end"][:5]),
                                                campus, bldg))
                inst = (s.get("instructors") or [{}])[0].get("name")
                secs.append(Section(s["section"], s.get("section_type", "LEC"), meetings, inst,
                                    s.get("open_seat_count"), s.get("enrollment_capacity")))
            if not secs:
                continue
            try:
                self._load_instructors(code, tcode)
            except RuntimeError:
                raise
            except Exception:
                pass
            e = self.enrich(code) or c
            out.append(Course(code, c.title, c.credits, secs, e.prereqs, [], e.tags, e.description,
                              c.offered_history))
        return out

    # ---------------------------------------------------------------- evaluations
    def course_stats(self, code: str) -> CourseStats:
        try:
            page = self._course_page(code)
        except RuntimeError:
            raise
        except Exception:
            return CourseStats()
        if not page:
            return CourseStats()
        cd = page.get("cached") or {}
        ev = cd.get("eval_summary") or {}
        resp = ev.get("responses") or {}
        gr = cd.get("grades") or {}
        mean, sd = _grade_stats(gr.get("grades") or [])
        med = gr.get("median_grade")
        return CourseStats(workload_pct=resp.get("q891"), median_grade=med[0] if isinstance(med, list) and med else med,
                           mean_gpa=mean, gpa_sd=sd, desire_pct=resp.get("q4"),
                           understanding_pct=resp.get("q1631"), expectations_pct=resp.get("q1633"),
                           n_responses=ev.get("num_responses"))

    def _instr_from_cache(self):
        """Rebuild instructor ratings from course-instructors files already cached (no new requests)."""
        if getattr(self, "_instr_loaded", False):
            return
        self._instr_loaded = True
        for safe in self.cache.keys("instr_"):
            code = safe[len("instr_"):].replace("_", " ")
            data = self.cache.get_safe(safe)
            if not isinstance(data, dict):
                continue
            for i in data.get("instructor_data", []):
                ev = {e["id"]: e["value"] for e in i.get("evaluations", []) if e.get("displayable")}
                rec = self._instr.setdefault(_name_key(i["full_name"]), {"full_name": i["full_name"], "by_course": {}, "terms": 0})
                if "199" in ev:
                    rec["by_course"].setdefault(code, round(ev["199"] * 100, 1))
                rec["terms"] = max(rec["terms"], i.get("terms_taught") or 0)

    def instructor_stats(self, name: str) -> InstructorStats:
        """Atlas gives clarity per instructor per course but not a response count, so n_responses is a
        rough proxy (about 20 responses per term taught) used only to weight Atlas vs a second source."""
        self._instr_from_cache()
        rec = self._instr.get(_name_key(name), {})
        vals = list(rec.get("by_course", {}).values())
        ext = self.ext.get(name) or self.ext.get(rec.get("full_name", ""), {})
        return InstructorStats(name=name, clarity_pct=round(sum(vals) / len(vals), 1) if vals else None,
                               n_responses=None if not vals else 20 * max(1, rec.get("terms", 1)),
                               history=[{"course": k, "clarity_pct": v} for k, v in rec.get("by_course", {}).items()],
                               **ext)

    def cohort(self) -> dict:
        """Atlas 'commonly taken with' and student levels. No stress data, so no combo penalties."""
        typical = {}
        for code in self.candidate_codes():
            page = self.cache.get("course_" + code)   # only what's already fetched; don't crawl for this
            if not page:
                continue
            lv = {x["name"]: x["value"] for x in (page.get("cached") or {}).get("student_levels", [])}
            if lv:
                order = {"freshman": 0, "first-year": 0, "first year": 0, "sophomore": 1, "junior": 2, "senior": 3}
                ranked = [(v, k) for k, v in lv.items() if k.lower() in order]
                if ranked:
                    share, best = max(ranked)
                    typical[code] = {"typical_term_index": order[best.lower()] * 2 + 1, "share_of_similar_students": share}
        return {"combos": [], "typical_paths": {"cohort": "all Atlas students who took each course", "courses": typical}}

    def _pmap(self, fn, items) -> list:
        """Run fn over items with a few worker threads, keeping this request's user and login."""
        items = list(items)
        ctxs = [contextvars.copy_context() for _ in items]     # one copy each: a context can't be shared by threads
        with ThreadPoolExecutor(self.WORKERS) as pool:
            return list(pool.map(lambda pair: pair[0].run(fn, pair[1]), zip(ctxs, items)))

    def _prefetch_course(self, code: str, term: str, tcode: str) -> None:
        """Download (into the cache) everything planning needs for one course. Safe to run in parallel."""
        c = self._catalog_index().get(code)
        try:
            self._course_page(code)
            if c and term in c.offered_history:
                data = self._sections(code)
                if data and tcode in data.get("terms", []):
                    if data.get("selected_term") != tcode:
                        self._cached(f"sections_{code}_{tcode}", lambda: self._fetch(
                            f"/api/section-table-data/{code.replace(' ', '%20')}/?term={tcode}"))
                    self._cached(f"instr_{code}", lambda: self._fetch(
                        f"/courses/{self._compact(code)}/{tcode}/course-instructors.json"))
        except RuntimeError:
            raise
        except Exception:
            pass

    def warm(self, term: str = "") -> dict:
        """Prefetch everything planning needs, a few requests at a time. Course data then lasts the term."""
        t0 = time.time()
        self.progress_total = 10
        self.student_history()
        cat = self.catalog()
        term = term or self.next_term()
        tcode = term_to_atlas(term)
        cands = self.candidate_codes()
        self.progress_total = getattr(self, "n_requests", 0) + 3 * len(cands)
        self._pmap(lambda code: self._prefetch_course(code, term, tcode), cands)
        offs = self.offerings(term)          # all cache hits now
        return {"catalog_courses": len(cat), "skipped_subjects": getattr(self, "skipped_subjects", []),
                "candidates": len(cands), "offered_next_term": len(offs),
                "requests": getattr(self, "n_requests", 0), "seconds": round(time.time() - t0)}


def get_source() -> DataSource:
    if is_hosted() or os.environ.get("ATLAS_PLANNER_SOURCE", "sample").lower() == "atlas":
        return AtlasSource()
    home_csv = user_home() / "external_ratings.csv"
    return SampleSource(home_csv if home_csv.exists() else None)
