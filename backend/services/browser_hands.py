"""Edward's hands in the owner's Chrome on this Mac.

Why (2026-09-07): Edward should be able to do what the owner can on the
machine — the owner opens a tab and signs in, Edward takes over — and most
"put X on your screen" asks are best served by opening a tab in that
Chrome, going full screen, and coming back to the /screen tab when done.
So there is one visible browser — the owner's own Chrome, with their
logins — reached through the Edward extension (``chrome-extension/``,
``browser_ext_service`` owns the socket). The headless shell, the
screencast tile and the embed promotion that came before are gone; the
/screen page draws only what Edward makes (since 2026-09-28 a stage: his
scene and what he puts over it).

**Off by default**: ``enabled()`` is ``EDWARD_CHROME_HANDS=on``. Off,
``start()`` does nothing, every action on the owner's Chrome raises
``ChromeHandsOff`` and nothing opens, raises or relaunches Chrome. The
headless target (Edward's own browser) works either way.

Every action runs against the **working tab**: the tab ``open_page`` made
or ``use_tab`` took over. Actions wait for the page to settle (ready state
complete, URL and height stable) and return where the tab is. Reads are
structured: page text plus numbered interactive elements tagged
``data-edward-ref`` in the DOM, so a click names a number, not a selector.
``run_steps`` runs a short list of actions in one call. ``look`` describes a
screenshot in words (the working tab, or the whole display).

Pure: ``format_page_read`` / ``scroll_phrase`` / ``find_elements`` /
``wheel_delta`` / ``key_params`` / ``is_settled`` / ``plan_steps`` /
``describe_hands``. Everything below ``ChromeTarget`` talks to the
extension. Every relayed command is logged as ``[browser]`` by the link.

Since 2026-10-11 the page actions take an optional ``target`` (a
``CdpTarget``): none means the working tab in the owner's Chrome, as
before; a ``headless_browser.HeadlessTarget`` runs the same actions in
Edward's own invisible browser. ``screenshot_target`` takes a picture over
CDP without activating a tab (``look``); ``look`` here stays for the
operator route.
"""

from __future__ import annotations

import asyncio
import base64
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional, Protocol, Union
from urllib.parse import urlparse

from services import browser_ext_service as bx
from services.browser_ext_service import (  # noqa: F401 — re-exported for callers
    HANDS_OFF,
    ChromeHandsOff,
    ExtensionUnavailable,
)

PAGE_TEXT_CHARS = 4_000
MAX_PAGE_ELEMENTS = 120
SCROLL_STEP_PX = 600
SETTLE_TIMEOUT_S = 8.0
SETTLE_POLL_S = 0.25
SETTLE_MIN_S = 0.4
MAX_STEPS = 12
STEP_ACTIONS = ("open", "click", "type", "key", "scroll", "back", "wait", "find", "read", "fullscreen", "look")


class NoWorkingTab(RuntimeError):
    """Edward is not holding a tab: open one or take one over first."""


# ---- pure -------------------------------------------------------------------------

_TAG_WORDS = {"a": "link", "button": "button", "input": "input", "textarea": "textarea", "select": "select"}


def _element_word(e: Dict[str, Any]) -> str:
    role = str(e.get("role") or "").strip().lower()
    tag = str(e.get("tag") or "").strip().lower()
    itype = str(e.get("type") or "").strip().lower()
    if tag == "input" and itype in ("checkbox", "radio", "submit", "search", "email", "password", "number", "date", "file"):
        return itype if itype != "search" else "searchbox"
    if role and role not in ("none", "presentation"):
        return role
    return _TAG_WORDS.get(tag, tag or "element")


def scroll_phrase(pos: Optional[Dict[str, Any]]) -> str:
    """'top of page, 22% visible' / 'scrolled 25% down' / 'bottom of page'."""
    if not isinstance(pos, dict):
        return ""
    try:
        y = float(pos.get("y") or 0)
        height = float(pos.get("height") or 0)
        view = float(pos.get("viewport") or 0)
    except (TypeError, ValueError):
        return ""
    if height <= 0 or view <= 0:
        return ""
    if height <= view + 2:
        return "whole page in view"
    if y <= 2:
        return f"top of page, {round(100 * view / height)}% visible"
    if y + view >= height - 2:
        return "bottom of page"
    return f"scrolled {round(100 * y / height)}% down"


