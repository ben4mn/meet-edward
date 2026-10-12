"""What Edward sent over iMessage, held in memory (2026-10-11;
docs/architecture/pings.md).

Edward sends from the owner's Apple ID, so every send comes back to the
listener as an is_from_me row. The transport (``imessage_service``) records
here what it is sending and which chat.db row it found for it. The listener
(``heartbeat/listener_imessage``) asks here whether a new from-me row is one
of those sends. Process-wide and thread-safe; ``load`` rebuilds it at start
from imessage_outgoing_sends. Times are aware UTC, the send ledger's
convention.
"""

# ADAPT: Edward sends iMessages as the owner, from the Apple ID this Mac is signed into.
#   How it works here: Messages.app on this Mac is signed into the owner's own Apple
#   ID; Edward's sends come back as is_from_me rows, told apart from the owner's own
#   by the chat.db ROWID the transport reads back (this module), then the signature.
#   To adapt: sign this Mac's Messages into the owner's Apple ID and set
#   EDWARD_OWNER_HANDLES; a separate Apple ID for Edward needs the echo checks reworked.

from __future__ import annotations

import hashlib
import itertools
import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, FrozenSet, Optional, Set

INFLIGHT_MATCH_S = 120     # a from-me row this long after a send started can still be that send
UNCONFIRMED_WINDOW_S = 60  # the self-chat breaker's window after a send whose row was not found

_lock = threading.Lock()
_rowids: Set[int] = set()
_guids: Set[str] = set()
_last_send: Dict[str, datetime] = {}
_unconfirmed: Dict[str, datetime] = {}
_inflight: Dict[int, Dict[str, Any]] = {}
_tokens = itertools.count(1)


def aware_utcnow() -> datetime:
    return datetime.now(timezone.utc)


def normalize(text: Optional[str]) -> str:
    """What two copies of a send are compared on: no signature, whitespace
    collapsed, case folded. Pure."""
    from utils.message_signature import _SIGNATURE_PATTERNS

    t = str(text or "").rstrip()
    for p in _SIGNATURE_PATTERNS:
        t = re.sub(p, "", t, flags=re.IGNORECASE).rstrip()
    return " ".join(t.split()).casefold()


def text_hash(text: Optional[str]) -> str:
    """imessage_outgoing_sends.text_norm_hash. Pure."""
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def _key(chat: Optional[str]) -> str:
    from services.pings.hooks import chat_key

    return chat_key(chat)


def begin(chat: str, text: str, now: Optional[datetime] = None) -> int:
    """The transport, before osascript. Returns a token for ``resolve``/``abandon``."""
    token = next(_tokens)
    with _lock:
        _inflight[token] = {"chat": _key(chat), "norm": normalize(text), "started": now or aware_utcnow(),
                            "rowid": None}
    return token


def claimed_rowid(token: int) -> Optional[int]:
    """The rowid the listener matched to this in-flight send, if it got there first."""
    with _lock:
        entry = _inflight.get(token)
        return entry.get("rowid") if entry else None


def resolve(token: int, *, rowid: Optional[int] = None, guid: Optional[str] = None,
            now: Optional[datetime] = None) -> None:
    """The send went out. ``rowid`` is its chat.db row from the read-back, or
    None when no row was found within the read-back window: the chat is then
    marked unconfirmed for ``UNCONFIRMED_WINDOW_S``."""
    now = now or aware_utcnow()
    with _lock:
        entry = _inflight.pop(token, None)
        if entry is None:
            return
        chat = entry["chat"]
        _last_send[chat] = now
        rowid = rowid if rowid is not None else entry.get("rowid")
        if rowid is not None:
            _rowids.add(int(rowid))
            if guid:
                _guids.add(str(guid))
        else:
            _unconfirmed[chat] = now


def abandon(token: int) -> None:
    """osascript failed: nothing went out."""
    with _lock:
        _inflight.pop(token, None)


def claim_inflight(chat: Optional[str], text: Optional[str], rowid: int,
                   now: Optional[datetime] = None) -> bool:
    """The listener saw a from-me row: is it an unresolved send to this chat
    with this text? If so it is Edward's, and the transport will find the
    row through ``claimed_rowid``."""
    now = now or aware_utcnow()
    key, norm = _key(chat), normalize(text)
    if not key or not norm:
        return False
    with _lock:
        for entry in _inflight.values():
            if (entry["chat"] == key and entry["norm"] == norm and entry["rowid"] is None
                    and (now - entry["started"]).total_seconds() <= INFLIGHT_MATCH_S):
                entry["rowid"] = int(rowid)
                _rowids.add(int(rowid))
                return True
    return False


def add(rowid: Optional[int], guid: Optional[str], chat: Optional[str], at: Optional[datetime]) -> None:
    with _lock:
        if rowid is not None:
            _rowids.add(int(rowid))
        if guid:
            _guids.add(str(guid))
        key = _key(chat)
        if key and at is not None and (key not in _last_send or at > _last_send[key]):
            _last_send[key] = at


def is_edward_rowid(rowid: Optional[int]) -> bool:
    with _lock:
        return rowid is not None and int(rowid) in _rowids


def edward_guids() -> FrozenSet[str]:
    with _lock:
        return frozenset(_guids)


def last_send(chat: Optional[str]) -> Optional[datetime]:
    with _lock:
        return _last_send.get(_key(chat))


def unconfirmed_recently(chat: Optional[str], now: Optional[datetime] = None,
                         window_s: float = UNCONFIRMED_WINDOW_S) -> bool:
    """An Edward send into this chat in the last ``window_s`` whose row was
    never found: the self-chat breaker's condition."""
    now = now or aware_utcnow()
    with _lock:
        at = _unconfirmed.get(_key(chat))
    return at is not None and (now - at).total_seconds() <= window_s


async def load(days: int = 30) -> int:
    """Rebuild from the ledger's ok rows. The rows it read; 0 on any error."""
    from sqlalchemy import text as sql

    from services.database import async_session

    since = aware_utcnow() - timedelta(days=days)
    try:
        async with async_session() as session:
            rows = (await session.execute(sql(
                "SELECT message_rowid, message_guid, chat_identifier, sent_at FROM imessage_outgoing_sends "
                "WHERE ok IS TRUE AND sent_at >= :since"), {"since": since})).all()
    except Exception:  # noqa: BLE001 - echo detection falls back to the hash and the signature
        return 0
    for rowid, guid, chat, at in rows:
        add(rowid, guid, chat, at)
    return len(rows)


def reset_for_tests() -> None:
    with _lock:
        _rowids.clear()
        _guids.clear()
        _last_send.clear()
        _unconfirmed.clear()
        _inflight.clear()
