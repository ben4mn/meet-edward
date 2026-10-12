"""
Scheduler service for Edward.

Runs an in-process asyncio loop that polls for due scheduled events
and executes them by invoking Edward's chat_with_memory() path.

Each event creates a new visible conversation (with title from event
description) so users can click the push notification to see Edward's
response. The [SCHEDULED EVENT] trigger message is filtered out in the
conversations router so only Edward's response is visible.
"""

import asyncio
import inspect
import os
from pathlib import Path
from typing import Any, Dict, Optional, Set, Tuple
import json
import traceback
import uuid
from datetime import datetime, timedelta, timezone

from services.scheduled_events_service import (
    get_due_events,
    mark_event_completed,
    mark_event_failed,
    recover_stale_processing_events,
)
from services.timezone_utils import now_local, format_local, current_tz_name
from services.owner import owner_text

_scheduler_task: asyncio.Task | None = None
_POLL_INTERVAL_SECONDS = 30
# Backstop for events orphaned in 'processing' by an in-process crash
# (asyncio.gather used to swallow the exception, so mark_event_failed never
# ran). Run only while no event is in flight: see _recover_stale_if_idle.
_STALE_PROCESSING_SECONDS = 1800

# The poll never waits on work (2026-09-24, plan C1/D4). Each due event
# runs as its own tracked task with its own bound; the loop moves on. Until
# then ``_process_due_events`` awaited ``gather`` of every due event inline,
# so one pulse stuck on a hung calendar call stopped every reminder, tick
# and ``wake_me`` behind it. A legacy event gets 600 s; the pulse and the
# wakes are bounded by their own timeouts, with this as a backstop above
# them (the Sunday nightly may run 900 s after its pack).
EVENT_TIMEOUT_SECONDS = 600
_EVENT_BACKSTOP_MARGIN_SECONDS = 120
# The ``*/5`` sweep is code only (the late-dose check and the noticing
# upkeep): two minutes is far above a healthy pass.
SWEEP_TIMEOUT_SECONDS = 120
# The nightly's turn: 600 s on a weekday, 900 s on Sunday (decay and the
# week's reflection). The backstop is sized for the longer one.
NIGHTLY_MAX_TIMEOUT_SECONDS = 900
_inflight: Dict[str, asyncio.Task] = {}

# A boot step that fails (Postgres still starting) is retried with backoff
# instead of leaving the scheduler dead for the process lifetime.
BOOT_RETRY_INITIAL_SECONDS = 10.0
BOOT_RETRY_MAX_SECONDS = 300.0

# Liveness the watchdog can see: the loop stamps this file after every
# successful poll. scripts/health_check.sh reads its mtime; the old check
# read the backend log's, which the /screen pollers keep fresh regardless.
DEFAULT_LIVENESS_PATH = "~/.edward-health/scheduler.alive"
_liveness_error_logged = False
_tz_warned = False


def _reset_for_tests() -> None:
    """Tests: forget in-flight events, the liveness error and the tz warning."""
    global _liveness_error_logged, _tz_warned
    for task in list(_inflight.values()):
        task.cancel()
    _inflight.clear()
    _liveness_error_logged = False
    _tz_warned = False


def inflight_event_ids() -> Set[str]:
    """Events this process is executing right now."""
    return {eid for eid, task in _inflight.items() if not task.done()}


def event_route(meta: Any) -> Optional[str]:
    """Where an event's metadata sends it: ``pulse``, ``mind_wake``,
    ``wakeup_tick``, ``proactive_sweep`` (2026-10-11), ``retired`` (the
    surplus queue's tick: a leftover row is cancelled, never run as an
    ordinary turn), or None for an ordinary (legacy) event turn. Pure."""
    if not isinstance(meta, dict):
        return None
    kind = meta.get("type")
    if kind in ("expression_pulse", "widget_refresh"):
        return "pulse"
    if kind == "mind_wake":
        return "mind_wake"
    if kind == "wakeup_tick":
        return "wakeup_tick"
    if kind == "proactive_sweep":
        return "proactive_sweep"
    if kind == "surplus_tick":
        return "retired"
    return None


def _event_meta(event) -> Any:
    raw = getattr(event, "event_metadata", None)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def event_timeout_for(meta: Any) -> float:
    """The bound on one event's execution. Pure."""
    route = event_route(meta)
    if route in (None, "retired"):
        return EVENT_TIMEOUT_SECONDS
    if route == "proactive_sweep":
        return SWEEP_TIMEOUT_SECONDS
    from services.mind_service import PACK_TIMEOUT_SECONDS, wake_timeout_for

    reason = "surfaces" if route == "pulse" else (
        "self" if route == "mind_wake" else wake_reason_for_tick(meta.get("kind")))
    turn = wake_timeout_for(reason)
    if reason == "nightly":
        turn = max(turn, NIGHTLY_MAX_TIMEOUT_SECONDS)
    return max(EVENT_TIMEOUT_SECONDS, turn + PACK_TIMEOUT_SECONDS + _EVENT_BACKSTOP_MARGIN_SECONDS)


def boot_retry_delay(attempt: int) -> float:
    """Seconds before boot attempt ``attempt + 1``: doubling, capped. Pure."""
    return min(BOOT_RETRY_MAX_SECONDS, BOOT_RETRY_INITIAL_SECONDS * (2 ** max(0, attempt - 1)))


def liveness_path() -> Path:
    """``EDWARD_SCHEDULER_ALIVE_PATH``, else ``~/.edward-health/scheduler.alive``."""
    return Path(os.path.expanduser(os.environ.get("EDWARD_SCHEDULER_ALIVE_PATH") or DEFAULT_LIVENESS_PATH))


