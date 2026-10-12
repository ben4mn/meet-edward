# Edward: the Chrome extension

This extension gives Edward hands in your own Google Chrome, on the Mac that runs Edward. Through it he can see your tabs, open pages, read, click and type, using the logins you already have. You can also open a tab yourself, sign in, and hand it over ("use tab 13").

The extension decides nothing. It keeps a WebSocket open to the backend on this Mac (`ws://127.0.0.1:8000/api/browser/ext`) and relays three kinds of message: the tab list, tab and window actions (open, activate, close, navigate, capture, full screen), and DevTools commands (`chrome.debugger`) for the tab Edward is working in. The backend makes every decision (`backend/services/browser_ext_service.py`, `backend/services/browser_hands.py`).

Edward has a second browser that does not need this extension: his own invisible one (Playwright's headless shell), with no logins. He uses it for ordinary browsing.

## Off by default

Until you turn the hands on, the backend refuses the extension's socket and Edward's `chrome` tool answers "Chrome hands are off". To turn them on:

1. In `.env` at the root of your checkout, set `EDWARD_CHROME_HANDS=on`, then restart the backend: `./restart.sh backend`.
2. Load the extension once: `chrome://extensions` → **Developer mode** on → **Load unpacked** → pick this `chrome-extension/` folder. Its card shows the ID `appfgnpjahkedldopocalbkoommcalae`, the same on every Mac (below).

With the hands on, Edward can also bring his `/screen` tab forward in Chrome, and relaunch a Chrome that is left with no windows after its extension stopped answering for 90 s. macOS asks once for permission to control Google Chrome (Automation), and sizing the window needs Accessibility. See [docs/ADAPTING.md](../docs/ADAPTING.md#3-macos-permissions). With the hands off, `/screen` still works: open it yourself, in any browser.

The socket accepts only a direct connection from this Mac: a loopback peer, no proxy or tunnel headers (`X-Forwarded-For`, `X-Forwarded-Host`, `X-Real-IP`, `Forwarded`, `CF-Connecting-IP`), and an `Origin` of `chrome-extension://<id>`, which a web page cannot forge, where `<id>` is this extension's. So neither a device on your network, nor anyone behind your tunnel, nor a web page, nor another extension you have installed can connect as Edward's hands.

**The extension's ID is fixed.** `manifest.json` carries a public `key`, so Chrome gives every unpacked copy of this folder the same ID, `appfgnpjahkedldopocalbkoommcalae`, and by default the backend accepts that ID and no other. Only the public key is in the repo. If you fork the extension and change or remove the key, your copy gets another ID: put it in `EDWARD_CHROME_EXTENSION_ID` (comma-separated for several; it replaces the default) and restart the backend. A refused socket closes with code 4003, and the backend log says why, naming the ID that was offered ("extension <id> is not Edward's (pin it with EDWARD_CHROME_EXTENSION_ID)").

Two limits. The key is public, so an extension built on purpose to impersonate this one can carry the same key and get the same ID: the pin keeps out the other extensions you install, not a deliberate copy. And a program running on this Mac can forge every header, so the socket guards against the network, web pages and other extensions, not against this Mac's own processes. Edward's own shell is kept out separately: it may not connect to port 8000 at all.

The address is written into `background.js` (`BACKEND_WS`), so change it there if your backend is not on port 8000. Keep it a `127.0.0.1` address: through the frontend's `/api` proxy or a tunnel the socket is refused. After changing the code, click the reload arrow on the extension's card, or restart Chrome. If you loaded the extension before its manifest had the `key`, remove it and load it again: its ID changes to `appf…`, and any `EDWARD_CHROME_EXTENSION_ID` that names the old ID must be updated or unset.

## What you will see

- Chrome shows an "is debugging this browser" bar while Edward is attached to a tab. That bar is the honest signal that he is driving. `EDWARD_CHROME_SILENT_DEBUGGER=on` hides it when Edward launches Chrome himself. Leave it off unless the bar gets in the way, e.g. of full-screen video.
- Every step Edward takes shows on the chat's work rail, and the backend log records it (`[browser]` lines in `/tmp/edward-backend.log`).

## The rails

These hold whatever the setting:

- Typing (or pressing Enter) anywhere but a search site or Edward's own `/screen`, and clicking something labelled send, post, reply, delete, accept and the like, all act **as you**: a message typed into Gmail goes out as you. Edward does any of these only when you asked for it in that turn.
- A payment step ("place order", "buy now", "pay", "add card" …) or typing a card number is refused in every turn.
- Edward never launches a second Chrome instance. It would take over `open -a` and every AppleScript `tell application "Google Chrome"`.

To keep Edward out of your browser entirely, leave `EDWARD_CHROME_HANDS` off and don't load the extension.
