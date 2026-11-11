"""End to end test of the hosted connector with a fake Atlas: OAuth (DCR + PKCE), connect page,
tokens, per-user tools, background download, encryption at rest."""
import base64, hashlib, json, os, secrets, sys, tempfile, threading, time, re, urllib.parse
ROOT = sys.argv[1]; sys.path.insert(0, ROOT)
tmp = tempfile.mkdtemp()
os.environ.update(BASE_URL="http://127.0.0.1:8765", STORE_PATH=f"{tmp}/t.db", ATLAS_PLANNER_WORKDIR=f"{tmp}/work",
                  SECRET_KEY=__import__("cryptography.fernet", fromlist=["Fernet"]).Fernet.generate_key().decode())
sys.argv = [sys.argv[0], ROOT]
sys.path.insert(0, os.path.join(ROOT, "tests"))
from planner import data_source as ds
import fake_atlas
seen_cookies = []
DELAY = [0.01]
def fake(self, path, kind="json", method="GET", body=None):
    seen_cookies.append(dict(self._http().cookies))
    time.sleep(DELAY[0])
    return fake_atlas.fake(self, path, kind, method, body)
ds.AtlasSource._fetch = fake
ds.AtlasSource.MIN_SECONDS_BETWEEN_REQUESTS = 0
from planner import remote_app as ra
ra._check_atlas = lambda c: "ejojic" if c.get("sessionid") == "GOODSESSION" else (_ for _ in ()).throw(ValueError("bad cookie"))

import uvicorn
def dbtext():
    if os.environ.get("DATABASE_URL"):
        import psycopg
        with psycopg.connect(os.environ["DATABASE_URL"]) as c:
            return " ".join(r[0] + " " + r[1] for r in c.execute("SELECT k, v FROM kv").fetchall())
    return open(f"{tmp}/t.db", "rb").read().decode("latin1")
cfg = uvicorn.Config(ra.app(), host="127.0.0.1", port=8765, log_level="warning")
srv = uvicorn.Server(cfg); threading.Thread(target=srv.run, daemon=True).start()
while not srv.started: time.sleep(0.1)
import httpx
B = "http://127.0.0.1:8765"; h = httpx.Client(base_url=B, follow_redirects=False)
prm = h.get("/.well-known/oauth-protected-resource/mcp"); print("PRM", prm.status_code, prm.json().get("authorization_servers"))
meta = h.get("/.well-known/oauth-authorization-server").json(); print("AS", meta["authorization_endpoint"], meta.get("registration_endpoint"))
assert h.post("/mcp", json={}).status_code == 401
reg = h.post("/register", json={"redirect_uris": ["https://claude.ai/api/mcp/auth_callback"], "client_name": "Claude",
                                "token_endpoint_auth_method": "none", "grant_types": ["authorization_code", "refresh_token"],
                                "response_types": ["code"]}).json()
cid = reg["client_id"]; print("registered", cid[:8])
ver = secrets.token_urlsafe(48); chal = base64.urlsafe_b64encode(hashlib.sha256(ver.encode()).digest()).rstrip(b"=").decode()
r = h.get("/authorize", params={"response_type": "code", "client_id": cid, "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                                "code_challenge": chal, "code_challenge_method": "S256", "state": "xyz", "scope": "atlas",
                                "resource": B + "/mcp"})
loc = r.headers["location"]; print("authorize ->", loc[:60]); assert "/connect?req=" in loc
page = h.get(loc.replace(B, "")); assert page.status_code == 200 and "sessionid" in page.text
req = urllib.parse.parse_qs(urllib.parse.urlparse(loc).query)["req"][0]
bad = h.post("/connect", data={"req": req, "cookie": "sessionid=WRONGWRONGWRONGWRONG"}); assert bad.status_code == 400 and "bad cookie" in bad.text
assert "Sign in with Atlas" in page.text and "helper.zip" in page.text
z = h.get("/helper.zip"); import zipfile, io; zf = zipfile.ZipFile(io.BytesIO(z.content))
man = json.loads(zf.read("atlas-planner-helper/manifest.json")); assert man["content_scripts"][0]["matches"] == [B + "/connect*"], man
assert sorted(zf.namelist()) == ["atlas-planner-helper/background.js", "atlas-planner-helper/content.js", "atlas-planner-helper/manifest.json"]
badj = h.post("/connect", json={"req": req, "cookie": "sessionid=NOTLOGGEDINYETXXXXXXX"}).json(); assert badj["ok"] is False, badj
ok = h.post("/connect", json={"req": req, "cookie": "sessionid=GOODSESSION; csrftoken=CSRF123"}).json(); assert ok["ok"] and ok["uniqname"] == "ejojic", ok
cb = urllib.parse.urlparse(ok["redirect"]); q = urllib.parse.parse_qs(cb.query); assert q["state"] == ["xyz"]
assert h.post("/connect", json={"req": req, "cookie": "sessionid=GOODSESSION"}).json().get("expired")
time.sleep(1); print("after connect:", ra._warm_status("ejojic"))
assert ra._warm_status("ejojic").get("state") in ("running", "done"), "download should start at connect"
tok = h.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                             "client_id": cid, "code_verifier": ver, "resource": B + "/mcp"}).json()
assert "access_token" in tok, tok; print("token ok, expires_in", tok["expires_in"])
assert "GOODSESSION" not in dbtext(), "cookie leaked to disk"
again = h.post("/token", data={"grant_type": "authorization_code", "code": q["code"][0], "redirect_uri": "https://claude.ai/api/mcp/auth_callback",
                               "client_id": cid, "code_verifier": ver}); assert again.status_code == 400
