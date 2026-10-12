"""
Gmail listener for Edward's heartbeat system.

Polls Gmail's history API for new inbox messages.
Writes HeartbeatEventModel rows to PostgreSQL, deduped by source_id, and
nothing else: an ``@edward`` subject kicks no immediate triage — anyone can
write one, and only an iMessage can ask Edward anything (2026-10-11).

Fixed 2026-10-11 (integrations review): ``stored`` sat after a ``return``
since 2026-06-21; a message more than 24 h old (``internalDate``) is
skipped while the mark still advances; an expired ``historyId`` re-seeds
and stores nothing (it used to read as an empty inbox forever); and
``listener_state()`` says when the last clean poll was. The listener runs
when "watch my inbox" is on and Gmail is connected (``ensure_started``:
the connect callback, the Settings switch and startup).
"""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from services.database import async_session, HeartbeatEventModel
from services.contact_identity_service import (
    append_identity_raw_data,
    resolve_contact_identity,
)

_last_history_id: str = ""
_listener_task: asyncio.Task | None = None
_poll_interval: int = 120

# Mail received longer ago than this is not news: skipped, the mark moves on.
STALE_AFTER_S = 24 * 3600

# What the health report reads (``listener_state``).
_last_ok_at: Optional[datetime] = None
_last_error: Optional[str] = None
_last_stats: dict = {}
_started_at: Optional[datetime] = None


def _make_source_id(gmail_id: str) -> str:
    """Generate a dedup source_id from Gmail message ID."""
    return f"gmail:{gmail_id}"


async def _seed_last_history_id() -> None:
    """Set _last_history_id to current so we only capture new emails going forward."""
    global _last_history_id

    from services.gmail_service import get_last_history_id, get_current_history_id, update_last_history_id

    # Try stored history ID first
    stored = await get_last_history_id()
    if stored:
        _last_history_id = stored
        print(f"[Heartbeat] Gmail listener: restored history_id {_last_history_id}")
        return

    # Seed from Gmail profile
    current = await get_current_history_id()
    if current:
        _last_history_id = current
        await update_last_history_id(current)
        print(f"[Heartbeat] Gmail listener: seeded history_id {_last_history_id}")
    else:
        print("[Heartbeat] Gmail listener: could not seed history_id")


