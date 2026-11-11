// Runs only on the Atlas Planner connect page. Relays its requests to the helper.
// The page finds the helper by sending a "ping" message.
window.addEventListener("message", (ev) => {
  if (ev.source !== window || !ev.data || ev.data.src !== "atlas-planner-page") return;
  if (ev.data.type === "ping") {
    window.postMessage({ src: "atlas-planner-helper", id: ev.data.id, res: { ok: true } }, location.origin);
    return;
  }
  chrome.runtime.sendMessage({ type: ev.data.type }, (res) => {
    window.postMessage({ src: "atlas-planner-helper", id: ev.data.id, res: res || {} }, location.origin);
  });
});
