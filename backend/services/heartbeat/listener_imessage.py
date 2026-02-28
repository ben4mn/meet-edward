"""
iMessage listener for Edward's heartbeat system.

Polls ~/Library/Messages/chat.db for new messages every 10 seconds.
Writes HeartbeatEventModel rows to PostgreSQL, deduped by source_id.
"""

import asyncio
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

MENTION_PATTERN = re.compile(r"@edward\b", re.IGNORECASE)

from services.database import async_session, HeartbeatEventModel, ExternalContactModel
from services.imessage_service import _recent_edward_sends
from sqlalchemy import select

# Apple epoch offset: seconds between Unix epoch (1970) and Apple epoch (2001)
APPLE_EPOCH_OFFSET = 978307200

_listener_task: asyncio.Task | None = None
_last_seen_rowid: int = 0
_POLL_INTERVAL = 10  # seconds

CHAT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")

# SQL to fetch new messages (include attributedBody for mentions/reactions where text is NULL)
FETCH_MESSAGES_SQL = """
SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me, m.date, m.service,
       h.id AS sender_id, c.chat_identifier, c.display_name
FROM message m
LEFT JOIN handle h ON m.handle_id = h.ROWID
LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
LEFT JOIN chat c ON cmj.chat_id = c.ROWID
WHERE m.ROWID > ? AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL)
      AND m.associated_message_type = 0
ORDER BY m.ROWID ASC
"""


def _extract_text_from_attributed_body(blob: bytes) -> Optional[str]:
    """Extract plain text from an NSAttributedString streamtyped blob.

    iMessage stores some messages (especially those with mentions like @edward)
    only in attributedBody as a binary NSArchiver blob rather than in the text column.
    """
    if not blob:
        return None
    try:
        decoded = blob.decode("utf-8", errors="replace")
        # The text content sits between the NSString marker and the next control sequence.
        # Find the longest readable substring that looks like actual message text.
        parts = re.findall(r"[\x20-\x7e\u00a0-\uffff]{3,}", decoded)
        # Filter out Cocoa class names, internal keys, and garbled fragments
        skip = {"streamtyped", "NSString", "NSObject", "NSAttributedString",
                "NSMutableAttributedString", "NSMutableString", "NSDictionary",
                "NSNumber", "NSValue", "NSMutableData", "NSData", "NSArray",
                "__kIMMessagePartAttributeName"}
        text_parts = []
        for p in parts:
            p = p.strip().strip("\ufffd")  # trim replacement chars from edges
            if not p or len(p) < 2:
                continue
            if any(s in p for s in skip):
                continue
            text_parts.append(p)
        if text_parts:
            # Pick the longest part — that's the actual message text
            best = max(text_parts, key=len)
            # Strip leading +/length prefix byte artifacts (e.g. "+9" before actual text)
            best = re.sub(r"^[+\d]{1,3}", "", best).strip()
            return best if best else None
    except Exception:
        pass
    return None


def _apple_timestamp_to_datetime(apple_ts: int) -> datetime:
    """Convert Apple Core Data timestamp (nanoseconds since 2001-01-01) to UTC datetime."""
    if apple_ts is None:
        return datetime.now(timezone.utc)
    unix_ts = apple_ts / 1e9 + APPLE_EPOCH_OFFSET
    return datetime.fromtimestamp(unix_ts, tz=timezone.utc)


def _query_chat_db(last_rowid: int) -> list[dict]:
    """Query chat.db for new messages. Runs in thread (sqlite3 is sync)."""
    if not Path(CHAT_DB_PATH).exists():
        return []

    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(FETCH_MESSAGES_SQL, (last_rowid,))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            # Prefer text column; fall back to extracting from attributedBody
            text = row["text"]
            if not text and row["attributedBody"]:
                text = _extract_text_from_attributed_body(row["attributedBody"])
            if not text:
                continue  # Skip messages with no extractable text

            results.append({
                "rowid": row["ROWID"],
                "text": text,
                "is_from_me": bool(row["is_from_me"]),
                "date": row["date"],
                "service": row["service"],
                "sender_id": row["sender_id"],
                "chat_identifier": row["chat_identifier"],
                "display_name": row["display_name"],
            })
        return results
    finally:
        conn.close()


