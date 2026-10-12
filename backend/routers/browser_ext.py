"""Edward's hands in the owner's Chrome: the extension's socket and the
operator endpoints (chrome-extension/background.js ↔ services/browser_hands.py).

The extension runs in the owner's Chrome on the same Mac and connects
straight to ``ws://127.0.0.1:8000`` with ``Origin: chrome-extension://<id>``.
The auth middleware does not see WebSockets, so the check lives here, and a
loopback peer alone proves nothing: Next's ``/api`` rewrite and a tunnel
both reach uvicorn from 127.0.0.1, which would let a LAN device, a page in
another browser on this Mac or anyone behind the tunnel become Edward's
hands. So the socket is accepted only when all three hold (``refusal``):

* no proxy header (``X-Forwarded-For``, ``X-Forwarded-Host``, ``X-Real-IP``,
  ``Forwarded``, ``CF-Connecting-IP``) — Next always adds
  ``X-Forwarded-Host``, cloudflared ``CF-Connecting-IP``;
* a loopback peer;
* ``Origin: chrome-extension://<id>`` (a web page cannot forge its Origin)
  with ``<id>`` Edward's own extension: ``EDWARD_EXTENSION_ID`` by default —
  ``chrome-extension/manifest.json`` carries a fixed public ``key``, so every
  unpacked copy has that ID — or the ids ``EDWARD_CHROME_EXTENSION_ID``
  names (comma-separated), which replace it. Any other extension, in any
  Chromium browser or profile on this Mac, is refused (2026-10-12 review:
  unpinned, a rogue extension could take the link over, read what Edward
  types and feed him the pages he reads), and the log names the id it
  offered.

A local program can still forge every header; the socket is for this Mac's
own processes. With ``EDWARD_CHROME_HANDS`` off (the default) the socket is
refused and the operator actions answer 503.
"""

import logging
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, WebSocket
from pydantic import BaseModel

from services import browser_ext_service, browser_hands, client_ip

logger = logging.getLogger(__name__)

router = APIRouter()

EXTENSION_ORIGIN_PREFIX = "chrome-extension://"
EXTENSION_ID_ENV = "EDWARD_CHROME_EXTENSION_ID"
# The ID chrome-extension/manifest.json's "key" gives every unpacked copy
# (the SHA-256 of the key's DER, first 32 hex digits, 0-f mapped to a-p;
# tests/test_public_browser_ext_socket.py checks the two agree). Only the
# public key is in the repo.
EDWARD_EXTENSION_ID = "appfgnpjahkedldopocalbkoommcalae"


def extension_id_from_key(key_b64: str) -> str:
    """The extension ID Chrome derives from a manifest ``key``."""
    import base64
    import hashlib

    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def _allowed_extension_ids() -> frozenset:
    """``EDWARD_CHROME_EXTENSION_ID`` (comma-separated) when set, else
    Edward's own extension's ID."""
    raw = os.getenv(EXTENSION_ID_ENV) or ""
    pinned = frozenset(p.strip().lower() for p in raw.split(",") if p.strip())
    return pinned or frozenset({EDWARD_EXTENSION_ID})


def refusal(websocket: Any) -> Optional[str]:
    """Why this socket is not Edward's extension, or ``None`` when it is.
    Pure but for the request and ``EDWARD_CHROME_EXTENSION_ID``."""
    proxied = [h for h in client_ip.PROXY_HEADERS if h in websocket.headers]
    if proxied:
        return ("proxied or tunnelled connections are refused (" + ", ".join(proxied)
                + "); the extension connects straight to 127.0.0.1:8000")
    host = (websocket.client.host if websocket.client else "") or ""
    if not client_ip.is_loopback(host):
        return "local extensions only (connect to 127.0.0.1:8000 from this Mac)"
    origin = (websocket.headers.get("origin") or "").strip()
    if not origin.lower().startswith(EXTENSION_ORIGIN_PREFIX) or not origin[len(EXTENSION_ORIGIN_PREFIX):].strip("/"):
        return "only Edward's Chrome extension may connect (Origin chrome-extension://…)"
    offered = origin[len(EXTENSION_ORIGIN_PREFIX):].strip("/").lower()
    if offered not in _allowed_extension_ids():
        shown = "".join(c for c in offered[:64] if c.isalnum()) or "?"
        return f"extension {shown} is not Edward's (pin it with {EXTENSION_ID_ENV})"
    return None


@router.websocket("/browser/ext")
async def browser_extension_socket(websocket: WebSocket) -> None:
    if not browser_ext_service.hands_enabled():
        await websocket.close(code=4004, reason="Chrome hands are off (EDWARD_CHROME_HANDS)")
        return
    why = refusal(websocket)
    if why:
        logger.warning("refused a Chrome-extension socket: %s", why)
        await websocket.close(code=4003, reason=why[:120])
        return
    await websocket.accept()
    await browser_ext_service.serve(websocket)


@router.get("/browser/ext/status")
async def browser_extension_status() -> Dict[str, Any]:
    return browser_ext_service.status()


@router.get("/browser/status")
async def browser_status() -> Dict[str, Any]:
    """Extension link, the tab Edward holds, full screen."""
    return browser_hands.status()


class BrowserAction(BaseModel):
    action: str                       # tabs | open | use | done | close | read | find | click | type | key | scroll | back | wait | fullscreen | steps | look
    url: Optional[str] = None
    tab_id: Optional[int] = None
    ref: Optional[int] = None
    text: Optional[str] = None
    query: Optional[str] = None
    key: Optional[str] = None
    submit: bool = True
    direction: str = "down"
    amount: int = 1
    url_contains: Optional[str] = None
    seconds: float = 10
    on: bool = True
    close: bool = False
    new_tab: bool = True
    fullscreen: bool = False
    at: str = "auto"
    steps: Optional[List[Dict[str, Any]]] = None


@router.post("/browser/act")
async def browser_act(req: BrowserAction) -> Dict[str, Any]:
    """Operator hands on the owner's Chrome (the same actions Edward's browser_* tools take)."""
    try:
        out = await browser_hands.act(req.action, **req.model_dump(exclude={"action"}))
        return out if isinstance(out, dict) else {"result": out}
    except browser_hands.NoWorkingTab as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except browser_ext_service.ExtensionUnavailable as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — the tab went away, Chrome refused
        raise HTTPException(status_code=409, detail=str(exc)[:300])