def stamp_liveness(now: Optional[datetime] = None) -> bool:
    """Write the poll's ISO time to the liveness file. Never raises; the
    first failure is printed once so a broken stamp is not silent."""
    global _liveness_error_logged
    path = liveness_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((now or datetime.now(timezone.utc)).isoformat() + "\n")
        return True
    except Exception as exc:  # noqa: BLE001
        if not _liveness_error_logged:
            _liveness_error_logged = True
            print(f"Scheduler: WARNING could not stamp liveness at {path}: {exc}")
        return False


# ---- the user's zone vs the host's (2026-09-24) ------------------------------------
#
# Commit 0f4396a found settings.timezone at America/Los_Angeles on a
# Central-time host Mac. Cron times are read in the user zone since then, so a
# stale setting now means the 07:00 wake fires at 09:00 Central. The guard
# warns once at start and the health report shows both; it never changes
# the setting (the Locale panel does that).

def host_timezone_name() -> Optional[str]:
    """The host's IANA zone: ``TZ``, else ``/etc/localtime``'s target (macOS
    and Linux), else ``/etc/timezone``. None when it cannot be told."""
    tz = (os.environ.get("TZ") or "").strip().lstrip(":")
    if tz:
        return tz
    try:
        target = os.path.realpath("/etc/localtime")
        if "zoneinfo/" in target:
            return target.split("zoneinfo/", 1)[1]
    except OSError:
        pass
    try:
        with open("/etc/timezone") as fh:
            return fh.read().strip() or None
    except OSError:
        return None


def timezone_mismatch(user_tz: Optional[str], host_tz: Optional[str], *, at: datetime) -> bool:
    """Do the two zones disagree on the UTC offset now or half a year from
    now (so a DST-only difference counts)? Names that differ but agree
    (US/Central vs America/Chicago) do not. An unknown host is no mismatch.
    Pure."""
    if not user_tz or not host_tz:
        return False
    from zoneinfo import ZoneInfo

    try:
        u, h = ZoneInfo(user_tz), ZoneInfo(host_tz)
    except Exception:  # noqa: BLE001
        return user_tz != host_tz
    for days in (0, 182):
        t = at + timedelta(days=days)
        if t.astimezone(u).utcoffset() != t.astimezone(h).utcoffset():
            return True
    return False


def timezone_config(user_tz: Optional[str], host_tz: Optional[str], *, at: Optional[datetime] = None) -> Dict[str, Any]:
    """``{timezone, host_timezone, mismatch}`` for the health report. Pure."""
    return {"timezone": user_tz, "host_timezone": host_tz,
            "mismatch": timezone_mismatch(user_tz, host_tz, at=at or datetime.now(timezone.utc))}


async def _settings_timezone() -> Optional[str]:
    from services.settings_service import get_settings
    return getattr(await get_settings(), "timezone", None)


async def current_timezone_config() -> Dict[str, Any]:
    """The live ``timezone_config`` (reads the settings row)."""
    try:
        user_tz = await _settings_timezone()
    except Exception:  # noqa: BLE001
        user_tz = current_tz_name()
    return timezone_config(user_tz, host_timezone_name())


async def check_timezone() -> Dict[str, Any]:
    """Compare ``settings.timezone`` with the host zone; warn once if they
    differ. Called by ``start_scheduler``. Never changes the setting."""
    global _tz_warned
    cfg = await current_timezone_config()
    if cfg["mismatch"] and not _tz_warned:
        _tz_warned = True
        print(f"Scheduler: WARNING settings.timezone is {cfg['timezone']} but this host runs on "
              f"{cfg['host_timezone']}. Every cron time (the 03:00 nightly, reminders) is read in "
              f"{cfg['timezone']}. If that is not where the owner is, fix it in Settings → Locale.")
    return cfg


# The one fixed wake (proactivity rebuild, 2026-10-11): the silent 03:00
# nightly reflection, in the owner's zone like every cron. Proactivity is
# noticing, not a schedule — the 07:00 morning, 18:00 evening and Sunday
# 09:00 reflect ticks (mind rebuild Phases 3 and 7) are retired and their
# live rows cancelled at boot; the nightly runs the reflection on Sundays.
# The mind schedules its own revisits with ``wake_me``.
#
# And one budgeted free-time run a day (design decision, 2026-10-11: one
# budgeted free-time run a day for the host Mac's stage and Edward's own
# projects — no push, no send): ``wake("free")`` at 13:00, when the stage
# can be seen, under a policy that reaches nobody and a per-run dollar cap.
FREE_CRON = "0 13 * * *"
WAKEUP_TICKS: tuple[tuple[str, str], ...] = (
    ("nightly", "0 3 * * *"),
    ("free", FREE_CRON),
)
RETIRED_WAKEUP_KINDS: tuple[str, ...] = (
    "morning_briefing", "evening_briefing", "stale_thread_check", "push_outcome_check",
    "midday_pulse", "location_pulse",
    "morning", "evening", "reflect",
)
# Old kinds keep their reason so history renders.
_TICK_TO_REASON = {"morning": "morning", "morning_briefing": "morning", "evening": "evening",
                   "evening_briefing": "evening", "reflect": "reflect", "nightly": "nightly", "free": "free"}
# What the surfaces pulse is asked after the nightly (the screens wake to it).
NIGHTLY_PULSE_EXTRA = ("The nightly reflection just ran. Set the morning scene: what the widget, the household "
                       "screen and your screen on this Mac should show when the house wakes up.")


def is_retired_wakeup_seed(meta: Optional[dict]) -> bool:
    """A wake-up tick row from before the mind must be cancelled, or it
    keeps firing next to the new one."""
    if not isinstance(meta, dict) or meta.get("type") != "wakeup_tick":
        return False
    return meta.get("kind") in RETIRED_WAKEUP_KINDS


