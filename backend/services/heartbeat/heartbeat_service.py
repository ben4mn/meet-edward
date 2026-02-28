"""
Heartbeat orchestrator for Edward.

Manages the triage loop, active-chat gating, and briefing system.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select, func, update
from services.database import async_session, HeartbeatEventModel, HeartbeatConfigModel, TriageResultModel
from services.heartbeat.listener_imessage import (
    start_imessage_listener,
    stop_imessage_listener,
    get_listener_status,
)
from services.heartbeat.listener_calendar import (
    start_calendar_listener,
    stop_calendar_listener,
    get_calendar_listener_status,
)
from services.heartbeat.listener_email import (
    start_email_listener,
    stop_email_listener,
    get_email_listener_status,
)

_heartbeat_task: asyncio.Task | None = None
_active_chats: set[str] = set()
_last_triage_at: Optional[datetime] = None
_cycle_count: int = 0
_triage_lock = asyncio.Lock()


# ===== Active chat gating =====

def register_active_chat(conversation_id: str) -> None:
    _active_chats.add(conversation_id)

def unregister_active_chat(conversation_id: str) -> None:
    _active_chats.discard(conversation_id)

def is_user_chatting() -> bool:
    return len(_active_chats) > 0


# ===== Config =====

async def _load_config() -> HeartbeatConfigModel:
    async with async_session() as session:
        result = await session.execute(
            select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()
        if not config:
            config = HeartbeatConfigModel(id="default")
            session.add(config)
            await session.commit()
            await session.refresh(config)
        return config


# ===== Briefing =====

async def get_pending_briefing() -> Optional[str]:
    """Get a briefing summary of notable events since the user last chatted.

    Returns a formatted string for injection into the system prompt, or None.
    """
    async with async_session() as session:
        result = await session.execute(
            select(HeartbeatEventModel)
            .where(
                HeartbeatEventModel.triage_status.in_(["noted", "acted", "escalated"]),
                HeartbeatEventModel.briefed == False,
            )
            .order_by(HeartbeatEventModel.created_at.asc())
            .limit(10)
        )
        events = list(result.scalars().all())

        if not events:
            return None

        lines = []
        for event in events:
            time_ago = _time_ago(event.created_at)
            sender = event.contact_name or event.sender or "Unknown"
            summary = (event.summary or "")[:100]
            status_label = event.triage_status.upper()

            if event.source == "calendar":
                lines.append(f"- [CALENDAR] {summary} ({time_ago})")
            elif event.source == "email":
                lines.append(f"- [EMAIL] {sender}: {summary} ({time_ago})")
            else:
                lines.append(f"- [{status_label}] {sender} texted: {summary} ({time_ago})")

        # Mark as briefed
        event_ids = [e.id for e in events]
        await session.execute(
            update(HeartbeatEventModel)
            .where(HeartbeatEventModel.id.in_(event_ids))
            .values(briefed=True)
        )
        await session.commit()

        return "\n".join(lines)


def _time_ago(dt: Optional[datetime]) -> str:
    """Human-readable time ago string."""
    if dt is None:
        return "recently"
    now = datetime.now(timezone.utc)
    # Handle naive datetimes
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    diff = now - dt
    minutes = int(diff.total_seconds() / 60)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"


# ===== Status =====

async def get_heartbeat_status() -> dict:
    """Get the current heartbeat status for the API."""
    config = await _load_config()

    # Count pending events
    async with async_session() as session:
        result = await session.execute(
            select(func.count(HeartbeatEventModel.id)).where(
                HeartbeatEventModel.triage_status == "pending"
            )
        )
        pending_count = result.scalar() or 0

    next_triage_at = None
    if _last_triage_at and config.enabled:
        next_dt = _last_triage_at + timedelta(seconds=config.triage_interval_seconds)
        next_triage_at = next_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    # Parse allowed_senders from config JSON
    allowed_senders = []
    if config.allowed_senders:
        try:
            import json
            parsed = json.loads(config.allowed_senders)
            if isinstance(parsed, list):
                allowed_senders = parsed
        except (json.JSONDecodeError, TypeError):
            pass

    return {
        "running": _heartbeat_task is not None and not _heartbeat_task.done(),
        "enabled": config.enabled,
        "triage_interval_seconds": config.triage_interval_seconds,
        "pending_count": pending_count,
        "last_triage_at": _last_triage_at.strftime("%Y-%m-%dT%H:%M:%SZ") if _last_triage_at else None,
        "next_triage_at": next_triage_at,
        "listener_status": get_listener_status(),
        "allowed_senders": allowed_senders,
        "tracks": {
            "imessage": {
                "enabled": config.imessage_enabled,
                "status": get_listener_status(),
                "poll_seconds": config.imessage_poll_seconds,
            },
            "calendar": {
                "enabled": config.calendar_enabled,
                "status": get_calendar_listener_status(),
                "poll_seconds": config.calendar_poll_seconds,
                "lookahead_minutes": config.calendar_lookahead_minutes,
            },
            "email": {
                "enabled": config.email_enabled,
                "status": get_email_listener_status(),
                "poll_seconds": config.email_poll_seconds,
            },
        },
        # Per-track config fields for frontend
        "imessage_enabled": config.imessage_enabled,
        "imessage_poll_seconds": config.imessage_poll_seconds,
        "calendar_enabled": config.calendar_enabled,
        "calendar_poll_seconds": config.calendar_poll_seconds,
        "calendar_lookahead_minutes": config.calendar_lookahead_minutes,
        "email_enabled": config.email_enabled,
        "email_poll_seconds": config.email_poll_seconds,
    }


# ===== Triage loop =====

async def _run_triage_cycle() -> None:
    """Run a single triage cycle. Delegates to triage_service.

    Uses a lock to prevent concurrent cycles from racing (e.g. immediate
    triage triggered by @edward overlapping with the scheduled loop).
    """
    global _last_triage_at, _cycle_count

    if _triage_lock.locked():
        print("[Heartbeat] Triage cycle already running, skipping")
        return

    async with _triage_lock:
        from services.heartbeat.triage_service import run_triage_cycle

        _cycle_count += 1
        await run_triage_cycle(_cycle_count)
        _last_triage_at = datetime.now(timezone.utc)


async def trigger_immediate_triage() -> None:
    """Trigger an immediate triage cycle (e.g. @edward mention detected)."""
    try:
        await _run_triage_cycle()
    except Exception as e:
        print(f"[Heartbeat] Immediate triage error: {e}")


async def _heartbeat_loop() -> None:
    """Main heartbeat loop — runs forever until cancelled."""
    global _last_triage_at

    while True:
        try:
            config = await _load_config()

            if not config.enabled:
                await asyncio.sleep(60)
                continue

            if is_user_chatting():
                await asyncio.sleep(30)
                continue

            await _run_triage_cycle()
            await asyncio.sleep(config.triage_interval_seconds)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Heartbeat] Triage loop error: {e}")
            await asyncio.sleep(60)


# ===== Start/Stop =====

async def start_heartbeat() -> None:
    """Start the heartbeat system."""
    global _heartbeat_task

    if _heartbeat_task is not None:
        return

    config = await _load_config()

    # Start each listener based on per-track config
    if config.imessage_enabled:
        await start_imessage_listener()

    if config.calendar_enabled:
        await start_calendar_listener(config)

    if config.email_enabled:
        await start_email_listener(config)

    # Start triage loop
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    print("[Heartbeat] System started")


async def stop_heartbeat() -> None:
    """Stop the heartbeat system."""
    global _heartbeat_task

    # Stop triage loop
    if _heartbeat_task is not None:
        _heartbeat_task.cancel()
        try:
            await _heartbeat_task
        except asyncio.CancelledError:
            pass
        _heartbeat_task = None

    # Stop all listeners
    await stop_imessage_listener()
    await stop_calendar_listener()
    await stop_email_listener()
    print("[Heartbeat] System stopped")
