"""Entity extraction off the critical path.

27 of 29 entity-extraction calls in the 24h before 2026-09-05 hit the
8-second ``wait_for``. The call ran inline inside
``extract_and_store_memories`` on Terra with a 1,024-token budget, while a
short Terra classification already takes 3.5-4.6 s. Five services read
entity output (memory retrieval, consolidation, the implication engine,
episode precedents, the external-intelligence watcher), so the graph layer
was starving while every extraction paid the prompt cost.

Now: a background task, 30 s budget, Luna by default, both outcomes logged
with elapsed time so the success rate is greppable.
"""

from __future__ import annotations

import asyncio
import os
import time

import pytest


def test_timeout_and_model_constants(monkeypatch):
    from services import entity_service

    # 30 → 90 on purpose (2026-09-25): 30 s sat under the call's p90;
    # pinned in tests/test_entity_extraction_budget.py.
    assert entity_service.ENTITY_TIMEOUT_SECONDS == 90
    assert entity_service.entity_model() == "gpt-5.6-luna"
    monkeypatch.setenv("EDWARD_ENTITY_MODEL", "gpt-5.6-terra")
    assert entity_service.entity_model() == "gpt-5.6-terra"


async def test_entity_llm_is_built_on_the_entity_model(monkeypatch):
    from services import entity_service

    built = {}

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = '[{"name": "Rowan", "entity_type": "person", "aliases": []}]'
            return R()

    def _build(**kw):
        built.update(kw)
        return _L()

    monkeypatch.setattr(entity_service, "build_background_llm", _build)
    out = await entity_service.extract_entities([{"role": "user", "content": "Rowan called"}])
    assert built["model"] == "gpt-5.6-luna"
    assert out and out[0]["name"] == "Rowan"


async def test_outcomes_are_logged_with_elapsed(monkeypatch, capsys):
    from services import entity_service

    class _Ok:
        async def ainvoke(self, messages):
            class R:
                content = '[{"name": "Jordan", "entity_type": "person", "aliases": []}]'
            return R()

    monkeypatch.setattr(entity_service, "build_background_llm", lambda **kw: _Ok())
    await entity_service.extract_entities([{"role": "user", "content": "Jordan texted"}])

    class _Slow:
        async def ainvoke(self, messages):
            await asyncio.sleep(5)

    monkeypatch.setattr(entity_service, "build_background_llm", lambda **kw: _Slow())
    monkeypatch.setattr(entity_service, "ENTITY_TIMEOUT_SECONDS", 0.05)
    await entity_service.extract_entities([{"role": "user", "content": "Jordan texted"}])

    out = capsys.readouterr().out
    assert "[ENTITY] ok 1 entities in " in out
    assert "[ENTITY] timed out after 0.05s" in out


async def test_entity_linking_does_not_block_extraction(monkeypatch):
    from services import memory_service, entity_service

    payload = ('[{"content": "Ellis is allergic to shellfish", "memory_type": "fact", "importance": 0.9, '
               '"temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.95, "about_owner": true}]')

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = payload
            return R()

    async def _store(memories):
        for m in memories:
            m.id = "mem-1"
        return memories

    async def _noop(*a, **k):
        return []

    linked = []

    async def _slow_entities(messages):
        await asyncio.sleep(1.0)
        return [{"name": "Ellis", "entity_type": "person", "aliases": []}]

    class _E:
        id = "ent-1"

    async def _upsert(**kw):
        linked.append(kw["name"])
        return _E()

    async def _link(**kw):
        return None

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    monkeypatch.setattr(entity_service, "extract_entities", _slow_entities)
    monkeypatch.setattr(entity_service, "upsert_entity", _upsert)
    monkeypatch.setattr(entity_service, "link_entity_to_memory", _link)

    t0 = time.monotonic()
    stored = await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "I'm allergic to shellfish"}],
        conversation_id="c-bg",
    )
    elapsed = time.monotonic() - t0

    assert len(stored) == 1
    assert elapsed < 0.6, f"extraction waited on entity linking ({elapsed:.2f}s)"
    assert linked == []
    await asyncio.sleep(1.3)
    assert linked == ["Ellis"], "the background task must still link entities"