def _get_max_rowid() -> int:
    """Get the current max ROWID from chat.db."""
    if not Path(CHAT_DB_PATH).exists():
        return 0
    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
    try:
        cursor = conn.execute("SELECT MAX(ROWID) FROM message")
        row = cursor.fetchone()
        return row[0] or 0
    finally:
        conn.close()


# In-memory cache: phone -> contact name (or None if unknown), resolved once per session
_contact_name_cache: dict[str, Optional[str]] = {}


async def _resolve_contact_name(phone_number: str) -> Optional[str]:
    """Resolve a phone number to a contact name using two-tier lookup + cache."""
    if not phone_number:
        return None

    if phone_number in _contact_name_cache:
        return _contact_name_cache[phone_number]

    # Tier 1: Check ExternalContactModel (fast, DB-only)
    try:
        async with async_session() as session:
            result = await session.execute(
                select(ExternalContactModel.contact_name).where(
                    ExternalContactModel.phone_number == phone_number
                )
            )
            name = result.scalar_one_or_none()
            if name:
                _contact_name_cache[phone_number] = name
                return name
    except Exception:
        pass

    # Tier 2: AppleScript Contacts lookup (slower, runs in thread)
    try:
        from services.contacts_service import lookup_phone

        result = await asyncio.to_thread(lookup_phone, phone_number)
        if result.get("success") and result.get("matches"):
            name = result["matches"][0]["name"]
            _contact_name_cache[phone_number] = name
            return name
    except Exception as e:
        print(f"[Heartbeat] Contact lookup failed for {phone_number}: {e}")

    _contact_name_cache[phone_number] = None
    return None


