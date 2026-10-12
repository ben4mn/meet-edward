// Edward's hands in the owner's Chrome (needs EDWARD_CHROME_HANDS=on on the backend).
//
// Keeps a WebSocket to the Edward backend on this Mac and relays three
// things: the tab list, tab/window actions (open, activate, close,
// navigate, capture, full screen), and DevTools commands (chrome.debugger)
// against the tab Edward is working in. Nothing here decides anything —
// the backend does (services/browser_ext_service.py, browser_hands.py).
//
// Load once: chrome://extensions → Developer mode → Load unpacked → this
// folder. After a code change: the reload arrow on the card, or restart
// Chrome (unpacked extensions are re-read from disk at startup).

const BACKEND_WS = "ws://127.0.0.1:8000/api/browser/ext";
const RECONNECT_MS = 3000;
const PING_MS = 20000;

let ws = null;
let pingTimer = null;
const attached = new Set();

function send(obj) {
  if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(obj));
}

async function listTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.map((t) => ({ id: t.id, windowId: t.windowId, title: t.title || "", url: t.url || "", active: !!t.active }));
}

async function attach(tabId) {
  if (attached.has(tabId)) return;
  await chrome.debugger.attach({ tabId }, "1.3");
  attached.add(tabId);
}

async function detach(tabId) {
  if (!attached.has(tabId)) return;
  try { await chrome.debugger.detach({ tabId }); } catch (e) { /* already gone */ }
  attached.delete(tabId);
}

async function windowOf(tabId) {
  const tab = await chrome.tabs.get(tabId);
  return tab.windowId;
}

async function handle(method, p) {
  switch (method) {
    case "tabs.list":
      return { tabs: await listTabs() };
    case "tabs.open": {
      const tab = await chrome.tabs.create({ url: p.url, active: p.active !== false });
      if (p.active !== false && tab.windowId) await chrome.windows.update(tab.windowId, { focused: true });
      return { tabId: tab.id };
    }
    case "tabs.activate": {
      const tab = await chrome.tabs.update(p.tabId, { active: true });
      if (tab && tab.windowId) await chrome.windows.update(tab.windowId, { focused: true });
      return { ok: true };
    }
    case "tabs.close":
      await detach(p.tabId);
      await chrome.tabs.remove(p.tabId);
      return { ok: true };
    case "tabs.navigate":
      await chrome.tabs.update(p.tabId, { url: p.url });
      return { ok: true };
    case "tabs.info": {
      const t = await chrome.tabs.get(p.tabId);
      return { id: t.id, windowId: t.windowId, title: t.title || "", url: t.url || "", active: !!t.active, status: t.status };
    }
    case "tabs.capture": {
      // What Chrome draws for the visible tab of that window (the caller
      // activates the tab first). JPEG so the backend can describe it.
      const windowId = await windowOf(p.tabId);
      const dataUrl = await chrome.tabs.captureVisibleTab(windowId, { format: "jpeg", quality: p.quality || 60 });
      return { dataUrl };
    }
    case "windows.fullscreen": {
      // Chrome's own full screen (the green button / F11) for the window
      // that holds the tab; off returns it to a normal window.
      const windowId = await windowOf(p.tabId);
      await chrome.windows.update(windowId, { state: p.on === false ? "normal" : "fullscreen", focused: true });
      return { ok: true, windowId };
    }
    case "attach":
      await attach(p.tabId);
      return { ok: true };
    case "detach":
      await detach(p.tabId);
      return { ok: true };
    case "cdp": {
      await attach(p.tabId);
      const result = await chrome.debugger.sendCommand({ tabId: p.tabId }, p.method, p.params || {});
      return result || {};
    }
    default:
      throw new Error(`unknown method ${method}`);
  }
}

function connect() {
  if (ws && (ws.readyState === WebSocket.OPEN || ws.readyState === WebSocket.CONNECTING)) return;
  try {
    ws = new WebSocket(BACKEND_WS);
  } catch (e) {
    setTimeout(connect, RECONNECT_MS);
    return;
  }
  ws.onopen = async () => {
    send({ type: "hello", version: 2, tabs: await listTabs() });
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = setInterval(() => send({ type: "ping" }), PING_MS);
  };
  ws.onmessage = async (ev) => {
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type !== "call") return;
    try {
      const result = await handle(msg.method, msg.params || {});
      send({ type: "result", id: msg.id, ok: true, result });
    } catch (e) {
      send({ type: "result", id: msg.id, ok: false, error: String(e && e.message ? e.message : e) });
    }
  };
  ws.onclose = () => {
    if (pingTimer) clearInterval(pingTimer);
    pingTimer = null;
    ws = null;
    setTimeout(connect, RECONNECT_MS);
  };
  ws.onerror = () => { try { ws.close(); } catch { /* noop */ } };
}

chrome.debugger.onDetach.addListener((source, reason) => {
  attached.delete(source.tabId);
  send({ type: "event", name: "detached", tabId: source.tabId, params: { reason } });
});

const tabsChanged = async () => send({ type: "tabs", tabs: await listTabs() });
chrome.tabs.onCreated.addListener(tabsChanged);
chrome.tabs.onRemoved.addListener(tabsChanged);
chrome.tabs.onUpdated.addListener((_id, info) => { if (info.status === "complete" || info.title) tabsChanged(); });
chrome.tabs.onActivated.addListener(tabsChanged);

// MV3 service workers sleep; the alarm wakes us to make sure we are connected.
chrome.alarms.create("edward-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(connect);
chrome.runtime.onStartup.addListener(connect);
chrome.runtime.onInstalled.addListener(connect);
connect();
