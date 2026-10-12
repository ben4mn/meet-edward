"""The one door into pings for code outside services/pings (AREA 3, 2026-10-11).

The iMessage listener, the send transport, AREA 2's tool loop, ``main.py``,
the health report and AREA 1's gate import only this module. It holds what
they share: the current-ping context that the transport and the tool loop
write into, and the row insert the listener runs inside its own
transaction. It reaches the runner lazily, so a caller never breaks when the
runner is off (EDWARD_PINGS=off) or not merged yet.

The runner and the other ping modules build on it and never copy it.
"""

from __future__ import annotations

import contextvars
import importlib
import logging
import os
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

# Kinds a turn must answer. A friend's inline reply to Edward's bubble is
# may-answer: to friends that bubble came from the owner's number.
MUST_ANSWER_KINDS = ("mention", "owner_direct")
TEXT_MAX = 10_000
_WIDTHS = {"channel": 20, "kind": 20, "status": 20, "source_ref": 255, "chat_identifier": 255,
           "chat_guid": 255, "chat_name": 255, "sender": 255, "sender_label": 255, "message_guid": 80}

# The ping a turn is answering. The runner sets a fresh dict (``new_context``)
# around the reply turn. asyncio.to_thread and create_task copy the context,
# so the send transport (in its worker thread) and AREA 2's tool loop mutate
# the SAME dict, and the runner reads what happened without waiting for a
# ledger write.
CURRENT_PING: "contextvars.ContextVar[Optional[Dict[str, Any]]]" = contextvars.ContextVar(
    "current_ping", default=None)
_LOCK = threading.Lock()


def enabled() -> bool:
    """Whether the runner answers pings. Detection and the pings rows happen either way."""
    return os.getenv("EDWARD_PINGS", "on").strip().lower() not in ("off", "0", "false", "no")


def utcnow() -> datetime:
    """Naive UTC, the convention of the pings and media_items columns. The
    send ledger (imessage_outgoing_sends) is timestamptz: use aware UTC there."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def must_answer(kind: str, from_owner: bool) -> bool:
    """Pure. The owner's inline reply to Edward's bubble cannot be meant for the owner."""
    return kind in MUST_ANSWER_KINDS or (kind == "reply_to_edward" and bool(from_owner))


def chat_key(chat: Optional[str]) -> str:
    """One spelling per chat: the tail of an AppleScript or chat.db guid
    ("any;+;chat123" -> "chat123"), "p:" plus a phone's last ten digits, else
    lowercased. Pure."""
    t = str(chat or "").strip()
    if ";" in t:
        t = t.rsplit(";", 1)[1].strip()
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 10 and all(ch in "0123456789+()-. " for ch in t):
        return "p:" + digits[-10:]
    return t.lower()


# ------------------------------------------------ the current ping ----

def new_context(*, ping_id: int, chat_identifier: str, chat_guid: Optional[str], is_group: bool,
                conversation_id: str, must_answer: bool, third_party: bool,
                kind: Optional[str] = None) -> Dict[str, Any]:
    """``kind`` is the ping's (``owner_direct`` is the owner's chat with
    themselves: the only chat where what is said is for the owner alone)."""
    return {"ping_id": int(ping_id), "chat_identifier": chat_identifier, "chat_guid": chat_guid,
            "is_group": bool(is_group), "conversation_id": conversation_id, "must_answer": bool(must_answer),
            "third_party": bool(third_party), "kind": kind, "inflight": 0, "sends": [], "tools": [], "stop": {},
            "media": {"images": 0, "bytes": 0}}


def current_ping() -> Optional[Dict[str, Any]]:
    return CURRENT_PING.get()


def same_chat(ctx: Optional[Dict[str, Any]], chat: Optional[str]) -> bool:
    if not ctx:
        return False
    key = chat_key(chat)
    return bool(key) and key in (chat_key(ctx.get("chat_identifier")), chat_key(ctx.get("chat_guid")))


def note_send_start(chat: Optional[str]) -> Optional[Dict[str, Any]]:
    """The transport, before osascript: count a send into the current ping's
    chat as in flight. Returns the context when it counted; pass it on."""
    ctx = CURRENT_PING.get()
    if not same_chat(ctx, chat):
        return None
    with _LOCK:
        ctx["inflight"] += 1
    return ctx


def note_send_end(ctx: Optional[Dict[str, Any]], *, ok: bool, purpose: str = "tool",
                  rowid: Optional[int] = None, route: Optional[str] = None, error: Optional[str] = None) -> None:
    """The transport, synchronously, after the send and its read-back and
    before it returns. Sync; never raises."""
    if ctx is None:
        return
    with _LOCK:
        ctx["inflight"] = max(0, int(ctx.get("inflight", 0)) - 1)
        ctx["sends"].append({"ok": bool(ok), "purpose": purpose, "rowid": rowid, "route": route,
                             "error": error, "at": utcnow()})


def note_tool(name: str, args: Any, ok: bool) -> None:
    """AREA 2's executor, after every tool call, beside turn_ledger's note. Sync."""
    ctx = CURRENT_PING.get()
    if ctx is None:
        return
    a = args if isinstance(args, dict) else {}
    with _LOCK:
        ctx["tools"].append({"name": str(name), "action": a.get("action"), "chat": a.get("chat_id") or a.get("to"),
                             "urgent": bool(a.get("urgent")), "ok": bool(ok)})


def note_stop(**fields: Any) -> None:
    """AREA 2's loop, as the turn ends: ``outcome`` (answered | honest_ending |
    deadline | turn_budget — the turn's own dollar cap, never the day's),
    ``stop_reason``, ``incomplete_reason``, ``error_class``, ``retryable``.
    Sync."""
    ctx = CURRENT_PING.get()
    if ctx is None:
        return
    with _LOCK:
        ctx["stop"].update({k: v for k, v in fields.items() if v is not None})


# ------------------------------------------------------ the row ----

def brief_error(exc: BaseException) -> str:
    """The error class and its first line only: a database error's DETAIL
    line carries the row, and a row carries someone's message."""
    orig = getattr(exc, "orig", None) or exc
    first = (str(orig).splitlines() or [""])[0]
    return f"{type(exc).__name__}: {first[:200]}"


