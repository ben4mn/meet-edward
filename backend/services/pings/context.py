"""Everything a ping turn reads, gathered in one call (AREA 3, 2026-10-11;
docs/architecture/pings.md).

``gather(ping)`` -> ``TurnContext(trigger, attachments, media, …)``. The
runner passes ``trigger`` as the turn's message, ``attachments`` as its
attachments, and adds ``media`` to ``CURRENT_PING["media"]``:

- the chat's last 20 lines (``get_chat_thread``), Edward's own marked
  (``by_edward`` from the listener, else the read-back index or his
  signature), and the roster with contact names;
- this message's photos and files, then photos and voice messages from the
  thread's last 30 minutes, newest first: HEIC, PNG, stickers, GIFs and
  link-preview images (``.pluginPayloadAttachment``) as JPEGs, a PDF as a
  document, a voice message as its words — at most 6 images and one PDF
  (``media.hooks.turn_attachments``);
- the links in the message, read first (``media.links``): iMessage's own
  preview, then the page through ``netguard``;
- the trigger itself (``render.build_trigger``): the whole text, fenced
  where someone other than the owner wrote it.

Every source is bounded (``GATHER_TIMEOUT_S``) and a failing one is simply
left out: the ping is answered with what could be read. Each media item has
its own bound (``ITEM_TIMEOUT_S``), so one slow video never costs the photos
beside it; an item still converting when its bound runs out is named in the
trigger with its ``look`` call (review 2026-10-11).
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional

from services.owner import owner_name
from services.pings import render

logger = logging.getLogger(__name__)

GATHER_TIMEOUT_S = 25.0
ITEM_TIMEOUT_S = 20.0           # one media item (a video's sound through Scribe can take a while)
THREAD_LINES = 20
RECENT_PHOTOS_S = 30 * 60
MAX_IMAGES = 6


@dataclass
class TurnContext:
    trigger: str
    attachments: List[Dict[str, Any]] = field(default_factory=list)
    media: Dict[str, int] = field(default_factory=lambda: {"images": 0, "bytes": 0})
    thread: List[Dict[str, Any]] = field(default_factory=list)
    names: Dict[str, str] = field(default_factory=dict)
    links: List[Dict[str, Any]] = field(default_factory=list)


def _get(ping: Any, key: str, default: Any = None) -> Any:
    if isinstance(ping, Mapping):
        return ping.get(key, default)
    return getattr(ping, key, default)


async def _bounded(awaitable, default):
    try:
        return await asyncio.wait_for(awaitable, GATHER_TIMEOUT_S)
    except Exception as exc:  # noqa: BLE001 - a source that fails is left out
        logger.warning("pings: context source skipped: %s", type(exc).__name__)
        return default


async def _name_for(handle: str) -> Optional[str]:
    from services.heartbeat.listener_imessage import _resolve_contact_name
    return await _resolve_contact_name(handle)


def _signed(text: str) -> bool:
    from utils.message_signature import _SIGNATURE_PATTERNS
    t = str(text or "").rstrip()
    return any(re.search(p, t, flags=re.IGNORECASE) for p in _SIGNATURE_PATTERNS)


def _mark_edward(thread: List[Dict[str, Any]]) -> None:
    from services import imessage_sent
    for line in thread:
        if "by_edward" not in line:
            line["by_edward"] = bool(line.get("is_from_me")) and (
                imessage_sent.is_edward_rowid(line.get("rowid")) or _signed(line.get("text") or ""))


def _aware(dt: Any) -> Optional[datetime]:
    if not isinstance(dt, datetime):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _who(line: Mapping[str, Any], names: Mapping[str, str]) -> str:
    if line.get("is_from_me"):
        return "Edward" if line.get("by_edward") else owner_name()
    sender = str(line.get("sender") or "someone")
    return names.get(sender) or sender


def _label(att: Mapping[str, Any], who: str, when: Optional[datetime]) -> str:
    clock = ""
    if when is not None:
        try:
            from services.timezone_utils import get_user_tz
            clock = " " + when.astimezone(get_user_tz()).strftime("%-I:%M %p").lower()
        except Exception:  # noqa: BLE001
            clock = ""
    return f"attachment:{att['rowid']} · {att.get('name') or 'attachment'} · from {who}{clock}"


def _is_recent_media(att: Mapping[str, Any]) -> bool:
    """A thread item worth carrying into the turn: a photo, a link preview,
    or a voice message ("@edward what did he say?"). Videos wait for look."""
    mime = str(att.get("mime_type") or "").lower()
    name = str(att.get("name") or att.get("path") or "")
    return (mime.startswith(("image/", "audio/")) or name.lower().endswith(".caf")
            or (not mime and name.endswith(".pluginPayloadAttachment")))


async def _event_attachments(event_id: Optional[str]) -> List[Dict[str, Any]]:
    """The message's attachments from its heartbeat event (when the message
    is older than the thread window)."""
    if not event_id:
        return []
    from services.database import HeartbeatEventModel, async_session
    async with async_session() as session:
        event = await session.get(HeartbeatEventModel, event_id)
        raw = getattr(event, "raw_data", None) if event is not None else None
    try:
        atts = (json.loads(raw or "{}") or {}).get("attachments") or []
    except (TypeError, ValueError):
        return []
    return [a for a in atts if isinstance(a, dict) and a.get("rowid") is not None]


async def _media_items(ping: Any, thread: List[Dict[str, Any]], names: Mapping[str, str],
                       now: datetime) -> List[Dict[str, Any]]:
    rowid = _get(ping, "message_rowid")
    own_line = next((line for line in thread if rowid is not None and line.get("rowid") == rowid), None)
    asker = owner_name() if _get(ping, "from_owner") else (_get(ping, "sender_label") or "someone")
    asked = _aware(_get(ping, "asked_at"))
    own = list((own_line or {}).get("attachments") or [])
    if not own and own_line is None:
        own = await _bounded(_event_attachments(_get(ping, "heartbeat_event_id")), [])
    items = [{**a, "label": _label(a, asker, asked)} for a in own]
    cutoff = now - timedelta(seconds=RECENT_PHOTOS_S)
    for line in reversed(thread):
        if line is own_line:
            continue
        when = _aware(line.get("timestamp"))
        if when is None or when < cutoff:
            continue
        who = _who(line, names)
        items += [{**a, "label": _label(a, who, when)} for a in (line.get("attachments") or [])
                  if _is_recent_media(a)]
    return items


def _is_image_item(att: Mapping[str, Any]) -> bool:
    return str(att.get("mime_type") or "").lower().startswith("image/")


async def _one_item(item: Dict[str, Any], max_images: int) -> Optional[List[Dict[str, Any]]]:
    """One item's attachment dicts under its own bound; None when it was
    still converting at the bound (the worker thread may finish later)."""
    from services.media import hooks as media_hooks

    try:
        return list(await asyncio.wait_for(media_hooks.turn_attachments([item], max_images=max_images),
                                           min(ITEM_TIMEOUT_S, GATHER_TIMEOUT_S)))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - left for look
        logger.warning("pings: a media item was not ready: %s", type(exc).__name__)
        return None


def _note_for(att: Dict[str, Any], what: str) -> Dict[str, Any]:
    """A short text note in place of media the turn doesn't carry."""
    label = str(att.get("filename") or "attachment").split(" · frame ")[0]
    m = re.search(r"attachment:\d+", label)
    look = f' — look("{m.group(0)}") to see it' if m else ""
    return {"filename": label, "mime_type": "text/plain",
            "data": base64.b64encode(f"{what}{look}".encode()).decode("ascii")}


