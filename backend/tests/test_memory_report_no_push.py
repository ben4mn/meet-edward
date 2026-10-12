"""The daily memory report no longer pushes (proactivity rebuild, F1, 2026-10-11).

It used to hand its figures to Edward as a due-now scheduled event in its
own thread (``memory-report-<date>``), and he pushed Ellis the link — a push
a day about the memory store's plumbing. Ellis is reached only through
``reach_service`` now, for what matters to him. ``--push`` (what the 09:05
LaunchAgent passes) writes one ``memory_report`` ledger row with the
verdict and the summary counts; ``GET /api/health/report`` shows the
newest. No push, no scheduled event, no conversation.
"""

from __future__ import annotations

import json

import pytest

from tests.test_memory_health_report import _metrics


@pytest.mark.unit
def test_the_summary_counts_fit_a_ledger_row():
    from scripts.memory_health_report import assess, summary_counts

    m = _metrics(memories_last_24h=20)
    s = summary_counts(m, assess(m))
    assert s["status"] == "needs a look" and s["misses"] == 1
    assert s["memories_last_24h"] == 20 and s["live"] == 1520 and s["open_flags"] == 1
    assert s["disk_free_gb"] == 25 and s["checkpoint_mb"] == 374
    assert len(json.dumps(s)) < 600


@pytest.mark.unit
def test_the_hand_off_to_edward_is_gone():
    import scripts.memory_health_report as mhr

    for gone in ("deliver_via_edward", "build_event_description", "ensure_report_conversation",
                 "report_conversation_id"):
        assert not hasattr(mhr, gone), gone


@pytest.mark.integration
async def test_push_writes_a_ledger_row_and_nothing_else(clean_db, monkeypatch):
    import scripts.memory_health_report as mhr
    from sqlalchemy import func, select

    from services import push_service
    from services.database import ConversationModel, LedgerEventModel, ScheduledEventModel, async_session

    async def collect():
        return _metrics()

    async def no_push(*a, **k):
        raise AssertionError("the memory report never pushes")

    monkeypatch.setattr(mhr, "collect", collect)
    monkeypatch.setattr(push_service, "send_push_notification", no_push)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert await mhr.main_async(push=True) == 0

    async with async_session() as s:
        rows = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "memory_report"))).scalars().all()
        events = (await s.execute(select(func.count()).select_from(ScheduledEventModel))).scalar_one()
        convs = (await s.execute(select(func.count()).select_from(ConversationModel)
                                 .where(ConversationModel.id.like("memory-report-%")))).scalar_one()
    assert [(r.source, r.outcome, r.reason) for r in rows] == [("script", "ok", "on track")]
    detail = json.loads(rows[0].detail_json)
    assert detail["memories_last_24h"] == 9 and detail["status"] == "on track"
    assert events == 0 and convs == 0


@pytest.mark.integration
async def test_without_push_it_only_prints(clean_db, monkeypatch, capsys):
    import scripts.memory_health_report as mhr
    from sqlalchemy import func, select

    from services.database import LedgerEventModel, async_session

    async def collect():
        return _metrics()

    monkeypatch.setattr(mhr, "collect", collect)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    assert await mhr.main_async(push=False) == 0
    assert "Memory: on track" in capsys.readouterr().out
    async with async_session() as s:
        assert (await s.execute(select(func.count()).select_from(LedgerEventModel))).scalar_one() == 0