def wake_reason_for_tick(kind: Optional[str]) -> str:
    """Which wake a tick row invites; an unknown kind is a manual wake."""
    return _TICK_TO_REASON.get(kind or "", "manual")


async def _ensure_wakeup_events() -> None:
    """Idempotently register the nightly wake and the cron seeds, and cancel
    the retired tick kinds (the pre-mind ones and, since 2026-10-11, the
    morning, evening and Sunday-reflect ticks). The same rows whatever
    ``EDWARD_PROACTIVITY`` says: the flag is a runtime switch, never a
    reseed."""
    from sqlalchemy import select
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _next_cron_utc, _utcnow

    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(ScheduledEventModel).where(ScheduledEventModel.status.in_(("pending", "processing")))
            )).scalars().all()
            retired = 0
            for r in rows:
                try:
                    if is_retired_wakeup_seed(json.loads(r.event_metadata or "{}")):
                        r.status = "cancelled"
                        retired += 1
                except (json.JSONDecodeError, TypeError):
                    continue
            if retired:
                await session.commit()
                print(f"Scheduler: retired {retired} pre-mind wake-up tick(s)")
    except Exception as exc:
        print(f"Scheduler: could not retire old wake-up ticks: {exc}")

    defaults = [(kind, cron, f"{kind.capitalize()} wake — the mind decides") for kind, cron in WAKEUP_TICKS]
    for kind, cron, description in defaults:
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(ScheduledEventModel).where(
                        ScheduledEventModel.recurrence_pattern == cron,
                        ScheduledEventModel.status.in_(("pending", "processing")),
                    )
                )
                existing = result.scalars().all()
                already = False
                for row in existing:
                    try:
                        meta = json.loads(row.event_metadata or "{}")
                        if meta.get("type") == "wakeup_tick" and meta.get("kind") == kind:
                            already = True
                            break
                    except (json.JSONDecodeError, TypeError):
                        continue
                if already:
                    continue
                next_fire = _next_cron_utc(cron, _utcnow())
                row = ScheduledEventModel(
                    id=str(uuid.uuid4()),
                    description=description,
                    scheduled_at=next_fire,
                    next_fire_at=next_fire,
                    recurrence_pattern=cron,
                    status="pending",
                    created_by="edward",
                    event_metadata=json.dumps(
                        {"type": "wakeup_tick", "kind": kind}
                    ),
                )
                session.add(row)
                await session.commit()
            print(f"Scheduler: registered wakeup tick {kind} (cron={cron})")
        except Exception as exc:
            print(f"Scheduler: failed to register wakeup tick {kind}: {exc}")

    await _seed_extra_cron_events()

# Non-LLM maintenance events the scheduler keeps alive. (type, cron)
# The pulse tick is cheap (signals only) and change-only since 2026-10-11:
# a model turn runs only when a surface's inputs moved — see
# expression_service.should_pulse. The sweep (2026-10-11) is code only:
# the late-dose check (care_watch) and the noticing upkeep, no model call.
# The surplus queue's */10 tick is retired (RETIRED_CRON_EVENTS).
PULSE_CRON = "*/15 * * * *"
SWEEP_CRON = "*/5 * * * *"
EXTRA_CRON_EVENTS: tuple[tuple[str, str], ...] = (
    ("expression_pulse", PULSE_CRON),
    ("proactive_sweep", SWEEP_CRON),
)
_SEED_DESCRIPTIONS = {
    "expression_pulse": "expression_pulse — Edward decides what his widget and the household screen show",
    "proactive_sweep": "proactive_sweep — Edward's checks: a late dose, things he's holding, what came of what he said",
}


def seed_description(etype: str) -> str:
    return _SEED_DESCRIPTIONS.get(etype, f"{etype} — Edward's own maintenance")


def is_stale_pulse_seed(meta_type: Optional[str], recurrence_pattern: Optional[str]) -> bool:
    """A pulse row on an old schedule must be retired, or it keeps firing
    next to the new one (the 30-minute seed, 2026-09-14)."""
    return meta_type == "expression_pulse" and (recurrence_pattern or "") != PULSE_CRON


def is_stale_cron_seed(meta_type: Optional[str], recurrence_pattern: Optional[str]) -> bool:
    """Any seeded cron row on a schedule its seed no longer has."""
    for etype, cron in EXTRA_CRON_EVENTS:
        if meta_type == etype:
            return (recurrence_pattern or "") != cron
    return False


# Event types that used to be seeded and must not keep firing: the
# deterministic widget composer, and the surplus queue's tick (2026-10-11).
RETIRED_CRON_EVENTS: tuple[str, ...] = ("widget_refresh", "surplus_tick")