def _pdf_note(att: Dict[str, Any]) -> Dict[str, Any]:
    return _note_for(att, "a PDF (one PDF per message)")


async def _attach(items: List[Dict[str, Any]]) -> tuple[List[Dict[str, Any]], List[str]]:
    """``(attachments, pending labels)``, in the items' order (this
    message's, then the thread's, newest first). Every item is converted at
    once, each under its own bound. The image budget (``MAX_IMAGES``) and
    the one PDF hold across items: an image past the first ``MAX_IMAGES``
    is listed, never converted, and frames past the budget are dropped."""
    budgets = []
    seen_images = 0
    for item in items:
        if _is_image_item(item):
            budgets.append(MAX_IMAGES if seen_images < MAX_IMAGES else 0)
            seen_images += 1
        else:
            budgets.append(MAX_IMAGES)
    results = await asyncio.gather(*(_one_item(item, budget) for item, budget in zip(items, budgets)))
    out: List[Dict[str, Any]] = []
    pending: List[str] = []
    images_left = MAX_IMAGES
    pdf_done = False
    for item, got in zip(items, results):
        if got is None:
            pending.append(str(item.get("label") or item.get("name") or f"attachment:{item.get('rowid')}"))
            continue
        dropped = None
        for att in got:
            mime = str(att.get("mime_type") or "")
            if mime == "image/jpeg":
                if images_left <= 0:
                    dropped = dropped or att
                    continue
                images_left -= 1
            elif mime == "application/pdf":
                if pdf_done:
                    att = _pdf_note(att)
                pdf_done = True
            out.append(att)
        if dropped is not None:
            out.append(_note_for(dropped, f"more images than one message carries ({MAX_IMAGES})"))
    return out, pending