async def _store_events(messages: list[dict]) -> tuple[int, bool]:
    """Store new message events in PostgreSQL. Returns (count stored, has_mentions)."""
    stored = 0
    has_mentions = False
    async with async_session() as session:
        for msg in messages:
            source_id = f"imessage:{msg['rowid']}"

            # Dedup check
            existing = await session.execute(
                select(HeartbeatEventModel.id).where(
                    HeartbeatEventModel.source_id == source_id
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Check for @edward mention (skip if this is Edward's own sent message)
            text = msg["text"] or ""
            if msg["is_from_me"] and text[:200] in _recent_edward_sends:
                try:
                    _recent_edward_sends.remove(text[:200])
                except ValueError:
                    pass
            elif MENTION_PATTERN.search(text):
                has_mentions = True

            # Resolve contact name
            contact_name = None
            if msg["is_from_me"]:
                contact_name = "Ben"
            elif msg["sender_id"]:
                contact_name = await _resolve_contact_name(msg["sender_id"])

            event = HeartbeatEventModel(
                source="imessage",
                event_type="message_sent" if msg["is_from_me"] else "message_received",
                sender=msg["sender_id"],
                contact_name=contact_name,
                chat_identifier=msg["chat_identifier"],
                chat_name=msg["display_name"],
                summary=text[:200],
                raw_data=json.dumps({
                    "rowid": msg["rowid"],
                    "service": msg["service"],
                    "date": msg["date"],
                    "full_text": msg["text"][:1000] if msg["text"] else None,
                }),
                source_id=source_id,
                is_from_user=msg["is_from_me"],
            )
            session.add(event)
            stored += 1

        if stored:
            await session.commit()
    return stored, has_mentions


async def _poll_once() -> None:
    """Single poll iteration."""
    global _last_seen_rowid

    messages = await asyncio.to_thread(_query_chat_db, _last_seen_rowid)
    if not messages:
        return

    # Update last seen rowid
    max_rowid = max(m["rowid"] for m in messages)
    _last_seen_rowid = max_rowid

    stored, has_mentions = await _store_events(messages)
    if stored:
        print(f"[Heartbeat] iMessage listener: stored {stored} new events (last rowid: {_last_seen_rowid})")

    # @edward mention detected → trigger immediate triage
    if has_mentions:
        print("[Heartbeat] @edward mention detected — triggering immediate triage")
        from services.heartbeat.heartbeat_service import trigger_immediate_triage
        await trigger_immediate_triage()


async def _listener_loop() -> None:
    """Main polling loop."""
    global _last_seen_rowid

    # Seed with current max rowid (don't process historical messages)
    _last_seen_rowid = await asyncio.to_thread(_get_max_rowid)
    print(f"[Heartbeat] iMessage listener started (seeded at rowid {_last_seen_rowid})")

    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[Heartbeat] iMessage poll error: {e}")
        await asyncio.sleep(_POLL_INTERVAL)


async def start_imessage_listener() -> None:
    """Start the iMessage listener."""
    global _listener_task
    if _listener_task is not None:
        return

    if not Path(CHAT_DB_PATH).exists():
        print(f"[Heartbeat] iMessage listener skipped: {CHAT_DB_PATH} not found")
        return

    _listener_task = asyncio.create_task(_listener_loop())


async def stop_imessage_listener() -> None:
    """Stop the iMessage listener."""
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
    print("[Heartbeat] iMessage listener stopped")


def get_chat_thread(chat_identifier: str, limit: int = 15) -> list[dict]:
    """Read recent messages from a specific chat thread in chat.db."""
    if not chat_identifier or not Path(CHAT_DB_PATH).exists():
        return []

    sql = """
    SELECT m.ROWID, m.text, m.attributedBody, m.is_from_me, m.date,
           h.id AS sender_id
    FROM message m
    LEFT JOIN handle h ON m.handle_id = h.ROWID
    LEFT JOIN chat_message_join cmj ON m.ROWID = cmj.message_id
    LEFT JOIN chat c ON cmj.chat_id = c.ROWID
    WHERE c.chat_identifier = ?
          AND (m.text IS NOT NULL OR m.attributedBody IS NOT NULL)
          AND m.associated_message_type = 0
    ORDER BY m.ROWID DESC
    LIMIT ?
    """

    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        cursor = conn.execute(sql, (chat_identifier, limit))
        rows = cursor.fetchall()
        results = []
        for row in rows:
            text = row["text"]
            if not text and row["attributedBody"]:
                text = _extract_text_from_attributed_body(row["attributedBody"])
            if not text:
                continue

            dt = _apple_timestamp_to_datetime(row["date"])
            results.append({
                "sender": row["sender_id"] or "me",
                "text": text,
                "is_from_me": bool(row["is_from_me"]),
                "timestamp": dt,
            })
        # Reverse to chronological order (query was DESC)
        results.reverse()
        return results
    finally:
        conn.close()


def format_chat_thread(messages: list[dict], contact_cache: dict[str, Optional[str]] = None) -> str:
    """Format a chat thread into a readable string for trigger context."""
    if not messages:
        return ""

    now = datetime.now(timezone.utc)
    lines = ["Recent messages in this chat:"]

    for msg in messages:
        # Relative time
        dt = msg["timestamp"]
        delta = now - dt
        seconds = int(delta.total_seconds())
        if seconds < 60:
            time_str = "just now"
        elif seconds < 3600:
            mins = seconds // 60
            time_str = f"{mins} min ago"
        elif seconds < 86400:
            hours = seconds // 3600
            time_str = f"{hours}h ago"
        else:
            days = seconds // 86400
            time_str = f"{days}d ago"

        # Sender display name
        if msg["is_from_me"]:
            sender = "You (Ben)"
        elif contact_cache and msg["sender"] in contact_cache and contact_cache[msg["sender"]]:
            sender = contact_cache[msg["sender"]]
        else:
            sender = msg["sender"]

        lines.append(f"[{time_str}] {sender}: {msg['text']}")

    return "\n".join(lines)


def get_listener_status() -> str:
    """Get the current listener status."""
    if _listener_task is None:
        return "stopped"
    if _listener_task.done():
        return "error"
    return "running"