def format_page_read(payload: Dict[str, Any]) -> str:
    """What Edward reads: title — URL, where he is on the page, the text, and
    the numbered interactive elements that click and type take."""
    title = " ".join(str(payload.get("title") or "").split())[:160]
    url = str(payload.get("url") or "")
    text = str(payload.get("text") or "").strip()
    if len(text) > PAGE_TEXT_CHARS:
        text = text[:PAGE_TEXT_CHARS].rstrip() + " …"
    lines = [f"{title} — {url}".strip(" —")]
    where = scroll_phrase(payload.get("scroll"))
    if where:
        lines.append(f"[{where}]")
    lines += ["", text or "(no visible text)", ""]
    els = [e for e in (payload.get("elements") or []) if isinstance(e, dict)][:MAX_PAGE_ELEMENTS]
    if els:
        lines.append("Interactive (click and type use these numbers):")
        for e in els:
            word = _element_word(e)
            label = " ".join(str(e.get("text") or "").split())[:120]
            bit = f"[{e.get('ref')}] {word}"
            if label:
                bit += f" {label}"
            if e.get("placeholder"):
                bit += f" (placeholder: {e['placeholder']})"
            if e.get("value") and word not in ("password",):
                bit += f" (value: {str(e['value'])[:60]})"
            if e.get("checked") is True:
                bit += " (checked)"
            if e.get("href") and word == "link":
                bit += f" → {e['href']}"
            lines.append(bit)
    else:
        lines.append("(nothing interactive in view — scroll to see more)")
    return "\n".join(lines)


