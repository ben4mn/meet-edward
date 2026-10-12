"""
Email listener for Edward's heartbeat system.

Polls Apple Mail's Envelope Index SQLite database for recent unread emails.
Same approach as listener_imessage.py — reads the DB directly for speed.
Writes HeartbeatEventModel rows to PostgreSQL, deduped by source_id, and
nothing else: an ``@edward`` subject kicks no immediate triage — anyone can
write one, and only an iMessage can ask Edward anything (2026-10-11).

Dark from 2026-06-21 to 2026-10-11 (``stored`` was assigned after a
``return``). Fixed with three rules (integrations review, 2026-10-11): a
message received more than 24 h ago is skipped while the mark still moves
past it (Mail.app was once down for four weeks and downloads old mail under
new ROWIDs — the rule always applies, so first sight after an outage is
covered); one poll drains the backlog page by page, saving the mark after
each; and ``listener_state()`` says when the last clean poll was, for the
health report.
"""

import asyncio
import hashlib
import json
import os
import re
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update

from services.database import async_session, HeartbeatEventModel, HeartbeatConfigModel
from services.contact_identity_service import (
    append_identity_raw_data,
    resolve_contact_identity,
)

# Mail.app Envelope Index path
# ADAPT: iCloud (and any Mail.app account's) mail is read from Mail.app's own Envelope Index.
#   How it works here: Mail.app stays running on the host Mac so the index stays fresh, and the backend's
#   python holds Full Disk Access (System Settings → Privacy & Security) so it can open ~/Library/Mail.
#   To adapt: grant Full Disk Access to the python that runs the backend and keep Mail.app open; if your
#   macOS keeps the index elsewhere (the V10 folder changes between releases) set EDWARD_MAIL_ENVELOPE_INDEX.
MAIL_DB_PATH = os.path.expanduser(
    os.getenv("EDWARD_MAIL_ENVELOPE_INDEX") or "~/Library/Mail/V10/MailData/Envelope Index"
)

# Track the highest ROWID we've seen to only fetch new messages
_last_rowid: int = 0
_listener_task: asyncio.Task | None = None
_poll_interval: int = 300
_fetch_limit: int = 20

# INBOX mailbox ROWIDs — resolved once at startup
_inbox_rowids: list[int] = []

# Mail received longer ago than this is not news: skipped, the mark moves on.
STALE_AFTER_S = 24 * 3600
# Pages of ``_fetch_limit`` rows one poll may drain (a backlog after an outage).
MAX_PAGES_PER_POLL = 50

# What the health report reads (``listener_state``).
_last_ok_at: Optional[datetime] = None
_last_error: Optional[str] = None
_last_stats: dict = {}
_started_at: Optional[datetime] = None


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


async def _load_persisted_rowid() -> int | None:
    """Load the persisted email_last_rowid from the database."""
    try:
        async with async_session() as session:
            result = await session.execute(
                select(HeartbeatConfigModel.email_last_rowid).where(
                    HeartbeatConfigModel.id == "default"
                )
            )
            return result.scalar_one_or_none()
    except Exception as e:
        print(f"[Heartbeat] Email listener: failed to load persisted rowid: {e}")
        return None


async def _persist_rowid(rowid: int) -> None:
    """Persist the current high-water mark to the database."""
    try:
        async with async_session() as session:
            await session.execute(
                update(HeartbeatConfigModel)
                .where(HeartbeatConfigModel.id == "default")
                .values(email_last_rowid=rowid)
            )
            await session.commit()
    except Exception as e:
        print(f"[Heartbeat] Email listener: failed to persist rowid: {e}")


