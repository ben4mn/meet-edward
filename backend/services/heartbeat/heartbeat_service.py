"""
Heartbeat orchestrator for Edward.

Manages the triage loop, active-chat gating, and briefing system.
"""

import asyncio
from datetime import datetime, timezone, timedelta
from typing import Optional

from services.timezone_utils import format_iso_local

from sqlalchemy import select, func, update
from services.database import async_session, HeartbeatEventModel, HeartbeatConfigModel
from services.owner import owner_text
from services.heartbeat.listener_imessage import (
    start_imessage_listener,
    stop_imessage_listener,
    get_listener_status,
)
from services.heartbeat.listener_email import (
    start_email_listener,
    stop_email_listener,
    get_email_listener_status,
)
from services.heartbeat.listener_gmail import (
    start_gmail_listener,
    stop_gmail_listener,
    get_gmail_listener_status,
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


def get_active_chat_id() -> Optional[str]:
    """Return one active conversation_id (deterministic-ish — first
    insertion order via set iteration). Used by ambient-event routing
    to know where to deposit a note."""
    if not _active_chats:
        return None
    return next(iter(_active_chats))


# ===== Ambient event routing =====
#
# When a heartbeat event is escalated (high urgency) AND a chat is
# currently active, route the event as an "ambient note" into that
# conversation. The note is consumed by streaming.py's briefing assembly
# on the next turn, so it surfaces in Edward's voice rather than as an
# interrupt. (The legacy triage engine called ``route_triaged_event``; the
# mind path hands escalations to the mind instead, so nothing calls it
# today. It stays for a path that wants to interleave an escalation.)

_pending_ambient_notes: dict[str, list[str]] = {}
_ambient_last_injected_at: dict[str, datetime] = {}
_AMBIENT_MIN_INTERVAL = timedelta(minutes=3)

# Statuses that warrant interleaving. 'noted' deliberately doesn't make
# the cut — those defer to the post-chat briefing as before.
_AMBIENT_ELIGIBLE_STATUSES = {"escalated"}


async def inject_ambient_event(conversation_id: str, summary: str) -> None:
    """Queue an ambient note for delivery to the named conversation on
    its next system-prompt assembly. Idempotent within a single note —
    the dedupe is by content, so repeated calls with the same summary
    won't double-stack."""
    bucket = _pending_ambient_notes.setdefault(conversation_id, [])
    if summary not in bucket:
        bucket.append(summary)


def consume_ambient_notes(conversation_id: str) -> list[str]:
    """Drain pending ambient notes for a conversation. Called by the
    chat path during system-prompt assembly. After draining, the notes
    are gone — they only fire once."""
    return _pending_ambient_notes.pop(conversation_id, [])


async def route_triaged_event(
    event_id: str,
    triage_status: str,
    summary: str,
    conversation_id: Optional[str] = None,
) -> None:
    """Decide whether a freshly-triaged event should interleave into an
    active conversation, and if so, queue it as an ambient note.

    - Only escalated events qualify (other statuses defer to the
      post-chat briefing).
    - Only if a chat is active (otherwise the regular briefing path
      handles it).
    - Rate-limited to one ambient note per 3 minutes per conversation
      so we don't carpet-bomb a focused chat.
    """
    if triage_status not in _AMBIENT_ELIGIBLE_STATUSES:
        return

    target = conversation_id or get_active_chat_id()
    if not target:
        return

    now = datetime.utcnow()
    last = _ambient_last_injected_at.get(target)
    if last and (now - last) < _AMBIENT_MIN_INTERVAL:
        return

    await inject_ambient_event(target, summary)
    _ambient_last_injected_at[target] = now


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

# In-process cache for the synthesized briefing. Key = tuple of event ids
# (order-stable); value = (timestamp, narrative). A new event invalidates
# the cache because the key changes; identical event sets re-use the
# synthesis for up to ~5 minutes.
_briefing_cache: dict[tuple, tuple[datetime, str]] = {}
_BRIEFING_CACHE_TTL = timedelta(minutes=5)


async def _fetch_briefing_events() -> list:
    """Fetch un-briefed events worth surfacing. Split out from
    get_pending_briefing so tests can swap the DB layer without touching
    the synthesis path."""
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
        return list(result.scalars().all())


async def _mark_events_briefed(event_ids: list) -> None:
    """Flip briefed=True for the events we just synthesized about."""
    if not event_ids:
        return
    async with async_session() as session:
        await session.execute(
            update(HeartbeatEventModel)
            .where(HeartbeatEventModel.id.in_(event_ids))
            .values(briefed=True)
        )
        await session.commit()


def _build_raw_bullet_lines(events) -> list[str]:
    """The legacy bullet format — still the input the background LLM
    synthesizes from, just no longer the user-facing output."""
    lines = []
    for event in events:
        time_ago = _time_ago(event.created_at)
        sender = event.contact_name or event.sender or "Unknown"
        summary = (event.summary or "")[:100]
        status_label = (event.triage_status or "").upper()

        if event.source == "calendar":
            lines.append(f"- [CALENDAR] {summary} ({time_ago})")
        elif event.source == "email":
            lines.append(f"- [EMAIL] {sender}: {summary} ({time_ago})")
        elif event.source == "gmail":
            lines.append(f"- [GMAIL] {sender}: {summary} ({time_ago})")
        else:
            lines.append(f"- [{status_label}] {sender} texted: {summary} ({time_ago})")
    return lines


BRIEFING_SYSTEM_PROMPT = owner_text(
    "You are Edward, a personal ambient intelligence speaking in your "
    "own voice — dry, anchored, declarative. Below are notable events "
    "that piled up while {OWNER} was away. Write a 2-3 sentence briefing "
    "(no bullets, no preamble, no headers) that foregrounds what "
    "matters and skips the noise. Reference people and content "
    "directly. Don't list — synthesize. Don't apologize for things "
    "you didn't do."
)


async def _synthesize_briefing_narrative(bullet_lines: list[str]) -> str:
    """Pipe raw bullets through the background LLM (GPT) to get a 2-3
    sentence Edward-voice synthesis. Falls back to the bullet list on
    any failure so the briefing never silently disappears."""
    from langchain_core.messages import HumanMessage, SystemMessage
    from services.llm_utils import build_background_llm

    system = SystemMessage(content=BRIEFING_SYSTEM_PROMPT)
    human = HumanMessage(content="\n".join(bullet_lines))

    try:
        # "triage" tier: the briefing synthesis runs inside the user's reply
        # turn — it must not be starved behind bulk background enrichment.
        llm = build_background_llm(temperature=0.4, max_tokens=300, priority="triage")
        result = await llm.ainvoke([system, human])
        narrative = (getattr(result, "content", "") or "").strip()
        if narrative:
            return narrative
    except Exception:  # noqa: BLE001 — narrative is best-effort
        pass

    # Fallback: bullets are better than silence.
    return "\n".join(bullet_lines)


async def get_pending_briefing() -> Optional[str]:
    """Return a narrative briefing of notable events since the user last
    chatted, or None if nothing's pending.

    The synthesis is cached for ~5 minutes keyed on the event-id set, so
    rapid re-opens of the chat don't re-spend tokens for the same
    briefing. A new heartbeat event invalidates the cache by changing
    the key.
    """
    events = await _fetch_briefing_events()
    if not events:
        return None

    event_ids = [e.id for e in events]
    cache_key = tuple(event_ids)
    now = datetime.utcnow()

    cached = _briefing_cache.get(cache_key)
    if cached and (now - cached[0]) < _BRIEFING_CACHE_TTL:
        # Don't re-mark events on cache hit — they were marked on the
        # first synthesis.
        return cached[1]

    bullet_lines = _build_raw_bullet_lines(events)
    narrative = await _synthesize_briefing_narrative(bullet_lines)

    _briefing_cache[cache_key] = (now, narrative)
    await _mark_events_briefed(event_ids)
    return narrative


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
        next_triage_at = format_iso_local(next_dt)

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
        # The mind path is the only path since the legacy triage engine
        # left, so escalations always go to the mind; the column is kept
        # for the API, and the panel's labels read this.
        "mind_enabled": True,
        "pending_count": pending_count,
        "last_triage_at": format_iso_local(_last_triage_at) if _last_triage_at else None,
        "next_triage_at": next_triage_at,
        "listener_status": get_listener_status(),
        "allowed_senders": allowed_senders,
        "tracks": {
            "imessage": {
                "enabled": config.imessage_enabled,
                "status": get_listener_status(),
                "poll_seconds": config.imessage_poll_seconds,
                "thread_limit_direct": config.imessage_thread_limit_direct,
                "thread_limit_group": config.imessage_thread_limit_group,
            },
            "email": {
                "enabled": config.email_enabled,
                "status": get_email_listener_status(),
                "poll_seconds": config.email_poll_seconds,
            },
            "gmail": {
                "enabled": config.gmail_enabled,
                "status": get_gmail_listener_status(),
                "poll_seconds": config.gmail_poll_seconds,
            },
        },
        # Per-track config fields for frontend
        "imessage_enabled": config.imessage_enabled,
        "imessage_poll_seconds": config.imessage_poll_seconds,
        "imessage_thread_limit_direct": config.imessage_thread_limit_direct,
        "imessage_thread_limit_group": config.imessage_thread_limit_group,
        "calendar_enabled": config.calendar_enabled,
        "calendar_poll_seconds": config.calendar_poll_seconds,
        "calendar_lookahead_minutes": config.calendar_lookahead_minutes,
        "email_enabled": config.email_enabled,
        "email_poll_seconds": config.email_poll_seconds,
        "gmail_enabled": config.gmail_enabled,
        "gmail_poll_seconds": config.gmail_poll_seconds,
    }


