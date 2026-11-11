// Atlas Planner helper: reads YOUR Atlas login cookie only when the Atlas Planner connect page asks,
// and opens the Atlas login page if you're not signed in. It talks to nothing else.
const ATLAS = "https://atlas.ai.umich.edu";

async function readSession() {
  const s = await chrome.cookies.get({ url: ATLAS, name: "sessionid" });
  const c = await chrome.cookies.get({ url: ATLAS, name: "csrftoken" });
  return { sessionid: s ? s.value : null, csrftoken: c ? c.value : null };
}

let loginTabId = null;

chrome.runtime.onMessage.addListener((msg, sender, reply) => {
  (async () => {
    if (msg.type === "get") {
      reply(await readSession());
    } else if (msg.type === "openLogin") {
      if (loginTabId !== null) {
        try { await chrome.tabs.update(loginTabId, { active: true }); reply({ ok: true }); return; } catch (e) { loginTabId = null; }
      }
      const tab = await chrome.tabs.create({ url: ATLAS + "/login/", active: true });
      loginTabId = tab.id;
      reply({ ok: true });
    } else if (msg.type === "done") {
      if (loginTabId !== null) { try { await chrome.tabs.remove(loginTabId); } catch (e) {} loginTabId = null; }
      if (sender.tab) { try { await chrome.tabs.update(sender.tab.id, { active: true }); } catch (e) {} }
      reply({ ok: true });
    }
  })();
  return true;
});