def _clip(value: Any, width: Optional[int]) -> Any:
    if value is None or width is None:
        return value
    return str(value)[:width]


async def create_ping(session, *, source_ref: str, channel: str, kind: str, from_owner: bool,
                      third_party: bool, sender_known: bool, asked_at: datetime, status: str = "open",
                      heartbeat_event_id: Optional[str] = None, message_rowid: Optional[int] = None,
                      message_guid: Optional[str] = None, chat_identifier: Optional[str] = None,
                      chat_guid: Optional[str] = None, chat_name: Optional[str] = None, is_group: bool = False,
                      sender: Optional[str] = None, sender_label: Optional[str] = None,
                      text: Optional[str] = None) -> Optional[int]:
    """The ping row for one message, inside the caller's transaction under a
    SAVEPOINT; its id, or None. A failure rolls back only the savepoint, so
    the caller's commit (the message itself) still lands, and the caller
    treats the message as an ordinary event: the mention wake answers a
    mention. Idempotent by ``source_ref``. Never raises."""
    from sqlalchemy import text as sql

    now = utcnow()
    params = {
        "source_ref": source_ref, "channel": channel, "kind": kind, "status": status,
        "must_answer": must_answer(kind, from_owner), "from_owner": bool(from_owner), "third_party": bool(third_party),
        "sender_known": bool(sender_known), "event_id": heartbeat_event_id, "rowid": message_rowid,
        "message_guid": message_guid, "chat_identifier": chat_identifier, "chat_guid": chat_guid,
        "chat_name": chat_name, "is_group": bool(is_group), "sender": sender, "sender_label": sender_label,
        "text": (text or "")[:TEXT_MAX], "asked_at": asked_at, "now": now,
    }
    params = {k: _clip(v, _WIDTHS.get(k)) for k, v in params.items()}
    try:
        async with session.begin_nested():
            pid = (await session.execute(sql(
                "INSERT INTO pings (source_ref, channel, kind, status, must_answer, from_owner, third_party, "
                "sender_known, heartbeat_event_id, message_rowid, message_guid, chat_identifier, chat_guid, "
                "chat_name, is_group, sender, sender_label, text, attempts, next_attempt_at, asked_at, "
                "detected_at, updated_at) VALUES (:source_ref, :channel, :kind, :status, :must_answer, "
                ":from_owner, :third_party, :sender_known, :event_id, :rowid, :message_guid, :chat_identifier, "
                ":chat_guid, :chat_name, :is_group, :sender, :sender_label, :text, 0, :now, :asked_at, :now, "
                ":now) ON CONFLICT (source_ref) DO NOTHING RETURNING id"), params)).scalar_one_or_none()
            if pid is None:
                pid = (await session.execute(sql("SELECT id FROM pings WHERE source_ref = :source_ref"),
                                             {"source_ref": params["source_ref"]})).scalar_one_or_none()
        return int(pid) if pid is not None else None
    except Exception as exc:  # noqa: BLE001 - the message must still be stored
        logger.warning("pings: could not record the ping for %s: %s", source_ref, brief_error(exc))
        return None


