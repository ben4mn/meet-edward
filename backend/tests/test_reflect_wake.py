"""Sunday reflection and the meeting hint — mind rebuild Phase 7.

The weekly-patterns job becomes a ``reflect`` wake seeded on Sunday 09:00.
Its pack carries the week raw: the mind's own ledger, every push and its
outcome, Ellis's verdicts, what Edward did, his live intentions and what he
wished for. The morning wake tells him meeting prep is his to schedule.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 20, 9, 0, tzinfo=timezone.utc)  # a Sunday


def test_the_sunday_reflect_tick_is_retired_for_the_nightly():
    """Since 2026-10-11 the week's reflection runs in the 03:00 nightly on
    Sundays; the Sunday 09:00 tick is retired (its rows cancelled at boot).
    The old kind still names its reason so history renders."""
    from services.scheduler_service import RETIRED_WAKEUP_KINDS, WAKEUP_TICKS, wake_reason_for_tick

    assert WAKEUP_TICKS[0] == ("nightly", "0 3 * * *") and "reflect" not in dict(WAKEUP_TICKS)
    assert "reflect" in RETIRED_WAKEUP_KINDS
    assert wake_reason_for_tick("reflect") == "reflect"


def _week(**kw):
    base = dict(
        wakes=[{"at": "2026-09-19T18:00:00Z", "source": "evening", "outcome": "acted",
                "note": "Pushed Ellis about the library hold.",
                "tools": [{"n": "send_push_notification", "ok": True}]}],
        pushes="  Sat 4:35pm mind: Library hold ready — opened",
        verdicts=[{"kind": "heartbeat", "verdict": "should_have_told_me", "note": "that was real",
                   "at": "2026-09-17T10:00:00Z"}],
        did=["Pushed Ellis about the library hold at 16:35 (Sat) (memory_id: d1)"],
        wishes=[{"title": "Read PDF attachments", "status": "open", "times_wanted": 2}],
    )
    base.update(kw)
    return base


def test_the_sunday_nightly_pack_carries_the_week_raw():
    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    text = render_pack(_pack(reason="nightly", now=NOW + timedelta(days=7), week=_week()))  # a Sunday
    assert "Your week" in text
    assert "evening: acted — Pushed Ellis about the library hold." in text
    assert "Library hold ready — opened" in text
    assert "should_have_told_me" in text and "that was real" in text
    assert "Pushed Ellis about the library hold at 16:35" in text
    assert "Read PDF attachments" in text and "wanted 2x" in text
    low = text.lower()
    assert "close the intentions" in low
    assert "what you learned about ellis" in low and "your own judgment" in low
    assert "confidence" in low, "thoughts about Ellis are beliefs, capped as such"


def test_only_the_nightly_gets_the_week():
    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    text = render_pack(_pack(reason="self", week=_week()))
    assert "Your week" not in text and "close the intentions" not in text.lower()


def test_no_pack_carries_the_morning_meeting_prep_hint_any_more():
    """The morning tick is gone (2026-10-11); a meeting that needs prep is
    the noticer's (a deadline) or a promise's, not a scheduled look."""
    from services.mind_service import WAKE_REASONS, render_pack
    from tests.test_mind_service import _pack

    for reason in WAKE_REASONS:
        text = render_pack(_pack(reason=reason, calendar=["9:00am Dentist", "2:00pm Sync with Parker"]))
        assert "9:00am Dentist" in text
        assert "Meeting prep is yours to schedule" not in text, reason


async def test_collect_week_reads_each_source_and_survives_a_failure(monkeypatch):
    import services.mind_service as ms

    async def recent(limit=20):
        return [
            {"at": (NOW - timedelta(days=1)).isoformat(), "source": "evening", "outcome": "acted", "note": "n1",
             "tools": [{"n": "leave_note", "ok": True}]},
            {"at": (NOW - timedelta(days=9)).isoformat(), "source": "morning", "outcome": "left_alone", "note": "old"},
        ]

    async def did_since(since, *, limit):
        return ["Pushed Ellis (memory_id: d1)"]

    async def verdicts(since, *, days=2, limit=10):
        assert days == 7 and limit >= 20
        return [{"kind": "heartbeat", "verdict": "right_call"}]

    async def pushes(*, hours, limit):
        assert hours == 24 * 7
        return "  Sat mind: library hold — opened"

    async def wishes(hours=24):
        raise RuntimeError("wishlist down")

    monkeypatch.setattr(ms, "recent", recent)
    monkeypatch.setattr(ms, "_did_lines_since", did_since)
    monkeypatch.setattr(ms, "_verdicts_since", verdicts)
    monkeypatch.setattr("services.push_ledger.recent_text", pushes)
    monkeypatch.setattr("services.wishlist_service.wishes_since", wishes)

    week = await ms.collect_week(NOW)
    assert [w["note"] for w in week["wakes"]] == ["n1"], "only the last seven days"
    assert week["did"] == ["Pushed Ellis (memory_id: d1)"]
    assert week["verdicts"][0]["verdict"] == "right_call"
    assert "library hold" in week["pushes"]
    assert week["wishes"] == [], "a failing source leaves a blank, never a missing pack"


@pytest.mark.integration
async def test_collect_pack_only_gathers_the_week_for_the_sunday_nightly(clean_db, monkeypatch):
    """(``clean_db`` because the other collectors touch the database; a bare
    async test would bind the engine to a loop the next test does not use.)"""
    import services.mind_service as ms

    calls = []

    async def fake_week(now, days=7):
        calls.append((now, days))
        return {"wakes": [], "pushes": "", "verdicts": [], "did": [], "wishes": []}

    monkeypatch.setattr(ms, "collect_week", fake_week)
    monkeypatch.setattr(ms, "last_wake_at", lambda: _none())
    ms.reset_state()
    p = await ms.collect_pack("self", now=NOW)
    assert p.week == {} and calls == []
    sunday = datetime(2026, 9, 20, 8, 0, tzinfo=timezone.utc)  # 03:00 Sunday in Springfield
    p = await ms.collect_pack("nightly", now=sunday)
    assert calls == [(sunday, 7)] and "wakes" in p.week
    p = await ms.collect_pack("nightly", now=sunday + timedelta(days=1))
    assert calls[-1] == (sunday + timedelta(days=1), 1), "a weeknight reads the day"


async def _none():
    return None


async def test_a_leftover_reflect_tick_is_cancelled_not_run(monkeypatch):
    from services import expression_service, mind_service, scheduler_service
    from tests.test_scheduler_widget_refresh import _event

    woke, refreshed, cancelled = [], [], []

    async def fake_wake(reason, **kw):
        woke.append(reason)
        return {"ok": True, "note": "n"}

    async def fake_cancel(event_id):
        cancelled.append(event_id)
        return True

    monkeypatch.setattr(mind_service, "wake", fake_wake)
    monkeypatch.setattr(expression_service, "pulse_in_background", lambda r, extra=None: refreshed.append(r))
    monkeypatch.setattr("services.scheduled_events_service.cancel_event", fake_cancel)
    await scheduler_service._execute_event(_event({"type": "wakeup_tick", "kind": "reflect"}))
    assert woke == [] and refreshed == [] and cancelled == ["ev-1"]


@pytest.mark.integration
async def test_the_nightly_is_seeded_at_three_and_no_reflect_tick(clean_db):
    from sqlalchemy import select

    from services import scheduler_service
    from services.database import ScheduledEventModel, async_session

    await scheduler_service._ensure_wakeup_events()
    async with async_session() as s:
        rows = (await s.execute(select(ScheduledEventModel))).scalars().all()
    ticks = {json.loads(r.event_metadata)["kind"]: r for r in rows
             if r.status == "pending" and json.loads(r.event_metadata or "{}").get("type") == "wakeup_tick"}
    assert set(ticks) == {"nightly", "free"}, "no reflect tick (free time is Ellis's answer #4)"
    assert ticks["nightly"].recurrence_pattern == "0 3 * * *"


@pytest.mark.integration
async def test_the_nightly_pack_lists_the_days_did_memories(clean_db):
    import services.mind_service as ms
    from services.memory_service import Memory, store_memory

    await store_memory(Memory(id=None, content="Pushed Ellis about the library hold at 16:35", memory_type="context",
                              importance=0.5, origin="did", about="self"))
    ms.reset_state()
    pack = await ms.collect_pack("nightly")
    assert any("Pushed Ellis about the library hold" in line for line in pack.week["did"])
    assert "Pushed Ellis about the library hold" in ms.render_pack(pack)