# ===== Triage loop =====

def _log_triage_loop_error(e: Exception) -> None:
    """Log a triage loop failure with its traceback. Bare str(e) hid the
    offending line for weeks (3,650 silent serialization failures)."""
    import traceback
    print(f"[Heartbeat] Triage loop error: {e}\n{traceback.format_exc()}")


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
        _last_triage_at = datetime.utcnow()


async def trigger_immediate_triage() -> None:
    """Trigger an immediate triage cycle (e.g. @edward mention detected).

    Waits for any in-flight cycle to finish rather than dropping the trigger,
    so @edward mentions aren't silently lost when a scheduled cycle is mid-run.
    """
    global _cycle_count, _last_triage_at
    try:
        async with _triage_lock:
            from services.heartbeat.triage_service import run_triage_cycle
            _cycle_count += 1
            await run_triage_cycle(_cycle_count)
            _last_triage_at = datetime.utcnow()
    except Exception as e:
        print(f"[Heartbeat] Immediate triage error: {e}")


# ===== Non-blocking triage requests =====
#
# The chat.db listener used to `await trigger_immediate_triage()` inline, so
# a long cycle stalled the 10s poll for its whole duration. It now sets an
# event; this runner coalesces bursts and runs one cycle per wake.

_trigger_event: Optional[asyncio.Event] = None
_trigger_task: Optional[asyncio.Task] = None
_trigger_reasons: list[str] = []