async def _seed_extra_cron_events() -> None:
    from sqlalchemy import select
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _next_cron_utc, _utcnow

    # Retire old seeds first so a renamed job does not run twice.
    try:
        async with async_session() as session:
            rows = (await session.execute(
                select(ScheduledEventModel).where(ScheduledEventModel.status.in_(("pending", "processing")))
            )).scalars().all()
            retired = 0
            for r in rows:
                try:
                    _mtype = (json.loads(r.event_metadata or "{}") or {}).get("type")
                    if _mtype in RETIRED_CRON_EVENTS or is_stale_cron_seed(_mtype, r.recurrence_pattern):
                        r.status = "cancelled"
                        retired += 1
                except (json.JSONDecodeError, TypeError):
                    continue
            if retired:
                await session.commit()
                print(f"Scheduler: retired {retired} old cron event(s) ({', '.join(RETIRED_CRON_EVENTS)})")
    except Exception as exc:
        print(f"Scheduler: could not retire old cron events: {exc}")

    for etype, cron in EXTRA_CRON_EVENTS:
        try:
            async with async_session() as session:
                rows = (await session.execute(
                    select(ScheduledEventModel).where(
                        ScheduledEventModel.recurrence_pattern == cron,
                        ScheduledEventModel.status.in_(("pending", "processing")),
                    )
                )).scalars().all()
                if any((json.loads(r.event_metadata or "{}") or {}).get("type") == etype for r in rows):
                    continue
                next_fire = _next_cron_utc(cron, _utcnow())
                session.add(ScheduledEventModel(
                    id=str(uuid.uuid4()),
                    description=seed_description(etype),
                    scheduled_at=next_fire,
                    next_fire_at=next_fire,
                    recurrence_pattern=cron,
                    status="pending",
                    created_by="edward",
                    event_metadata=json.dumps({"type": etype}),
                ))
                await session.commit()
            print(f"Scheduler: registered {etype} (cron={cron})")
        except Exception as exc:
            print(f"Scheduler: could not seed {etype}: {exc}")


async def _boot() -> bool:
    """The start-up steps: reclaim what the previous process stranded in
    'processing' (nothing can legitimately be executing at process start)
    and seed the ticks. False when a step failed; never raises."""
    try:
        recovered = await recover_stale_processing_events(stale_after_seconds=0)
        if recovered:
            print(f"Scheduler: recovered {recovered} event(s) orphaned in 'processing'")
        await _ensure_wakeup_events()
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"Scheduler: boot step failed ({type(exc).__name__}: {exc}) — retrying with backoff")
        return False


async def start_scheduler() -> None:
    """Start the scheduler polling loop. Never leaves it dead: a failing
    boot step is retried by the loop with backoff (2026-09-24)."""
    global _scheduler_task
    if _scheduler_task is not None:
        return
    try:
        await check_timezone()
    except Exception as exc:  # noqa: BLE001
        print(f"Scheduler: timezone check skipped: {exc}")
    booted = await _boot()
    _scheduler_task = asyncio.create_task(_scheduler_loop(booted=booted))
    print(f"Scheduler started (polling every {_POLL_INTERVAL_SECONDS}s)"
          + ("" if booted else " — boot will be retried before the first poll"))


async def stop_scheduler() -> None:
    """Stop the scheduler polling loop and the events it is running (an
    interrupted event stays 'processing' and is reclaimed at next start)."""
    global _scheduler_task
    tasks = [t for t in _inflight.values() if not t.done()]
    if _scheduler_task is not None:
        tasks.append(_scheduler_task)
    for task in tasks:
        task.cancel()
    for task in tasks:
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
    _inflight.clear()
    if _scheduler_task is not None:
        _scheduler_task = None
        print("Scheduler stopped")


async def _scheduler_loop(booted: bool = True) -> None:
    """Main polling loop — runs forever until cancelled.

    Until the boot steps succeed the loop only retries them (with backoff)
    and does not poll or stamp liveness: polling first could hand this
    process's own claimed events to a late start-up reclaim, and a
    scheduler that cannot boot must look dead to the watchdog."""
    attempt = 0
    while not booted:
        attempt += 1
        await asyncio.sleep(boot_retry_delay(attempt))
        booted = await _boot()
        if booted:
            print(f"Scheduler: boot succeeded on retry {attempt}; polling now")
    while True:
        try:
            await _recover_stale_if_idle()
            await _process_due_events()
            stamp_liveness()
        except Exception as e:
            print(f"Scheduler poll error: {e}")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _recover_stale_if_idle() -> int:
    """The backstop for events stranded in 'processing', run only while this
    process is executing none. The poll no longer waits for events, so the
    pass would otherwise run beside them — and ``updated_at`` comes from
    the server's ``now()``, which lands in the session's zone: on a Postgres
    whose timezone is not UTC a row claimed a second ago looks hours old
    and would be failed or re-armed mid-flight. Every event this process
    runs is bounded well under ``_STALE_PROCESSING_SECONDS`` and marked on
    any exit, so with nothing in flight a 'processing' row is an orphan."""
    if inflight_event_ids():
        return 0
    recovered = await recover_stale_processing_events(stale_after_seconds=_STALE_PROCESSING_SECONDS)
    if recovered:
        print(f"Scheduler: recovered {recovered} stale 'processing' event(s)")
    return recovered


async def _process_due_events() -> None:
    """Fetch due events and start each as its own tracked task. Returns at
    once: the loop never waits for an event to finish."""
    events = await get_due_events()
    if not events:
        return

    print(f"Scheduler: {len(events)} due event(s) found")
    for event in events:
        running = _inflight.get(event.id)
        if running is not None and not running.done():
            # Still executing from an earlier poll (a stale-recovery re-arm
            # made it due again): never run the same event twice at once.
            print(f"Scheduler: event {event.id} is still running — not dispatching it again")
            continue
        _dispatch(event)


def _dispatch(event) -> asyncio.Task:
    task = asyncio.create_task(_run_event(event))
    _inflight[event.id] = task

    def _forget(t: asyncio.Task, event_id: str = event.id) -> None:
        if _inflight.get(event_id) is t:
            _inflight.pop(event_id, None)

    task.add_done_callback(_forget)
    return task


