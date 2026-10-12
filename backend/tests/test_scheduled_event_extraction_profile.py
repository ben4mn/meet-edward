"""A scheduled event's trigger is Edward's own words (memory audit §7.1, 2026-09-27).

``scheduler_service`` runs a fired event through ``chat_with_memory`` with
the default ``chat`` extraction profile, so the trigger — a reminder Edward
or the heartbeat wrote days earlier ("in-app chat reminder only, do not
contact Theo") — was read as Ellis speaking and stored as a ``heard``/``owner``
instruction. 94 of 187 event rows say "in-app" and 56 say "do not
message/contact"; 9 of the 35 live "standing instructions from Ellis" came
from scheduled-event turns, including the two blanket rules retrieved 5,395
and 4,025 times ("…must not message any external contact directly…").

The ``scheduled_event`` profile (batch B makes the scheduler pass it; an
unknown profile falls back to ``chat``, so merge order does not matter):
  * the trigger is Edward's own: nothing is ``heard``; what the trigger
    quotes from someone else is ``read``; an unlabelled row is his
    ``thought``;
  * a delivery note in the trigger is never an instruction about Ellis —
    such rows are dropped, not stored;
  * genuinely new facts that came out of the turn are kept.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


@pytest.mark.unit
def test_the_profile_is_known():
    from services.memory_service import VALID_EXTRACTION_PROFILES

    assert "scheduled_event" in VALID_EXTRACTION_PROFILES


@pytest.mark.unit
def test_the_prompt_carries_its_addendum_and_only_for_that_profile():
    from services.memory_service import SCHEDULED_EVENT_EXTRACTION_ADDENDUM, build_extraction_prompt

    assert SCHEDULED_EVENT_EXTRACTION_ADDENDUM in build_extraction_prompt("USER: hi", "None",
                                                                          profile="scheduled_event", now=NOW)
    for other in ("chat", "heartbeat", "mind"):
        assert SCHEDULED_EVENT_EXTRACTION_ADDENDUM not in build_extraction_prompt("USER: hi", "None",
                                                                                  profile=other, now=NOW)


@pytest.mark.unit
def test_the_addendum_says_the_trigger_is_his_own_and_no_instruction_for_owner():
    from services.memory_service import SCHEDULED_EVENT_EXTRACTION_ADDENDUM as text

    low = text.lower()
    assert "you wrote" in low
    assert "in-app" in low and "never an instruction" in low
    assert '"origin": "read"' in text
    heard = [l for l in text.splitlines() if '"origin": "heard"' in l]
    assert heard and all("nothing" in l.lower() or "never" in l.lower() for l in heard)


def _item(**over):
    base = {"content": "Edward should send Ellis only in-app chat reminders and must not message Theo directly",
            "memory_type": "instruction", "importance": 0.9, "temporal_nature": "timeless",
            "source_type": "user_stated", "confidence": 0.95, "origin": "heard", "about": "owner"}
    base.update(over)
    return base


@pytest.mark.unit
@pytest.mark.parametrize("over,expected", [
    ({}, "read"),                                              # heard, about Ellis → read
    ({"about": "self", "memory_type": "context"}, "thought"),  # heard, about himself → his thought
    ({"origin": None, "memory_type": "context"}, "thought"),   # unlabelled → his own words
])
def test_nothing_from_a_trigger_is_heard(over, expected):
    from services.memory_service import coerce_extracted_fields

    item = _item(**over)
    if item.get("origin") is None:
        item.pop("origin")
    fields, misses = coerce_extracted_fields(item, now=NOW, profile="scheduled_event")
    assert fields["origin"] == expected
    assert fields["source_type"] != "user_stated"
    assert "origin" in misses


@pytest.mark.unit
def test_a_missing_about_is_his_own():
    from services.memory_service import default_about_for_profile

    assert default_about_for_profile("scheduled_event") == "self"


@pytest.mark.unit
def test_an_instruction_about_owner_is_never_stored_under_the_profile():
    from services.memory_service import coerce_extracted_fields, should_store_under_profile

    fields, _ = coerce_extracted_fields(_item(), now=NOW, profile="scheduled_event")
    assert should_store_under_profile(fields, "scheduled_event") is False
    # a fact from the turn is kept
    fact, _ = coerce_extracted_fields(_item(memory_type="fact", origin="read", about="contact",
                                            content="Theo's flight lands at 6:40pm Sunday"),
                                      now=NOW, profile="scheduled_event")
    assert should_store_under_profile(fact, "scheduled_event") is True
    # the chat profile is unchanged: Ellis's own instruction is stored
    chat, _ = coerce_extracted_fields(_item(), now=NOW, profile="chat")
    assert should_store_under_profile(chat, "chat") is True


_TRIGGER = ("[Scheduled event] Remind Ellis in-app to call Theo about the briefing. In-app chat reminder only; "
            "do not contact Theo. Theo wrote on Thursday: \"my flight lands 6:40pm Sunday\".")

_PAYLOAD = (
    '[{"content": "Edward should send Ellis only in-app chat reminders and must not message Theo directly", '
    '"memory_type": "instruction", "importance": 0.9, "temporal_nature": "timeless", '
    '"source_type": "user_stated", "confidence": 0.95, "origin": "heard", "about": "owner"},'
    '{"content": "Ellis needs to call Theo about the briefing", "memory_type": "context", "importance": 0.6, '
    '"temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9, "about": "owner"},'
    '{"content": "Theo\'s flight lands at 6:40pm Sunday", "memory_type": "fact", "importance": 0.6, '
    '"temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9, "origin": "read", '
    '"about": "contact"}]'
)


async def test_a_reminder_turn_stores_no_instruction_and_nothing_heard(monkeypatch):
    from services import memory_service

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = _PAYLOAD

            return R()

    stored = []

    async def _store(memories):
        stored.extend(memories)
        return memories

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    monkeypatch.setattr("services.entity_service.extract_entities", _noop)
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": _TRIGGER},
                  {"role": "assistant", "content": "Reminder: call Theo about the briefing."}],
        conversation_id="c-reminder",
        extraction_profile="scheduled_event",
    )
    assert not [m for m in stored if m.memory_type == "instruction"]
    assert not [m for m in stored if m.origin == "heard"]
    assert "Theo's flight lands at 6:40pm Sunday" in [m.content for m in stored]
    assert all('"profile": "scheduled_event"' in (m.source_ref or "") for m in stored)
