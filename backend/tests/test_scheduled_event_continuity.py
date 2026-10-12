"""Events fire with continuity — mind rebuild Phase 4.

A self-scheduled event used to fire as a stranger in a fresh conversation
("your future self has NO memory of the original context"). Now it fires
in the thread that made it, or in the mind, and the trigger carries its
prior fires and the intention that created it.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

BACKEND = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "conversation_id,source,expected",
    [
        ("abc", "user", "abc"),
        ("abc", "edward_thought", "abc"),
        ("abc", "scheduled_event", "abc"),
        ("abc", "heartbeat", "edward-mind"),      # never replay into a contact-facing thread
        ("abc", "orchestrator_worker", "edward-mind"),
        ("abc", None, "edward-mind"),             # the thread no longer exists
        (None, None, "edward-mind"),
    ],
)
def test_event_thread_choice(conversation_id, source, expected):
    from services.scheduler_service import event_thread

    assert event_thread(conversation_id, source) == expected


def test_build_event_trigger_carries_previous_fires_and_intention():
    from services.scheduler_service import build_event_trigger

    event = SimpleNamespace(
        id="ev-1", description="Check ACME at the close and tell Ellis if it moved more than 3%",
        scheduled_at=datetime(2026, 9, 18, 21, 30), fire_count=2, last_fired_at=datetime(2026, 9, 17, 21, 30),
        last_result="ACME closed at $142, down 4%; pushed Ellis.", recurrence_pattern="30 16 * * 1-5",
        created_at=datetime(2026, 9, 16, 15, 0),
    )
    text = build_event_trigger(event, conversation_id="edward-mind", now=datetime(2026, 9, 18, 21, 31),
                               intention="Watching ACME because Ellis's bonus depends on it",
                               intention_until=datetime(2026, 10, 18))
    assert text.startswith("[SCHEDULED EVENT]")
    assert "fired 2 time(s)" in text and "ACME closed at $142" in text
    assert "Watching ACME because Ellis's bonus depends on it" in text and "until 2026-10-18" in text
    assert "recurring (30 16 * * 1-5)" in text
    assert "use url='/chat?c=edward-mind'" in text

    first = build_event_trigger(SimpleNamespace(id="ev-2", description="x", scheduled_at=None, fire_count=0,
                                                last_fired_at=None, last_result=None, recurrence_pattern=None,
                                                created_at=None), conversation_id="c", now=datetime(2026, 9, 18))
    assert "first time it fires" in first


def test_scheduler_prompt_and_manual_no_longer_claim_amnesia():
    from services.graph.tools import get_scheduled_event_tools_description
    from services.scheduler_service import _build_scheduler_system_prompt

    manual = get_scheduled_event_tools_description()
    assert "NO memory" not in manual and "EPHEMERAL" not in manual and "ephemeral" not in manual
    assert "why" in manual and "fire where" in manual.lower()

    prompt = _build_scheduler_system_prompt("You are Edward.", SimpleNamespace(description="x", scheduled_at=None,
                                                                              delivery_channel="chat"))
    assert "cancel_scheduled_event" in prompt
    assert "no longer worth doing" in prompt


@pytest.mark.parametrize(
    "recurrence,scheduled_at,now,expected",
    [
        (None, datetime(2026, 9, 20, 16, 0), datetime(2026, 9, 18), datetime(2026, 9, 21, 16, 0)),   # one-shot: +1 day
        ("0 9 * * *", datetime(2026, 9, 19, 9, 0), datetime(2026, 9, 18), datetime(2026, 10, 18)),      # recurring: 30 days
    ],
)
def test_intention_valid_until(recurrence, scheduled_at, now, expected):
    from services.scheduled_events_service import intention_valid_until

    assert intention_valid_until(recurrence, scheduled_at, now) == expected


async def _stub_turn(monkeypatch, calls):
    import services.graph as graph_pkg
    import services.settings_service as ss

    class _S:
        model = "gpt-6-astra"
        temperature = 0.4
        system_prompt = "be edward"
        provider = "openai"

    async def fake_chat(**kw):
        calls.append(kw)
        return "Checked ACME; nothing moved."

    async def fake_graph():
        return object()

    async def fake_settings():
        return _S()

    monkeypatch.setattr(graph_pkg, "chat_with_memory", fake_chat)
    monkeypatch.setattr(graph_pkg, "get_graph", fake_graph)
    monkeypatch.setattr(ss, "get_settings", fake_settings)


@pytest.mark.integration
async def test_event_fires_in_its_own_thread_when_it_exists(clean_db, monkeypatch):
    from services import scheduler_service
    from services.conversation_service import create_conversation
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _utcnow

    calls = []
    await _stub_turn(monkeypatch, calls)
    await create_conversation("chat-abc", title="ACME chat", source="user")
    async with async_session() as s:
        s.add(ScheduledEventModel(id="ev-1", description="Check ACME", scheduled_at=_utcnow(), next_fire_at=_utcnow(),
                                  status="processing", conversation_id="chat-abc", fire_count=1,
                                  last_result="ACME closed at $142."))
        await s.commit()
    async with async_session() as s:
        ev = await s.get(ScheduledEventModel, "ev-1")
    await scheduler_service._execute_event(ev)
    assert calls[0]["conversation_id"] == "chat-abc"
    assert "ACME closed at $142." in calls[0]["message"]
    assert calls[0]["turn_source"] == "scheduled_event"
    async with async_session() as s:
        row = await s.get(ScheduledEventModel, "ev-1")
    assert row.status == "completed" and row.fire_count == 2
    assert "Checked ACME" in (row.last_result or "")


@pytest.mark.integration
async def test_event_without_a_thread_fires_in_the_mind(clean_db, monkeypatch):
    from services import scheduler_service
    from services.conversation_service import get_conversation
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _utcnow

    calls = []
    await _stub_turn(monkeypatch, calls)
    async with async_session() as s:
        s.add(ScheduledEventModel(id="ev-2", description="Remind Ellis about the Britta filter", scheduled_at=_utcnow(),
                                  next_fire_at=_utcnow(), status="processing", conversation_id="gone-thread"))
        await s.commit()
    async with async_session() as s:
        ev = await s.get(ScheduledEventModel, "ev-2")
    await scheduler_service._execute_event(ev)
    assert calls[0]["conversation_id"] == "edward-mind"
    assert await get_conversation("edward-mind") is not None
    assert "first time it fires" in calls[0]["message"]


@pytest.mark.integration
async def test_event_trigger_carries_the_linked_intention(clean_db, monkeypatch):
    from services import scheduler_service
    from services.database import ScheduledEventModel, async_session
    from services.memory_service import Memory, store_memory
    from services.scheduled_events_service import _utcnow

    calls = []
    await _stub_turn(monkeypatch, calls)
    mem = await store_memory(Memory(id=None, content="Watching ACME because Ellis's bonus depends on it",
                                    memory_type="intention", importance=0.7, origin="thought", about="self",
                                    valid_until=datetime(2026, 10, 18)))
    async with async_session() as s:
        s.add(ScheduledEventModel(id="ev-3", description="Check ACME", scheduled_at=_utcnow(), next_fire_at=_utcnow(),
                                  status="processing", event_metadata=json.dumps({"memory_id": mem.id})))
        await s.commit()
    async with async_session() as s:
        ev = await s.get(ScheduledEventModel, "ev-3")
    await scheduler_service._execute_event(ev)
    assert "Watching ACME because Ellis's bonus depends on it" in calls[0]["message"]
    assert "until 2026-10-18" in calls[0]["message"]
