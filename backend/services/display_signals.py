"""Real-time signal sources for the always-on display's avatar.

The avatar's state (idle / listening / speaking / thinking / looking-*)
is driven by a snapshot of these signals, polled every ~2s by the frontend.

Where each signal comes from:
  - voice_state            : process-local hook (voice_ws.py calls mark_*)
  - active_chat_streams    : heartbeat_service._active_chats (existing set)
  - active_orchestrator    : DB query against orchestrator_tasks
  - recent_alert_at        : DB query for triage_status IN (mention, escalated, calendar_urgent)
                             on heartbeat_events in the last 90s
  - calendar_near_fire_at  : display_service.get_calendar_nowait() (the cache,
                             never a wait) — first event starting in <= 5m
"""

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from sqlalchemy import desc, func, select

from services.database import (
    HeartbeatEventModel,
    OrchestratorTaskModel,
    async_session,
)


# ---- Voice state (process-local) ----------------------------------------

# "idle" | "listening" | "speaking"
_voice_state: str = "idle"
_voice_state_changed_at: float = 0.0


def mark_voice_listening() -> None:
    global _voice_state, _voice_state_changed_at
    _voice_state = "listening"
    _voice_state_changed_at = time.time()


def mark_voice_speaking() -> None:
    global _voice_state, _voice_state_changed_at
    _voice_state = "speaking"
    _voice_state_changed_at = time.time()


def mark_voice_idle() -> None:
    global _voice_state, _voice_state_changed_at
    _voice_state = "idle"
    _voice_state_changed_at = time.time()


def get_voice_state() -> str:
    # Auto-clear stale states — a voice session that crashed mid-turn would
    # otherwise leave the display stuck on "speaking" forever.
    if _voice_state != "idle" and time.time() - _voice_state_changed_at > 30:
        return "idle"
    return _voice_state


# ---- Composed snapshot -------------------------------------------------

async def get_signals_summary() -> Dict[str, Any]:
    """Snapshot of everything the gaze controller needs.

    Cheap to call — runs ~30 RPM at 2s frontend cadence. Two small queries.
    """
    voice = get_voice_state()

    # Chat streams (in-process set)
    try:
        from services.heartbeat.heartbeat_service import _active_chats
        active_streams = len(_active_chats)
    except Exception:
        active_streams = 0

    # Orchestrator + recent alerts in one DB session
    active_tasks = 0
    recent_alert_at: Optional[str] = None
    try:
        async with async_session() as session:
            active_tasks = int((await session.execute(
                select(func.count(OrchestratorTaskModel.id))
                .where(OrchestratorTaskModel.status == "running")
            )).scalar() or 0)

            cutoff = datetime.utcnow() - timedelta(seconds=90)
            row = (await session.execute(
                select(HeartbeatEventModel.created_at)
                .where(HeartbeatEventModel.triage_status.in_(
                    ["mention", "escalated", "calendar_urgent"]
                ))
                .where(HeartbeatEventModel.created_at >= cutoff)
                .order_by(desc(HeartbeatEventModel.created_at))
                .limit(1)
            )).first()
            if row:
                ts = row[0]
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                recent_alert_at = ts.isoformat()
    except Exception:
        # Fail soft — display still functions without these signals.
        pass

    # Calendar near-fire — read display_service's cache without waiting:
    # this poller runs every 2 s per open /screen, and awaiting a cold
    # calendar put it behind a 10 s apple-mcp read (2026-09-25 audit). An
    # expired cache refreshes in the background; the next poll sees it.
    calendar_near_fire_at: Optional[str] = None
    try:
        from services import display_service
        cal = display_service.get_calendar_nowait() or {}
        now = datetime.now().astimezone()
        for ev in cal.get("events", []):
            try:
                start = datetime.fromisoformat(ev["start"])
            except (TypeError, ValueError, KeyError):
                continue
            delta = start - now
            if timedelta(seconds=0) <= delta <= timedelta(minutes=5):
                calendar_near_fire_at = ev["start"]
                break
    except Exception:
        pass

    return {
        # The interrupt ladder is gone (2026-10-11); the key stays null so
        # the stage's alert strip reads the same shape.
        "pending_interrupt": None,
        "voice_state": voice,                  # "idle" | "listening" | "speaking"
        "active_chat_streams": active_streams,  # int
        "active_orchestrator_tasks": active_tasks,
        "recent_alert_at": recent_alert_at,    # iso or null
        "calendar_near_fire_at": calendar_near_fire_at,
        "fetched_at": datetime.utcnow().isoformat() + "Z",
    }
