"""An instruction is Ellis's only when Ellis said it (memory audit §7.2, 2026-09-27).

The pack's "Standing instructions from Ellis" section, which outranks a
mention, is every live ``instruction`` with ``origin=heard about=owner``.
Of the 35 rows that qualified, only 7 came from Ellis's own chat: the rest
were minted by turns where the "user" message was not Ellis — scheduled-event
triggers, heartbeat threads, worker tasks, wake packs — because a missing
origin defaulted to ``heard`` and nothing checked who was speaking.

Rule: an ``instruction`` keeps ``heard``/``owner`` only when the turn is
Ellis's own (turn source chat, voice, widget or alexa — or no turn at all
under the chat profile, the pre-ledger path). Any other turn demotes it:
``read`` when someone else said it (heartbeat, twilio), otherwise
``thought`` (Edward's own words: a wake, the pulse, an event, a worker).
Only instructions — a fact Ellis states in a heartbeat thread is still his.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


def _instruction(**over):
    base = {"content": "Never text my boss for me", "memory_type": "instruction", "importance": 0.9,
            "temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.95,
            "origin": "heard", "about": "owner"}
    base.update(over)
    return base


@pytest.mark.unit
@pytest.mark.parametrize("turn_source", ["chat", "voice", "widget", "alexa"])
def test_owners_own_turns_keep_his_instruction(turn_source):
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(_instruction(), now=NOW, turn_source=turn_source)
    assert (fields["origin"], fields["about"]) == ("heard", "owner")
    assert fields["source_type"] == "user_stated"
    assert "origin" not in misses


@pytest.mark.unit
def test_no_turn_under_the_chat_profile_is_unchanged():
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(_instruction(), now=NOW)
    assert (fields["origin"], fields["about"]) == ("heard", "owner")


@pytest.mark.unit
@pytest.mark.parametrize("turn_source,expected", [
    ("heartbeat", "read"), ("twilio", "read"),
    ("mind", "thought"), ("pulse", "thought"), ("scheduled_event", "thought"), ("worker", "thought"),
    ("briefing", "thought"), ("background", "thought"),
])
def test_other_turns_never_produce_a_heard_owner_instruction(turn_source, expected):
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(_instruction(), now=NOW, turn_source=turn_source)
    assert (fields["origin"], fields["about"]) != ("heard", "owner")
    assert fields["origin"] == expected
    assert fields["source_type"] == "inferred"
    assert "origin" in misses


@pytest.mark.unit
def test_the_heartbeat_profile_without_a_turn_is_someone_elses_thread():
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(_instruction(), now=NOW, profile="heartbeat")
    assert fields["origin"] == "read"


@pytest.mark.unit
def test_only_instructions_are_checked():
    """A fact Ellis states in a heartbeat thread is still something he said."""
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(
        _instruction(memory_type="fact", content="Ellis is flying to Denver on Friday"),
        now=NOW, turn_source="heartbeat",
    )
    assert fields["origin"] == "heard"
    assert "origin" not in misses


_PAYLOAD = (
    '[{"content": "Edward should never contact external people directly unless Ellis explicitly authorizes it", '
    '"memory_type": "instruction", "importance": 1.0, "temporal_nature": "timeless", '
    '"source_type": "user_stated", "confidence": 0.95, "about": "owner"}]'
)


@pytest.mark.parametrize("source", ["worker", "heartbeat", "scheduled_event"])
async def test_extraction_reads_the_speaker_from_the_turn(monkeypatch, source):
    from services import memory_service, turn_ledger

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
    rec = turn_ledger.TurnRecord.start(conversation_id="c-1", source=source, model=None, provider=None)
    token = turn_ledger._current.set(rec)
    try:
        await memory_service.extract_and_store_memories(
            messages=[{"role": "user", "content": "Reminder (in-app only): call Theo"}],
            conversation_id="c-1",
        )
    finally:
        turn_ledger._current.reset(token)
    assert stored
    assert not [m for m in stored if (m.memory_type, m.origin, m.about) == ("instruction", "heard", "owner")]


async def test_remember_outside_owners_turn_cannot_mint_his_instruction(monkeypatch):
    from services import memory_service, turn_ledger
    from services.graph.tools import remember

    captured = []

    async def _store(memories):
        captured.extend(memories)
        for m in memories:
            m.id = "m-1"
        return memories

    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    rec = turn_ledger.TurnRecord.start(conversation_id="hb-1", source="heartbeat", model=None, provider=None)
    token = turn_ledger._current.set(rec)
    try:
        out = await remember.ainvoke({"content": "Never text my boss for me", "memory_type": "instruction",
                                      "about": "owner", "origin": "heard"})
    finally:
        turn_ledger._current.reset(token)
    assert out.startswith("ok")
    assert captured[0].origin == "read"
