"""``proactivity_report`` — one place to see whether noticing works.

Why (proactivity-spec §2.9, 2026-10-11): a dead noticer looks exactly like
a quiet day. The report counts passes (and failed, deferred and
parse-failed ones apart from an honest ``[]``), noticings and what the gate
and the speaker did with them, and gives a verdict a health check can read:
``off`` / ``stalled`` / ``failing`` / ``ok``. The trust line puts the pings
Ellis sent beside what Edward noticed and said on his own.
"""

import json
import sys
from datetime import datetime, timedelta, timezone

import pytest

from services import noticing_service
from tests.area1_fakes import fake_module

pytestmark = pytest.mark.integration

def _clock():
    """Now, read when called — module constants go stale in a long suite."""
    return datetime.now(timezone.utc)


def _naive(dt):
    return dt.astimezone(timezone.utc).replace(tzinfo=None)


@pytest.fixture
def no_pings(monkeypatch):
    fake_module(monkeypatch, "services.pings.hooks", None)   # import → ImportError
    fake_module(monkeypatch, "services.pings", None)


@pytest.fixture
def swept(monkeypatch):
    monkeypatch.setattr(noticing_service, "_last_sweep_at", _clock() - timedelta(minutes=3))


async def _pass(outcome="ok", *, ago_min=1, error_class=None, source="noticer"):
    from services.database import LedgerEventModel, async_session

    detail = {"events": 3, "noticed": 0}
    if error_class:
        detail["error_class"] = error_class
    async with async_session() as s:
        s.add(LedgerEventModel(kind="notice", source=source, outcome=outcome, ref_id="p",
                               detail_json=json.dumps(detail), created_at=_naive(_clock() - timedelta(minutes=ago_min))))
        await s.commit()


async def _inbound(n, *, ago_min=30, status="escalated", from_me=False, raw=None):
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        for i in range(n):
            s.add(HeartbeatEventModel(source="imessage", event_type="message_received", summary="hi",
                                      is_from_user=from_me, triage_status=status,
                                      raw_data=json.dumps(raw) if raw else None,
                                      created_at=_naive(_clock() - timedelta(minutes=ago_min))))
        await s.commit()


async def _noticing(**kw):
    from services.database import NoticingModel, async_session

    base = dict(kind="deadline", claim="c", created_at=_naive(_clock() - timedelta(minutes=2)))
    base.update(kw)
    async with async_session() as s:
        s.add(NoticingModel(**base))
        await s.commit()


