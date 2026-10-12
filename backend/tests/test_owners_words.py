"""Ellis steers Edward from iMessage (proactivity spec §2.8 G5, §3.2.6; P1, 2026-10-11).

Ellis's own ``@Edward …`` text in any chat — "stop pushing me about soccer" —
is Ellis speaking, and becomes a standing instruction the speaker and every
wake obey. AREA 3's runner calls ``memory_service.remember_owners_words``
after a ping turn whose ping is Ellis's own; it extracts **only Ellis's text**
(never the thread, never a friend's line) under the ``chat`` profile with
``turn_source="owner_ping"``, one of ``OWNER_SPEAKS_SOURCES``, so the row is
heard/owner and ``mind_service.is_owners_instruction`` renders it as his.
"""

from __future__ import annotations

import json

import pytest

PAYLOAD = json.dumps([{"content": "Ellis: don't push me about soccer — a note is enough",
                       "memory_type": "instruction", "origin": "heard", "about": "owner",
                       "confidence": 0.9, "importance": 0.8}])


def _stub_extraction(monkeypatch):
    from services import memory_service

    seen = []

    class _L:
        async def ainvoke(self, messages):
            seen.append(messages)

            class R:
                content = PAYLOAD

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
    return seen, stored


@pytest.mark.unit
def test_a_ping_from_owner_is_owner_speaking():
    from services import llm_spend, memory_service

    assert "owner_ping" in memory_service.OWNER_SPEAKS_SOURCES
    assert memory_service.is_owners_own_turn("owner_ping") is True
    assert set(llm_spend.OWNER_TURN_SOURCES) == set(memory_service.OWNER_SPEAKS_SOURCES)


@pytest.mark.unit
async def test_owners_words_become_his_standing_instruction(monkeypatch):
    from services import memory_service
    from services.mind_service import is_owners_instruction

    seen, stored = _stub_extraction(monkeypatch)
    n = await memory_service.remember_owners_words("@Edward stop pushing me about soccer", conversation_id="ping-77",
                                                 ping_id=77, chat_identifier="chat-owner-self")
    assert n == 1
    (m,) = stored
    assert (m.memory_type, m.origin, m.about) == ("instruction", "heard", "owner")
    ref = json.loads(m.source_ref)
    assert ref["turn_source"] == "owner_ping" and ref["profile"] == "chat" and ref["kind"] == "extraction"
    assert ref["ping_id"] == 77 and ref["chat_identifier"] == "chat-owner-self" and ref["conversation_id"] == "ping-77"
    row = {"source_ref": m.source_ref, "source_conversation_id": m.source_conversation_id}
    assert is_owners_instruction(row, {"ping-77": "external_message"})
    assert is_owners_instruction(row, {}), "his own provenance vouches even when the ping's thread is gone"
    from services.memory_decay_service import owners_own_word

    assert owners_own_word(m), "decay never lapses his own instruction as someone else's"


@pytest.mark.unit
async def test_only_owners_own_text_is_read(monkeypatch):
    from services import memory_service

    calls = []

    async def spy(messages, conversation_id, **kw):
        calls.append((messages, conversation_id, kw))
        return []

    monkeypatch.setattr(memory_service, "extract_and_store_memories", spy)
    await memory_service.remember_owners_words("@Edward only push me about travel", conversation_id="ping-9",
                                             ping_id=9, chat_identifier="chat-trivia")
    (messages, conv, kw) = calls[0]
    assert messages == [{"role": "user", "content": "@Edward only push me about travel"}]
    assert conv == "ping-9" and kw["extraction_profile"] == "chat"


@pytest.mark.unit
async def test_it_never_fails_the_ping(monkeypatch):
    from services import memory_service

    async def boom(*a, **k):
        raise RuntimeError("extraction down")

    monkeypatch.setattr(memory_service, "extract_and_store_memories", boom)
    assert await memory_service.remember_owners_words("@Edward hi", conversation_id="ping-1") == 0
    assert await memory_service.remember_owners_words("   ", conversation_id="ping-1") == 0