async def _seed_last_rowid() -> None:
    """Set _last_rowid to current max so we only capture new emails going forward."""
    global _last_rowid, _inbox_rowids

    # Try loading persisted value first (survives restarts)
    stored = await _load_persisted_rowid()
    if stored and stored > 0:
        _last_rowid = stored
        print(f"[Heartbeat] Email listener: restored rowid {_last_rowid} from database")
        # Still need to resolve inbox mailbox ROWIDs
        if os.path.exists(MAIL_DB_PATH):
            try:
                conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
                conn.execute("PRAGMA query_only = ON")
                try:
                    _inbox_rowids = _resolve_inbox_rowids(conn)
                finally:
                    conn.close()
            except Exception:
                pass
        return

    # Fall back to seeding from Mail.app database
    if not os.path.exists(MAIL_DB_PATH):
        return

    try:
        conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
        conn.execute("PRAGMA query_only = ON")
        try:
            cursor = conn.execute("SELECT MAX(ROWID) FROM messages")
            row = cursor.fetchone()
            if row and row[0]:
                _last_rowid = row[0]
                await _persist_rowid(_last_rowid)

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
    conn = sqlite3.connect(f"file:{MAIL_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
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


async def _store_page(rows: list[dict], stale_before: float) -> tuple[int, int, int]:
    """Store one page's fresh rows (deduped by source_id). Returns
    ``(stored, skipped_stale, max_rowid)``; every row counts toward the
    mark, stored or not."""
    stored = 0
    skipped = 0
    max_rowid = _last_rowid

    async with async_session() as session:
        for row in rows:
            rowid = row["ROWID"]
            subject = row["subject"] or "(no subject)"
            sender = row["sender"] or "Unknown"
            date_received = row["date_received"] or 0

            if rowid > max_rowid:
                max_rowid = rowid

            if date_received < stale_before:
                skipped += 1
                continue

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

            raw_payload = {
                "rowid": rowid,
                "subject": subject,
                "sender": sender,
                "date_received": date_str,
                "model_category": row.get("model_category"),
                "model_subcategory": row.get("model_subcategory"),
                "unsubscribe_type": row.get("unsubscribe_type"),
                "automated_conversation": row.get("automated_conversation"),
                "links": re.findall(r"https?://\S+", subject or "")[:8],
            }
            raw_data = json.dumps(raw_payload)
            identity = await resolve_contact_identity(
                sender=sender,
                chat_name=subject,
                source="email",
                raw_data=raw_data,
            )
            raw_data = append_identity_raw_data(raw_data, identity)
            event_id = str(uuid.uuid4())
            hb_event = HeartbeatEventModel(
                id=event_id,
                source="email",
                event_type="email_received",
                sender=sender,
                contact_name=None,
                person_key=identity.person_key,
                display_label=identity.display_label,
                identity_provenance=identity.provenance,
                chat_identifier=None,
                chat_name=None,
                summary=summary[:200],
                raw_data=raw_data,
                source_id=source_id,
                is_from_user=False,
            )
            session.add(hb_event)
            stored += 1

        if stored:
            await session.commit()
    return stored, skipped, max_rowid


async def _poll_once() -> None:
    """One poll: drain new unread mail page by page (oldest ROWID first),
    store what arrived in the last 24 h, move the mark past everything."""
    global _last_rowid, _last_ok_at, _last_error, _last_stats

    if not os.path.exists(MAIL_DB_PATH):
        _last_error = "Mail's Envelope Index isn't there"
        return

    now = datetime.now(timezone.utc)
    stale_before = now.timestamp() - STALE_AFTER_S
    stored = skipped = pages = 0
    try:
        while pages < MAX_PAGES_PER_POLL:
            rows = await asyncio.to_thread(_query_mail_db, _last_rowid, _fetch_limit, _inbox_rowids or None)
            pages += 1
            if not rows:
                break
            page_stored, page_skipped, max_rowid = await _store_page(rows, stale_before)
            stored += page_stored
            skipped += page_skipped
            if max_rowid > _last_rowid:
                _last_rowid = max_rowid
                await _persist_rowid(_last_rowid)
            if len(rows) < _fetch_limit:
                break
    except Exception as e:  # noqa: BLE001 — recorded for the health report
        _last_error = f"{type(e).__name__}: {e}"[:200]
        print(f"[Heartbeat] Email poll error: {e}")
        return

    _last_ok_at = now
    _last_error = None
    _last_stats = {"stored": stored, "skipped_stale": skipped, "pages": pages, "mark": _last_rowid}
    if stored or skipped:
        print(f"[Heartbeat] Email listener: stored {stored}, skipped {skipped} older than 24 h "
              f"(mark → {_last_rowid})")


def listener_state() -> dict:
    """The listener as the health report sees it: running or not, the last
    clean poll (UTC ISO), the last error and what that poll did."""
    return {
        "status": get_email_listener_status(),
        "last_ok_at": _last_ok_at.isoformat() if _last_ok_at else None,
        "started_at": _started_at.isoformat() if _started_at else None,
        "last_error": _last_error,
        "last_poll": dict(_last_stats),
        "mark": _last_rowid,
    }


async def _listener_loop() -> None:
    """Main polling loop."""
    await _seed_last_rowid()

    if _last_rowid == 0:
        print("[Heartbeat] Email listener: seed returned rowid=0, refusing to poll (would fetch all unread). Stopping.")
        return

    print(f"[Heartbeat] Email listener started (poll every {_poll_interval}s, seeded at rowid {_last_rowid})")

    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[Heartbeat] Email poll error: {e}")
        await asyncio.sleep(_poll_interval)


async def start_email_listener(config) -> None:
    """Start the email listener with config-driven poll interval."""
    global _listener_task, _poll_interval, _started_at, _fetch_limit

    if _listener_task is not None:
        return

    if not os.path.exists(MAIL_DB_PATH):
        print(f"[Heartbeat] Email listener skipped: Mail database not found at {MAIL_DB_PATH}")
        return

    _poll_interval = getattr(config, "email_poll_seconds", 300)
    _fetch_limit = 20

    _started_at = datetime.now(timezone.utc)
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