async def test_off_mode_with_a_recent_sweep_reads_off(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    r = await noticing_service.proactivity_report(days=7)
    assert r["mode"] == "off" and r["verdict"] == "off" and r["push_scope"] == "floor"
    assert r["trust"].startswith("pings: (the ping runner is not installed)")
    assert "proactive:" in r["trust"]
    assert r["stalls"]["last_sweep_at"]


async def test_no_sweep_for_15_minutes_is_stalled(clean_db, monkeypatch, no_pings):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    monkeypatch.setattr(noticing_service, "_last_sweep_at", _clock() - timedelta(minutes=20))
    monkeypatch.setattr(noticing_service, "_STARTED_AT", _clock() - timedelta(hours=2))
    assert (await noticing_service.proactivity_report())["verdict"] == "stalled"


async def test_a_fresh_process_is_not_stalled_before_its_first_sweep(clean_db, monkeypatch, no_pings):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    monkeypatch.setattr(noticing_service, "_last_sweep_at", None)
    monkeypatch.setattr(noticing_service, "_STARTED_AT", _clock() - timedelta(minutes=2))
    assert (await noticing_service.proactivity_report())["verdict"] == "off"


async def test_ten_inbound_and_no_passes_is_stalled(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "shadow")
    await _inbound(10)
    await _inbound(5, status="ping")             # pings never count as inbound for noticing
    r = await noticing_service.proactivity_report()
    assert r["stalls"]["inbound_6h"] == 10 and r["stalls"]["passes_6h"] == 0
    assert r["verdict"] == "stalled"


async def test_passes_keep_it_ok(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "shadow")
    await _inbound(10)
    for _ in range(4):
        await _pass()
    r = await noticing_service.proactivity_report()
    assert r["verdict"] == "ok" and r["stalls"]["passes_6h"] == 4


async def test_half_the_passes_failing_is_failing(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    await _pass()
    await _pass()
    await _pass("failed", error_class="parse")
    await _pass("failed", error_class="TimeoutError")
    r = await noticing_service.proactivity_report()
    assert r["verdict"] == "failing"
    today = r["days"][-1]
    assert today["failed"] == 2 and today["parse_failures"] == 1 and today["passes"] == 4


async def test_three_passes_one_failed_is_too_few_to_call_failing(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    await _pass()
    await _pass("failed")
    await _pass("failed")
    assert (await noticing_service.proactivity_report())["verdict"] == "ok"


async def test_a_held_item_past_due_is_failing(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    await _noticing(status="held", due_at=_naive(_clock() - timedelta(minutes=30)))
    r = await noticing_service.proactivity_report()
    assert r["stalls"]["held_past_due"] == 1 and r["verdict"] == "failing"


async def test_two_speak_failures_in_a_day_is_failing(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    await _noticing(status="failed", attempts=2)
    await _noticing(status="failed", attempts=2)
    assert (await noticing_service.proactivity_report())["verdict"] == "failing"


async def test_days_count_what_was_noticed_and_spoken(clean_db, monkeypatch, no_pings, swept):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    await _noticing(status="spoken", channel="push", surfaced_at=_naive(_clock() - timedelta(minutes=50)))
    await _noticing(status="spoken", channel="note", surfaced_at=_naive(_clock() - timedelta(minutes=50)),
                    outcome="acted")
    await _noticing(status="silent", decision="silent")
    await _noticing(status="held", due_at=_naive(_clock() + timedelta(hours=1)))
    # care_watch writes its ledger row (the key that counts) and, beside it, a
    # care noticing: one alert, counted once.
    await _noticing(status="spoken", channel="push", origin="care", kind="household")
    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        s.add(LedgerEventModel(kind="care_alert", source="care_watch", outcome="pushed", ref_id="care:biscuit:d:18:00",
                               created_at=_naive(_clock() - timedelta(minutes=1))))
        s.add(LedgerEventModel(kind="care_alert", source="care_watch", outcome="failed", ref_id="care:biscuit:d:06:00",
                               created_at=_naive(_clock() - timedelta(minutes=1))))
        await s.commit()
    await _pass()
    r = await noticing_service.proactivity_report()
    today = r["days"][-1]
    assert today["noticed"] == 4                 # the care alert is not a noticing
    assert today["spoken"] == 2 and today["pushes"] == 1 and today["notes"] == 1
    assert today["silent"] == 1 and today["held"] == 1 and today["acted"] == 1
    assert today["care_alerts"] == 1
    assert "4 noticed / 2 spoken / 1 pushed" in r["trust"]
    assert len(r["days"]) == 7
    assert "noticer passes" in r["headline"]


async def test_the_trust_line_reads_the_pings_report(clean_db, monkeypatch, swept):
    import types

    async def report(days):
        return {"days": days, "total": 21, "by_status": {"answered": 18, "fallback": 1, "declined": 1,
                                                          "failed": 1}, "answered_within_2m": 15}

    fake = types.ModuleType("services.pings.hooks")
    fake.report = report
    fake_module(monkeypatch, "services.pings.hooks", fake)
    monkeypatch.setenv("EDWARD_PROACTIVITY", "off")
    r = await noticing_service.proactivity_report()
    assert r["trust"].startswith("pings: 15/21 answered ≤2 min, 4 late, 1 unanswered")


async def test_the_report_never_raises(monkeypatch, no_pings):
    import services.database as database

    def broken(*a, **k):
        raise RuntimeError("no database")

    monkeypatch.setattr(database, "async_session", broken)
    r = await noticing_service.proactivity_report()
    assert "error" in r and r["verdict"] in ("failing", "stalled", "off", "ok")


async def test_the_last_care_check_comes_from_care_watch(clean_db, monkeypatch, no_pings, swept):
    """care_watch (1A) keeps ``last_check() -> {"at": <iso>, "result": …}``;
    the report read a ``last_check_at`` it never had, so the stall line
    always said the late-dose check had never run (integration, 2026-10-11)."""
    from services import care_watch

    at = (_clock() - timedelta(minutes=4)).replace(microsecond=0)
    monkeypatch.setattr(care_watch, "_last_check", {"at": at.isoformat(), "result": {"alerts": 0}})
    rep = await noticing_service.proactivity_report(days=1)
    assert rep["stalls"]["last_care_check_at"] == at.isoformat()

    monkeypatch.setattr(care_watch, "_last_check", {"at": None, "result": None})
    rep = await noticing_service.proactivity_report(days=1)
    assert rep["stalls"]["last_care_check_at"] is None
