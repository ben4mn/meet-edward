"""Push engagement after the engine layer (mind rebuild Phase 8).

Two writers remain: ``mark_event_acted_on`` and ``mark_recent_pushes_acted``.
The per-entity affinity table and the relationship-thread refresh are
gone; the heartbeat event's ``user_acted_on_it`` is the signal.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest


def test_only_the_two_markers_remain():
    from services import push_engagement

    assert not hasattr(push_engagement, "get_candidate_engagement_prior")
    assert not hasattr(push_engagement, "extract_affinity_labels")
    assert callable(push_engagement.mark_event_acted_on) and callable(push_engagement.mark_recent_pushes_acted)


@pytest.mark.integration
async def test_mark_event_acted_on_flips_the_flag_once(clean_db):
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session
    from services.push_engagement import mark_event_acted_on

    async with async_session() as s:
        s.add(HeartbeatEventModel(id="ev-1", source="imessage", event_type="message_received", sender="Rowan",
                                  summary="pill?", triage_status="escalated", notified_at=datetime.utcnow()))
        await s.commit()
    assert await mark_event_acted_on("ev-1") is True
    assert await mark_event_acted_on("ev-1") is False, "already acted"
    assert await mark_event_acted_on("nope") is False
    async with async_session() as s:
        row = (await s.execute(select(HeartbeatEventModel).where(HeartbeatEventModel.id == "ev-1"))).scalar_one()
    assert row.user_acted_on_it is True and row.user_acted_at is not None


@pytest.mark.integration
async def test_recent_pushes_count_as_acted_when_owner_chats(clean_db):
    from services.database import HeartbeatEventModel, async_session
    from services.push_engagement import mark_recent_pushes_acted

    now = datetime.utcnow()
    async with async_session() as s:
        s.add(HeartbeatEventModel(id="fresh", source="imessage", event_type="message_received", sender="A",
                                  summary="x", triage_status="escalated", notified_at=now - timedelta(minutes=5)))
        s.add(HeartbeatEventModel(id="stale", source="imessage", event_type="message_received", sender="B",
                                  summary="y", triage_status="escalated", notified_at=now - timedelta(hours=3)))
        s.add(HeartbeatEventModel(id="never", source="imessage", event_type="message_received", sender="C",
                                  summary="z", triage_status="dismissed"))
        await s.commit()
    assert await mark_recent_pushes_acted(within_minutes=30) == 1
    assert await mark_recent_pushes_acted(within_minutes=30) == 0
