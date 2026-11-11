"""Atlas Planner as a hosted Claude connector (remote MCP over HTTPS).

Add it once in Claude: Settings > Connectors > Add custom connector > paste https://<your-host>/mcp.
Connecting opens a page on this server where the student pastes their Atlas login cookie. No passwords
or Duo codes ever touch this server.

Privacy design
  * The Atlas cookie lives only inside the encrypted OAuth token Claude holds. It is never written to disk.
  * A student's profile, audit and results are stored encrypted (SECRET_KEY), keyed by uniqname.
  * Course catalog, sections and evaluations are shared between students (same for everyone), which
    makes the planner fast for everyone after the first download.

Env vars
  BASE_URL      public https URL of this server, no trailing slash (required)
  SECRET_KEY    Fernet key (required). python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
  DATABASE_URL  postgres://... (free Neon/Supabase). Without it, a local SQLite file is used.
  PORT          default 8000
"""
from __future__ import annotations

import contextvars
import functools
import html
import json
import os
import re
import secrets
import threading
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode

import anyio
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import (AccessToken, AuthorizationCode, AuthorizationParams,
                                      OAuthAuthorizationServerProvider, RefreshToken, construct_redirect_uri)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.server.mcpserver import MCPServer
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from . import server as planner
from .data_source import CURRENT_COOKIES, CURRENT_USER, AtlasSource, user_home
from .store import decrypt, encrypt, get_store