async def _run_event(event) -> None:
    """One event under its bound. An overrun is a failed event (a recurring
    one advances to its next fire), recorded in the ledger.

    An ordinary event takes its thread before the clock starts
    (``_take_event_thread``): the bound is for the event's own turn, not
    for the turn ahead of it in the thread (review of batch B, 2026-09-27).
    A reminder falling back to ``edward-mind`` during the Sunday reflection
    (900 s) used to wait for the thread inside its 600 s, be cancelled, and
    be written "timed out after 600s" — lost, and labelled as having run.
    """
    from services import thread_turns

    meta = _event_meta(event)
    bound = event_timeout_for(meta)
    slot = await _take_event_thread(event) if event_route(meta) is None else None
    try:
        # Marked held for the whole event, so its turn (and its error note)
        # runs in the thread it already holds rather than waiting on it.
        with thread_turns.holding(slot.conversation_id if slot is not None else None):
            await asyncio.wait_for(_execute_event(event), timeout=bound)
    except asyncio.TimeoutError:
        print(f"Scheduler: Event {event.id} ran past {bound:g}s — marked failed")
        try:
            await mark_event_failed(event.id, f"timed out after {bound:g}s")
        except Exception as exc:  # noqa: BLE001
            print(f"Scheduler: could not mark {event.id} failed: {exc}")
        _record_event_outcome(event, "failed", 1, error=TimeoutError(f"timed out after {bound:g}s"))
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        print(f"Scheduler: Event {event.id} crashed outside its own handling: {exc}")
        try:
            await mark_event_failed(event.id, f"{type(exc).__name__}: {exc}"[:1000])
        except Exception as mark_exc:  # noqa: BLE001
            print(f"Scheduler: could not mark {event.id} failed: {mark_exc}")
    finally:
        if slot is not None:
            slot.release()


async def _take_event_thread(event):
    """Wait for an ordinary event's thread and hold it (``thread_turns``),
    or None when the thread cannot be read — then ``_execute_event`` reads
    it again inside its own handling, which marks the event failed.

    Routed events are not taken here: a wake takes ``thread_lock`` itself.
    """
    from services import thread_turns

    try:
        conversation_id = await _event_conversation_id(event)
    except Exception as exc:  # noqa: BLE001
        print(f"Scheduler: could not read the thread for {event.id}: {exc}")
        return None
    return await thread_turns.acquire(conversation_id, "scheduled_event")


async def _event_conversation_id(event) -> str:
    """The thread an ordinary event fires in (``event_thread``), reading
    the source of the thread that made it."""
    from services.conversation_service import get_conversation

    source = None
    if getattr(event, "conversation_id", None):
        existing = await get_conversation(event.conversation_id)
        source = existing.source if existing is not None else None
    return event_thread(getattr(event, "conversation_id", None), source)


# Threads an event may fire back into. A contact-facing heartbeat thread is
# never one of them: a reminder must not replay into someone else's chat.
EVENT_THREAD_SOURCES = ("user", "edward_thought", "scheduled_event")
MIND_THREAD_ID = "edward-mind"


def event_thread(conversation_id: Optional[str], source: Optional[str]) -> str:
    """Where an event fires: the thread that made it when it still exists
    and is the owner's or Edward's own, else the mind (mind rebuild Phase 4)."""
    if conversation_id and source in EVENT_THREAD_SOURCES:
        return conversation_id
    return MIND_THREAD_ID


def build_event_trigger(event, *, conversation_id: str, now: datetime, intention: Optional[str] = None,
                        intention_until: Optional[datetime] = None) -> str:
    """The trigger message for a fired event: what it is, why it exists,
    how it went last time. Pure."""
    fires = int(getattr(event, "fire_count", 0) or 0)
    lines = [
        "[SCHEDULED EVENT] The following event is now due.",
        f"Description: '{event.description}'",
        f"Scheduled at: {format_local(event.scheduled_at) if getattr(event, 'scheduled_at', None) else 'N/A'}",
        f"Current time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} ({current_tz_name()})",
    ]
    rec = getattr(event, "recurrence_pattern", None)
    lines.append(f"This event is recurring ({rec})." if rec else "This event fires once.")
    created = getattr(event, "created_at", None)
    if intention:
        horizon = f" (until {intention_until.date().isoformat()})" if intention_until else ""
        when = f" on {created.date().isoformat()}" if created else ""
        lines.append(f"You scheduled it{when} because: {intention}{horizon}")
    if fires > 0:
        last = getattr(event, "last_fired_at", None)
        last_s = f" (last {format_local(last)})" if last else ""
        lines.append(f"It has fired {fires} time(s){last_s}.")
        result = " ".join(str(getattr(event, "last_result", "") or "").split())[:500]
        if result:
            lines.append(f"Last time you concluded: {result}")
    else:
        lines.append("This is the first time it fires.")
    lines.append(f"Conversation ID: {conversation_id} (use url='/chat?c={conversation_id}' in push notifications to link here)")
    lines.append("Do what it asks now, in this thread. If it is no longer worth doing, cancel it.")
    return "\n".join(lines)


