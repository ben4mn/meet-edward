"""
End-to-end smoke tests for the Edward stabilization plan.

These tests exercise the three fixes together:
  1. JSON helper lets memory extraction recover dict-wrapped payloads.
  2. iMessage listener persists and restores its high-water mark.
  3. Retrieval filters low-confidence memories; store-side guard rejects garbage.

The goal is to prove the fixes compose correctly — an extraction that would
have silently returned [] under the old code now stores a memory, and that
memory survives a confidence-aware retrieval call.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fix #1 + #3 compose: bad-shape LLM JSON → memory extracted & retrievable
# ---------------------------------------------------------------------------


async def test_extraction_recovery_then_retrieval_end_to_end(clean_db, monkeypatch):
    """A GPT-5.4-shaped response (dict-wrapped) now extracts a memory, which
    then passes the confidence filter and surfaces on retrieval."""
    from services import database, memory_service

    async def _noop_entities(*a, **kw):
        return []
    monkeypatch.setattr("services.entity_service.extract_entities", _noop_entities)

    wrapped = (
        '{"memories": [{"content": "Ellis is allergic to shellfish", '
        '"memory_type": "fact", "importance": 0.9, "temporal_nature": "timeless", '
        '"source_type": "user_stated", "confidence": 0.95}]}'
    )

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = wrapped
            return R()

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())

    # --- extraction path ---
    await memory_service.extract_and_store_memories(
        messages=[
            {"role": "user", "content": "I'm allergic to shellfish."},
            {"role": "assistant", "content": "Noted."},
        ],
        conversation_id="e2e-1",
    )

    # --- retrieval path (default min_confidence=0.5) ---
    results = await memory_service.retrieve_memories(
        "allergies", limit=5, min_score=0.0
    )
    contents = [r.content for r in results]
    assert any("shellfish" in c for c in contents), (
        f"Extracted memory must be retrievable. Got: {contents}"
    )


async def test_garbage_extraction_stays_out_of_retrieval(clean_db, monkeypatch):
    """A GPT-5.4 response with low-confidence inferred memory is dropped by
    the store-side guard and therefore never surfaces on retrieval."""
    from services import database, memory_service

    async def _noop_entities(*a, **kw):
        return []
    monkeypatch.setattr("services.entity_service.extract_entities", _noop_entities)

    # confidence=0.15 + inferred → guarded out at ingestion
    payload = (
        '[{"content": "maybe owner likes pickles", "memory_type": "preference", '
        '"importance": 0.3, "source_type": "inferred", "confidence": 0.15}]'
    )

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = payload
            return R()

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())

    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "chat about food"}],
        conversation_id="e2e-2",
    )

    results = await memory_service.retrieve_memories(
        "pickles", limit=5, min_score=0.0
    )
    assert all("pickles" not in r.content for r in results), (
        "Low-confidence inferred extraction must never reach retrieval"
    )


# ---------------------------------------------------------------------------
# Fix #2: iMessage listener survives two rapid restarts without replay
# ---------------------------------------------------------------------------


async def test_imessage_listener_survives_two_restarts_no_replay(clean_db):
    """Full restart cycle against the live test DB: seed on cold start,
    restore on warm start, persist after poll."""
    from services import database
    from services.heartbeat import listener_imessage as listener

    async with database.async_session() as session:
        session.add(database.HeartbeatConfigModel(id="default", digest_token_cap=800))
        await session.commit()

    # --- Cold start ---
    listener._last_seen_rowid = 0
    with patch.object(listener, "_get_max_rowid", return_value=10_000):
        persisted = await listener._load_persisted_rowid()
        assert persisted is None
        seed = 10_000
        listener._last_seen_rowid = seed
        await listener._persist_rowid(seed)

    # --- First poll bumps rowid ---
    fake_msgs = [
        {"rowid": 10_050, "text": "hi", "is_from_me": False, "date": 0, "service": "iMessage",
         "sender_id": "+15555550100", "chat_identifier": "c1", "display_name": None,
         "is_group": False, "guid": "g1"},
    ]
    # _store_events returns a StoreResult now (it grew owner-direct and
    # inbound-direct flags); one stored message, nothing to triage at once.
    stored = listener.StoreResult(stored=1, has_mentions=False, has_owner_direct=False, has_inbound_direct=False)
    with patch.object(listener, "_query_chat_db", return_value=fake_msgs):
        with patch.object(listener, "_store_events", AsyncMock(return_value=stored)):
            await listener._poll_once()
    assert listener._last_seen_rowid == 10_050

    # --- Warm restart: load persisted, no re-seed from chat.db ---
    listener._last_seen_rowid = 0
    persisted_2 = await listener._load_persisted_rowid()
    assert persisted_2 == 10_050, "Persisted rowid must survive restart"

    # --- Second rapid restart: still 10_050, no drift ---
    persisted_3 = await listener._load_persisted_rowid()
    assert persisted_3 == 10_050
