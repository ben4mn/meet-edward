"""Where an urgent reach to the owner lands (2026-10-11).

Why (docs/architecture/push-and-notes.md): push and Today may be channels
the owner does not live in — many people live in iMessage. Which one an
urgent reach uses is the owner's choice, so it is a flag
(``EDWARD_OWNER_CHANNEL``), read at call time:

* ``push`` (the default) — Web Push (``push_service``);
* ``imessage_self`` — a text to the owner's own first handle, signed
  "Edward:". Only once structural echo detection exists
  (``services.imessage_sent`` or ``imessage_service.load_sent_index``):
  without it Edward's own text would come back as the owner's self-chat
  ping;
* ``edward_number`` — an SMS from Edward's own Twilio number to the owner's
  phone handle.

The owner's handles are the heartbeat config's ``imessage_owner_handles``
column, then ``EDWARD_OWNER_HANDLES`` (``services.owner``).

Every channel writes ``push_log`` with ``source="reach:<origin>"`` and the
URL, so the push budget counts all of them the same way, and every failure
(no handle, no Twilio, a send that fails) falls back to a push. Quiet hours
and the budget were applied by ``reach_service`` before this is called.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
from typing import Any, Dict, List, Optional

from services import host_config
from services.owner import owner_handles as _env_owner_handles

logger = logging.getLogger(__name__)

CHANNEL_ENV = "EDWARD_OWNER_CHANNEL"
CHANNELS = ("push", "imessage_self", "edward_number")
SIGN = "Edward: "


def channel() -> str:
    raw = (os.getenv(CHANNEL_ENV) or "push").strip().lower()
    return raw if raw in CHANNELS else "push"


def structural_echo_available() -> bool:
    """Does the ROWID echo index exist? Without it a text to the owner's
    self-chat would read back as their own ping."""
    try:
        mod = importlib.import_module("services.imessage_sent")
        if callable(getattr(mod, "is_edward_rowid", None)):
            return True
    except Exception:  # noqa: BLE001
        pass
    try:
        from services import imessage_service

        return callable(getattr(imessage_service, "load_sent_index", None))
    except Exception:  # noqa: BLE001
        return False


def public_url(path: Optional[str]) -> str:
    """A tappable link for a text: the app's public origin plus the path
    (``host_config.public_base_url``: ``EDWARD_PUBLIC_BASE_URL``, else the
    origin of ``EDWARD_PUBLIC_SCREEN_URL``, else ``http://localhost:3000``)."""
    base = host_config.public_base_url()
    p = (path or "/today").strip()
    if p.startswith("http://") or p.startswith("https://"):
        return p
    return base + (p if p.startswith("/") else "/" + p)


def _merge(*groups) -> List[str]:
    seen, out = set(), []
    for group in groups:
        for h in group:
            h = str(h or "").strip()
            if h and h.lower() not in seen:
                seen.add(h.lower())
                out.append(h)
    return out


async def _owner_handles() -> List[str]:
    """The heartbeat config's owner handles first, then ``EDWARD_OWNER_HANDLES``."""
    stored: List[str] = []
    try:
        from services.database import HeartbeatConfigModel, async_session

        async with async_session() as s:
            cfg = await s.get(HeartbeatConfigModel, "default")
        raw = getattr(cfg, "imessage_owner_handles", None) if cfg is not None else None
        if raw:
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = [x.strip() for x in str(raw).split(",")]
            stored = [str(h).strip() for h in (parsed if isinstance(parsed, list) else [parsed]) if str(h).strip()]
    except Exception as exc:  # noqa: BLE001
        logger.warning("owner_channel: owner handles unreadable: %s", exc)
    return _merge(stored, _env_owner_handles())


def _is_phone(handle: str) -> bool:
    return len(re.sub(r"\D", "", handle)) >= 10 and re.fullmatch(r"[0-9+()\-.\s]+", handle) is not None


def _text(title: str, body: str, url: Optional[str]) -> str:
    head = (title or "").strip()
    main = (body or "").strip()
    line = f"{head} — {main}" if head and head.lower() not in main.lower() else main or head
    return f"{SIGN}{line}\n{public_url(url)}"


async def _log(*, title: str, body: str, url: Optional[str], origin: str, conversation_id: Optional[str]) -> Optional[str]:
    from services import push_ledger

    return await push_ledger.record(source=f"reach:{origin}"[:40], title=title, body=body, url=url, tag="edward-reach",
                                    conversation_id=conversation_id, delivery_id=None, sent=1, failed=0,
                                    held_reason=None)


async def _push(*, title: str, body: str, url: Optional[str], origin: str,
                conversation_id: Optional[str]) -> Dict[str, Any]:
    from services import push_service

    if not push_service.is_configured():
        return {"sent": 0, "error": "push is not configured", "channel": "push"}
    out = await push_service.send_push_notification(
        title=title, body=body, url=url, tag="edward-reach", source=f"reach:{origin}"[:40],
        conversation_id=conversation_id, respect_quiet_hours=False)
    out = dict(out or {})
    out["channel"] = "push"
    return out


async def deliver(*, title: str, body: str, url: Optional[str], origin: str,
                  conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """Deliver one urgent reach on the owner's channel: ``{"sent", "ledger_id",
    "channel", "fallback_reason"?}``. Raises only when the push fallback
    itself raises."""
    want = channel()
    why: Optional[str] = None
    if want == "imessage_self":
        if not structural_echo_available():
            why = "no structural echo detection yet"
        else:
            handles = await _owner_handles()
            if not handles:
                why = "no owner handle"
            else:
                try:
                    from services import imessage_service

                    res = await asyncio.to_thread(imessage_service.send_imessage, handles[0], _text(title, body, url))
                    if isinstance(res, dict) and res.get("success"):
                        lid = await _log(title=title, body=body, url=url, origin=origin,
                                         conversation_id=conversation_id)
                        print(f"[reach] {origin} → the owner's self-chat")
                        return {"sent": 1, "failed": 0, "total": 1, "ledger_id": lid, "channel": "imessage_self"}
                    why = f"the text failed ({(res or {}).get('error', 'unknown') if isinstance(res, dict) else res})"
                except Exception as exc:  # noqa: BLE001
                    why = f"the text failed ({type(exc).__name__})"
    elif want == "edward_number":
        from services import twilio_service

        if not twilio_service.is_configured():
            why = "Twilio is not configured"
        else:
            phones = [h for h in await _owner_handles() if _is_phone(h)]
            if not phones:
                why = "no phone handle for the owner"
            else:
                try:
                    await twilio_service.send_sms(phones[0], _text(title, body, url))
                    lid = await _log(title=title, body=body, url=url, origin=origin, conversation_id=conversation_id)
                    print(f"[reach] {origin} → Edward's number")
                    return {"sent": 1, "failed": 0, "total": 1, "ledger_id": lid, "channel": "edward_number"}
                except Exception as exc:  # noqa: BLE001
                    why = f"the SMS failed ({type(exc).__name__})"
    out = await _push(title=title, body=body, url=url, origin=origin, conversation_id=conversation_id)
    if why:
        out["fallback_reason"] = why
        print(f"[reach] {want} fell back to a push: {why}")
    return out
