"""Key/value storage for the hosted connector.

Local use (Claude Desktop) never touches this: everything stays in ~/.atlas-planner files.
Hosted use stores three kinds of things:
  * "shared"       public-ish Atlas course data (catalog, sections, evaluations). Same for every student,
                   so one student's download makes everyone else's fast. Not encrypted.
  * "user:<uniq>"  one student's profile, audit, history, last results. Encrypted with SECRET_KEY.
  * "oauth:*"      registered clients and short lived authorization codes.

Backends: Postgres when DATABASE_URL is set (free Neon/Supabase), otherwise a local SQLite file.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from typing import Any, Optional

_lock = threading.Lock()


def _fernet():
    from cryptography.fernet import Fernet
    key = os.environ.get("SECRET_KEY")
    if not key:
        raise RuntimeError("SECRET_KEY isn't set. Generate one with: python -c \"from cryptography.fernet "
                           "import Fernet; print(Fernet.generate_key().decode())\"")
    try:
        return Fernet(key.encode())
    except ValueError:
        # Any random string works (e.g. a host's auto generated secret): derive a proper Fernet key from it.
        import base64
        import hashlib
        return Fernet(base64.urlsafe_b64encode(hashlib.sha256(key.encode()).digest()))


def encrypt(obj: Any) -> str:
    return _fernet().encrypt(json.dumps(obj).encode()).decode()


def decrypt(token: str, max_age: Optional[int] = None) -> Any:
    return json.loads(_fernet().decrypt(token.encode(), ttl=max_age))


class Store:
    @staticmethod
    def _clean_url(url: str) -> str:
        """Tolerate pasted connection strings: 'DATABASE_URL=...', quotes, extra lines, channel_binding."""
        import re
        m = re.search(r"postgres(?:ql)?://[^\s'\"]+", url or "")
        if not m:
            return (url or "").strip()
        u = m.group(0)
        u = re.sub(r"[&?]channel_binding=[^&]*", "", u)
        if "?" not in u and "&" in u:
            u = u.replace("&", "?", 1)
        return u

    def __init__(self, url: Optional[str] = None):
        self.url = self._clean_url(url or os.environ.get("DATABASE_URL") or "")
        self.pg = self.url.startswith(("postgres://", "postgresql://"))
        self._local = threading.local()
        self._init()

    # ------------------------------------------------------------ connection
    def _conn(self):
        c = getattr(self._local, "c", None)
        if c is not None:
            if self.pg and c.closed:
                c = None
            else:
                return c
        if self.pg:
            import psycopg
            c = psycopg.connect(self.url, autocommit=True)
        else:
            path = self.url.removeprefix("sqlite:///") or os.environ.get("STORE_PATH", "atlas_planner.db")
            c = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._local.c = c
        return c

    def _x(self, sql: str, args: tuple = ()):
        if self.pg:
            sql = sql.replace("?", "%s")
        for attempt in range(2):
            try:
                c = self._conn()
                cur = c.cursor()
                cur.execute(sql, args)
                return cur
            except Exception:
                self._local.c = None
                if attempt:
                    raise

    def _init(self):
        self._x("CREATE TABLE IF NOT EXISTS kv (ns TEXT NOT NULL, k TEXT NOT NULL, v TEXT NOT NULL, "
                "expires DOUBLE PRECISION, updated DOUBLE PRECISION, PRIMARY KEY (ns, k))")

    # ------------------------------------------------------------ api
    def _secret(self, ns: str) -> bool:
        return ns.startswith("user:")

    # Shared course data is the same for everyone and changes rarely: keep a copy in memory so a plan
    # doesn't make thousands of round trips to the database.
    MEM_SECONDS = 600
    MEM_MAX = 5000

    def _mem(self) -> dict:
        if not hasattr(self, "_m"):
            self._m = {}
        return self._m

    def get(self, ns: str, key: str) -> Any:
        if ns == "shared":
            hit = self._mem().get(key)
            if hit and hit[0] > time.time():
                return hit[1]
        v = self._get(ns, key)
        if ns == "shared" and v is not None:
            m = self._mem()
            if len(m) > self.MEM_MAX:
                m.clear()
            m[key] = (time.time() + self.MEM_SECONDS, v)
        return v

    def _get(self, ns: str, key: str) -> Any:
        row = self._x("SELECT v, expires FROM kv WHERE ns=? AND k=?", (ns, key)).fetchone()
        if not row:
            return None
        v, exp = row
        if exp and exp < time.time():
            self.delete(ns, key)
            return None
        return decrypt(v) if self._secret(ns) else json.loads(v)

    def put(self, ns: str, key: str, value: Any, ttl: Optional[float] = None) -> None:
        if ns == "shared":
            self._mem()[key] = (time.time() + self.MEM_SECONDS, value)
        v = encrypt(value) if self._secret(ns) else json.dumps(value)
        exp = time.time() + ttl if ttl else None
        with _lock:
            self._x("DELETE FROM kv WHERE ns=? AND k=?", (ns, key))
            self._x("INSERT INTO kv (ns, k, v, expires, updated) VALUES (?, ?, ?, ?, ?)",
                    (ns, key, v, exp, time.time()))

    def delete(self, ns: str, key: str) -> None:
        if ns == "shared":
            self._mem().pop(key, None)
        self._x("DELETE FROM kv WHERE ns=? AND k=?", (ns, key))

    @staticmethod
    def _like(prefix: str) -> str:
        return prefix.replace("!", "!!").replace("%", "!%").replace("_", "!_") + "%"

    def delete_prefix(self, ns: str, prefix: str = "") -> None:
        if ns == "shared":
            for k in [k for k in self._mem() if k.startswith(prefix)]:
                self._mem().pop(k, None)
        self._x("DELETE FROM kv WHERE ns=? AND k LIKE ? ESCAPE '!'", (ns, self._like(prefix)))

    def keys(self, ns: str, prefix: str = "") -> list[str]:
        rows = self._x("SELECT k, expires FROM kv WHERE ns=? AND k LIKE ? ESCAPE '!'",
                       (ns, self._like(prefix))).fetchall()
        now = time.time()
        return [k for k, exp in rows if not exp or exp >= now]

    def count(self, ns: str, prefix: str = "") -> int:
        return len(self.keys(ns, prefix))


_store: Optional[Store] = None


def get_store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    return _store
