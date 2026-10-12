"""Memory becomes his — mind rebuild Phase 1 (docs/mind-rebuild-plan-2026-09-18.md).

One store holds what Edward heard, read, thought, decided and did. Two
columns make those distinguishable at retrieval:

  * ``origin``: heard (Ellis said it), read (a message, email, page or screen
    Edward read), thought (something Edward concluded or decided), did (an
    action Edward took, with its outcome)
  * ``about``: owner, contact, self, world

``memory_type`` gains ``intention`` — a thing Edward has decided to do or
watch, with ``valid_until`` as its horizon.

``origin`` is orthogonal to ``tier``: a thought is an observation until it
is reinforced, then a belief, then knowledge. The ladder is untouched.

The extraction-only ``about_owner`` flag (never stored) is replaced by the
``about`` column; the old key is still accepted from the extractor.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Vocabulary and dataclass
# ---------------------------------------------------------------------------


def test_memory_dataclass_defaults_heard_and_owner():
    from services.memory_service import Memory

    m = Memory(id=None, content="x", memory_type="fact", importance=0.5)
    assert m.origin == "heard"
    assert m.about == "owner"


def test_vocabularies():
    from services.memory_service import VALID_ABOUTS, VALID_MEMORY_TYPES, VALID_ORIGINS

    assert VALID_ORIGINS == ("heard", "read", "thought", "did")
    assert VALID_ABOUTS == ("owner", "contact", "self", "world")
    assert "intention" in VALID_MEMORY_TYPES


def test_retrieve_memories_accepts_about_and_origins():
    import inspect

    from services.memory_service import retrieve_memories

    params = inspect.signature(retrieve_memories).parameters
    assert "about" in params and params["about"].default is None
    assert "origins" in params and params["origins"].default is None


# ---------------------------------------------------------------------------
# Storage and retrieval (integration)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_new_columns_default_heard_and_owner(clean_db):
    from sqlalchemy import select

    from services.database import MemoryModel, async_session
    from services.memory_service import Memory, store_memory

    stored = await store_memory(Memory(id=None, content="Ellis's dog is Biscuit", memory_type="fact", importance=0.8))
    async with async_session() as s:
        row = (await s.execute(select(MemoryModel).where(MemoryModel.id == stored.id))).scalar_one()
    assert row.origin == "heard"
    assert row.about == "owner"


@pytest.mark.integration
async def test_store_and_read_back_origin_and_about(clean_db):
    from services.memory_service import Memory, get_all_memories, get_memory_by_id, store_memory

    stored = await store_memory(
        Memory(
            id=None,
            content="Watching ACME because Ellis's savings depend on it",
            memory_type="intention",
            importance=0.7,
            origin="thought",
            about="self",
        )
    )
    got = await get_memory_by_id(stored.id)
    assert (got.origin, got.about, got.memory_type) == ("thought", "self", "intention")
    listed = {m.id: m for m in await get_all_memories()}
    assert listed[stored.id].origin == "thought"
    assert listed[stored.id].about == "self"


@pytest.mark.integration
async def test_retrieve_filters_by_about_and_origin(clean_db):
    from services.memory_service import Memory, retrieve_memories, store_memory

    mine = await store_memory(
        Memory(
            id=None,
            content="I decided to watch Acme stock for Ellis",
            memory_type="intention",
            importance=0.7,
            origin="thought",
            about="self",
        )
    )
    his = await store_memory(
        Memory(
            id=None,
            content="Ellis is worried the Acme downgrade hits his savings",
            memory_type="context",
            importance=0.7,
            origin="heard",
            about="owner",
        )
    )

    kw = dict(limit=5, min_confidence=0.0, min_score=0.0)  # the test is about the filters, not the scoring
    both = {m.id for m in await retrieve_memories("Acme", **kw)}
    assert {mine.id, his.id} <= both

    only_self = {m.id for m in await retrieve_memories("Acme", about="self", **kw)}
    assert only_self == {mine.id}

    only_thoughts = {m.id for m in await retrieve_memories("Acme", origins=["thought", "did"], **kw)}
    assert only_thoughts == {mine.id}

    # The filter survives into the returned objects too.
    got = (await retrieve_memories("Acme", about="self", **kw))[0]
    assert got.origin == "thought" and got.about == "self"


@pytest.mark.integration
async def test_update_memory_can_set_valid_until(clean_db):
    from services.memory_service import Memory, retrieve_memories, store_memory, update_memory

    stored = await store_memory(
        Memory(id=None, content="Check the Acme price every weekday", memory_type="intention", importance=0.6,
               origin="thought", about="self")
    )
    assert stored.id in {m.id for m in await retrieve_memories("Acme price", limit=5, min_confidence=0.0)}

    updated = await update_memory(stored.id, valid_until=datetime.utcnow() - timedelta(seconds=1))
    assert updated is not None and updated.valid_until is not None
    assert stored.id not in {m.id for m in await retrieve_memories("Acme price", limit=5, min_confidence=0.0)}


@pytest.mark.integration
async def test_update_memory_accepts_intention_type_and_about(clean_db):
    from services.memory_service import Memory, store_memory, update_memory

    stored = await store_memory(Memory(id=None, content="keep an eye on ACME", memory_type="context", importance=0.5))
    updated = await update_memory(stored.id, memory_type="intention", about="self", origin="thought")
    assert (updated.memory_type, updated.about, updated.origin) == ("intention", "self", "thought")


@pytest.mark.integration
async def test_a_reinforced_thought_climbs_the_tier_ladder(clean_db):
    """The same thought reached three times is a belief — by the existing ladder."""
    from services.memory_service import Memory, get_memory_by_id, store_memories_with_conflict_detection

    def thought():
        return Memory(id=None, content="Ellis cares about Acme stock because of his savings", memory_type="context",
                      importance=0.6, origin="thought", about="self")

    first = (await store_memories_with_conflict_detection([thought()]))[0]
    assert first.tier == "observation"
    await store_memories_with_conflict_detection([thought()])
    third = (await store_memories_with_conflict_detection([thought()]))[0]
    assert third.id == first.id
    row = await get_memory_by_id(first.id)
    assert row.reinforcement_count == 2
    assert row.tier == "belief"
    assert row.origin == "thought" and row.about == "self"


@pytest.mark.integration
async def test_thoughts_are_queued_for_the_graph(clean_db, monkeypatch):
    """A thought enters the memory graph the same way a fact about Ellis does."""
    from sqlalchemy import select

    from services.database import GraphIngestQueueModel, async_session
    from services.memory_service import Memory, store_memory

    monkeypatch.setenv("GRAPHITI_ENABLED", "true")
    stored = await store_memory(
        Memory(id=None, content="I am watching Acme for Ellis", memory_type="intention", importance=0.6,
               origin="thought", about="self")
    )
    for _ in range(10):  # the dual-write is a fire-and-forget task
        await asyncio.sleep(0.01)
    async with async_session() as s:
        rows = (await s.execute(select(GraphIngestQueueModel).where(GraphIngestQueueModel.memory_id == stored.id))).scalars().all()
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# Extraction: fields, prompt, pipeline (unit)
# ---------------------------------------------------------------------------


def _item(**kw):
    base = {"content": "x", "memory_type": "fact", "temporal_nature": "timeless", "source_type": "user_stated",
            "confidence": 0.9, "importance": 0.8}
    base.update(kw)
    return base


def test_coerce_reads_origin_and_about():
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(_item(origin="thought", about="self", memory_type="intention"))
    assert (fields["origin"], fields["about"], fields["memory_type"]) == ("thought", "self", "intention")
    assert "origin" not in misses and "about" not in misses


def test_coerce_maps_legacy_about_owner():
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(_item(about_owner=True))
    assert fields["about"] == "owner" and "about" not in misses
    fields, misses = coerce_extracted_fields(_item(about_owner=False))
    assert fields["about"] == "contact" and "about" not in misses


def test_coerce_defaults_and_reports_misses():
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(_item())
    assert fields["origin"] == "heard" and fields["about"] == "owner"
    assert "origin" in misses and "about" in misses
    fields, misses = coerce_extracted_fields(_item(origin="banana", about="nobody"))
    assert fields["origin"] == "heard" and fields["about"] == "owner"
    assert "origin" in misses and "about" in misses


def test_coerce_caps_confidence_for_assistant_beliefs_about_owner():
    """What Edward concludes about Ellis is a belief, not testimony."""
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(_item(origin="thought", about="owner", confidence=0.95))
    assert fields["confidence"] == 0.6
    fields, _ = coerce_extracted_fields(_item(origin="heard", about="owner", confidence=0.95))
    assert fields["confidence"] == 0.95


def test_about_owner_flag_is_derived_for_the_heartbeat_floor():
    from services.memory_service import coerce_extracted_fields, should_store_under_profile

    owner, _ = coerce_extracted_fields(_item(about="owner", memory_type="context", importance=0.4))
    contact, _ = coerce_extracted_fields(_item(about="contact", memory_type="context", importance=0.4))
    assert owner["about_owner"] is True and contact["about_owner"] is False
    assert should_store_under_profile(owner, "heartbeat") is True
    assert should_store_under_profile(contact, "heartbeat") is False


def test_prompt_describes_origin_and_about_for_every_profile():
    from services.memory_service import (
        MIND_EXTRACTION_ADDENDUM,
        VALID_EXTRACTION_PROFILES,
        build_extraction_prompt,
    )

    # scheduled_event: memory audit §7.1 (2026-09-27), the trigger is Edward's own words.
    assert VALID_EXTRACTION_PROFILES == ("chat", "heartbeat", "mind", "scheduled_event")
    for profile in VALID_EXTRACTION_PROFILES:
        p = build_extraction_prompt("USER: hi", "None", profile=profile)
        assert '"origin"' in p and '"about"' in p
        assert "thought" in p and "intention" in p
        assert "about_owner" not in p
    assert MIND_EXTRACTION_ADDENDUM in build_extraction_prompt("USER: hi", "None", profile="mind")
    assert MIND_EXTRACTION_ADDENDUM not in build_extraction_prompt("USER: hi", "None", profile="chat")


def test_chat_profile_still_refuses_assistant_testimony_about_owner():
    from services.memory_service import ASSISTANT_TESTIMONY_RULE, build_extraction_prompt

    p = build_extraction_prompt("USER: hi", "None", profile="chat")
    assert ASSISTANT_TESTIMONY_RULE in p
    assert "not from the assistant" not in p, "the old blanket exclusion is replaced by the origin rule"


_PAYLOAD = (
    '[{"content": "Ellis is worried the Acme downgrade will hit his savings", "memory_type": "context", '
    '"importance": 0.7, "temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9, '
    '"origin": "heard", "about": "owner"},'
    '{"content": "I decided to watch Acme stock for Ellis and tell him if it moves more than 3%", '
    '"memory_type": "intention", "importance": 0.7, "temporal_nature": "evolving", "source_type": "inferred", '
    '"confidence": 0.8, "origin": "thought", "about": "self"},'
    '{"content": "Ellis probably wants fewer stock pings on weekends", "memory_type": "preference", '
    '"importance": 0.4, "temporal_nature": "evolving", "source_type": "inferred", "confidence": 0.9, '
    '"origin": "thought", "about": "owner"}]'
)


def _stub_pipeline(monkeypatch):
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
    return stored


async def test_extraction_keeps_assistant_decision_as_thought(monkeypatch):
    from services import memory_service

    stored = _stub_pipeline(monkeypatch)
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "the Acme downgrade might hit my savings"},
                  {"role": "assistant", "content": "I'll keep an eye on ACME and tell you if it moves more than 3%."}],
        conversation_id="edward-mind",
        extraction_profile="mind",
    )
    by_origin = {m.origin: m for m in stored}
    # A wake's pack is not Ellis speaking (H1, 2026-09-27): what the extractor
    # called "heard" there is something Edward read.
    assert "heard" not in by_origin
    assert by_origin["read"].about == "owner"
    assert by_origin["thought"].memory_type in ("intention", "preference")
    intention = next(m for m in stored if m.memory_type == "intention")
    assert (intention.origin, intention.about) == ("thought", "self")


async def test_assistant_claims_about_owner_are_thoughts_with_capped_confidence(monkeypatch):
    from services import memory_service

    stored = _stub_pipeline(monkeypatch)
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "hi"}], conversation_id="c1", extraction_profile="chat"
    )
    belief = next(m for m in stored if m.origin == "thought" and m.about == "owner")
    assert belief.confidence == 0.6


# ---------------------------------------------------------------------------
# Prompt formatting
# ---------------------------------------------------------------------------


def test_context_line_shows_origin_and_about_when_not_the_default():
    from services.graph.streaming import format_memory_line
    from services.memory_service import Memory

    thought = Memory(id="m1", content="Watching ACME for Ellis", memory_type="intention", importance=0.7,
                     origin="thought", about="self", source_type="inferred", confidence=0.7)
    line = format_memory_line(thought)
    assert line.startswith("- [intention][thought/self][inferred, 0.7] Watching ACME for Ellis")
    assert line.endswith("(memory_id: m1)")

    heard = Memory(id="m2", content="Ellis's dog is Biscuit", memory_type="fact", importance=0.8,
                   source_type="user_stated", confidence=0.9)
    assert format_memory_line(heard).startswith("- [fact][user_stated, 0.9] Ellis's dog is Biscuit")


def test_context_line_accepts_dict_shaped_memories():
    from services.graph.streaming import format_memory_line

    line = format_memory_line({"id": "m3", "content": "x", "memory_type": "context", "importance": 0.5,
                               "origin": "read", "about": "contact", "temporal_nature": "temporary"})
    assert "[context][read/contact]" in line and "(memory_id: m3)" in line


def test_nodes_and_streaming_share_one_formatter():
    src = Path(__file__).resolve().parents[1] / "services" / "graph" / "nodes.py"
    text = src.read_text()
    assert "format_memory_line" in text
    assert "f\"- [{memory['memory_type']}]" not in text


def test_heartbeat_profile_defaults_a_missing_about_to_contact():
    """The third-party floor must survive an extractor that drops the field."""
    from services.memory_service import coerce_extracted_fields, default_about_for_profile, should_store_under_profile

    assert default_about_for_profile("heartbeat") == "contact"
    assert default_about_for_profile("chat") == "owner"
    assert default_about_for_profile("mind") == "owner"
    fields, misses = coerce_extracted_fields(_item(memory_type="context", importance=0.4),
                                             default_about=default_about_for_profile("heartbeat"))
    assert fields["about"] == "contact" and "about" in misses
    assert should_store_under_profile(fields, "heartbeat") is False
