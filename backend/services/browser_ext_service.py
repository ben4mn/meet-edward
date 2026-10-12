"""Edward's hands in the owner's own Chrome — the extension link.

Why (2026-09-07): Edward should be able to do in the browser what the
owner can — the owner opens a tab and signs in, Edward takes over. The
owner's Chrome has their logins and sits on their monitor. A small
extension (``chrome-extension/``) keeps one WebSocket to this backend and
relays the tab list plus DevTools commands against whichever tab Edward is
working in. This module owns that socket: request/response with ids, the
tab cache, events (screencast frames, detaches) fanned out to listeners.

**Off by default.** ``EDWARD_CHROME_HANDS=on`` turns the hands on; until
then the socket is refused, every call raises ``ChromeHandsOff`` and
nothing launches, raises, relaunches or quits Chrome (``hands_enabled``).

Pure: ``encode_call`` / ``decode_message`` / ``format_tabs``. I/O:
``ExtensionLink`` and ``serve``. The hands themselves live in
``browser_hands``.
Every command Edward sends through here is logged as ``[browser]`` — he is
acting in the owner's name.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable, Dict, List, Optional
from urllib.parse import urlparse

CALL_TIMEOUT_S = 25.0
MAX_TITLE = 70


class ExtensionUnavailable(RuntimeError):
    """No extension connected, or it did not answer in time."""


# ADAPT: Edward's hands drive Google Chrome on this Mac through an unpacked extension
#   How it works here: chrome-extension/ is loaded once at chrome://extensions and connects to
#   ws://127.0.0.1:8000/api/browser/ext; screen_window_service raises Chrome over AppleScript.
#   To adapt: set EDWARD_CHROME_HANDS=on and load chrome-extension/ (see its README); leave it off
#   on a host where Edward must never touch the owner's browser.
HANDS_OFF = ("Chrome hands are off — set EDWARD_CHROME_HANDS=on and load chrome-extension/ "
             "(see its README)")


def hands_enabled() -> bool:
    """``EDWARD_CHROME_HANDS=on`` (default off): may Edward use the owner's
    Chrome at all? Read per call."""
    return (os.getenv("EDWARD_CHROME_HANDS") or "off").strip().lower() in ("on", "1", "true", "yes")


class ChromeHandsOff(ExtensionUnavailable):
    """The hands are switched off (``EDWARD_CHROME_HANDS`` is not on)."""

    def __init__(self, message: str = HANDS_OFF):
        super().__init__(message)


# ---- pure -------------------------------------------------------------------------

def encode_call(msg_id: int, method: str, params: Optional[Dict[str, Any]] = None) -> str:
    return json.dumps({"type": "call", "id": msg_id, "method": method, "params": params or {}})


def decode_message(text: str) -> Optional[Dict[str, Any]]:
    try:
        msg = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(msg, dict) or msg.get("type") not in ("hello", "result", "event", "tabs", "ping"):
        return None
    return msg


def _short_url(url: str) -> str:
    p = urlparse(url or "")
    if not p.netloc:
        return url or ""
    host = p.netloc.replace("www.", "")
    return (host + p.path + ("#" + p.fragment if p.fragment else "")).rstrip("/")[:90]


def _is_screen(url: str) -> bool:
    try:
        from services.screen_window_service import is_screen_url
        return is_screen_url(url)
    except Exception:  # noqa: BLE001
        return "/screen" in (url or "")


def format_tabs(tabs: List[Dict[str, Any]], *, working_tab_id: Optional[int] = None) -> str:
    """One line per tab: [id] title — host/path (flags)."""
    lines = []
    for t in tabs:
        url = str(t.get("url") or "")
        title = " ".join(str(t.get("title") or "").split())[:MAX_TITLE]
        if url.startswith("chrome://newtab") or url == "about:blank":
            title = title or "new tab"
        flags = []
        if t.get("active"):
            flags.append("active")
        if working_tab_id is not None and t.get("id") == working_tab_id:
            flags.append("yours")
        if _is_screen(url):
            flags.append("your screen")
        line = f"[{t.get('id')}] {title or '(untitled)'} — {_short_url(url)}"
        if flags:
            line += f" ({', '.join(flags)})"
        lines.append(line)
    return "\n".join(lines) if lines else "(no tabs)"


# ---- the link ---------------------------------------------------------------------

Listener = Callable[[Dict[str, Any]], Awaitable[None]]


class ExtensionLink:
    def __init__(self, ws: Any):
        self.ws = ws
        self.tabs: List[Dict[str, Any]] = []
        self.connected_at = time.time()
        self.last_seen = time.time()
        self._next_id = 1
        self._pending: Dict[int, asyncio.Future] = {}

    async def call(self, method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = CALL_TIMEOUT_S) -> Any:
        msg_id = self._next_id
        self._next_id += 1
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[msg_id] = fut
        try:
            await self.ws.send_text(encode_call(msg_id, method, params))
            return await asyncio.wait_for(fut, timeout=timeout)
        except asyncio.TimeoutError:
            raise ExtensionUnavailable(f"Chrome extension did not answer {method} in {timeout:.0f}s")
        finally:
            self._pending.pop(msg_id, None)

    def handle_message(self, text: str) -> Optional[Dict[str, Any]]:
        """Resolve a pending call or return an event/tabs message for fan-out."""
        self.last_seen = time.time()
        msg = decode_message(text)
        if not msg:
            return None
        kind = msg["type"]
        if kind == "result":
            fut = self._pending.get(int(msg.get("id") or 0))
            if fut and not fut.done():
                if msg.get("ok"):
                    fut.set_result(msg.get("result"))
                else:
                    fut.set_exception(RuntimeError(str(msg.get("error") or "extension error")))
            return None
        if kind in ("hello", "tabs"):
            tabs = msg.get("tabs")
            if isinstance(tabs, list):
                self.tabs = [t for t in tabs if isinstance(t, dict)]
            return {"type": "tabs", "tabs": self.tabs}
        if kind == "event":
            return msg
        return None

    def fail_all(self, reason: str) -> None:
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ExtensionUnavailable(reason))
        self._pending.clear()


_link: Optional[ExtensionLink] = None
_listeners: List[Listener] = []
# When the link last dropped (None while connected or before any connection).
_disconnected_at: Optional[float] = None
# After a drop, give the extension this long to reconnect on its own before
# asking screen_window_service whether Chrome is a window-less zombie
# (2026-09-14: Chrome relaunched itself without windows after an update,
# the extension worker was unloaded, and the link stayed down for hours).
RECOVERY_GRACE_S = 90.0
_recovery_task: Optional["asyncio.Task"] = None


def _schedule_recovery() -> None:
    global _recovery_task
    if not hands_enabled():
        return
    if _recovery_task is not None and not _recovery_task.done():
        return
    try:
        _recovery_task = asyncio.get_running_loop().create_task(_recover_if_still_down())
    except RuntimeError:
        _recovery_task = None


async def _recover_if_still_down() -> None:
    try:
        await asyncio.sleep(RECOVERY_GRACE_S)
        if connected() or not hands_enabled():
            return
        print(f"[browser] hands still down after {RECOVERY_GRACE_S:.0f}s; checking whether Chrome needs a relaunch")
        from services import screen_window_service

        result = await screen_window_service.recover_hands()
        print(f"[browser] recovery: {result}")
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"[browser] recovery failed: {exc}")


def connected() -> bool:
    return _link is not None and hands_enabled()


def link() -> Optional[ExtensionLink]:
    return _link


def add_listener(fn: Listener) -> None:
    if fn not in _listeners:
        _listeners.append(fn)


async def call(method: str, params: Optional[Dict[str, Any]] = None, *, timeout: float = CALL_TIMEOUT_S) -> Any:
    if not hands_enabled():
        raise ChromeHandsOff()
    if _link is None:
        raise ExtensionUnavailable("the Edward extension is not connected in Chrome")
    if method != "tabs.list":
        p = dict(params or {})
        if method == "cdp":
            print(f"[browser] tab {p.get('tabId')}: {p.get('method')}" + (
                f" {json.dumps(p.get('params'))[:160]}" if p.get("params") and p.get("method") not in ("Page.screencastFrameAck",) else ""))
        else:
            print(f"[browser] {method} {json.dumps(p)[:200]}")
    return await _link.call(method, params, timeout=timeout)


async def tabs(*, refresh: bool = True) -> List[Dict[str, Any]]:
    if not hands_enabled():
        raise ChromeHandsOff()
    if _link is None:
        raise ExtensionUnavailable("the Edward extension is not connected in Chrome")
    if refresh:
        res = await _link.call("tabs.list", {})
        if isinstance(res, dict) and isinstance(res.get("tabs"), list):
            _link.tabs = res["tabs"]
    return list(_link.tabs)


async def _fan_out(msg: Dict[str, Any]) -> None:
    for fn in list(_listeners):
        try:
            await fn(msg)
        except Exception as exc:  # noqa: BLE001
            print(f"[browser] listener failed: {exc}")


async def serve(websocket: Any) -> None:
    """Run one extension connection to completion (FastAPI WebSocket).
    With the hands off the connection is closed at once."""
    global _link, _disconnected_at
    if not hands_enabled():
        try:
            await websocket.close(code=4004, reason="Chrome hands are off (EDWARD_CHROME_HANDS)")
        except Exception:  # noqa: BLE001
            pass
        return
    if _link is not None:
        old, _link = _link, None
        old.fail_all("replaced by a newer extension connection")
        try:
            await old.ws.close(code=4000)
        except Exception:  # noqa: BLE001
            pass
    _link = ExtensionLink(websocket)
    _disconnected_at = None
    print("[browser] Chrome extension connected")
    try:
        while True:
            text = await websocket.receive_text()
            out = _link.handle_message(text) if _link else None
            if out:
                await _fan_out(out)
    except Exception as exc:  # noqa: BLE001 — disconnect, or the socket errored
        reason = type(exc).__name__
        print(f"[browser] Chrome extension disconnected ({reason})")
    finally:
        if _link is not None and _link.ws is websocket:
            _link.fail_all("Chrome extension disconnected")
            _link = None
            _disconnected_at = time.time()
            _schedule_recovery()
            await _fan_out({"type": "event", "name": "disconnected", "tabId": None, "params": {}})


def status() -> Dict[str, Any]:
    if not hands_enabled():
        # No "connected" key: switched off is not "down" (wake_state.hands_view
        # reads a missing key as unknown, so no screen claims his hands fell).
        return {"enabled": False, "message": HANDS_OFF}
    if _link is None:
        down = int(time.time() - _disconnected_at) if _disconnected_at is not None else None
        return {"connected": False, "enabled": True, "disconnected_at": _disconnected_at, "down_for_s": down}
    return {"connected": True, "enabled": True, "tabs": len(_link.tabs), "connected_at": _link.connected_at,
            "last_seen": _link.last_seen}
