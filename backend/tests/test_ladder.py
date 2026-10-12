"""The learning ladder as Ellis and the nightly see it (proactivity-spec §2.8, G16, G17).

Why (2026-10-11): demotions have to be visible, or a category that went
quiet looks like Edward lost interest. The fold itself is pure and pinned
in ``test_speak_gate.py``; here the levels are read back from real rows —
the health report's ``categories``, the code-written digest the nightly and
the speaker read — and care alerts never enter it. No model writes any of it.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import noticing_service as ns
from services import speak_gate

pytestmark = pytest.mark.integration


async def _spoken(nid, category, outcome, *, hours_ago, channel="push", origin="noticer"):
    from services.database import NoticingModel, async_session

    at = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).replace(tzinfo=None)
    async with async_session() as s:
        s.add(NoticingModel(id=nid, kind="deadline", category=category, claim="c", status="spoken", channel=channel,
                            origin=origin, surfaced_at=at, created_at=at, outcome=outcome, outcome_at=at))
        await s.commit()


async def test_two_ignored_pushes_show_as_a_demotion_in_the_report(clean_db, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    await _spoken("f1", "family", "acted", hours_ago=72, channel="note")
    await _spoken("f2", "family", "ignored", hours_ago=48)
    await _spoken("f3", "family", "ignored", hours_ago=24)
    levels = await ns._category_levels(datetime.now(timezone.utc))
    assert levels["family"]["level"] == "note"
    assert levels["family"]["last"][-2:] == ["ignored", "ignored"]
    assert levels["family"]["probation_ends"]
    assert levels["security"]["level"] == "note"
    assert levels["travel"]["level"] == "push"


async def test_care_alerts_never_enter_the_ladder(clean_db):
    await _spoken("c1", "household", "ignored", hours_ago=48, origin="care")
    await _spoken("c2", "household", "ignored", hours_ago=24, origin="care")
    history = await ns._history(datetime.now(timezone.utc))
    assert history == ()
    assert speak_gate.channel_ceiling("household", history, datetime.now(timezone.utc)) == "push"


async def test_the_digest_says_today_and_the_levels_in_code(clean_db, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    await _spoken("t1", "travel", "acted", hours_ago=1)
    await _spoken("f2", "family", "ignored", hours_ago=48)
    await _spoken("f3", "family", "ignored", hours_ago=24)
    lines = await ns.digest()
    text = "\n".join(lines)
    assert lines[0].startswith("Noticing is on")
    assert "spoke" in text and "Travel: push" in text
    assert "Family: note" in text and "ignored" in text
    assert "Security: note" in text


async def test_the_report_carries_the_levels(clean_db, monkeypatch):
    monkeypatch.setenv("EDWARD_PROACTIVITY", "notice")
    monkeypatch.setattr(ns, "_last_sweep_at", datetime.now(timezone.utc))
    await _spoken("t1", "travel", "acted", hours_ago=1)
    r = await ns.proactivity_report()
    assert r["categories"]["travel"]["level"] == "push"