def _build_scheduler_system_prompt(base_system_prompt: str, event) -> str:
    """Build a system prompt for scheduled event execution.

    Wraps the user's base system prompt with scheduler-specific instructions
    that enforce the correct delivery channel.
    """
    # Channel-specific delivery rules
    channel = (event.delivery_channel or "").strip().lower()

    if channel == "sms":
        delivery_rules = (
            "- You MUST deliver via SMS using send_message(channel=\"sms\") or send_sms().\n"
            "- Do NOT just respond in chat — the user is not watching.\n"
            "- If SMS fails, report the failure but do not silently swallow it."
        )
    elif channel == "imessage":
        delivery_rules = (
            "- You MUST deliver via iMessage using send_imessage() or send_message(channel=\"imessage\").\n"
            "- Do NOT just respond in chat — the user is not watching.\n"
            "- If iMessage fails, report the failure but do not silently swallow it."
        )
    elif channel == "chat":
        delivery_rules = (
            "- Respond normally in chat. No external message is needed.\n"
            "- The response will be stored in the event record for later review."
        )
    elif channel == "alexa":
        delivery_rules = (
            "- This event asked for an Alexa announcement, but there is no announcement\n"
            "  tool in this build: tell the owner with tell_owner instead, and say it was\n"
            "  meant to be spoken on the Echo.\n"
            "- Keep it concise and clear, as if it were read aloud."
        )
    else:
        # No channel specified — infer from description
        delivery_rules = (
            "- If the description asks you to send a message/text someone, use the appropriate messaging tool.\n"
            "- Otherwise, respond normally in chat. The response will be stored in the event record."
        )

    return (
        f"{base_system_prompt}\n\n"
        f"---\n"
        f"{_SCHEDULED_EVENT_INTRO}\n\n"
        f"DELIVERY RULES:\n"
        f"{delivery_rules}\n\n"
        f"{_SCHEDULED_EVENT_VOICE}"
    )


# The fixed halves of the scheduled-event prompt, rendered once so they stay
# byte-identical between events.
_SCHEDULED_EVENT_INTRO = owner_text(
    "You are now executing a scheduled event autonomously — one you or {OWNER} set up, in the "
    "thread where it was made, so your earlier turns here are yours. Right now {OWNER} is NOT in an "
    "active conversation, but this conversation is exactly where they will land "
    "when they tap the push notification you fire. If the event is no longer worth doing "
    "(the thing it watched is settled, {OWNER} said stop, it has fired uselessly for days), "
    "cancel it with cancel_scheduled_event and say why."
)
_SCHEDULED_EVENT_VOICE = owner_text(
    "After you take any tool actions, write your final assistant message to {OWNER} as if "
    "you were opening a thought-thread with them: the substance behind the reminder, "
    "any context you have on it (recent messages, prior promises, calendar), what "
    "action you'd suggest. Do NOT narrate your own actions ('Sent the push…', "
    "'Texted Rowan…') — that reads as a tool log. Speak to them directly so the "
    "tap-through opens to something they can read and reply to."
)


# The extraction profile an event's turn runs under (memory audit
# 2026-09-27). The trigger is text Edward wrote to himself when he
# scheduled the event; under the ``chat`` profile it read as the owner
# speaking, and reminder turns stored ``heard/owner`` instructions ("never
# contact external people directly unless the owner explicitly authorizes",
# 4,025 retrievals). ``memory_service`` owns the profile; an unknown one falls
# back to ``chat`` there, so this is safe to pass before it exists.
SCHEDULED_EVENT_EXTRACTION_PROFILE = "scheduled_event"


def extraction_kwargs_for_event(event) -> dict:
    """``{"skip_extraction": True}`` when the event's metadata asks for it.

    The daily memory-health report runs as a scheduled event so the owner
    lands in a readable conversation when they tap the push; its trigger is figures
    about the memory store itself, which must not become memories.
    """
    try:
        meta = json.loads(event.event_metadata or "{}") or {}
    except (TypeError, ValueError):
        return {}
    return {"skip_extraction": True} if meta.get("skip_extraction") is True else {}


def event_reach(meta: Any) -> Tuple[bool, bool]:
    """(may_reach_people, may_push) an event's turn may have: what the turn
    that made it could do (``event_metadata.reach``, stamped by
    ``scheduled_events_service.create_event``), else both — today's
    behaviour for an unstamped event. Pure."""
    reach = meta.get("reach") if isinstance(meta, dict) else None
    if not isinstance(reach, dict):
        return True, True
    return bool(reach.get("may_reach_people", True)), bool(reach.get("may_push", True))


def event_policy(event) -> Any:
    """The ``TurnPolicy`` an ordinary event's turn runs under
    (``services.toolset.policy``). None until that module exists.

    Whose it is decides ``owner_asked``: ``scheduled_events_service.
    event_is_owners`` — the Events UI, an owner turn's ``created_in``, an
    old event outside Edward's threads, ``requested_by_owner`` — the same
    rule the spend cap uses (review 2026-10-11: reading only
    ``requested_by_owner`` fired every reminder the owner made as Edward's
    own turn: linted, held overnight, refused as a repeat). The owner's own
    event may reach people and push. Edward's never gets more reach than the turn
    that made it had (``event_reach``): a task the 03:00 nightly scheduled
    cannot push at 07:01."""
    try:
        from services.toolset.policy import TurnPolicy
    except ImportError:
        return None
    from services.scheduled_events_service import event_is_owners

    try:
        meta = json.loads(getattr(event, "event_metadata", None) or "{}") or {}
    except (TypeError, ValueError):
        meta = {}
    owners = event_is_owners(event) or meta.get("requested_by_owner") is True
    may_reach, may_push = (True, True) if owners else event_reach(meta)
    return TurnPolicy(source="scheduled_event", owner_asked=owners, may_reach_people=may_reach, may_push=may_push)


def event_policy_kwargs(event, turn_fn: Any) -> dict:
    """``{"policy": …}`` when the policy exists and the turn takes it."""
    policy = event_policy(event)
    if policy is None or not _accepts(turn_fn, "policy"):
        return {}
    return {"policy": policy}


def _record_event_outcome(event, outcome: str, attempts: int, error: Optional[BaseException] = None) -> None:
    """Ledger row per fired event: attempts, whether anything was delivered
    (a successful send/push in the turn), the turn it ran as."""
    try:
        from services import event_ledger, turn_ledger

        turn = turn_ledger.last_finished_turn()
        tools = turn.tools if turn is not None else []
        event_ledger.record_event(
            kind="scheduled_event", source="scheduler", outcome=outcome, ref_id=getattr(event, "id", None),
            reason=turn_ledger.error_class(error) if error is not None else None,
            detail={
                "attempts": attempts,
                "delivered": event_ledger.delivered(tools),
                "turn_id": turn.id if turn is not None else None,
                "recurring": bool(getattr(event, "recurrence_pattern", None)),
            },
        )
    except Exception:  # noqa: BLE001
        pass


