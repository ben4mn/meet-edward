"""The scheduler's seeds after the proactivity rebuild (2026-10-11).

Proactivity is noticing, not a schedule. The boot:

* registers one wake-up tick, the silent ``nightly`` at 03:00 in Ellis's zone,
  and cancels the 07:00 morning, 18:00 evening and Sunday-09:00 reflect
  ticks (their live rows, idempotently);
* keeps the ``*/15`` surfaces pulse (now a change-only code check) and adds
  the ``*/5`` ``proactive_sweep`` (code only: the late-dose alert and the
  noticing upkeep);
* retires the surplus queue's ``*/10`` tick;
* seeds the same rows whatever ``EDWARD_PROACTIVITY`` says, so the flag is
  a pure runtime switch that never needs a reseed.

A retired row that fires anyway (it was due before the boot cancelled it)
is cancelled, never run as a wake or an ordinary event turn.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from services import scheduler_service as sched
from tests.area1_fakes import fake_module


@pytest.fixture(autouse=True)
def _no_snoozes(monkeypatch):
    """The sweep re-sends due snoozes; no test here reads ``push_log``."""
    from services import push_ledger

    async def none_due(now=None):
        return 0

    monkeypatch.setattr(push_ledger, "resend_due_snoozes", none_due)


def _event(meta: dict, eid: str = "ev-1"):
    return SimpleNamespace(id=eid, event_metadata=json.dumps(meta), description="x",
                           delivery_channel=None, conversation_id=None)


# ------------------------------------------------------------------ pure --

@pytest.mark.unit
def test_the_ticks_are_the_nightly_and_the_days_free_time():
    # Ellis's answer #4 (2026-10-11): one budgeted free-time run a day stays.
    assert sched.WAKEUP_TICKS == (("nightly", "0 3 * * *"), ("free", "0 13 * * *"))
    assert sched.wake_reason_for_tick("nightly") == "nightly"
    assert sched.wake_reason_for_tick("free") == "free"
    for kind in ("morning", "evening", "reflect"):
        assert kind in sched.RETIRED_WAKEUP_KINDS
        assert sched.is_retired_wakeup_seed({"type": "wakeup_tick", "kind": kind})
    assert not sched.is_retired_wakeup_seed({"type": "wakeup_tick", "kind": "nightly"})
    # Old kinds still name their reason, so history renders.
    assert sched.wake_reason_for_tick("morning") == "morning"
    assert sched.wake_reason_for_tick("reflect") == "reflect"


@pytest.mark.unit
def test_the_cron_seeds_are_the_pulse_and_the_sweep():
    assert sched.EXTRA_CRON_EVENTS == (("expression_pulse", "*/15 * * * *"), ("proactive_sweep", "*/5 * * * *"))
    assert set(sched.RETIRED_CRON_EVENTS) >= {"widget_refresh", "surplus_tick"}
    assert "spend" not in sched.seed_description("proactive_sweep")
    assert sched.seed_description("proactive_sweep").startswith("proactive_sweep — ")
    assert not hasattr(sched, "SURPLUS_CRON") and not hasattr(sched, "is_stale_surplus_seed")


@pytest.mark.unit
def test_routes_and_bounds():
    assert sched.event_route({"type": "proactive_sweep"}) == "proactive_sweep"
    assert sched.event_route({"type": "surplus_tick"}) == "retired"
    assert sched.event_route({"type": "expression_pulse"}) == "pulse"
    assert sched.event_timeout_for({"type": "proactive_sweep"}) == sched.SWEEP_TIMEOUT_SECONDS == 120
    assert sched.event_timeout_for({"type": "surplus_tick"}) == sched.EVENT_TIMEOUT_SECONDS
    # The Sunday nightly may run 900 s after its pack: the backstop sits above it.
    assert sched.event_timeout_for({"type": "wakeup_tick", "kind": "nightly"}) > 900


@pytest.mark.unit
def test_the_sweep_is_plumbing_on_today():
    from services.today_service import PLUMBING_EVENT_TYPES

    assert "proactive_sweep" in PLUMBING_EVENT_TYPES
    assert "surplus_tick" in PLUMBING_EVENT_TYPES, "old rows still hide"


# ------------------------------------------------------------- routing --

@pytest.fixture
def completed(monkeypatch):
    done = []

    async def fake_complete(event_id, result=None, **kw):
        done.append((event_id, result))

    monkeypatch.setattr(sched, "mark_event_completed", fake_complete)
    return done


@pytest.fixture
def cancelled(monkeypatch):
    gone = []

    async def fake_cancel(event_id):
        gone.append(event_id)
        return True

    monkeypatch.setattr("services.scheduled_events_service.cancel_event", fake_cancel)
    return gone


@pytest.mark.unit
async def test_the_nightly_tick_wakes_the_nightly_then_sets_the_morning_scene(monkeypatch, completed):
    from services import expression_service, mind_service

    woke, pulses = [], []

    async def fake_wake(reason, **kw):
        woke.append(reason)
        return {"ok": True, "note": "quiet day"}

    monkeypatch.setattr(mind_service, "wake", fake_wake)
    monkeypatch.setattr(expression_service, "pulse_in_background",
                        lambda reason, extra=None: pulses.append((reason, extra)))
    await sched._execute_event(_event({"type": "wakeup_tick", "kind": "nightly"}))
    assert woke == ["nightly"]
    assert pulses and pulses[0][0] == "nightly" and "morning" in (pulses[0][1] or "").lower()
    assert completed and completed[0][0] == "ev-1"


@pytest.mark.unit
@pytest.mark.parametrize("meta", [{"type": "wakeup_tick", "kind": "morning"},
                                  {"type": "wakeup_tick", "kind": "evening"},
                                  {"type": "wakeup_tick", "kind": "reflect"},
                                  {"type": "wakeup_tick", "kind": "stale_thread_check"},
                                  {"type": "surplus_tick"}])
async def test_a_retired_row_that_fires_is_cancelled_not_run(monkeypatch, completed, cancelled, meta):
    from services import expression_service, mind_service

    async def no_wake(*a, **k):
        raise AssertionError("a retired tick never wakes the mind")

    def no_pulse(*a, **k):
        raise AssertionError("nor pulses the surfaces")

    monkeypatch.setattr(mind_service, "wake", no_wake)
    monkeypatch.setattr(expression_service, "pulse_in_background", no_pulse)
    await sched._execute_event(_event(meta))
    assert cancelled == ["ev-1"] and completed == []


@pytest.mark.unit
async def test_a_self_wake_carries_its_event_meta_when_the_wake_takes_it(monkeypatch, completed):
    from services import mind_service

    seen = []

    async def fake_wake(reason, *, extra=None, event_meta=None, **kw):
        seen.append((reason, extra, event_meta))
        return {"ok": True, "note": "n"}

    monkeypatch.setattr(mind_service, "wake", fake_wake)
    meta = {"type": "mind_wake", "why": "Tell Trivia crew when a used pair under $150 shows up",
            "ping": {"ping_id": "p1", "chat_identifier": "chat42"}}
    await sched._execute_event(_event(meta))
    assert seen == [("self", meta["why"], meta)]


@pytest.mark.unit
async def test_a_self_wake_without_event_meta_support_still_runs(monkeypatch, completed):
    from services import mind_service

    seen = []

    async def old_wake(reason, *, extra=None, message=None):
        seen.append((reason, extra))
        return {"ok": True, "note": "n"}

    monkeypatch.setattr(mind_service, "wake", old_wake)
    await sched._execute_event(_event({"type": "mind_wake", "why": "check the parcel"}))
    assert seen == [("self", "check the parcel")]


@pytest.mark.unit
async def test_the_sweep_route_runs_care_watch_and_the_noticing_sweep(monkeypatch, completed, capsys):
    import sys
    import types

    from services import care_watch

    async def check(now=None):
        return {"alerts": 1, "checked": 3}

    async def sweep():
        return {"due": 2, "outcomes": 1}

    fake = types.ModuleType("services.noticing_service")
    fake.sweep = sweep
    fake_module(monkeypatch, "services.noticing_service", fake)
    monkeypatch.setattr(care_watch, "check", check)
    await sched._execute_event(_event({"type": "proactive_sweep"}))
    assert completed and json.loads(completed[0][1]) == {"care": {"alerts": 1, "checked": 3},
                                                          "notice": {"due": 2, "outcomes": 1}, "snoozes": 0}
    assert "[sweep] ok" in capsys.readouterr().out
    assert sched.last_sweep()["at"] is not None


@pytest.mark.unit
async def test_the_sweep_survives_a_broken_care_check_and_no_noticing_service(monkeypatch, completed, capsys):
    import sys

    from services import care_watch

    async def broken(now=None):
        raise RuntimeError("hub board unreadable")

    fake_module(monkeypatch, "services.noticing_service", None)   # import raises ImportError
    monkeypatch.setattr(care_watch, "check", broken)
    await sched._execute_event(_event({"type": "proactive_sweep"}))
    out = json.loads(completed[0][1])
    assert "hub board unreadable" in out["care_error"] and "notice" not in out
    assert "[sweep] ok" in capsys.readouterr().out


# ------------------------------------------------------------ the boot --

async def _live_rows():
    from sqlalchemy import select

    from services.database import ScheduledEventModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(ScheduledEventModel))).scalars().all()
    return rows


def _seed_key(r):
    meta = json.loads(r.event_metadata or "{}")
    return (meta.get("type"), meta.get("kind"), r.recurrence_pattern)


@pytest.mark.integration
async def test_boot_seeds_the_nightly_and_the_sweep_and_cancels_the_old(clean_db):
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _utcnow

    async with async_session() as s:
        for eid, meta, cron in (
            ("m", {"type": "wakeup_tick", "kind": "morning"}, "0 7 * * *"),
            ("e", {"type": "wakeup_tick", "kind": "evening"}, "0 18 * * *"),
            ("r", {"type": "wakeup_tick", "kind": "reflect"}, "0 9 * * 0"),
            ("s", {"type": "surplus_tick"}, "*/10 * * * *"),
            ("owner", {"type": "reminder"}, "0 7 * * *"),
        ):
            s.add(ScheduledEventModel(id=eid, description=eid, scheduled_at=_utcnow(), next_fire_at=_utcnow(),
                                      recurrence_pattern=cron, status="pending", event_metadata=json.dumps(meta)))
        await s.commit()

    await sched._ensure_wakeup_events()

    rows = await _live_rows()
    by_id = {r.id: r for r in rows}
    for eid in ("m", "e", "r", "s"):
        assert by_id[eid].status == "cancelled", eid
    assert by_id["owner"].status == "pending", "Ellis's own 07:00 reminder is not a tick"
    live = {_seed_key(r) for r in rows if r.status == "pending" and r.id != "owner"}
    assert live == {("wakeup_tick", "nightly", "0 3 * * *"), ("wakeup_tick", "free", "0 13 * * *"),
                    ("expression_pulse", None, "*/15 * * * *"), ("proactive_sweep", None, "*/5 * * * *")}

    # Idempotent: a second boot adds nothing.
    await sched._ensure_wakeup_events()
    again = [r for r in await _live_rows() if r.status == "pending"]
    assert len(again) == len([r for r in rows if r.status == "pending"])


@pytest.mark.integration
@pytest.mark.parametrize("mode", ["off", "shadow", "notice", "legacy"])
async def test_the_seeds_are_the_same_in_every_mode(clean_db, monkeypatch, mode):
    monkeypatch.setenv("EDWARD_PROACTIVITY", mode)
    await sched._ensure_wakeup_events()
    live = sorted(_seed_key(r) for r in await _live_rows() if r.status == "pending")
    assert live == sorted([("wakeup_tick", "nightly", "0 3 * * *"), ("wakeup_tick", "free", "0 13 * * *"),
                           ("expression_pulse", None, "*/15 * * * *"), ("proactive_sweep", None, "*/5 * * * *")])


@pytest.mark.unit
def test_an_unknown_mode_is_off(monkeypatch):
    noticing_service = pytest.importorskip("services.noticing_service")
    monkeypatch.setenv("EDWARD_PROACTIVITY", "legacy")
    assert noticing_service.mode() == "off"
    monkeypatch.setenv("EDWARD_PROACTIVITY", "SHADOW")
    assert noticing_service.mode() in ("shadow", "off")


@pytest.mark.unit
async def test_the_sweep_brings_snoozed_pushes_back(monkeypatch, completed):
    """"Snooze 1h" on a push was re-sent by the interrupt ladder's advance
    loop (every 30 s). The ladder is gone; the sweep re-sends due snoozes
    now (within five minutes of due)."""
    import sys

    from services import care_watch, push_ledger

    async def check(now=None):
        return {"alerts": 0}

    resent = []

    async def resend_due_snoozes(now=None):
        resent.append(now)
        return 2

    fake_module(monkeypatch, "services.noticing_service", None)
    monkeypatch.setattr(care_watch, "check", check)
    monkeypatch.setattr(push_ledger, "resend_due_snoozes", resend_due_snoozes)
    await sched._execute_event(_event({"type": "proactive_sweep"}))
    assert resent == [None]
    assert json.loads(completed[0][1])["snoozes"] == 2


@pytest.mark.unit
async def test_a_broken_snooze_scan_never_stops_the_sweep(monkeypatch, completed):
    import sys

    from services import care_watch, push_ledger

    async def check(now=None):
        return {"alerts": 1}

    async def broken(now=None):
        raise RuntimeError("push_log unreadable")

    fake_module(monkeypatch, "services.noticing_service", None)
    monkeypatch.setattr(care_watch, "check", check)
    monkeypatch.setattr(push_ledger, "resend_due_snoozes", broken)
    await sched._execute_event(_event({"type": "proactive_sweep"}))
    out = json.loads(completed[0][1])
    assert out["care"] == {"alerts": 1} and "push_log unreadable" in out["snoozes_error"]