def request_triage(reason: str) -> None:
    _trigger_reasons.append(reason)
    if _trigger_event is not None:
        _trigger_event.set()


def _reset_trigger_for_tests() -> None:
    global _trigger_event, _trigger_task
    _trigger_event = None
    _trigger_task = None
    _trigger_reasons.clear()


async def _trigger_runner() -> None:
    global _trigger_event
    _trigger_event = asyncio.Event()
    if _trigger_reasons:
        _trigger_event.set()
    while True:
        await _trigger_event.wait()
        _trigger_event.clear()
        reasons = list(_trigger_reasons)
        _trigger_reasons.clear()
        if reasons:
            print(f"[Heartbeat] triage requested ({', '.join(sorted(set(reasons)))}) — running cycle")
        try:
            await trigger_immediate_triage()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            print(f"[Heartbeat] requested triage failed: {e}")


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
                print(f"[Heartbeat] User is chatting, deferring triage (active: {_active_chats})")
                await asyncio.sleep(30)
                continue

            await _run_triage_cycle()
            await asyncio.sleep(config.triage_interval_seconds)

        except asyncio.CancelledError:
            raise
        except Exception as e:
            _log_triage_loop_error(e)
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

    if config.email_enabled:
        await start_email_listener(config)

    if config.gmail_enabled:
        await start_gmail_listener(config)

    # Start the triage loop and the listener's trigger runner.
    global _trigger_task
    _heartbeat_task = asyncio.create_task(_heartbeat_loop())
    _trigger_task = asyncio.create_task(_trigger_runner())
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

    global _trigger_task
    if _trigger_task is not None:
        _trigger_task.cancel()
        try:
            await _trigger_task
        except asyncio.CancelledError:
            pass
        _trigger_task = None

    # Stop all listeners
    await stop_imessage_listener()
    await stop_email_listener()
    await stop_gmail_listener()
    print("[Heartbeat] System stopped")
