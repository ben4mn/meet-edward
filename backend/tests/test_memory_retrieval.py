"""
Tests for confidence-aware memory retrieval (Fix #3).

Low-confidence inferred memories from the silent-extraction-failure era
pollute context. These tests drive two changes:

  1. ``retrieve_memories`` accepts a ``min_confidence`` parameter (default 0.5)
     and filters out memories below the floor.
  2. ``extract_and_store_memories`` rejects newly-extracted memories with
     ``confidence < 0.3 AND source_type == "inferred"`` — stops the bleed.
"""

from __future__ import annotations

import inspect
import uuid

import pytest


# ---------------------------------------------------------------------------
# Signature check — the parameter must exist
# ---------------------------------------------------------------------------


def test_retrieve_memories_has_min_confidence_param():
    from services.memory_service import retrieve_memories

    sig = inspect.signature(retrieve_memories)
    assert "min_confidence" in sig.parameters, (
        "retrieve_memories must accept min_confidence parameter. "
        "Default should be 0.5 so existing callers benefit immediately."
    )
    default = sig.parameters["min_confidence"].default
    assert default == 0.5, (
        "Default min_confidence should be 0.5 — the plan's chosen floor for "
        "keeping garbage out of retrieval without removing legitimate memories."
    )


# ---------------------------------------------------------------------------
# Helper for DB seeding (integration)
# ---------------------------------------------------------------------------


async def _insert_memory(session, **kwargs):
    """Insert a MemoryModel row with sensible defaults."""
    from services import memory_service
    from services.database import MemoryModel

    content = kwargs.get("content", "test memory")
    # Compute a real embedding so the vector query returns results.
    embedding = memory_service.get_embedding(content)

    mem = MemoryModel(
        id=kwargs.get("id") or str(uuid.uuid4()),
        content=content,
        embedding=embedding,
        memory_type=kwargs.get("memory_type", "fact"),
        importance=kwargs.get("importance", 0.5),
        temporal_nature=kwargs.get("temporal_nature", "timeless"),
        source_type=kwargs.get("source_type", "inferred"),
        confidence=kwargs.get("confidence", 0.5),
        reinforcement_count=kwargs.get("reinforcement_count", 0),
    )
    session.add(mem)
    return mem


# ---------------------------------------------------------------------------
# Retrieval filter — integration
# ---------------------------------------------------------------------------


async def test_retrieve_filters_memories_below_min_confidence(clean_db):
    """Seed the DB with mixed-confidence memories; retrieve with min=0.5;
    expect only the 0.6 and 0.8 memories back."""
    from services import database, memory_service

    async with database.async_session() as session:
        await _insert_memory(session, content="Ellis visited Paris", confidence=0.2)
        await _insert_memory(session, content="Ellis visited Paris last summer", confidence=0.4)
        await _insert_memory(session, content="Ellis loves visiting Paris", confidence=0.6)
        await _insert_memory(session, content="Ellis lives in Paris half the year", confidence=0.8)
        await session.commit()

    results = await memory_service.retrieve_memories(
        "Paris travel", limit=10, min_score=0.0, min_confidence=0.5,
    )

    # All returned memories must be >= 0.5 confidence.
    for m in results:
        assert m.confidence >= 0.5, f"Got memory with confidence {m.confidence}"
    # Both high-confidence memories should be returned
    confidences = sorted(m.confidence for m in results)
    assert 0.6 in confidences
    assert 0.8 in confidences
    # Low-confidence must be filtered out
    assert 0.2 not in confidences
    assert 0.4 not in confidences


async def test_retrieve_with_lower_min_confidence_recalls_more(clean_db):
    """Reflection and deep retrieval need broader recall — passing 0.3 must
    return memories that 0.5 would drop."""
    from services import database, memory_service

    async with database.async_session() as session:
        await _insert_memory(session, content="Ellis likes jazz music", confidence=0.35)
        await _insert_memory(session, content="Ellis likes jazz piano", confidence=0.7)
        await session.commit()

    strict = await memory_service.retrieve_memories(
        "jazz", limit=10, min_score=0.0, min_confidence=0.5,
    )
    broad = await memory_service.retrieve_memories(
        "jazz", limit=10, min_score=0.0, min_confidence=0.3,
    )

    strict_confs = sorted(m.confidence for m in strict)
    broad_confs = sorted(m.confidence for m in broad)

    assert 0.35 not in strict_confs
    assert 0.35 in broad_confs
    assert 0.7 in strict_confs
    assert 0.7 in broad_confs


