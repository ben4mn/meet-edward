"""A scheduled event's turn extracts with the ``scheduled_event`` profile.

Why (memory audit 2026-09-27, §7.1): the scheduler ran event turns with the
``chat`` extraction profile, so the trigger — text Edward wrote to himself
when he scheduled the event — was read as Ellis speaking and stored as
``heard/owner`` instructions ("must not message any external contact
directly", 4,025 retrievals, came from a reminder turn). The profile itself
lives in ``memory_service`` (batch A1); an unknown profile falls back to
``chat`` there, so passing it is safe before that lands. ``skip_extraction``
(the memory report) still wins.
"""

from __future__ import annotations

import json

import pytest


async def _stub_turn(monkeypatch, calls, fail_first=False):
    import services.graph as graph_pkg
    import services.settings_service as ss

    class _S:
        model = "gpt-6-astra"
        temperature = 0.4
        system_prompt = "be edward"
        provider = "openai"

    async def fake_chat(**kw):
        calls.append(kw)
        if fail_first and len(calls) == 1:
            raise RuntimeError("codex hiccup")
        return "Reminded Ellis about the filter."

    async def fake_graph():
        return object()

    async def fake_settings():
        return _S()

    monkeypatch.setattr(graph_pkg, "chat_with_memory", fake_chat)
    monkeypatch.setattr(graph_pkg, "get_graph", fake_graph)
    monkeypatch.setattr(ss, "get_settings", fake_settings)


async def _event(event_id, meta=None):
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _utcnow

    async with async_session() as s:
        s.add(ScheduledEventModel(id=event_id, description="Remind Ellis about the Britta filter",
                                  scheduled_at=_utcnow(), next_fire_at=_utcnow(), status="processing",
                                  event_metadata=json.dumps(meta) if meta is not None else None))
        await s.commit()
    async with async_session() as s:
        return await s.get(ScheduledEventModel, event_id)


def test_the_profile_name_is_the_contract_with_memory_service():
    from services.scheduler_service import SCHEDULED_EVENT_EXTRACTION_PROFILE

    assert SCHEDULED_EVENT_EXTRACTION_PROFILE == "scheduled_event"


@pytest.mark.integration
async def test_event_turn_passes_the_scheduled_event_profile(clean_db, monkeypatch):
    from services import scheduler_service

    calls = []
    await _stub_turn(monkeypatch, calls)
    await scheduler_service._execute_event(await _event("ev-p1"))
    assert calls[0]["extraction_profile"] == "scheduled_event"
    assert "skip_extraction" not in calls[0]


@pytest.mark.integration
async def test_the_retry_passes_the_profile_too(clean_db, monkeypatch):
    from services import scheduler_service

    calls = []
    await _stub_turn(monkeypatch, calls, fail_first=True)
    await scheduler_service._execute_event(await _event("ev-p2"))
    assert len(calls) == 2
    assert [c["extraction_profile"] for c in calls] == ["scheduled_event", "scheduled_event"]


@pytest.mark.integration
async def test_skip_extraction_still_reaches_the_turn(clean_db, monkeypatch):
    from services import scheduler_service

    calls = []
    await _stub_turn(monkeypatch, calls)
    await scheduler_service._execute_event(
        await _event("ev-p3", {"type": "memory_health_report", "skip_extraction": True}))
    assert calls[0]["skip_extraction"] is True
    assert calls[0]["extraction_profile"] == "scheduled_event"
