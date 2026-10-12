"""Binding chat stand-down (2026-07-09).

Someone in a group chat: "@edward I'm talking to them, not you" → Edward:
"Got it — I'll stay out of this one." → two minutes later their next message
arrived as a fresh follow-up and he messaged them again — breaking his own
promise, with the owner actively replying in the same thread.

The owner's decision: stand-downs are REMEMBERED AND BINDING. Edward's
judgment decides when to stand down (he calls the stand_down tool himself),
but once recorded the send tools refuse that chat in turns the owner is not
part of, anything that needs attention goes to the owner instead, and a
fresh @edward mention in that chat (or expiry, 24h) releases it.

The legacy heartbeat reply turns that enforced it inline
(``_execute_classification``, ``STANDDOWN_EXCLUDED_TOOLS``, the context
line, the mention trigger) left with the legacy triage engine; the record,
lookup and release below are what the send tools, the mind path and the
ping runner use.
"""

import inspect
from datetime import datetime, timedelta, timezone

import pytest

from services.heartbeat import triage_service


# ---------------------------------------------------------------------------
# storage: record / get-active / release / expiry
# ---------------------------------------------------------------------------


def test_standdown_model_exists():
    from services.database import ChatStanddownModel

    cols = set(ChatStanddownModel.__table__.columns.keys())
    for required in ("chat_identifier", "requested_by", "requested_at", "expires_at", "released_at"):
        assert required in cols, f"missing column {required}"


@pytest.mark.integration
async def test_record_then_get_active(clean_db):
    await triage_service.record_standdown("+15550001111", requested_by="Rowan")

    active = await triage_service._get_active_standdown("+15550001111")
    assert active is not None
    assert active.requested_by == "Rowan"

    assert await triage_service._get_active_standdown("+15559990000") is None


@pytest.mark.integration
async def test_expired_standdown_is_inactive(clean_db):
    from services.database import ChatStanddownModel, async_session

    async with async_session() as session:
        session.add(ChatStanddownModel(
            chat_identifier="+15550001111",
            requested_by="Rowan",
            requested_at=datetime.now(timezone.utc) - timedelta(hours=30),
            expires_at=datetime.now(timezone.utc) - timedelta(hours=6),
        ))
        await session.commit()

    assert await triage_service._get_active_standdown("+15550001111") is None


@pytest.mark.integration
async def test_release_standdown(clean_db):
    await triage_service.record_standdown("+15550001111", requested_by="Rowan")
    await triage_service._release_standdown("+15550001111")

    assert await triage_service._get_active_standdown("+15550001111") is None


@pytest.mark.integration
async def test_re_recording_refreshes(clean_db):
    await triage_service.record_standdown("+15550001111", requested_by="Rowan")
    # A second stand-down in the same chat must not crash (upsert/refresh).
    await triage_service.record_standdown("+15550001111", requested_by="Rowan")

    assert await triage_service._get_active_standdown("+15550001111") is not None


async def test_get_active_fails_open_to_none(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(triage_service, "async_session", boom)
    assert await triage_service._get_active_standdown("+15550001111") is None


# ---------------------------------------------------------------------------
# the stand_down tool (Edward records his own commitment)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_stand_down_tool_records(clean_db):
    from services.graph import tools as graph_tools

    result = await graph_tools.stand_down.ainvoke(
        {"chat_id": "+15550001111", "requested_by": "Rowan"}
    )

    assert "stood down" in result.lower() or "stand-down" in result.lower()
    assert await triage_service._get_active_standdown("+15550001111") is not None


def test_stand_down_tool_is_registered():
    src = inspect.getsource(__import__("services.tool_registry", fromlist=["x"]))
    assert "stand_down" in src