async def _poll_once() -> None:
    """Single poll iteration — fetch new inbox messages via Gmail history API."""
    global _last_history_id, _last_ok_at, _last_error, _last_stats

    if not _last_history_id:
        await _seed_last_history_id()
        return

    from services import gmail_service

    now = datetime.now(timezone.utc)
    try:
        new_messages = await gmail_service.get_history(_last_history_id)
    except gmail_service.GmailHistoryExpired as e:
        current = await gmail_service.get_current_history_id()
        if not current:
            # Not a clean poll: the mark is still the expired one.
            _last_error = f"history expired and couldn't re-seed ({e})"[:200]
            print(f"[Heartbeat] Gmail listener: history expired ({e}) and couldn't re-seed — will try again")
            return
        _last_history_id = current
        await gmail_service.update_last_history_id(current)
        _last_ok_at, _last_error = now, None
        _last_stats = {"stored": 0, "skipped_stale": 0, "reseeded": True}
        print(f"[Heartbeat] Gmail listener: history expired ({e}) — re-seeded at {_last_history_id}, stored nothing")
        return
    except Exception as e:  # noqa: BLE001 — recorded for the health report
        _last_error = f"{type(e).__name__}: {e}"[:200]
        print(f"[Heartbeat] Gmail poll error: {e}")
        return

    stored = 0
    skipped = 0
    stale_before_ms = (now.timestamp() - STALE_AFTER_S) * 1000

    if new_messages:
        async with async_session() as session:
            for msg in new_messages:
                msg_id = msg["message_id"]
                source_id = _make_source_id(msg_id)

                # Dedup check
                existing = await session.execute(
                    select(HeartbeatEventModel.id).where(
                        HeartbeatEventModel.source_id == source_id
                    )
                )
                if existing.scalar_one_or_none():
                    continue

                # Fetch metadata for this message
                metadata = await gmail_service.get_message_metadata(msg_id)
                if not metadata:
                    continue

                internal_date = metadata.get("internal_date")
                if internal_date and internal_date < stale_before_ms:
                    skipped += 1
                    continue

                sender = metadata.get("sender", "Unknown")
                subject = metadata.get("subject", "(no subject)")
                snippet = metadata.get("snippet", "")
                labels = metadata.get("labels", [])

                summary = f"From {sender}: {subject}"

                raw_payload = {
                    "gmail_id": msg_id,
                    "subject": subject,
                    "sender": sender,
                    "snippet": snippet,
                    "labels": labels,
                    "links": metadata.get("links", [])[:8] if isinstance(metadata.get("links"), list) else [],
                    "has_unsubscribe": metadata.get("has_unsubscribe", False),
                }
                raw_data = json.dumps(raw_payload)
                identity = await resolve_contact_identity(
                    sender=sender,
                    chat_name=subject,
                    source="gmail",
                    raw_data=raw_data,
                )
                raw_data = append_identity_raw_data(raw_data, identity)
                event_id = str(uuid.uuid4())
                hb_event = HeartbeatEventModel(
                    id=event_id,
                    source="gmail",
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

        # Update history ID — the current profile history ID, since we've processed everything
        new_history_id = await gmail_service.get_current_history_id()
        if new_history_id:
            _last_history_id = new_history_id
            await gmail_service.update_last_history_id(new_history_id)

    _last_ok_at, _last_error = now, None
    _last_stats = {"stored": stored, "skipped_stale": skipped}
    if stored or skipped:
        print(f"[Heartbeat] Gmail listener: stored {stored}, skipped {skipped} older than 24 h")


def listener_state() -> dict:
    """The listener as the health report sees it."""
    return {
        "status": get_gmail_listener_status(),
        "last_ok_at": _last_ok_at.isoformat() if _last_ok_at else None,
        "started_at": _started_at.isoformat() if _started_at else None,
        "last_error": _last_error,
        "last_poll": dict(_last_stats),
    }


async def _listener_loop() -> None:
    """Main polling loop."""
    await _seed_last_history_id()
    print(f"[Heartbeat] Gmail listener started (poll every {_poll_interval}s)")

    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[Heartbeat] Gmail poll error: {e}")
        await asyncio.sleep(_poll_interval)


async def start_gmail_listener(config) -> None:
    """Start the Gmail listener with config-driven poll interval."""
    global _listener_task, _poll_interval, _started_at

    if _listener_task is not None:
        return

    # Check if Gmail is configured
    from services.gmail_service import is_configured
    if not await is_configured():
        print("[Heartbeat] Gmail listener skipped: not configured")
        return

    _poll_interval = getattr(config, "gmail_poll_seconds", 120)
    _started_at = datetime.now(timezone.utc)
    _listener_task = asyncio.create_task(_listener_loop())


async def stop_gmail_listener() -> None:
    """Stop the Gmail listener."""
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
    print("[Heartbeat] Gmail listener stopped")


async def ensure_started() -> None:
    """Start the listener when "watch my inbox" is on and Gmail is connected
    (the connect callback, the Settings switch). Never raises."""
    try:
        from sqlalchemy import select as _select

        from services.database import HeartbeatConfigModel
        from services import gmail_service

        async with async_session() as session:
            config = (await session.execute(
                _select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default")
            )).scalar_one_or_none()
        if config is None or not config.gmail_enabled:
            return
        if not await gmail_service.is_configured():
            return
        await start_gmail_listener(config)
    except Exception as e:  # noqa: BLE001 — a connect must not fail on the listener
        print(f"[Heartbeat] Gmail listener: couldn't start ({type(e).__name__}: {e})")


def get_gmail_listener_status() -> str:
    """Get the current listener status."""
    if _listener_task is None:
        return "stopped"
    if _listener_task.done():
        return "error"
    return "running"