def _accepts(fn: Any, name: str) -> bool:
    """Does ``fn`` take ``name`` as a keyword (or ``**kwargs``)? The wake
    gains ``event_meta`` and the turn gains ``policy`` in other branches of
    the rebuild; until they land neither is passed."""
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    return name in params or any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values())


_last_sweep: Dict[str, Any] = {"at": None, "result": None}


def last_sweep() -> Dict[str, Any]:
    """When this process last ran the ``proactive_sweep`` and what it did."""
    return dict(_last_sweep)


async def _run_proactive_sweep() -> Dict[str, Any]:
    """The ``*/5`` sweep (2026-10-11), code only: the late-dose check
    (``care_watch``, outside every flag and the spend cap), the noticing
    upkeep (``noticing_service.sweep``: held items due, failed passes
    retried, outcomes, expiry, pruning — when that module is merged) and
    the snoozed pushes that are due again (``push_ledger.resend_due_snoozes``;
    the retired interrupt ladder's loop did that every 30 s). One part
    failing never stops the others. The ``[sweep] ok`` line is the
    watchdog's liveness signal."""
    out: Dict[str, Any] = {}
    try:
        from services import care_watch

        out["care"] = await care_watch.check()
    except Exception as exc:  # noqa: BLE001
        out["care_error"] = repr(exc)[:200]
    try:
        from services import push_ledger

        out["snoozes"] = await push_ledger.resend_due_snoozes()
    except Exception as exc:  # noqa: BLE001
        out["snoozes_error"] = repr(exc)[:200]
    try:
        from services import noticing_service
    except ImportError:
        noticing_service = None
    if noticing_service is not None:
        try:
            out["notice"] = await noticing_service.sweep()
        except Exception as exc:  # noqa: BLE001 — the noticing sweep is meant never to raise
            out["notice_error"] = repr(exc)[:200]
    care = out.get("care") or {}
    notice = out.get("notice") or {}
    print(f"[sweep] ok care={care.get('alerts', 'error' if 'care_error' in out else 0)} "
          f"due={notice.get('due', '-') if isinstance(notice, dict) else '-'} "
          f"outcomes={notice.get('outcomes', '-') if isinstance(notice, dict) else '-'}")
    _last_sweep.update(at=datetime.now(timezone.utc).isoformat(), result=out)
    return out


async def _cancel_retired(event, why: str) -> None:
    """A retired seed that fired before the boot cancelled it: cancel it
    now, never run it."""
    from services import scheduled_events_service

    print(f"Scheduler: event {event.id} is a retired {why} — cancelled, not run")
    await scheduled_events_service.cancel_event(event.id)


async def _execute_event(event) -> None:
    """Execute a single scheduled event.

    Two routing paths:
    1. ``event_metadata`` says ``{"type": "wakeup_tick", "kind": "nightly"}``
       or ``{"type": "mind_wake"}`` → mind_service.wake() (a judgment turn),
       ``{"type": "expression_pulse"}`` → the surfaces pulse, or
       ``{"type": "proactive_sweep"}`` → the code-only sweep; a retired
       seed (a pre-2026-10-11 tick, the surplus tick) is cancelled
    2. Otherwise → chat_with_memory (legacy chat-driven scheduling)

    A routed event that raises is marked failed and stays failed: it used
    to fall through to the legacy path and run again as a full-tool turn
    in ``edward-mind`` — a second turn for a wake that had already run.
    """
    meta = _event_meta(event)
    route = event_route(meta)
    if route is not None:
        try:
            if route == "retired" or (route == "wakeup_tick" and is_retired_wakeup_seed(meta)):
                await _cancel_retired(event, str(meta.get("kind") or meta.get("type")))
                return
            if route == "pulse":
                # Edward's surfaces run on his judgment: one turn that may
                # touch the widget, the household screen, or nothing.
                # ("widget_refresh" is the retired deterministic composer;
                # any leftover row is treated as a pulse.)
                from services import expression_service
                result = await expression_service.run_expression_pulse("cron")
            elif route == "proactive_sweep":
                result = await _run_proactive_sweep()
            elif route == "mind_wake":
                # A wake Edward asked for himself (``wake_me``): the mind,
                # with his reason in the pack — and the event's metadata,
                # so a promise made in a ping is followed through in that
                # chat (``event_meta.ping``), once the wake takes it.
                from services import mind_service
                kw: Dict[str, Any] = {"extra": meta.get("why") or event.description}
                if _accepts(mind_service.wake, "event_meta"):
                    kw["event_meta"] = meta
                result = await mind_service.wake("self", **kw)
            else:
                # The fixed wakes: the silent nightly (then the surfaces
                # pulse sets the morning scene) and the day's free time.
                reason = wake_reason_for_tick(meta.get("kind"))
                from services import mind_service
                result = await mind_service.wake(reason)
                if reason == "nightly":
                    from services import expression_service
                    expression_service.pulse_in_background("nightly", extra=NIGHTLY_PULSE_EXTRA)
            await mark_event_completed(event.id, result=json.dumps(result, default=str)[:1000])
        except Exception as routed_exc:  # noqa: BLE001
            print(f"Scheduler: {route} event {event.id} failed: {routed_exc}")
            try:
                await mark_event_failed(event.id, f"{route}: {type(routed_exc).__name__}: {routed_exc}"[:1000])
            except Exception as mark_exc:  # noqa: BLE001
                print(f"Scheduler: could not mark {event.id} failed: {mark_exc}")
            _record_event_outcome(event, "failed", 1, error=routed_exc)
        return

    from services import thread_turns

    # Fire where the event was made (a thread the owner or Edward owns), else in
    # the mind — never a fresh amnesiac conversation (Phase 4).
    try:
        conversation_id = await _event_conversation_id(event)
    except Exception as e:  # noqa: BLE001
        await _fail_event(event, e, attempts=0)
        return
    # One hold on the thread for the whole event — both attempts and the
    # error note. ``_run_event`` took it before the clock
    # started, so under it this does not wait.
    slot = await thread_turns.acquire(conversation_id, "scheduled_event")
    try:
        # An event the owner did not make is Edward's own work: on the Claude
        # API it stops at the daily cap and never spends the reserve the owner
        # keeps past it (review 2026-10-11; ``event_is_owners``).
        from contextlib import nullcontext

        from services import llm_spend
        from services.scheduled_events_service import event_is_owners

        own = nullcontext() if event_is_owners(event) else llm_spend.own_work_scope()
        with thread_turns.holding(conversation_id), own:
            await _execute_event_turn(event, conversation_id)
    finally:
        slot.release()