def find_elements(elements: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
    """Elements whose text, placeholder, role, tag or href contain the query."""
    q = " ".join(str(query or "").split()).lower()
    if not q:
        return []
    out = []
    for e in elements:
        hay = " ".join(str(e.get(k) or "") for k in ("text", "placeholder", "role", "tag", "href", "type")).lower()
        if q in hay:
            out.append(e)
    return out


def wheel_delta(direction: str, amount: int = 1) -> tuple:
    d = str(direction or "down").strip().lower()
    n = max(1, min(int(amount or 1), 10)) * SCROLL_STEP_PX
    if d == "down":
        return (0, n)
    if d == "up":
        return (0, -n)
    if d == "right":
        return (n, 0)
    if d == "left":
        return (-n, 0)
    raise ValueError("direction must be up, down, left or right")


_KEYS = {
    "enter": ("Enter", "Enter", 13, "\r"), "return": ("Enter", "Enter", 13, "\r"),
    "escape": ("Escape", "Escape", 27, None), "esc": ("Escape", "Escape", 27, None),
    "tab": ("Tab", "Tab", 9, "\t"), "backspace": ("Backspace", "Backspace", 8, None), "delete": ("Delete", "Delete", 46, None),
    "space": (" ", "Space", 32, " "), " ": (" ", "Space", 32, " "),
    "arrowup": ("ArrowUp", "ArrowUp", 38, None), "arrowdown": ("ArrowDown", "ArrowDown", 40, None),
    "arrowleft": ("ArrowLeft", "ArrowLeft", 37, None), "arrowright": ("ArrowRight", "ArrowRight", 39, None),
    "up": ("ArrowUp", "ArrowUp", 38, None), "down": ("ArrowDown", "ArrowDown", 40, None),
    "left": ("ArrowLeft", "ArrowLeft", 37, None), "right": ("ArrowRight", "ArrowRight", 39, None),
    "pageup": ("PageUp", "PageUp", 33, None), "pagedown": ("PageDown", "PageDown", 34, None),
    "home": ("Home", "Home", 36, None), "end": ("End", "End", 35, None),
}


def key_params(key: str) -> Dict[str, Any]:
    """CDP Input.dispatchKeyEvent params (minus type) for a named key or a
    single character — Enter, Escape, Tab, arrows, or 'f' (YouTube's
    fullscreen), 'k' (pause) and the like."""
    k = str(key or "")
    if not k:
        raise ValueError("key must be a key name (Enter, Escape, Tab, ArrowDown…) or one character")
    spec = _KEYS.get(k.strip().lower()) if k.strip() else _KEYS.get(k)
    if spec:
        name, code, vk, text = spec
    elif len(k) == 1:
        name, text = k, k
        code = ("Key" + k.upper()) if k.isalpha() else ("Digit" + k) if k.isdigit() else ""
        vk = ord(k.upper())
    else:
        raise ValueError(f"unknown key {key!r}")
    out: Dict[str, Any] = {"key": name, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk}
    if text is not None:
        out["text"] = text
    return out


def is_settled(prev: Optional[Dict[str, Any]], cur: Optional[Dict[str, Any]]) -> bool:
    """Two consecutive snapshots that agree, with the document complete."""
    if not prev or not cur:
        return False
    return (cur.get("ready") == "complete" and prev.get("url") == cur.get("url")
            and prev.get("height") == cur.get("height") and prev.get("ready") == cur.get("ready"))


def plan_steps(steps: Any) -> List[Dict[str, Any]]:
    """Validate a browser_steps list. ValueError names the offending step."""
    if not isinstance(steps, list):
        raise ValueError("steps must be a list of {action: …} objects")
    if len(steps) > MAX_STEPS:
        raise ValueError(f"at most {MAX_STEPS} steps per call")
    out: List[Dict[str, Any]] = []
    for i, raw in enumerate(steps, start=1):
        if not isinstance(raw, dict):
            raise ValueError(f"step {i}: must be an object with an action")
        action = str(raw.get("action") or "").strip().lower()
        if action not in STEP_ACTIONS:
            raise ValueError(f"step {i}: action {action or '(missing)'!r} is not one of {', '.join(STEP_ACTIONS)}")
        s: Dict[str, Any] = {"action": action}
        if action == "open":
            url = str(raw.get("url") or "").strip()
            if not url:
                raise ValueError(f"step {i}: open needs a url")
            s["url"] = url
            s["new_tab"] = bool(raw.get("new_tab", True))
        elif action == "click":
            if raw.get("ref") is None:
                raise ValueError(f"step {i}: click needs a ref (number from a read/find)")
            s["ref"] = int(raw["ref"])
        elif action == "type":
            if raw.get("text") is None:
                raise ValueError(f"step {i}: type needs text")
            s["text"] = str(raw["text"])
            s["ref"] = int(raw["ref"]) if raw.get("ref") is not None else None
            s["submit"] = bool(raw.get("submit", True))
        elif action == "key":
            s["key"] = str(raw.get("key") or "Enter")
            key_params(s["key"])
        elif action == "scroll":
            s["direction"] = str(raw.get("direction") or "down")
            s["amount"] = int(raw.get("amount") or 1)
            wheel_delta(s["direction"], s["amount"])
        elif action == "wait":
            s["text"] = raw.get("text")
            s["url_contains"] = raw.get("url_contains")
            s["seconds"] = float(raw.get("seconds") or 10)
        elif action == "find":
            q = str(raw.get("query") or "").strip()
            if not q:
                raise ValueError(f"step {i}: find needs a query")
            s["query"] = q
        elif action == "fullscreen":
            s["on"] = bool(raw.get("on", True))
        out.append(s)
    return out


def describe_hands(status: Dict[str, Any]) -> str:
    """One phrase for the pulse trigger: where Edward's hands are."""
    if status.get("enabled") is False or (status.get("extension") or {}).get("enabled") is False:
        return "off (EDWARD_CHROME_HANDS is not on)"
    if not (status.get("extension") or {}).get("connected"):
        return "the Edward extension is not connected in Chrome"
    tab_id = status.get("working_tab")
    if tab_id is None:
        return "idle (no tab of yours open)"
    tab = status.get("tab") or {}
    url = str(tab.get("url") or "")
    p = urlparse(url)
    where = (p.netloc.replace("www.", "") + p.path).rstrip("/") if p.netloc else url
    title = " ".join(str(tab.get("title") or "").split())[:80]
    out = f"in Chrome tab {tab_id}"
    if title or where:
        out += " — " + (f"{title} ({where[:80]})" if title and where else (title or where))
    if status.get("fullscreen"):
        out += ", full screen"
    return out


# ---- state -------------------------------------------------------------------------

_working_tab_id: Optional[int] = None
_fullscreen: bool = False
_started = False


def working_tab() -> Optional[int]:
    return _working_tab_id


def set_working_tab(tab_id: Optional[int]) -> None:
    global _working_tab_id, _fullscreen
    new = int(tab_id) if tab_id is not None else None
    if new != _working_tab_id:
        _fullscreen = False
    _working_tab_id = new


def _cached_tab(tab_id: Optional[int]) -> Optional[Dict[str, Any]]:
    link = bx.link()
    if link is None or tab_id is None:
        return None
    for t in link.tabs:
        if t.get("id") == tab_id:
            return {"url": t.get("url"), "title": t.get("title")}
    return None


def enabled() -> bool:
    """Are Edward's hands in the owner's Chrome switched on
    (``EDWARD_CHROME_HANDS=on``; off by default)?"""
    return bx.hands_enabled()


def _require_link() -> None:
    """Raise unless the hands are on and the extension is connected."""
    if not enabled():
        raise ChromeHandsOff()
    if not bx.connected():
        raise ExtensionUnavailable("the Edward extension is not connected in Chrome")


def status() -> Dict[str, Any]:
    return {"enabled": enabled(), "extension": bx.status(), "working_tab": _working_tab_id,
            "tab": _cached_tab(_working_tab_id), "fullscreen": _fullscreen}


async def on_event(msg: Dict[str, Any]) -> None:
    """Extension events: a detached or closed working tab is let go."""
    name = msg.get("name")
    if name == "disconnected" or (name == "detached" and msg.get("tabId") == _working_tab_id and _working_tab_id is not None):
        if _working_tab_id is not None:
            print(f"[browser] working tab {_working_tab_id} gone ({name}); hands idle")
        set_working_tab(None)


def start() -> None:
    """Called from the lifespan. A no-op while the hands are off."""
    global _started
    if not enabled():
        print("[browser] Chrome hands are off (EDWARD_CHROME_HANDS); the extension link is not served")
        return
    if not _started:
        bx.add_listener(on_event)
        _started = True
        print("[browser] hands ready (Edward extension link)")


# ---- the working tab ----------------------------------------------------------------

class CdpTarget(Protocol):
    """Anything the page actions can drive: a tab in the owner's Chrome
    (through the extension) or a page in Edward's own headless browser
    (``headless_browser.HeadlessTarget``, 2026-10-11). Both speak CDP."""

    tab_id: Union[int, str]

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None, *,
                   timeout: float = ...) -> Dict[str, Any]: ...