# ------------------------------------------------- reads (async) ----

async def addressed_recently(chat_id: str, hours: float = 24) -> bool:
    """Did this chat ping Edward (any kind, any status but ``held``) within
    ``hours``? AREA 2's recipient rule (b) and AREA 1's gate rule 10 read it.
    The rows are written whatever EDWARD_PINGS says. False on any error."""
    key = chat_key(chat_id)
    if not key:
        return False
    try:
        from sqlalchemy import text as sql

        from services.database import async_session

        since = utcnow() - timedelta(hours=float(hours))
        async with async_session() as session:
            rows = (await session.execute(sql(
                "SELECT DISTINCT chat_identifier, chat_guid FROM pings "
                "WHERE asked_at >= :since AND status <> 'held'"), {"since": since})).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: addressed_recently failed: %s", exc)
        return False
    return any(key in (chat_key(c), chat_key(g)) for c, g in rows)


async def open_chats() -> Set[str]:
    """Chat keys with a ping open or running. AREA 1 opens no speak turn there. Empty on error."""
    try:
        from sqlalchemy import text as sql

        from services.database import async_session

        async with async_session() as session:
            rows = (await session.execute(sql(
                "SELECT DISTINCT chat_identifier FROM pings WHERE status IN ('open', 'running')"))).all()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: open_chats failed: %s", exc)
        return set()
    return {chat_key(c) for (c,) in rows if c}


# ------------------------------------------- the runner, lazily ----

def _mod(name: str):
    try:
        return importlib.import_module(f"services.pings.{name}")
    except ImportError:
        return None


def kick() -> None:
    """Tell the runner a ping is waiting (call after the caller's commit)."""
    runner = _mod("runner") if enabled() else None
    if runner is None:
        return
    try:
        runner.kick()
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: kick failed: %s", exc)


def chats_in_flight() -> Set[str]:
    """Chat keys with a ping turn running in this process right now. Sync."""
    runner = _mod("runner")
    try:
        return set(runner.chats_in_flight()) if runner is not None else set()
    except Exception:  # noqa: BLE001
        return set()


async def start() -> None:
    runner = _mod("runner") if enabled() else None
    if runner is not None:
        await runner.start()


async def stop() -> None:
    runner = _mod("runner")
    if runner is not None:
        await runner.stop()


async def pack_lines(now: Optional[datetime] = None, hours: float = 6) -> List[str]:
    """Lines for the mind's pack: recent pings and how each ended."""
    store = _mod("store")
    if store is None:
        return []
    try:
        return list(await store.pack_lines(now or utcnow(), hours=hours))
    except Exception as exc:  # noqa: BLE001
        logger.warning("pings: pack_lines failed: %s", exc)
        return []


async def report(days: int) -> Dict[str, Any]:
    store = _mod("store")
    if store is None:
        return {"error": "pings not installed"}
    return await store.report(days)