ref = h.post("/token", data={"grant_type": "refresh_token", "refresh_token": tok["refresh_token"], "client_id": cid}).json(); assert "access_token" in ref
H = {"Authorization": "Bearer " + tok["access_token"], "Accept": "application/json, text/event-stream", "MCP-Protocol-Version": "2025-06-18"}
def rpc(method, params=None, i=[0]):
    i[0] += 1
    r = h.post("/mcp", headers=H, json={"jsonrpc": "2.0", "id": i[0], "method": method, "params": params or {}})
    assert r.status_code == 200, (r.status_code, r.text[:300]); return r.json()
init = rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}})
print("server:", init["result"]["serverInfo"]["name"])
tools = [t["name"] for t in rpc("tools/list")["result"]["tools"]]; print(len(tools), "tools"); assert "plan_all_semesters" in tools
def call(name, args=None):
    res = rpc("tools/call", {"name": name, "arguments": args or {}})["result"]
    return res["content"][0]["text"]
print("update_profile:", call("update_profile", {"changes": {"name": "Elena", "atlas_plans": [
    {"plan": "Biomedical Engineering BSE", "type": "MAJ"}, {"plan": "AI BSE BioE", "type": "MIN"}, {"plan": "Business Admin BSE BioE", "type": "MIN"}],
    "programs": ["Biomedical Engineering BSE", "AI BSE BioE", "Business Admin BSE BioE", "Premed (template)"]}}))
prog = json.loads(call("get_progress")); print("progress gpa:", prog.get("gpa_now"), "history", len(prog["history"]))
t0 = time.time(); first = call("plan_all_semesters"); waited = time.time() - t0
print(f"first plan call waited {waited:.1f}s ->", first[:60].replace("\n", " "))
assert '"loading"' not in first, first
st = ra._warm_status("ejojic"); print("warm:", st.get("state"), st.get("summary") or st.get("error")); assert st.get("ever_done")
plan = json.loads(call("plan_all_semesters")); print("plan:", "ok" if plan.get("ok") else plan)
assert all(c.get("sessionid") == "GOODSESSION" for c in seen_cookies)
print("search:", call("search_offerings", {"query": "BIOMEDE"})[:120].replace("\n", " "))
print("atlas_login:", call("atlas_login")[:60])
# encryption at rest + persistence
raw = dbtext(); assert "Elena" not in raw and "ejojic\"" not in raw.replace("user:ejojic", "")
print("profile persisted:", "Elena" in call("get_profile"))
# second user can't see first user's data
# slow Atlas: the tool waits a bit, then says loading (with percent) instead of making Claude retry
DELAY[0] = 0.5
ra.provider.db.delete_prefix("shared", "cache:"); ra.provider.db.delete_prefix("user:ejojic", "cache:")
orig_wait = ra._wait_ready
async def short_wait(uid, cookies, seconds=25): return await orig_wait(uid, cookies, 2)
ra._wait_ready = short_wait
call("update_profile", {"changes": {"atlas_plans": [{"plan": "Biomedical Engineering BSE", "type": "MAJ"}]}})
t0 = time.time(); r = json.loads(call("optimize_schedule")); print(f"slow: waited {time.time()-t0:.1f}s ->", r.get("status"), r.get("percent_done"), r.get("next", "")[:40])
assert r["status"] == "loading" and "Do NOT call" in r["next"]
print("refresh:", json.loads(call("refresh_atlas_data"))["status"])
print("ALL REMOTE OK")