class ChromeTarget:
    """A tab in the owner's Chrome, reached through the extension."""

    def __init__(self, tab_id: int):
        self.tab_id = int(tab_id)

    async def send(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = bx.CALL_TIMEOUT_S) -> Dict[str, Any]:
        res = await bx.call("cdp", {"tabId": self.tab_id, "method": method, "params": params or {}}, timeout=timeout)
        return res if isinstance(res, dict) else {}


def _target(target: Optional[CdpTarget] = None) -> CdpTarget:
    """The given target, else the working tab in the owner's Chrome."""
    if target is not None:
        return target
    _require_link()
    if _working_tab_id is None:
        raise NoWorkingTab("you are not holding a tab in Chrome")
    return ChromeTarget(_working_tab_id)


_JS_READ = r"""(() => {
  const vis = (el) => { const r = el.getBoundingClientRect();
    return r.width > 2 && r.height > 2 && r.bottom > 0 && r.right > 0 && r.top < innerHeight * 2 && r.left < innerWidth; };
  document.querySelectorAll('[data-edward-ref]').forEach((e) => e.removeAttribute('data-edward-ref'));
  const main = document.querySelector('main, [role=main], #primary, #contents, #content, article') || document.body;
  const sel = 'a[href],button,input:not([type=hidden]),textarea,select,[role=button],[role=link],[role=tab],[role=option],[role=menuitem],[role=checkbox],[role=switch],[contenteditable=true]';
  const inMain = [...main.querySelectorAll(sel)];
  const inMainSet = new Set(inMain);
  const rest = [...document.querySelectorAll(sel)].filter((el) => !inMainSet.has(el));
  const els = []; const byHref = new Map(); let i = 0;
  const labelFor = (el) => {
    if (el.id) { const l = document.querySelector('label[for="' + el.id.replace(/"/g, '\\"') + '"]'); if (l && l.innerText) return l.innerText; }
    const p = el.closest('label'); if (p && p.innerText) return p.innerText; return '';
  };
  for (const el of inMain.concat(rest)) {
    if (!vis(el)) continue;
    const tag = el.tagName.toLowerCase();
    const isField = tag === 'input' || tag === 'textarea' || tag === 'select' || el.isContentEditable;
    let text = (el.innerText || el.getAttribute('aria-label') || el.getAttribute('title') || (isField ? labelFor(el) : '') || (isField ? '' : el.value) || '').trim().replace(/\s+/g, ' ').slice(0, 120);
    const href = el.href || null;
    if (!text && !href && !isField) continue;
    if (href && !isField) {
      const prev = byHref.get(href);
      if (prev) { if (!prev.text && text) { prev.text = text; prev.el.removeAttribute('data-edward-ref'); el.setAttribute('data-edward-ref', String(prev.ref)); prev.el = el; } continue; }
    }
    i += 1; el.setAttribute('data-edward-ref', String(i));
    const rec = { ref: i, tag, role: el.getAttribute('role') || null, type: el.getAttribute('type') || null, text, href,
      placeholder: el.getAttribute('placeholder') || null,
      value: (isField && tag !== 'select' && el.type !== 'password') ? String(el.value || '').slice(0, 80) : null,
      checked: (el.type === 'checkbox' || el.type === 'radio') ? !!el.checked : (el.getAttribute('aria-checked') === 'true' ? true : null), el };
    if (href && !isField) byHref.set(href, rec);
    els.push(rec);
    if (i >= 150) break;
  }
  const out = els.map(({ el, ...r }) => r);
  const text = ((main !== document.body ? main.innerText : '') || document.body.innerText || '').replace(/\n{3,}/g, '\n\n').slice(0, 6000);
  const d = document.documentElement;
  return { url: location.href, title: document.title, text, elements: out,
    scroll: { y: window.scrollY, height: Math.max(d.scrollHeight, document.body ? document.body.scrollHeight : 0), viewport: innerHeight },
    fullscreen: !!document.fullscreenElement };
})()"""