async def _fail_event(event, e: BaseException, attempts: int) -> None:
    error_msg = f"{str(e)}\n{''.join(traceback.format_exception(type(e), e, e.__traceback__))}"
    await mark_event_failed(event.id, error_msg[:1000])
    _record_event_outcome(event, "failed", attempts, error=e)
    print(f"Scheduler: Event {event.id} failed — {str(e)}")


async def _execute_event_turn(event, conversation_id: str) -> None:
    """An ordinary event's turn in ``conversation_id``, whose thread the
    caller holds."""
    from services.graph import get_graph, chat_with_memory
    from services.graph.tools import set_current_conversation_id
    from services.settings_service import get_settings

    attempts = 0
    try:
        settings = await get_settings()
        graph = await get_graph()

        if conversation_id == MIND_THREAD_ID:
            from services import mind_service
            await mind_service._ensure_conversation()

        set_current_conversation_id(conversation_id)

        # Scheduler-specific system prompt wrapping the base prompt
        system_prompt = _build_scheduler_system_prompt(settings.system_prompt, event)

        # The intention behind the event, if the tool recorded one.
        intention_text, intention_until = None, None
        try:
            from services.scheduled_events_service import linked_memory_id
            mid = linked_memory_id(getattr(event, "event_metadata", None))
            if mid:
                from services.memory_service import get_memory_by_id
                mem = await get_memory_by_id(mid)
                if mem is not None:
                    intention_text, intention_until = mem.content, mem.valid_until
        except Exception as exc:
            print(f"Scheduler: intention lookup failed for {event.id}: {exc}")

        now = now_local()
        trigger_message = build_event_trigger(
            event, conversation_id=conversation_id, now=now,
            intention=intention_text, intention_until=intention_until,
        )

        policy_kw = event_policy_kwargs(event, chat_with_memory)

        # First attempt
        attempts = 1
        try:
            response = await chat_with_memory(
                turn_source="scheduled_event",
                message=trigger_message,
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                model=settings.model,
                temperature=settings.temperature,
                graph=graph,
                provider=settings.provider,
                extraction_profile=SCHEDULED_EVENT_EXTRACTION_PROFILE,
                **extraction_kwargs_for_event(event),
                **policy_kw,
            )
        except Exception as first_err:
            print(f"Scheduler: Event {event.id} first attempt failed ({str(first_err)[:100]}), retrying...")
            # Retry once on the same model. This used to swap in a Claude id
            # that build_chat_llm rewrote to the chat default — a silent
            # model switch, and on 2026-09-09 a switch onto the one tier
            # that was out of weekly quota.
            attempts = 2
            response = await chat_with_memory(
                turn_source="scheduled_event",
                message=trigger_message,
                conversation_id=conversation_id,
                system_prompt=system_prompt,
                model=settings.model,
                temperature=settings.temperature,
                graph=graph,
                provider=settings.provider,
                extraction_profile=SCHEDULED_EVENT_EXTRACTION_PROFILE,
                **extraction_kwargs_for_event(event),
                **policy_kw,
            )

        result_summary = str(response)[:500] if response else "No response"
        await mark_event_completed(event.id, result_summary)
        _record_event_outcome(event, "completed", attempts)
        print(f"Scheduler: Event {event.id} completed — {result_summary[:100]}")

    except Exception as e:
        await _fail_event(event, e, attempts)

        # Save minimal checkpoint so conversation shows the error instead of "no messages".
        # A write into the thread like a turn's: it happens under the event's
        # hold on the thread, never racing a turn's checkpoint.
        try:
            from langchain_core.messages import HumanMessage, AIMessage
            error_messages = [
                HumanMessage(content=trigger_message),
                AIMessage(content=f"This scheduled event failed to execute.\n\nError: {str(e)}"),
            ]
            config = {"configurable": {"thread_id": conversation_id}}
            await graph.aupdate_state(config, {
                "messages": error_messages,
                "conversation_id": conversation_id,
                "system_prompt": system_prompt,
                "model": settings.model,
                "temperature": settings.temperature,
                "current_response": "",
                "is_complete": True,
                "retrieved_memories": [],
                "plan_steps": None,
                "tool_calls": [],
                "current_node": "complete",
                "node_history": ["failed"],
            }, as_node="extract_memory")
        except Exception as save_err:
            print(f"Scheduler: Failed to save error checkpoint for {event.id}: {save_err}")
