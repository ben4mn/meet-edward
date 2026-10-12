"""The scheduler keeps the iPhone widget fresh (Session G triggers).

Two paths: a dedicated ``{"type": "widget_refresh"}`` cron event every 30
minutes, and a refresh after the morning/evening wake-up ticks so the
widget reflects whatever Edward just decided to say.
"""

import json
from types import SimpleNamespace

import pytest

from services import scheduler_service

pytestmark = pytest.mark.unit


def _event(meta: dict):
    return SimpleNamespace(id="ev-1", event_metadata=json.dumps(meta), description="x",
                           delivery_channel=None, conversation_id=None)


async def test_leftover_widget_refresh_event_runs_a_pulse(monkeypatch):
    """The deterministic composer is retired; a stale row still does the right thing."""
    from services import expression_service

    ran, completed = [], []

    async def fake_pulse(reason):
        ran.append(reason)
        return {"ok": True, "note": "n"}

    async def fake_complete(event_id, result=None, **kw):
        completed.append((event_id, result))

    monkeypatch.setattr(expression_service, "run_expression_pulse", fake_pulse)
    monkeypatch.setattr(scheduler_service, "mark_event_completed", fake_complete)
    await scheduler_service._execute_event(_event({"type": "widget_refresh"}))
    assert ran == ["cron"]
    assert completed and completed[0][0] == "ev-1"


async def test_the_nightly_tick_pulses_the_surfaces_afterwards(monkeypatch):
    """Since the proactivity rebuild (2026-10-11) the only tick is the
    nightly; it pulses the surfaces afterwards to set the morning scene.
    A retired tick (morning, a pre-mind kind) is cancelled, not run."""
    from services import expression_service, mind_service

    refreshed, woke, cancelled = [], [], []

    async def fake_wake(reason, **kw):
        woke.append(reason)
        return {"ok": True, "note": "n"}

    def fake_bg(reason, extra=None):
        refreshed.append(reason)

    async def fake_complete(event_id, result=None, **kw):
        pass

    async def fake_cancel(event_id):
        cancelled.append(event_id)
        return True

    monkeypatch.setattr(mind_service, "wake", fake_wake)
    monkeypatch.setattr(expression_service, "pulse_in_background", fake_bg)
    monkeypatch.setattr(scheduler_service, "mark_event_completed", fake_complete)
    monkeypatch.setattr("services.scheduled_events_service.cancel_event", fake_cancel)
    await scheduler_service._execute_event(_event({"type": "wakeup_tick", "kind": "nightly"}))
    await scheduler_service._execute_event(_event({"type": "wakeup_tick", "kind": "morning"}))
    await scheduler_service._execute_event(_event({"type": "wakeup_tick", "kind": "stale_thread_check"}))
    assert woke == ["nightly"]
    assert refreshed == ["nightly"]
    assert cancelled == ["ev-1", "ev-1"]


def test_expression_pulse_cron_is_seeded():
    assert ("expression_pulse", scheduler_service.PULSE_CRON) in scheduler_service.EXTRA_CRON_EVENTS
    assert scheduler_service.PULSE_CRON == "*/15 * * * *"  # the tick is cheap; the pulse decides


def test_a_pulse_seed_with_an_old_schedule_is_retired():
    """Changing the cadence must not leave the old 30-minute row firing
    next to the new one."""
    assert scheduler_service.is_stale_pulse_seed("expression_pulse", "*/30 * * * *") is True
    assert scheduler_service.is_stale_pulse_seed("expression_pulse", scheduler_service.PULSE_CRON) is False
    assert scheduler_service.is_stale_pulse_seed("wakeup_tick", "*/30 * * * *") is False
