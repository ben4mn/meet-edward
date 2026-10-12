"""Ticks become wakes — mind rebuild Phase 3, narrowed 2026-10-11.

Phase 3 made the morning and evening ticks invite the mind and retired the
stale-thread and push-outcome ticks (what they computed is in the raw pack
already). The proactivity rebuild (2026-10-11) retired the morning, evening
and Sunday-reflect ticks too: the only tick is the silent 03:00 nightly,
and a retired row that fires is cancelled. ``wakeup_service.py`` and
``wakeup_signals.py`` are deleted; the Journal and the health report read
the mind's ledger instead of ``wakeup_ticks``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]


def test_the_default_ticks_are_the_nightly_and_free_time():
    from services.scheduler_service import WAKEUP_TICKS

    assert WAKEUP_TICKS == (("nightly", "0 3 * * *"), ("free", "0 13 * * *"))


@pytest.mark.parametrize(
    "meta,expected",
    [
        ({"type": "wakeup_tick", "kind": "nightly"}, False),
        ({"type": "wakeup_tick", "kind": "morning"}, True),
        ({"type": "wakeup_tick", "kind": "evening"}, True),
        ({"type": "wakeup_tick", "kind": "reflect"}, True),
        ({"type": "wakeup_tick", "kind": "morning_briefing"}, True),
        ({"type": "wakeup_tick", "kind": "evening_briefing"}, True),
        ({"type": "wakeup_tick", "kind": "stale_thread_check"}, True),
        ({"type": "wakeup_tick", "kind": "push_outcome_check"}, True),
        ({"type": "expression_pulse"}, False),
        ({}, False),
    ],
)
def test_is_retired_wakeup_seed(meta, expected):
    from services.scheduler_service import is_retired_wakeup_seed

    assert is_retired_wakeup_seed(meta) is expected


@pytest.mark.parametrize("kind,expected", [("morning", "morning"), ("morning_briefing", "morning"),
                                           ("evening", "evening"), ("evening_briefing", "evening"),
                                           ("nightly", "nightly"), ("reflect", "reflect"),
                                           ("stale_thread_check", "manual"), (None, "manual")])
def test_wake_reason_for_tick(kind, expected):
    from services.scheduler_service import wake_reason_for_tick

    assert wake_reason_for_tick(kind) == expected


def test_wakeup_service_is_gone():
    assert not (BACKEND / "services" / "wakeup_service.py").exists()
    assert not (BACKEND / "services" / "wakeup_signals.py").exists()
    assert not (BACKEND / "routers" / "wakeup.py").exists()
    for path in list((BACKEND / "services").rglob("*.py")) + list((BACKEND / "routers").rglob("*.py")) + [BACKEND / "main.py"]:
        text = path.read_text()
        assert "wakeup_service" not in text and "wakeup_signals" not in text, path


@pytest.mark.integration
async def test_wakeup_tick_rows_route_to_the_mind(clean_db, monkeypatch):
    from services import scheduler_service
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _utcnow

    calls = []

    async def fake_wake(reason, **kw):
        calls.append(reason)
        return {"ok": True, "note": "quiet"}

    pulses = []
    monkeypatch.setattr("services.mind_service.wake", fake_wake)
    monkeypatch.setattr("services.expression_service.pulse_in_background",
                        lambda reason, extra=None: pulses.append(reason))

    async with async_session() as s:
        for eid, kind in (("ev-n", "nightly"), ("ev-m", "morning"), ("ev-old", "evening_briefing")):
            s.add(ScheduledEventModel(id=eid, description=kind, scheduled_at=_utcnow(), next_fire_at=_utcnow(),
                                      status="processing", event_metadata=json.dumps({"type": "wakeup_tick", "kind": kind})))
        await s.commit()
    for eid in ("ev-n", "ev-m", "ev-old"):
        async with async_session() as s:
            ev = await s.get(ScheduledEventModel, eid)
        await scheduler_service._execute_event(ev)
    assert calls == ["nightly"], "a retired tick that fires is cancelled, never a wake"
    assert pulses == ["nightly"], "the surfaces pulse follows the nightly (the morning scene)"
    async with async_session() as s:
        assert (await s.get(ScheduledEventModel, "ev-n")).status == "completed"
        assert (await s.get(ScheduledEventModel, "ev-m")).status == "cancelled"
        assert (await s.get(ScheduledEventModel, "ev-old")).status == "cancelled"


@pytest.mark.integration
async def test_retired_tick_kinds_are_cancelled_and_new_ones_seeded(clean_db):
    from sqlalchemy import select

    from services import scheduler_service
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _utcnow

    async with async_session() as s:
        s.add(ScheduledEventModel(id="stale", description="old", scheduled_at=_utcnow(), next_fire_at=_utcnow(),
                                  recurrence_pattern="30 */2 * * *", status="pending",
                                  event_metadata=json.dumps({"type": "wakeup_tick", "kind": "stale_thread_check"})))
        s.add(ScheduledEventModel(id="oldmorning", description="old", scheduled_at=_utcnow(), next_fire_at=_utcnow(),
                                  recurrence_pattern="0 7 * * *", status="pending",
                                  event_metadata=json.dumps({"type": "wakeup_tick", "kind": "morning_briefing"})))
        await s.commit()

    await scheduler_service._ensure_wakeup_events()

    async with async_session() as s:
        rows = (await s.execute(select(ScheduledEventModel))).scalars().all()
    by_id = {r.id: r for r in rows}
    assert by_id["stale"].status == "cancelled"
    assert by_id["oldmorning"].status == "cancelled"
    live = {json.loads(r.event_metadata)["kind"]: r for r in rows
            if r.status == "pending" and json.loads(r.event_metadata or "{}").get("type") == "wakeup_tick"}
    assert set(live) == {"nightly", "free"}
    assert live["nightly"].recurrence_pattern == "0 3 * * *"

    # Idempotent on the next boot.
    await scheduler_service._ensure_wakeup_events()
    async with async_session() as s:
        n = len([r for r in (await s.execute(select(ScheduledEventModel))).scalars().all() if r.status == "pending"
                 and json.loads(r.event_metadata or "{}").get("type") == "wakeup_tick"])
    assert n == 2


@pytest.mark.integration
async def test_journal_week_reads_the_mind_ledger(clean_db):
    from services import mind_service
    from services.journal_service import build_weekly_journal

    for source, outcome, note in (("morning", "acted", "Pushed Ellis about the ACME drop"),
                                  ("morning", "left_alone", "quiet"), ("self", "left_alone", "nothing moved")):
        await mind_service._record_now(source=source, outcome=outcome, note=note, tools=[])
    summary = await build_weekly_journal()
    wakes = summary["wakes"]
    assert {r["outcome"]: r["count"] for r in wakes["by_outcome"]} == {"acted": 1, "left_alone": 2}
    assert wakes["total_in_window"] == 3
    assert wakes["recent"][0]["note"] == "nothing moved" and wakes["recent"][0]["source"] == "self"
    assert "wakeup_ticks" not in summary


def test_health_report_summarizes_wakes_from_ledger_rows():
    from services import health_report_service as hr

    events = [
        {"kind": "mind", "source": "morning", "outcome": "acted"},
        {"kind": "mind", "source": "morning", "outcome": "left_alone"},
        {"kind": "mind", "source": "self", "outcome": "failed"},
        {"kind": "decision", "source": "pulse", "outcome": "changed"},
    ]
    w = hr.summarize_wakes(events)
    assert w == {"wakes": 3, "by_source": {"morning": {"acted": 1, "left_alone": 1}, "self": {"failed": 1}},
                 "by_outcome": {"acted": 1, "left_alone": 1, "failed": 1}}