BASE_URL = (os.environ.get("BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://localhost:8000").rstrip("/")
ATLAS = "https://atlas.ai.umich.edu"
ACCESS_TTL = 7 * 24 * 3600
REFRESH_TTL = 120 * 24 * 3600
SCOPE = "atlas"

# Tools that need the full course download. Until it's done they start it in the background
# and answer right away, so a chat never sits waiting on a timeout.
HEAVY = {"refresh_atlas_data", "plan_all_semesters", "optimize_schedule", "search_offerings", "course_report",
         "compare_courses", "what_if_scenario", "find_study_abroad_term"}
LOCAL_ONLY = {"atlas_login"}

INSTRUCTIONS = planner.INSTRUCTIONS.replace(
    "  1. Atlas login (atlas_login) when the source is 'atlas'.\n",
    "  1. (Login already happened when the user connected this connector.)\n") + """

HOSTED CONNECTOR NOTES: If a tool returns {"status": "loading"}, tell the user their Atlas data is still
downloading in the background (first time only, a few minutes), say roughly how far along it is, and offer to
continue with setup questions meanwhile. Try the tool again when they reply. If a tool says the Atlas session
expired, tell them to reconnect: Settings > Connectors > Atlas Planner > Disconnect, then Connect.
The HTML view_file paths are on the server and not viewable: draw the week grid yourself from `grid`."""


# ====================================================================== OAuth
def _parse_cookies(text: str) -> dict:
    """Accepts 'sessionid=..; csrftoken=..', two lines 'name value', or just the sessionid value."""
    text = (text or "").strip().strip('"').strip("'")
    out = {}
    for m in re.finditer(r"(sessionid|csrftoken)\s*[=:\t ]\s*([A-Za-z0-9_\-.:]+)", text):
        out[m.group(1)] = m.group(2)
    if not out and re.fullmatch(r"[A-Za-z0-9_\-.:]{20,}", text):
        out["sessionid"] = text
    return out


def _check_atlas(cookies: dict) -> str:
    """Return the uniqname if these cookies are a live Atlas session."""
    import requests
    s = requests.Session()
    for k, v in cookies.items():
        s.cookies.set(k, v, domain="atlas.ai.umich.edu", path="/")
    r = s.get(ATLAS + "/my-dashboard/", timeout=30, headers={"User-Agent": "atlas-planner (personal schedule planning)"})
    m = re.search(r'username="([a-z0-9]+)"', r.text)
    if r.status_code != 200 or not m or "/accounts/login" in r.url or "weblogin" in r.url:
        raise ValueError("That cookie didn't log into Atlas. Make sure you copied sessionid while logged in.")
    if "csrftoken" not in cookies and s.cookies.get("csrftoken"):
        cookies["csrftoken"] = s.cookies.get("csrftoken")
    return m.group(1)


class AtlasOAuth(OAuthAuthorizationServerProvider):
    def __init__(self):
        self.db = get_store()

    async def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        d = self.db.get("oauth", "client:" + client_id)
        return OAuthClientInformationFull.model_validate(d) if d else None

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        self.db.put("oauth", "client:" + client_info.client_id, client_info.model_dump(mode="json"))

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        rid = secrets.token_urlsafe(24)
        self.db.put("oauth", "req:" + rid, {"client_id": client.client_id, **params.model_dump(mode="json")}, ttl=1800)
        return f"{BASE_URL}/connect?{urlencode({'req': rid})}"

    async def load_authorization_code(self, client, authorization_code: str) -> Optional[AuthorizationCode]:
        d = self.db.get("oauth", "code:" + authorization_code)
        if not d:
            return None
        rec = decrypt(d)
        if rec["client_id"] != client.client_id:
            return None
        return AuthorizationCode.model_validate({k: v for k, v in rec.items() if k != "cookies"})

    def _tokens(self, client_id: str, uid: str, cookies: dict, scopes: list[str], resource) -> OAuthToken:
        now = int(time.time())
        base = {"uid": uid, "c": cookies, "cid": client_id, "sc": scopes, "res": resource}
        at = "at." + encrypt({**base, "t": "a", "exp": now + ACCESS_TTL})
        rt = "rt." + encrypt({**base, "t": "r", "exp": now + REFRESH_TTL})
        return OAuthToken(access_token=at, token_type="Bearer", expires_in=ACCESS_TTL,
                          scope=" ".join(scopes), refresh_token=rt)

    async def exchange_authorization_code(self, client, authorization_code: AuthorizationCode) -> OAuthToken:
        d = self.db.get("oauth", "code:" + authorization_code.code)
        self.db.delete("oauth", "code:" + authorization_code.code)
        if not d:
            from mcp.server.auth.provider import TokenError
            raise TokenError(error="invalid_grant", error_description="code already used")
        rec = decrypt(d)
        return self._tokens(client.client_id, rec["subject"], rec["cookies"], rec["scopes"] or [SCOPE], rec.get("resource"))

    @staticmethod
    def _read(token: str, prefix: str) -> Optional[dict]:
        if not token.startswith(prefix):
            return None
        try:
            d = decrypt(token[len(prefix):])
        except Exception:
            return None
        return d if d.get("exp", 0) > time.time() else None

    async def load_refresh_token(self, client, refresh_token: str) -> Optional[RefreshToken]:
        d = self._read(refresh_token, "rt.")
        if not d or d["cid"] != client.client_id:
            return None
        return RefreshToken(token=refresh_token, client_id=d["cid"], scopes=d["sc"], expires_at=d["exp"], subject=d["uid"])

    async def exchange_refresh_token(self, client, refresh_token: RefreshToken, scopes: list[str]) -> OAuthToken:
        d = self._read(refresh_token.token, "rt.")
        return self._tokens(client.client_id, d["uid"], d["c"], scopes or d["sc"], d.get("res"))

    async def load_access_token(self, token: str) -> Optional[AccessToken]:
        d = self._read(token, "at.")
        if not d:
            return None
        return AccessToken(token=token, client_id=d["cid"], scopes=d["sc"], expires_at=d["exp"],
                           resource=d.get("res"), subject=d["uid"])

    async def revoke_token(self, token) -> None:
        return None


provider = AtlasOAuth()
mcp = MCPServer(
    "Atlas Planner",
    instructions=INSTRUCTIONS,
    auth_server_provider=provider,
    auth=AuthSettings(issuer_url=BASE_URL, resource_server_url=BASE_URL + "/mcp", required_scopes=[SCOPE],
                      validate_token_resource=False,
                      client_registration_options=ClientRegistrationOptions(enabled=True, valid_scopes=[SCOPE],
                                                                            default_scopes=[SCOPE]),
                      revocation_options=RevocationOptions(enabled=False)),
)


# ====================================================================== connect page
PAGE = """<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Connect Atlas Planner</title><style>
body{{font:16px/1.5 -apple-system,system-ui,sans-serif;max-width:600px;margin:48px auto;padding:0 20px;color:#1d1d1f}}
h1{{font-size:26px;margin-bottom:4px}} .sub{{color:#666;margin-top:0}}
.big{{display:inline-block;background:#00274c;color:#ffcb05;border:0;padding:14px 26px;border-radius:10px;font-size:18px;cursor:pointer;text-decoration:none}}
.big[disabled]{{opacity:.6;cursor:default}} .status{{margin:16px 0;color:#333;min-height:24px}}
.err{{background:#fdecea;color:#8a1c12;padding:10px;border-radius:8px}} .note{{color:#777;font-size:13px;margin-top:36px}}
.card{{border:1px solid #ddd;border-radius:12px;padding:16px 20px;margin:20px 0}} ol li{{margin:6px 0}}
code{{background:#f2f2f4;padding:1px 5px;border-radius:4px}} textarea{{width:100%;height:70px;font:13px monospace;padding:8px;box-sizing:border-box}}
details{{margin-top:24px;color:#555}} .small{{font-size:14px}}
</style></head><body>
<h1>Connect Atlas Planner</h1><p class="sub">Plan your UMich schedule with Claude, using your own Atlas data.</p>
{error}
<div id="withHelper" style="display:none">
  <button class="big" id="go">Sign in with Atlas</button>
  <div class="status" id="status"></div>
</div>
<div id="noHelper" class="card" style="display:none">
  <b>One time setup (about 1 minute, Chrome)</b>
  <ol class="small">
    <li><a href="/helper.zip">Download the Atlas Planner helper</a> and double click the zip to unzip it.</li>
    <li>Open <code>chrome://extensions</code> in a new tab and turn on <b>Developer mode</b> (top right).</li>
    <li>Click <b>Load unpacked</b> and choose the unzipped <code>atlas-planner-helper</code> folder.</li>
    <li>Come back here and <a href="" onclick="location.reload();return false">reload this page</a>.</li>
  </ol>
  <span class="small">The helper only runs on this page. It reads your Atlas login when you click Sign in, and nothing else.</span>
</div>
<details><summary>Other ways to connect</summary>
<p class="small">Paste your Atlas <code>sessionid</code> cookie instead: in Atlas, open developer tools (Cmd+Option+I),
Application &gt; Cookies &gt; atlas.ai.umich.edu, copy the <code>sessionid</code> value.</p>
<form method="post" action="/connect"><input type="hidden" name="req" value="{req}">
<textarea name="cookie" placeholder="sessionid value" required></textarea><p><button type="submit">Connect</button></p></form>
</details>
<p class="note">Your Atlas login is only kept inside the encrypted token Claude holds for you, never stored on this server.
Your planning profile is stored encrypted. Atlas data is for your own planning only. This is a student project,
not an official University of Michigan service.</p>
<script>
const REQ = {req_json};
let n = 0;
function ask(type) {{
  return new Promise(res => {{
    const id = ++n;
    const on = ev => {{ if (ev.source === window && ev.data && ev.data.src === "atlas-planner-helper" && ev.data.id === id) {{
      window.removeEventListener("message", on); res(ev.data.res); }} }};
    window.addEventListener("message", on);
    window.postMessage({{src: "atlas-planner-page", type, id}}, location.origin);
  }});
}}
const say = (t) => document.getElementById("status").textContent = t;
let lastTried = null;
async function attempt() {{
  const c = await ask("get");
  if (!c.sessionid || c.sessionid === lastTried) return false;
  lastTried = c.sessionid;
  const r = await fetch("/connect", {{method: "POST", headers: {{"Content-Type": "application/json"}},
    body: JSON.stringify({{req: REQ, cookie: "sessionid=" + c.sessionid + (c.csrftoken ? "; csrftoken=" + c.csrftoken : "")}})}});
  const j = await r.json().catch(() => ({{}}));
  if (j.ok) {{ say("Connected as " + j.uniqname + ". Taking you back to Claude..."); await ask("done"); location.href = j.redirect; return true; }}
  if (j.expired) {{ say(j.error); return "stop"; }}
  return false;
}}
async function go() {{
  const btn = document.getElementById("go"); btn.disabled = true;
  say("Checking your Atlas login...");
  let r = await attempt();
  if (r) return;
  await ask("openLogin");
  say("Log in to Atlas in the tab that just opened (uniqname + Duo). This page will continue by itself.");
  for (let i = 0; i < 200; i++) {{
    await new Promise(s => setTimeout(s, 3000));
    r = await attempt();
    if (r === true || r === "stop") return;
  }}
  say("Timed out. Click Sign in with Atlas to try again."); btn.disabled = false;
}}
async function init() {{
  const found = await Promise.race([ask("ping").then(() => true), new Promise(r => setTimeout(() => r(false), 600))]);
  if (found) {{ document.getElementById("withHelper").style.display = ""; document.getElementById("go").onclick = go; }}
  else document.getElementById("noHelper").style.display = "";
}}
init();
</script>
</body></html>"""


def _expired_page() -> Response:
    return HTMLResponse("<p>This connect link expired. Start again from Claude's Connectors settings.</p>", 400)


@mcp.custom_route("/connect", methods=["GET", "POST"])
async def connect(request: Request) -> Response:
    if request.method == "GET":
        rid = request.query_params.get("req", "")
        if not provider.db.get("oauth", "req:" + rid):
            return _expired_page()
        return HTMLResponse(PAGE.format(req=html.escape(rid), req_json=json.dumps(rid), error=""))
    is_json = request.headers.get("content-type", "").startswith("application/json")
    form = await (request.json() if is_json else request.form())
    rid = str(form.get("req", ""))
    req = provider.db.get("oauth", "req:" + rid)
    if not req:
        msg = "This connect link expired. Start again from Claude's Connectors settings."
        return JSONResponse({"ok": False, "expired": True, "error": msg}) if is_json else _expired_page()
    cookies = _parse_cookies(str(form.get("cookie", "")))
    try:
        if "sessionid" not in cookies:
            raise ValueError("I couldn't find a sessionid value in what you pasted.")
        uid = await anyio.to_thread.run_sync(_check_atlas, cookies)
    except Exception as e:
        msg = str(e) if isinstance(e, ValueError) else "Couldn't reach Atlas. Try again in a minute."
        if is_json:
            return JSONResponse({"ok": False, "error": msg})
        return HTMLResponse(PAGE.format(req=html.escape(rid), req_json=json.dumps(rid),
                                        error=f'<p class="err">{html.escape(msg)}</p>'), 400)
    provider.db.delete("oauth", "req:" + rid)
    code = secrets.token_urlsafe(32)
    rec = {"code": code, "scopes": req.get("scopes") or [SCOPE], "expires_at": time.time() + 600,
           "client_id": req["client_id"], "code_challenge": req["code_challenge"], "redirect_uri": req["redirect_uri"],
           "redirect_uri_provided_explicitly": req["redirect_uri_provided_explicitly"],
           "resource": req.get("resource"), "subject": uid, "cookies": cookies}
    provider.db.put("oauth", "code:" + code, encrypt(rec), ttl=600)
    # fresh login: forget cached personal results so the audit is re-read
    provider.db.delete_prefix(f"user:{uid}", "cache:me_")
    provider.db.delete_prefix(f"user:{uid}", "cache:whatif_")
    target = construct_redirect_uri(req["redirect_uri"], code=code, state=req.get("state"))
    if is_json:
        return JSONResponse({"ok": True, "uniqname": uid, "redirect": target})
    return RedirectResponse(target, 302)


@mcp.custom_route("/helper.zip", methods=["GET"])
async def helper_zip(request: Request) -> Response:
    """The Chrome helper, set to run only on this server's connect page."""
    import io
    import zipfile
    folder = Path(__file__).parent / "helper"
    manifest = (folder / "manifest.template.json").read_text().replace("__CONNECT_URL__", BASE_URL + "/connect*")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("atlas-planner-helper/manifest.json", manifest)
        for name in ("background.js", "content.js"):
            z.write(folder / name, "atlas-planner-helper/" + name)
    return Response(buf.getvalue(), media_type="application/zip",
                    headers={"Content-Disposition": 'attachment; filename="atlas-planner-helper.zip"'})


@mcp.custom_route("/", methods=["GET"])
async def home(request: Request) -> Response:
    return HTMLResponse("<h1>Atlas Planner</h1><p>A Claude connector for UMich schedule planning. "
                        f"Add <code>{BASE_URL}/mcp</code> as a custom connector in Claude.</p>")


@mcp.custom_route("/healthz", methods=["GET"])
async def health(request: Request) -> Response:
    return Response("ok")


# ====================================================================== per user files + background download
_user_locks: dict[str, threading.Lock] = {}
FILES = ("profile.yaml", "last_week.json", "external_ratings.csv")


def _lock(uid: str) -> threading.Lock:
    return _user_locks.setdefault(uid, threading.Lock())


def _sync_down(uid: str) -> dict:
    home = user_home()
    seen = {}
    for name in FILES:
        v = provider.db.get(f"user:{uid}", "file:" + name)
        p = home / name
        if v is not None:
            p.write_text(v)
        elif p.exists():
            p.unlink()
        seen[name] = v
    return seen


def _sync_up(uid: str, before: dict) -> None:
    home = user_home()
    for name in FILES:
        p = home / name
        now = p.read_text() if p.exists() else None
        if now is not None and now != before.get(name):
            provider.db.put(f"user:{uid}", "file:" + name, now)


def _warm_status(uid: str) -> dict:
    return provider.db.get(f"user:{uid}", "warm") or {}


def _start_warm(uid: str, cookies: dict) -> dict:
    st = _warm_status(uid)
    if st.get("state") == "running" and time.time() - st.get("updated", 0) < 300:
        return st

    def job():
        CURRENT_USER.set(uid)
        CURRENT_COOKIES.set(cookies)
        started = time.time()
        put = lambda d: provider.db.put(f"user:{uid}", "warm", {**d, "started": started, "updated": time.time()})  # noqa: E731
        put({"state": "running", "requests": 0})
        try:
            with _lock(uid):
                before = _sync_down(uid)
                src = AtlasSource()
                src.on_request = lambda n: put({"state": "running", "requests": n}) if n % 10 == 0 else None
                summary = src.warm()
                _sync_up(uid, before)
            put({"state": "done", "summary": summary})
        except Exception as e:
            put({"state": "error", "error": f"{e.__class__.__name__}: {e}"})

    threading.Thread(target=contextvars.Context().run, args=(job,), daemon=True).start()
    return {"state": "running", "requests": 0}


def _ready(uid: str) -> bool:
    st = _warm_status(uid)
    return st.get("state") == "done" and time.time() - st.get("updated", 0) < 6 * 24 * 3600


def _loading_reply(uid: str, cookies: dict) -> str:
    st = _warm_status(uid)
    if st.get("state") == "error":
        err = st["error"]
        _start_warm(uid, cookies)
        return json.dumps({"status": "loading", "note": f"Last download hit an error ({err}); retrying now."})
    st = _start_warm(uid, cookies)
    n = st.get("requests", 0)
    shared = provider.db.count("shared", "cache:")
    return json.dumps({
        "status": "loading",
        "message": "Downloading your Atlas data in the background (first time only). Course data other students "
                   "already loaded is reused, so it's usually a few minutes. Ask again shortly.",
        "requests_done": n, "courses_cached_on_server": shared,
        "elapsed_seconds": round(time.time() - st.get("started", time.time())) if st.get("started") else 0,
    }, indent=1)


def _hosted(fn):
    name = fn.__name__

    @functools.wraps(fn)
    async def tool(**kwargs):
        tok = get_access_token()
        d = AtlasOAuth._read(tok.token, "at.") if tok else None
        if not d:
            return json.dumps({"error": "Not connected. Reconnect Atlas Planner in Claude's Connectors settings."})
        uid, cookies = d["uid"], d["c"]
        if name in LOCAL_ONLY:
            return ("You're already logged in through the connector. If Atlas logged you out, reconnect: "
                    "Settings > Connectors > Atlas Planner > Disconnect, then Connect.")
        if name in HEAVY and not _ready(uid):
            return _loading_reply(uid, cookies)
        if name == "refresh_atlas_data":
            provider.db.put(f"user:{uid}", "warm", {"state": "stale", "updated": 0})
            return _loading_reply(uid, cookies)

        def run():
            CURRENT_USER.set(uid)
            CURRENT_COOKIES.set(cookies)
            with _lock(uid):
                before = _sync_down(uid)
                try:
                    return fn(**kwargs)
                finally:
                    _sync_up(uid, before)

        out = await anyio.to_thread.run_sync(lambda: contextvars.Context().run(run))
        if name in ("update_profile", "save_profile") and isinstance(out, str) and "atlas_plans" in json.dumps(kwargs):
            provider.db.put(f"user:{uid}", "warm", {"state": "stale", "updated": 0})   # new majors: re-download
        return out
    return tool


for _fn in planner.TOOLS:
    mcp.add_tool(_hosted(_fn), name=_fn.__name__, description=_fn.__doc__)


def app():
    from mcp.server.transport_security import TransportSecuritySettings
    from urllib.parse import urlparse
    host = urlparse(BASE_URL).netloc
    return mcp.streamable_http_app(
        stateless_http=True, json_response=True, host="0.0.0.0",
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
                                                     allowed_hosts=[host, "localhost:*", "127.0.0.1:*"],
                                                     allowed_origins=[BASE_URL, "https://claude.ai", "https://claude.com"]))


def main():
    import uvicorn
    Path(os.environ.get("ATLAS_PLANNER_WORKDIR", "/tmp/atlas-planner")).mkdir(parents=True, exist_ok=True)
    uvicorn.run(app(), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")), proxy_headers=True,
                forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
