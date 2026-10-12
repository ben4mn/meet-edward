"""Instructions that name their own window expire on time.

"Edward should stay out of the +1555 thread for 24 hours" was stored on
2026-09-04 at importance 1.0 with no ``valid_until``, so it outlived its day
and kept being injected. The column, the retrieval clause, and the
consolidation retirement path already exist; only extraction ignored them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 5, 15, 0, 0, tzinfo=timezone.utc)


def test_valid_until_parsed_iso_to_naive_utc():
    from services.memory_service import parse_valid_until

    assert parse_valid_until("2026-09-06T15:00:00Z", now=NOW) == datetime(2026, 9, 6, 15, 0, 0)
    # an offset is honoured, then dropped so the stamp matches consolidation's naive-UTC convention
    assert parse_valid_until("2026-09-06T10:00:00-05:00", now=NOW) == datetime(2026, 9, 6, 15, 0, 0)
    # naive input is taken as UTC
    assert parse_valid_until("2026-09-06T15:00:00", now=NOW) == datetime(2026, 9, 6, 15, 0, 0)


def test_valid_until_garbage_and_absent():
    from services.memory_service import parse_valid_until

    assert parse_valid_until(None, now=NOW) is None
    assert parse_valid_until("", now=NOW) is None
    assert parse_valid_until("tomorrow-ish", now=NOW) is None
    assert parse_valid_until(12345, now=NOW) is None


def test_valid_until_in_past_rejected():
    """The extractor hallucinating a date before the conversation must not
    store a memory that is retired on arrival."""
    from services.memory_service import parse_valid_until

    assert parse_valid_until("2026-09-05T14:59:00Z", now=NOW) is None
    assert parse_valid_until("2026-09-05T15:00:00Z", now=NOW) is None


def test_coerce_reports_invalid_but_not_absent_valid_until():
    from services.memory_service import coerce_extracted_fields

    base = {"memory_type": "instruction", "temporal_nature": "temporary",
            "source_type": "user_stated", "confidence": 0.9, "importance": 1.0, "origin": "heard", "about": "owner"}
    fields, misses = coerce_extracted_fields(base, now=NOW)
    assert fields["valid_until"] is None and misses == []

    fields, misses = coerce_extracted_fields({**base, "valid_until": "2026-09-06T15:00:00Z"}, now=NOW)
    assert fields["valid_until"] == datetime(2026, 9, 6, 15, 0, 0) and misses == []

    fields, misses = coerce_extracted_fields({**base, "valid_until": "next week"}, now=NOW)
    assert fields["valid_until"] is None and misses == ["valid_until"]


def test_prompt_carries_the_current_time_and_the_rule():
    from services.memory_service import build_extraction_prompt

    prompt = build_extraction_prompt("USER: hi", "None", profile="chat", now=NOW)
    assert "2026-09-05T15:00:00Z" in prompt
    assert "valid_until" in prompt


def _payload(valid_until: str) -> str:
    return (
        '[{"content": "Edward should stay out of the +15555550171 thread for 24 hours", '
        '"memory_type": "instruction", "importance": 1.0, "temporal_nature": "temporary", '
        '"source_type": "user_stated", "confidence": 0.95, "about_owner": true, '
        f'"valid_until": "{valid_until}"}}]'
    )


async def test_instruction_with_window_is_stored_with_valid_until(monkeypatch):
    """Extraction compares the window with the real clock (a window already
    past is refused), so the extractor's answer is a day from now — a fixed
    date went stale the day after it was written."""
    from services import memory_service

    window_end = (datetime.now(timezone.utc) + timedelta(days=1)).replace(microsecond=0)
    payload = _payload(window_end.strftime("%Y-%m-%dT%H:%M:%SZ"))

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = payload
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
        messages=[{"role": "user", "content": "stay out of that thread for a day"}],
        conversation_id="c-1",
    )
    assert len(stored) == 1
    assert stored[0].valid_until == window_end.replace(tzinfo=None)


async def test_expired_instruction_not_retrieved(clean_db):
    from services import memory_service
    from services.memory_service import Memory

    live = Memory(id=None, content="Edward should remind Ellis about the shellfish allergy at dinners",
                  memory_type="instruction", importance=0.9, source_type="user_stated", confidence=0.9)
    expired = Memory(id=None, content="Edward should stay silent about shellfish for one day",
                     memory_type="instruction", importance=1.0, source_type="user_stated", confidence=0.9,
                     valid_until=datetime.utcnow() - timedelta(hours=1))
    await memory_service.store_memory(live)
    await memory_service.store_memory(expired)

    results = await memory_service.retrieve_memories("shellfish", limit=5, min_score=0.0)
    contents = [r.content for r in results]
    assert any("remind Ellis" in c for c in contents)
    assert not any("stay silent" in c for c in contents), "an expired instruction must not be retrieved"
