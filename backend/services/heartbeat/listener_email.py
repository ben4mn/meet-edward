"""
Email listener for Edward's heartbeat system.

Polls Apple Mail's Envelope Index SQLite database for recent unread emails.
Same approach as listener_imessage.py — reads the DB directly for speed.
Writes HeartbeatEventModel rows to PostgreSQL, deduped by source_id.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from services.database import async_session, HeartbeatEventModel

MENTION_PATTERN = re.compile(r"@edward\b", re.IGNORECASE)

# Mail.app Envelope Index path
MAIL_DB_PATH = os.path.expanduser(
    "~/Library/Mail/V10/MailData/Envelope Index"
)

# Track the highest ROWID we've seen to only fetch new messages
_last_rowid: int = 0
_listener_task: asyncio.Task | None = None
_poll_interval: int = 300
_fetch_limit: int = 20

# INBOX mailbox ROWIDs — resolved once at startup
_inbox_rowids: list[int] = []


def _make_source_id(rowid: int) -> str:
    """Generate a dedup source_id from Mail message ROWID."""
    return f"email:{rowid}"


def _resolve_inbox_rowids(conn: sqlite3.Connection) -> list[int]:
    """Query INBOX mailbox ROWIDs. Called once during startup."""
    try:
        cursor = conn.execute(
            "SELECT ROWID FROM mailboxes WHERE url LIKE '%/INBOX'"
        )
        return [row[0] for row in cursor.fetchall()]
    except Exception as e:
        print(f"[Heartbeat] Email listener: failed to resolve INBOX mailboxes: {e}")
        return []


async def _seed_last_rowid() -> None:
    """Set _last_rowid to current max so we only capture new emails going forward."""
    global _last_rowid, _inbox_rowids

    if not os.path.exists(MAIL_DB_PATH):
        return

    try:
        conn = sqlite3.connect(f"file:{MAIL_DB_PATH}?mode=ro", uri=True)
        try:
            cursor = conn.execute("SELECT MAX(ROWID) FROM messages")
            row = cursor.fetchone()
            if row and row[0]:
                _last_rowid = row[0]

            # Resolve INBOX mailbox ROWIDs once
            _inbox_rowids = _resolve_inbox_rowids(conn)
            if _inbox_rowids:
                print(f"[Heartbeat] Email listener: resolved {len(_inbox_rowids)} INBOX mailbox(es)")
            else:
                print("[Heartbeat] Email listener: no INBOX mailboxes found, will poll all mailboxes")
        finally:
            conn.close()
    except Exception as e:
        print(f"[Heartbeat] Email listener: seed error: {e}")


def _query_mail_db(last_rowid: int, limit: int, inbox_rowids: list[int] | None = None) -> list[dict]:
    """Query Mail Envelope Index for new unread messages. Runs in thread (sqlite3 is sync)."""
    conn = sqlite3.connect(f"file:{MAIL_DB_PATH}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        # Build INBOX filter clause
        inbox_clause = ""
        params: list = [last_rowid]
        if inbox_rowids:
            placeholders = ",".join("?" for _ in inbox_rowids)
            inbox_clause = f"AND m.mailbox IN ({placeholders})"
            params.extend(inbox_rowids)
        params.append(limit)

        cursor = conn.execute(
            f"""
            SELECT m.ROWID, s.subject, a.address as sender,
                   m.date_received, m.read,
                   m.unsubscribe_type, m.automated_conversation,
                   mgd.model_category, mgd.model_subcategory
            FROM messages m
            LEFT JOIN subjects s ON m.subject = s.ROWID
            LEFT JOIN addresses a ON m.sender = a.ROWID
            LEFT JOIN message_global_data mgd ON m.message_id = mgd.message_id
            WHERE m.ROWID > ?
              AND m.read = 0
              {inbox_clause}
            ORDER BY m.ROWID ASC
            LIMIT ?
            """,
            params,
        )
        return [dict(row) for row in cursor.fetchall()]
    finally:
        conn.close()


async def _poll_once() -> None:
    """Single poll iteration — fetch recent unread emails from Mail.app database."""
    global _last_rowid

    if not os.path.exists(MAIL_DB_PATH):
        return

    try:
        rows = await asyncio.to_thread(_query_mail_db, _last_rowid, _fetch_limit, _inbox_rowids or None)
    except Exception as e:
        print(f"[Heartbeat] Email poll DB error: {e}")
        return

    if not rows:
        return

    stored = 0
    has_mentions = False
    max_rowid = _last_rowid

    async with async_session() as session:
        for row in rows:
            rowid = row["ROWID"]
            subject = row["subject"] or "(no subject)"
            sender = row["sender"] or "Unknown"
            date_received = row["date_received"] or 0

            if rowid > max_rowid:
                max_rowid = rowid

            source_id = _make_source_id(rowid)

            # Dedup check
            existing = await session.execute(
                select(HeartbeatEventModel.id).where(
                    HeartbeatEventModel.source_id == source_id
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Convert Unix timestamp to ISO string
            try:
                dt = datetime.fromtimestamp(date_received, tz=timezone.utc)
                date_str = dt.isoformat()
            except (OSError, ValueError):
                date_str = ""

            summary = f"From {sender}: {subject}"

            # Check for @edward mention in subject
            if MENTION_PATTERN.search(subject):
                has_mentions = True

            hb_event = HeartbeatEventModel(
                source="email",
                event_type="email_received",
                sender=sender,
                contact_name=None,
                chat_identifier=None,
                chat_name=None,
                summary=summary[:200],
                raw_data=json.dumps({
                    "rowid": rowid,
                    "subject": subject,
                    "sender": sender,
                    "date_received": date_str,
                    "model_category": row.get("model_category"),
                    "model_subcategory": row.get("model_subcategory"),
                    "unsubscribe_type": row.get("unsubscribe_type"),
                    "automated_conversation": row.get("automated_conversation"),
                }),
                source_id=source_id,
                is_from_user=False,
            )
            session.add(hb_event)
            stored += 1

        if stored:
            await session.commit()
            print(f"[Heartbeat] Email listener: stored {stored} new unread emails")

    _last_rowid = max_rowid

    # @edward mention detected → trigger immediate triage
    if has_mentions:
        print("[Heartbeat] @edward mention in email detected — triggering immediate triage")
        from services.heartbeat.heartbeat_service import trigger_immediate_triage
        await trigger_immediate_triage()


async def _listener_loop() -> None:
    """Main polling loop."""
    await _seed_last_rowid()
    print(f"[Heartbeat] Email listener started (poll every {_poll_interval}s, seeded at rowid {_last_rowid})")

    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[Heartbeat] Email poll error: {e}")
        await asyncio.sleep(_poll_interval)


async def start_email_listener(config) -> None:
    """Start the email listener with config-driven poll interval."""
    global _listener_task, _poll_interval, _fetch_limit

    if _listener_task is not None:
        return

    if not os.path.exists(MAIL_DB_PATH):
        print(f"[Heartbeat] Email listener skipped: Mail database not found at {MAIL_DB_PATH}")
        return

    _poll_interval = getattr(config, "email_poll_seconds", 300)
    _fetch_limit = 20

    _listener_task = asyncio.create_task(_listener_loop())


async def stop_email_listener() -> None:
    """Stop the email listener."""
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
    print("[Heartbeat] Email listener stopped")


def get_email_listener_status() -> str:
    """Get the current listener status."""
    if _listener_task is None:
        return "stopped"
    if _listener_task.done():
        return "error"
    return "running"