async def test_retrieve_treats_null_confidence_as_half(clean_db):
    """Backward compatibility — memories written before the confidence column
    was added have NULL confidence. They should be treated as 0.5 so existing
    data doesn't vanish."""
    from services import database, memory_service
    from services.database import MemoryModel
    from sqlalchemy import text

    async with database.async_session() as session:
        # Insert via raw SQL to set confidence=NULL (ORM default is 0.5)
        embedding = memory_service.get_embedding("legacy memory about dogs")
        embedding_str = "[" + ",".join(str(x) for x in embedding) + "]"
        await session.execute(
            text(
                "INSERT INTO memories (id, content, embedding, memory_type, "
                "importance, temporal_nature, source_type, confidence) "
                "VALUES (:id, :content, CAST(:emb AS vector), 'fact', 0.6, "
                "'timeless', 'user_stated', NULL)"
            ),
            {"id": str(uuid.uuid4()), "content": "Legacy memory about dogs", "emb": embedding_str},
        )
        await session.commit()

    results = await memory_service.retrieve_memories(
        "dogs", limit=10, min_score=0.0, min_confidence=0.5,
    )
    # NULL confidence memory should be returned (treated as 0.5, meets floor)
    assert any("Legacy memory about dogs" in m.content for m in results)


# ---------------------------------------------------------------------------
# Store-side guard — stops the bleeding
# ---------------------------------------------------------------------------


async def test_extract_drops_low_confidence_inferred_memories(monkeypatch):
    """A memory extracted with source_type='inferred' AND confidence<0.3
    must NOT be persisted — even if the JSON parses cleanly."""
    from services import memory_service

    stored = []

    async def _fake_store(memories):
        stored.extend(memories)
        return memories

    monkeypatch.setattr(
        memory_service, "store_memories_with_conflict_detection", _fake_store
    )

    async def _noop_entities(*a, **kw):
        return []
    monkeypatch.setattr(
        "services.entity_service.extract_entities", _noop_entities,
    )

    # Mix of: keep (0.7 inferred), keep (0.2 user_stated), drop (0.2 inferred)
    payload = (
        '[{"content": "HIGH inferred", "memory_type": "fact", '
        '"source_type": "inferred", "confidence": 0.7},'
        '{"content": "LOW user_stated", "memory_type": "fact", '
        '"source_type": "user_stated", "confidence": 0.2},'
        '{"content": "LOW inferred", "memory_type": "fact", '
        '"source_type": "inferred", "confidence": 0.2}]'
    )

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = payload
            return R()

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())

    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "stuff"}],
        conversation_id="c1",
    )

    contents = [m.content for m in stored]
    assert "HIGH inferred" in contents, "High-confidence inferred must be stored"
    assert "LOW user_stated" in contents, "User-stated memories bypass the guard"
    assert "LOW inferred" not in contents, (
        "Low-confidence INFERRED memories must be dropped at the store-side guard"
    )


async def test_extract_drops_low_confidence_logs_reason(monkeypatch, caplog):
    """When the guard drops a memory, it should log so ops can trace it."""
    import logging
    from services import memory_service

    async def _fake_store(memories):
        return memories

    monkeypatch.setattr(
        memory_service, "store_memories_with_conflict_detection", _fake_store
    )
    async def _noop_entities(*a, **kw):
        return []
    monkeypatch.setattr(
        "services.entity_service.extract_entities", _noop_entities,
    )

    payload = (
        '[{"content": "low conf inferred", "memory_type": "fact", '
        '"source_type": "inferred", "confidence": 0.15}]'
    )

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = payload
            return R()

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())

    with caplog.at_level(logging.INFO):
        # Print statements aren't captured by caplog by default, but we accept
        # either print or log. Redirect stdout to capture prints.
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            await memory_service.extract_and_store_memories(
                messages=[{"role": "user", "content": "stuff"}],
                conversation_id="c2",
            )
        output = buf.getvalue()

    assert "low" in output.lower() and "confidence" in output.lower(), (
        f"Dropped memory should be logged. Got output: {output[-400:]}"
    )