async def gather(ping: Any, *, attempt: int = 1, max_attempts: int = 2, last_error: Any = None,
                 now: Optional[datetime] = None, legacy: Optional[bool] = None) -> TurnContext:
    """The trigger and attachments for one ping turn. Never raises for a
    source that fails; the trigger is always built."""
    from services.heartbeat import listener_imessage
    from services.media import links as media_links

    now = _aware(now) or datetime.now(timezone.utc)
    chat = str(_get(ping, "chat_identifier") or "")
    text = str(_get(ping, "text") or "")

    thread, participants = await asyncio.gather(
        _bounded(asyncio.to_thread(listener_imessage.get_chat_thread, chat, THREAD_LINES), []),
        _bounded(asyncio.to_thread(listener_imessage.get_chat_participants, chat), []),
    )
    thread = [dict(line) for line in (thread or [])]
    _mark_edward(thread)

    handles = [str(p.get("handle")) for p in (participants or []) if p.get("handle")]
    speakers = {str(line.get("sender")) for line in thread if not line.get("is_from_me") and line.get("sender")}
    wanted = list(dict.fromkeys([*handles, *sorted(speakers)]))
    resolved = await asyncio.gather(*(_bounded(_name_for(h), None) for h in wanted))
    names = {h: n for h, n in zip(wanted, resolved) if n}
    roster = [names.get(h) or h for h in handles]

    items = await _media_items(ping, thread, names, now)
    (attachments, pending), links = await asyncio.gather(
        _attach(items),
        _bounded(media_links.read_links(text, message_rowid=_get(ping, "message_rowid"),
                                        sender_known=bool(_get(ping, "sender_known", True))), []),
    )
    attachments = list(attachments or [])
    try:
        from services.media.present import media_counts
        media = media_counts(attachments)
    except ImportError:
        media = {"images": 0, "bytes": 0}

    trigger = render.build_trigger(
        text=text, asked_at=_get(ping, "asked_at") or now, chat_identifier=chat,
        chat_name=_get(ping, "chat_name"), is_group=bool(_get(ping, "is_group")), roster=roster,
        sender_label=str(_get(ping, "sender_label") or _get(ping, "sender") or "someone"),
        from_owner=bool(_get(ping, "from_owner")), kind=str(_get(ping, "kind") or "mention"),
        sender_known=bool(_get(ping, "sender_known", True)), thread=thread, names=names,
        message_rowid=_get(ping, "message_rowid"), attached=[a["filename"] for a in attachments],
        pending=pending, links=list(links or []), attempt=attempt, max_attempts=max_attempts, last_error=last_error, now=now,
        legacy=legacy)
    return TurnContext(trigger=trigger, attachments=attachments, media=media, thread=thread, names=names,
                       links=list(links or []))