_JS_INFO = "({url: location.href, title: document.title, ready: document.readyState, height: document.documentElement.scrollHeight, fullscreen: !!document.fullscreenElement})"


async def _evaluate(target: CdpTarget, expression: str) -> Any:
    res = await target.send("Runtime.evaluate", {"expression": expression, "returnByValue": True, "awaitPromise": True})
    if "exceptionDetails" in res:
        raise RuntimeError(str(res["exceptionDetails"].get("text") or "page script failed"))
    return (res.get("result") or {}).get("value")


async def _info(target: CdpTarget) -> Dict[str, Any]:
    v = await _evaluate(target, _JS_INFO)
    return v if isinstance(v, dict) else {"url": "", "title": ""}


async def _settle(target: CdpTarget, *, timeout: float = SETTLE_TIMEOUT_S) -> Dict[str, Any]:
    """Wait until the page stops changing (or the timeout), then say where it is."""
    deadline = time.monotonic() + timeout
    await asyncio.sleep(SETTLE_MIN_S)
    prev: Optional[Dict[str, Any]] = None
    cur: Dict[str, Any] = {}
    while True:
        try:
            cur = await _info(target)
        except Exception:  # noqa: BLE001 — mid-navigation the context is gone for a moment
            cur = {"url": None, "ready": "loading"}
        if is_settled(prev, cur):
            break
        if time.monotonic() >= deadline:
            cur = {**cur, "note": "the page was still changing when I stopped waiting"}
            break
        prev = cur
        await asyncio.sleep(SETTLE_POLL_S)
    out = {"tab_id": target.tab_id, "url": cur.get("url") or "", "title": cur.get("title") or ""}
    if cur.get("note"):
        out["note"] = cur["note"]
    return out


async def _elements(target: CdpTarget) -> Dict[str, Any]:
    payload = await _evaluate(target, _JS_READ)
    return payload if isinstance(payload, dict) else {}


async def read_page(*, target: Optional[CdpTarget] = None) -> str:
    return format_page_read(await _elements(_target(target)))


async def find_on_page(query: str, *, target: Optional[CdpTarget] = None) -> str:
    payload = await _elements(_target(target))
    hits = find_elements(payload.get("elements") or [], query)
    if not hits:
        return (f"Nothing on the page matches {query!r} (title: {payload.get('title')!r}). "
                "read shows everything in view; scroll reveals more.")
    return format_page_read({"url": payload.get("url"), "title": payload.get("title"), "text": "", "elements": hits})


