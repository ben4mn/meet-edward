"""The scheduler never blocks on work, and the watchdog can see it
(plan 2026-09-24, C1 "silent stoppers" and D4).

* ``_process_due_events`` used to ``await asyncio.gather`` every due event
  inline, so one slow event — a pulse awaiting a hung calendar, a wake —
  stopped the poll loop: no reminder, tick or ``wake_me`` fired behind it.
  Now each event is a tracked, detached task with its own bound, an event
  still running is never dispatched twice, and the loop moves on.
* A pulse or wake branch that raised used to fall through to the legacy
  path and re-run the event as a full-tool turn in ``edward-mind``.
* A failing boot step (``recover_stale_processing_events`` raising while
  Postgres comes up) left the scheduler dead for the process lifetime.
* The poll loop stamps a liveness file the watchdog reads; the old check
  read the backend log's mtime, which the /screen pollers keep fresh.
* ``settings.timezone`` is compared with the host zone at start.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import scheduler_service as ss


def _event(event_id, meta=None, **kw):
    base = dict(id=event_id, event_metadata=json.dumps(meta) if meta is not None else None, description="x",
                delivery_channel=None, conversation_id=None, recurrence_pattern=None)
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture(autouse=True)
def _fresh_scheduler():
    ss._reset_for_tests()
    yield
    ss._reset_for_tests()


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_event_routes():
    assert ss.event_route({"type": "expression_pulse"}) == "pulse"
    assert ss.event_route({"type": "widget_refresh"}) == "pulse"
    assert ss.event_route({"type": "mind_wake", "why": "x"}) == "mind_wake"
    assert ss.event_route({"type": "wakeup_tick", "kind": "morning"}) == "wakeup_tick"
    assert ss.event_route({"type": "memory_health_report"}) is None
    assert ss.event_route(None) is None and ss.event_route("junk") is None


def test_event_bounds():
    from services import mind_service

    assert ss.EVENT_TIMEOUT_SECONDS == 600
    assert ss.event_timeout_for(None) == 600
    assert ss.event_timeout_for({"type": "memory_health_report"}) == 600
    assert ss.event_timeout_for({"type": "expression_pulse"}) == 600
    assert ss.event_timeout_for({"type": "wakeup_tick", "kind": "morning"}) == 600
    nightly = ss.event_timeout_for({"type": "wakeup_tick", "kind": "nightly"})
    reason = ss.wake_reason_for_tick("nightly")
    assert nightly > mind_service.wake_timeout_for(reason) + mind_service.PACK_TIMEOUT_SECONDS


def test_liveness_path(monkeypatch, tmp_path):
    monkeypatch.delenv("EDWARD_SCHEDULER_ALIVE_PATH", raising=False)
    assert str(ss.liveness_path()).endswith(".edward-health/scheduler.alive")
    monkeypatch.setenv("EDWARD_SCHEDULER_ALIVE_PATH", str(tmp_path / "x" / "alive"))
    assert ss.liveness_path() == tmp_path / "x" / "alive"


def test_stamp_liveness_writes_the_iso_time(monkeypatch, tmp_path):
    target = tmp_path / "health" / "scheduler.alive"
    monkeypatch.setenv("EDWARD_SCHEDULER_ALIVE_PATH", str(target))
    now = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)
    assert ss.stamp_liveness(now) is True
    assert target.read_text().strip() == now.isoformat()


def test_boot_backoff():
    delays = [ss.boot_retry_delay(n) for n in range(1, 10)]
    assert delays[0] == ss.BOOT_RETRY_INITIAL_SECONDS
    assert delays == sorted(delays)
    assert max(delays) == ss.BOOT_RETRY_MAX_SECONDS


@pytest.mark.parametrize(
    "user,host,expected",
    [
        ("America/Chicago", "America/Chicago", False),
        ("America/Winnipeg", "America/Chicago", False),  # different names, same clock
        ("Etc/UTC", "UTC", False),
        ("America/Los_Angeles", "America/Chicago", True),
        ("Europe/London", "UTC", True),
        ("America/Chicago", None, False),
    ],
)
def test_timezone_mismatch(user, host, expected):
    at = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    assert ss.timezone_mismatch(user, host, at=at) is expected


def test_host_timezone_name_prefers_tz(monkeypatch):
    monkeypatch.setenv("TZ", ":America/Denver")
    assert ss.host_timezone_name() == "America/Denver"


def test_timezone_config_shape():
    at = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    cfg = ss.timezone_config("America/Los_Angeles", "America/Chicago", at=at)
    assert cfg == {"timezone": "America/Los_Angeles", "host_timezone": "America/Chicago", "mismatch": True}


async def test_check_timezone_warns_once(monkeypatch, capsys):
    monkeypatch.setattr(ss, "_settings_timezone", lambda: _tz("America/Los_Angeles"))
    monkeypatch.setattr(ss, "host_timezone_name", lambda: "America/Chicago")
    first = await ss.check_timezone()
    second = await ss.check_timezone()
    assert first["mismatch"] is True and second["mismatch"] is True
    out = capsys.readouterr().out
    assert out.count("WARNING") == 1 and "America/Los_Angeles" in out and "America/Chicago" in out


async def _tz(name):
    return name


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------


async def test_a_slow_event_does_not_block_the_poll(monkeypatch):
    from services import conversation_service

    release = asyncio.Event()
    done = []

    async def execute(event):
        if event.id == "slow":
            await release.wait()
        done.append(event.id)

    async def due():
        # Two of Ellis's threads: events in one thread take turns (followups
        # M3, ``test_thread_serialization.py``), events in two run side by side.
        return [_event("slow", conversation_id="chat-a"), _event("fast", conversation_id="chat-b")]

    async def owner_thread(conversation_id):
        return SimpleNamespace(source="user")

    monkeypatch.setattr(conversation_service, "get_conversation", owner_thread)
    monkeypatch.setattr(ss, "_execute_event", execute)
    monkeypatch.setattr(ss, "get_due_events", due)
    t0 = time.monotonic()
    await ss._process_due_events()
    assert time.monotonic() - t0 < 0.5, "the poll returns while an event is still running"
    await asyncio.sleep(0.05)
    assert done == ["fast"]
    assert ss.inflight_event_ids() == {"slow"}
    release.set()
    await asyncio.sleep(0.05)
    assert done == ["fast", "slow"] and ss.inflight_event_ids() == set()


async def test_an_event_still_running_is_not_dispatched_twice(monkeypatch):
    release = asyncio.Event()
    runs = []

    async def execute(event):
        runs.append(event.id)
        await release.wait()

    async def due():
        return [_event("ev-1")]

    monkeypatch.setattr(ss, "_execute_event", execute)
    monkeypatch.setattr(ss, "get_due_events", due)
    await ss._process_due_events()
    await asyncio.sleep(0.02)
    await ss._process_due_events()
    await asyncio.sleep(0.02)
    assert runs == ["ev-1"]
    release.set()
    await asyncio.sleep(0.02)


async def test_a_legacy_event_that_overruns_is_failed(monkeypatch):
    failed, recorded = [], []

    async def execute(event):
        await asyncio.sleep(3600)

    async def due():
        return [_event("ev-1")]

    async def mark_failed(event_id, error):
        failed.append((event_id, error))

    monkeypatch.setattr(ss, "_execute_event", execute)
    monkeypatch.setattr(ss, "get_due_events", due)
    monkeypatch.setattr(ss, "mark_event_failed", mark_failed)
    monkeypatch.setattr(ss, "_record_event_outcome", lambda *a, **k: recorded.append((a, k)))
    monkeypatch.setattr(ss, "EVENT_TIMEOUT_SECONDS", 0.05)
    await ss._process_due_events()
    await asyncio.sleep(0.3)
    assert failed and failed[0][0] == "ev-1" and "timed out" in failed[0][1]
    assert recorded and recorded[0][0][1] == "failed"
    assert ss.inflight_event_ids() == set()


async def test_a_wake_branch_that_raises_is_not_rerun_as_a_legacy_turn(monkeypatch):
    from services import expression_service, mind_service

    woke, failed = [], []

    async def fake_wake(reason, **kw):
        woke.append(reason)
        return {"ok": True, "note": "n"}

    def boom(reason, extra=None):
        raise RuntimeError("pulse scheduling broke")

    async def legacy_turn(**kw):
        raise AssertionError("a routed event must never fall through to a legacy chat turn")

    async def mark_failed(event_id, error):
        failed.append((event_id, error))

    async def mark_completed(event_id, result=None):
        raise AssertionError("not completed after a failure")

    monkeypatch.setattr(mind_service, "wake", fake_wake)
    monkeypatch.setattr(expression_service, "pulse_in_background", boom)
    monkeypatch.setattr("services.graph.chat_with_memory", legacy_turn)
    monkeypatch.setattr(ss, "mark_event_failed", mark_failed)
    monkeypatch.setattr(ss, "mark_event_completed", mark_completed)
    monkeypatch.setattr(ss, "_record_event_outcome", lambda *a, **k: None)
    await ss._execute_event(_event("ev-1", {"type": "wakeup_tick", "kind": "nightly"}))
    assert woke == ["nightly"]
    assert failed and failed[0][0] == "ev-1" and "pulse scheduling broke" in failed[0][1]


# ---------------------------------------------------------------------------
# The loop
# ---------------------------------------------------------------------------


async def test_a_failed_boot_is_retried_and_the_loop_stamps_liveness(monkeypatch, tmp_path):
    target = tmp_path / "scheduler.alive"
    monkeypatch.setenv("EDWARD_SCHEDULER_ALIVE_PATH", str(target))
    attempts, polls, stale = [], [], []

    async def recover(stale_after_seconds):
        if stale_after_seconds == 0:
            attempts.append(1)
            if len(attempts) < 3:
                raise ConnectionRefusedError("postgres is still starting")
        else:
            stale.append(stale_after_seconds)
        return 0

    async def nothing():
        return None

    async def poll():
        polls.append(1)

    monkeypatch.setattr(ss, "recover_stale_processing_events", recover)
    monkeypatch.setattr(ss, "_ensure_wakeup_events", nothing)
    monkeypatch.setattr(ss, "_process_due_events", poll)
    monkeypatch.setattr(ss, "check_timezone", lambda: _tz({}))
    monkeypatch.setattr(ss, "BOOT_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(ss, "BOOT_RETRY_MAX_SECONDS", 0.02)
    monkeypatch.setattr(ss, "_POLL_INTERVAL_SECONDS", 0.02)
    await ss.start_scheduler()  # never raises
    try:
        for _ in range(100):
            await asyncio.sleep(0.02)
            if len(polls) >= 2 and target.exists():
                break
        assert len(attempts) == 3, "boot retried until it succeeded"
        assert len(polls) >= 2, "then the loop polls"
        assert stale and stale[0] == ss._STALE_PROCESSING_SECONDS, "stale recovery still runs every poll"
        stamped = datetime.fromisoformat(target.read_text().strip())
        assert (datetime.now(timezone.utc) - stamped).total_seconds() < 5
    finally:
        await ss.stop_scheduler()


async def test_no_liveness_while_boot_keeps_failing(monkeypatch, tmp_path):
    target = tmp_path / "scheduler.alive"
    monkeypatch.setenv("EDWARD_SCHEDULER_ALIVE_PATH", str(target))

    async def recover(stale_after_seconds):
        raise ConnectionRefusedError("postgres is down")

    monkeypatch.setattr(ss, "recover_stale_processing_events", recover)
    monkeypatch.setattr(ss, "check_timezone", lambda: _tz({}))
    monkeypatch.setattr(ss, "BOOT_RETRY_INITIAL_SECONDS", 0.01)
    monkeypatch.setattr(ss, "BOOT_RETRY_MAX_SECONDS", 0.02)
    await ss.start_scheduler()
    try:
        await asyncio.sleep(0.2)
        assert not target.exists(), "a scheduler that cannot boot must look dead to the watchdog"
    finally:
        await ss.stop_scheduler()


async def test_stale_recovery_waits_while_this_process_runs_an_event(monkeypatch):
    """The poll no longer blocks on events, so its stale-recovery pass now
    runs while they execute. ``updated_at`` is written by the server's
    ``now()``, which lands in the session's zone — on a Postgres whose
    timezone is not UTC a just-claimed row already looks hours old, and
    the pass would fail or re-arm an event mid-flight. With nothing in
    flight, anything still 'processing' is an orphan and is reclaimed."""
    release = asyncio.Event()
    stale = []

    async def recover(stale_after_seconds):
        stale.append(stale_after_seconds)
        return 0

    async def execute(event):
        await release.wait()

    async def due():
        return [_event("ev-1")]

    monkeypatch.setattr(ss, "recover_stale_processing_events", recover)
    monkeypatch.setattr(ss, "_execute_event", execute)
    monkeypatch.setattr(ss, "get_due_events", due)
    await ss._process_due_events()
    await asyncio.sleep(0.02)
    assert await ss._recover_stale_if_idle() == 0 and stale == []
    release.set()
    await asyncio.sleep(0.02)
    await ss._recover_stale_if_idle()
    assert stale == [ss._STALE_PROCESSING_SECONDS]


async def test_stop_cancels_in_flight_events(monkeypatch):
    started = asyncio.Event()

    async def execute(event):
        started.set()
        await asyncio.sleep(3600)

    async def due():
        return [_event("ev-1")]

    monkeypatch.setattr(ss, "_execute_event", execute)
    monkeypatch.setattr(ss, "get_due_events", due)
    await ss._process_due_events()
    await asyncio.wait_for(started.wait(), 1)
    await ss.stop_scheduler()
    assert ss.inflight_event_ids() == set()