async def _click_ref(target: CdpTarget, ref: int) -> None:
    js = ("(() => { const el = document.querySelector('[data-edward-ref=\"%d\"]'); if (!el) return null;"
          " if (el.tagName === 'A') el.target = '_self';"
          " el.scrollIntoView({block: 'center', inline: 'nearest'});"
          " const r = el.getBoundingClientRect(); return {x: r.left + r.width / 2, y: r.top + r.height / 2}; })()") % ref
    pt = await _evaluate(target, js)
    if not isinstance(pt, dict):
        raise RuntimeError(f"no element [{ref}] on the page any more — read again for fresh numbers")
    x, y = float(pt["x"]), float(pt["y"])
    await target.send("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y})
    await target.send("Input.dispatchMouseEvent", {"type": "mousePressed", "x": x, "y": y, "button": "left", "clickCount": 1})
    await target.send("Input.dispatchMouseEvent", {"type": "mouseReleased", "x": x, "y": y, "button": "left", "clickCount": 1})


async def click(ref: int, *, target: Optional[CdpTarget] = None) -> Dict[str, Any]:
    target = _target(target)
    await _click_ref(target, int(ref))
    return await _settle(target)


async def _press(target: CdpTarget, key: str) -> None:
    params = key_params(key)
    for t in ("keyDown", "keyUp"):
        p = {"type": t, **params}
        if t == "keyUp":
            p.pop("text", None)
        await target.send("Input.dispatchKeyEvent", p)


async def type_text(text: str, *, ref: Optional[int] = None, submit: bool = True,
                    target: Optional[CdpTarget] = None) -> Dict[str, Any]:
    target = _target(target)
    if ref is not None:
        await _click_ref(target, int(ref))
        await asyncio.sleep(0.3)
    await target.send("Input.insertText", {"text": str(text)})
    if submit:
        await _press(target, "Enter")
    return await _settle(target)


async def press_key(key: str, *, target: Optional[CdpTarget] = None) -> Dict[str, Any]:
    target = _target(target)
    await _press(target, key)
    return await _settle(target, timeout=3.0)


async def scroll(direction: str = "down", amount: int = 1, *, target: Optional[CdpTarget] = None) -> Dict[str, Any]:
    dx, dy = wheel_delta(direction, amount)
    target = _target(target)
    size = await _evaluate(target, "({w: innerWidth, h: innerHeight})")
    w, h = (size or {}).get("w", 1200), (size or {}).get("h", 800)
    await target.send("Input.dispatchMouseEvent", {"type": "mouseWheel", "x": w / 2, "y": h / 2, "deltaX": dx, "deltaY": dy})
    await asyncio.sleep(0.6)
    return {"tab_id": target.tab_id, **(await _info(target))}


async def back(*, target: Optional[CdpTarget] = None) -> Dict[str, Any]:
    target = _target(target)
    await _evaluate(target, "history.back(); true")
    return await _settle(target)


async def wait_for(*, text: Optional[str] = None, url_contains: Optional[str] = None, timeout: float = 10.0,
                   target: Optional[CdpTarget] = None) -> Dict[str, Any]:
    """Wait for a word to appear on the page and/or the URL to contain a
    string — or, with neither, for the page to settle."""
    target = _target(target)
    if not text and not url_contains:
        return {**(await _settle(target, timeout=timeout)), "matched": True}
    deadline = time.monotonic() + max(0.5, float(timeout))
    needle = str(text or "").lower()
    while True:
        try:
            info = await _info(target)
            body = ""
            if needle:
                body = str(await _evaluate(target, "(document.body && document.body.innerText || '').slice(0, 200000)") or "").lower()
            ok_text = (needle in body) if needle else True
            ok_url = (str(url_contains) in str(info.get("url") or "")) if url_contains else True
            if ok_text and ok_url:
                return {"tab_id": target.tab_id, "url": info.get("url"), "title": info.get("title"), "matched": True}
        except Exception:  # noqa: BLE001
            info = {}
        if time.monotonic() >= deadline:
            return {"tab_id": target.tab_id, "url": info.get("url"), "title": info.get("title"), "matched": False}
        await asyncio.sleep(0.5)


async def navigate(url: str, *, target: Optional[CdpTarget] = None) -> Dict[str, Any]:
    target = _target(target)
    await target.send("Page.navigate", {"url": url})
    return await _settle(target)


# ---- tabs: open / take over / full screen / hand back --------------------------------

async def open_page(url: str, *, new_tab: bool = True, fullscreen: bool = False) -> Dict[str, Any]:
    """Open a URL in the owner's Chrome, in front, as the working tab."""
    _require_link()
    if new_tab or _working_tab_id is None:
        res = await bx.call("tabs.open", {"url": url, "active": True})
        set_working_tab(int((res or {}).get("tabId")))
        target = ChromeTarget(_working_tab_id)
        info = await _settle(target)
    else:
        info = await navigate(url)
    if fullscreen:
        # The tab is the point; full screen is a bonus. A stale extension
        # once answered "unknown method" here and the whole open read as failed.
        try:
            await set_fullscreen(True)
        except Exception as exc:  # noqa: BLE001
            info["note"] = (info.get("note", "") + " " if info.get("note") else "") + \
                f"full screen did not take ({str(exc)[:80]}); try fullscreen again or the player's own button"
    info["fullscreen"] = _fullscreen
    return info


async def use_tab(tab_id: int, *, activate: bool = True) -> Dict[str, Any]:
    """Take over an existing tab (one the owner logged in to, say)."""
    _require_link()
    await bx.call("attach", {"tabId": int(tab_id)})
    if activate:
        await bx.call("tabs.activate", {"tabId": int(tab_id)})
    set_working_tab(int(tab_id))
    target = ChromeTarget(int(tab_id))
    return {"tab_id": int(tab_id), **(await _info(target)), "fullscreen": _fullscreen}


async def set_fullscreen(on: bool = True) -> Dict[str, Any]:
    """Full screen for the working tab's window (Chrome's own, like the
    green button / F11). Off returns the window to normal."""
    global _fullscreen
    target = _target()
    await bx.call("windows.fullscreen", {"tabId": target.tab_id, "on": bool(on)})
    _fullscreen = bool(on)
    await asyncio.sleep(1.0)
    return {"fullscreen": _fullscreen}


async def release(*, close: bool = False, back_to_screen: bool = True) -> Dict[str, Any]:
    """Hands off: leave full screen, detach (or close the tab), bring the
    /screen tab forward again."""
    from services.screen_window_service import is_screen_url
    tab_id = _working_tab_id
    out: Dict[str, Any] = {"released": tab_id}
    if tab_id is None:
        return out
    try:
        if _fullscreen:
            try:
                await bx.call("windows.fullscreen", {"tabId": tab_id, "on": False})
                out["left_fullscreen"] = True
                await asyncio.sleep(0.8)
            except Exception as exc:  # noqa: BLE001
                out["fullscreen_error"] = str(exc)[:120]
        if close:
            await bx.call("tabs.close", {"tabId": tab_id})
            out["closed"] = True
        else:
            await bx.call("detach", {"tabId": tab_id})
        if back_to_screen:
            for t in await bx.tabs():
                if is_screen_url(str(t.get("url") or "")):
                    await bx.call("tabs.activate", {"tabId": t["id"]})
                    out["screen_tab"] = t["id"]
                    break
    except Exception as exc:  # noqa: BLE001
        out["error"] = str(exc)[:160]
    finally:
        set_working_tab(None)
    return out


# ---- eyes ----------------------------------------------------------------------------

async def screenshot_tab() -> tuple:
    """(jpeg bytes, url) of the working tab, as Chrome draws it."""
    target = _target()
    await bx.call("tabs.activate", {"tabId": target.tab_id})
    res = await bx.call("tabs.capture", {"tabId": target.tab_id})
    data_url = str((res or {}).get("dataUrl") or "")
    if "," not in data_url:
        raise RuntimeError("Chrome returned no image for the tab")
    info = await _info(target)
    return base64.b64decode(data_url.split(",", 1)[1]), str(info.get("url") or "")


async def screenshot_target(target: Optional[CdpTarget] = None, *, quality: int = 70) -> tuple:
    """(jpeg bytes, url) of a target — the working tab or a headless page —
    from CDP ``Page.captureScreenshot``. Never activates a tab: ``look`` may
    run from any wake, and switching the owner's active tab while they
    work is not Edward's to do (2026-10-11)."""
    target = _target(target)
    res = await target.send("Page.captureScreenshot", {"format": "jpeg", "quality": int(quality)})
    data = str((res or {}).get("data") or "")
    if not data:
        raise RuntimeError("the page returned no image")
    info = await _info(target)
    return base64.b64decode(data), str(info.get("url") or "")


# ADAPT: seeing this Mac's display needs macOS Screen Recording permission for the backend's Python
#   How it works here: `screencapture -x` runs from the backend process, whose interpreter was granted
#   Screen Recording in System Settings → Privacy & Security; without it the capture fails with a clear error.
#   To adapt: grant Screen Recording to the Python that runs the backend (the venv's base interpreter), or
#   leave it ungranted and look("screen") answers that it could not capture the display.
async def screenshot_display() -> tuple:
    """(jpeg bytes, label) of this Mac's whole display (Screen Recording permission)."""
    import tempfile
    from pathlib import Path
    path = Path(tempfile.gettempdir()) / f"edward-display-{int(time.time() * 1000)}.jpg"
    proc = await asyncio.create_subprocess_exec("screencapture", "-x", "-t", "jpg", str(path),
                                                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    await asyncio.wait_for(proc.wait(), timeout=10)
    if proc.returncode != 0 or not path.exists():
        raise RuntimeError("could not capture the Mac's display (Screen Recording permission?)")
    data = path.read_bytes()
    path.unlink(missing_ok=True)
    return data, "this Mac's display"


async def look(at: str = "auto") -> str:
    """A screenshot described in words: at="page" (your working tab),
    "display" (the whole monitor), "auto" (page if you hold one)."""
    import tempfile
    from pathlib import Path
    where = str(at or "auto").strip().lower()
    if where == "auto":
        where = "page" if (_working_tab_id is not None and enabled() and bx.connected()) else "display"
    try:
        data, url = await (screenshot_tab() if where == "page" else screenshot_display())
    except Exception as exc:  # noqa: BLE001
        if where == "page":
            return f"Could not picture your tab ({exc})."
        try:
            from services import screen_service
            snap = await screen_service.get_snapshot()
            pieces = ", ".join(p["kind"] for p in snap["scene"]["pieces"])
            return (f"Could not take a picture ({exc}). From the state: the screen shows "
                    f"{screen_service.describe_show(snap['show'])}; pieces: {pieces}.")
        except Exception:  # noqa: BLE001
            return f"Could not look at the screen: {exc}"
    path = Path(tempfile.gettempdir()) / f"edward-look-{int(time.time() * 1000)}.jpg"
    path.write_bytes(data)
    try:
        from services.attachment_vision import describe_image
        text = await describe_image(path, priority="interactive")
    except Exception as exc:  # noqa: BLE001
        text = f"(screenshot taken but the vision model failed: {exc})"
    finally:
        try:
            path.unlink()
        except OSError:
            pass
    label = f"your tab in Chrome ({url})" if where == "page" else "this Mac's display"
    return f"Looking at {label}: {text}"


# ---- batched steps -------------------------------------------------------------------

def _where(info: Dict[str, Any]) -> str:
    out = f"now at {info.get('url') or '?'}" + (f" — \"{info['title']}\"" if info.get("title") else "")
    if info.get("note"):
        out += f" ({info['note']})"
    return out


async def run_steps(steps: Any) -> str:
    """Run a short list of actions in order; stop at the first failure and
    say which. Ends with where the tab is."""
    plan = plan_steps(steps)
    lines: List[str] = []
    for i, s in enumerate(plan, start=1):
        a = s["action"]
        try:
            if a == "open":
                info = await (open_page(s["url"]) if s["new_tab"] else navigate(s["url"]))
                lines.append(f"{i}. open {s['url']} — {_where(info)}")
            elif a == "click":
                info = await click(s["ref"])
                lines.append(f"{i}. click [{s['ref']}] — {_where(info)}")
            elif a == "type":
                info = await type_text(s["text"], ref=s["ref"], submit=s["submit"])
                lines.append(f"{i}. type {s['text']!r}{' + Enter' if s['submit'] else ''} — {_where(info)}")
            elif a == "key":
                info = await press_key(s["key"])
                lines.append(f"{i}. key {s['key']} — {_where(info)}")
            elif a == "scroll":
                info = await scroll(s["direction"], s["amount"])
                lines.append(f"{i}. scroll {s['direction']} ×{s['amount']} — {_where(info)}")
            elif a == "back":
                info = await back()
                lines.append(f"{i}. back — {_where(info)}")
            elif a == "wait":
                info = await wait_for(text=s["text"], url_contains=s["url_contains"], timeout=s["seconds"])
                lines.append(f"{i}. wait — {'seen' if info.get('matched') else 'not seen in time'}; {_where(info)}")
            elif a == "find":
                lines.append(f"{i}. find {s['query']!r}:\n{await find_on_page(s['query'])}")
            elif a == "read":
                lines.append(f"{i}. read:\n{await read_page()}")
            elif a == "fullscreen":
                await set_fullscreen(s["on"])
                lines.append(f"{i}. fullscreen {'on' if s['on'] else 'off'}")
            elif a == "look":
                lines.append(f"{i}. look: {await look('page')}")
        except Exception as exc:  # noqa: BLE001
            lines.append(f"{i}. {a} failed: {exc}")
            try:
                from services import event_ledger
                from services.turn_ledger import error_class

                event_ledger.record_event(
                    kind="browser", source="steps", outcome="failed",
                    reason=f"step {i} {a}: {error_class(exc)}", detail={"step": i, "action": a, "of": len(steps)},
                )
            except Exception:  # noqa: BLE001
                pass
            rest = len(plan) - i
            if rest:
                lines.append(f"({rest} later step{'s' if rest != 1 else ''} not run)")
            break
    return "\n".join(lines)


# ---- operator hands (routers/browser_ext.py) -----------------------------------------

Handler = Callable[..., Awaitable[Any]]


async def act(action: str, **kw) -> Any:
    """One action by name, for the operator endpoint."""
    a = str(action or "").strip().lower()
    if a == "tabs":
        tabs = await bx.tabs()
        return {"tabs": tabs, "text": bx.format_tabs(tabs, working_tab_id=_working_tab_id)}
    if a == "open":
        return await open_page(str(kw.get("url") or kw.get("text") or ""), new_tab=bool(kw.get("new_tab", True)),
                               fullscreen=bool(kw.get("fullscreen", False)))
    if a == "use":
        return await use_tab(int(kw.get("tab_id") or kw.get("ref") or 0))
    if a == "done":
        return await release(close=bool(kw.get("close", False)))
    if a == "close":
        # Any tab by id, without taking it over (chrome:// pages cannot be attached).
        tab_id = int(kw.get("tab_id") or 0)
        await bx.call("tabs.close", {"tabId": tab_id})
        if tab_id == _working_tab_id:
            set_working_tab(None)
        return {"closed": tab_id}
    if a == "read":
        return {"page": await read_page()}
    if a == "find":
        return {"page": await find_on_page(str(kw.get("query") or kw.get("text") or ""))}
    if a == "click":
        return await click(int(kw.get("ref") or 0))
    if a == "type":
        return await type_text(str(kw.get("text") or ""), ref=kw.get("ref"), submit=bool(kw.get("submit", True)))
    if a == "key":
        return await press_key(str(kw.get("key") or "Enter"))
    if a == "scroll":
        return await scroll(str(kw.get("direction") or "down"), int(kw.get("amount") or 1))
    if a == "back":
        return await back()
    if a == "wait":
        return await wait_for(text=kw.get("text"), url_contains=kw.get("url_contains"), timeout=float(kw.get("seconds") or 10))
    if a == "fullscreen":
        return await set_fullscreen(bool(kw.get("on", True)))
    if a == "steps":
        return {"result": await run_steps(kw.get("steps") or [])}
    if a == "look":
        return {"description": await look(str(kw.get("at") or "auto"))}
    raise ValueError("action must be one of tabs, open, use, done, close, read, find, click, type, key, scroll, back, wait, fullscreen, steps, look")
