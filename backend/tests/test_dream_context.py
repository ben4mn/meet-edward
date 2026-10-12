"""
DreamContext tests (Track B1).

Reflection and deep retrieval both call Haiku to generate search queries
from the same conversation context. They duplicated that work. The
``services.dream.context.generate_search_queries`` helper provides a
shared, cached entry point — one LLM call per turn serves both consumers.

Tests:
  - Back-to-back calls with the same conversation + last human message
    use the cache and make exactly one LLM call.
  - A different conversation_id yields a separate cache entry.
  - A different last-human message invalidates the entry.
  - The caller gets a list clipped to ``n``.
  - The helper goes through ``build_background_llm`` (not ChatAnthropic).
"""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock

import pytest


class _CountingLLM:
    def __init__(self, content: str):
        self._content = content
        self.calls = 0

    async def ainvoke(self, messages):
        assert isinstance(messages, list)
        self.calls += 1

        class _R:
            pass

        r = _R()
        r.content = self._content
        return r


@pytest.fixture(autouse=True)
def _clear_dream_cache():
    """Drop the DreamContext in-process cache before each test."""
    try:
        from services.dream import context as dream_ctx

        dream_ctx._cache.clear()
    except Exception:
        pass
    yield


async def test_back_to_back_calls_share_cache(monkeypatch):
    from services.dream.context import generate_search_queries

    stub = _CountingLLM('["query one", "query two", "query three", "query four", "query five"]')
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)

    messages = [
        {"role": "user", "content": "what do you remember about my hiking trips?"},
        {"role": "assistant", "content": "Let me check..."},
    ]
    conversation_id = "conv-1"

    first = await generate_search_queries(conversation_id, messages, n=5)
    second = await generate_search_queries(conversation_id, messages, n=5)

    assert first == second, "Cached result should be identical on second call."
    assert stub.calls == 1, (
        f"Second call within cache TTL must reuse the cached queries. "
        f"Expected 1 LLM call, got {stub.calls}."
    )


async def test_different_conversation_is_separate_cache_entry(monkeypatch):
    from services.dream.context import generate_search_queries

    stub = _CountingLLM('["qa", "qb", "qc", "qd", "qe"]')
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)

    messages = [{"role": "user", "content": "same question"}]

    await generate_search_queries("conv-A", messages, n=5)
    await generate_search_queries("conv-B", messages, n=5)

    assert stub.calls == 2, (
        f"Two different conversations must each make their own LLM call; "
        f"got {stub.calls}."
    )


async def test_new_human_message_invalidates_cache(monkeypatch):
    from services.dream.context import generate_search_queries

    stub = _CountingLLM('["x", "y", "z", "w", "v"]')
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)

    msgs_a = [{"role": "user", "content": "tell me about hiking"}]
    msgs_b = [{"role": "user", "content": "tell me about cooking"}]

    await generate_search_queries("conv-X", msgs_a, n=5)
    await generate_search_queries("conv-X", msgs_b, n=5)

    assert stub.calls == 2, (
        f"Different last-human messages in the same conversation must not "
        f"share cache; got {stub.calls}."
    )


async def test_respects_n_parameter(monkeypatch):
    from services.dream.context import generate_search_queries

    stub = _CountingLLM('["q1", "q2", "q3", "q4", "q5"]')
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)

    messages = [{"role": "user", "content": "hi"}]
    result = await generate_search_queries("conv-n", messages, n=3)

    assert len(result) == 3, f"n=3 should return 3 queries, got {len(result)}."


async def test_reflection_and_deep_retrieval_share_cache(monkeypatch):
    """End-to-end wiring check: when both reflection and deep retrieval run
    for the same conversation turn, they go through DreamContext and
    together make exactly one LLM call for query generation.

    Proves the Track B1 goal: ~1 LLM call per turn instead of 2.
    """
    from services.reflection_service import generate_reflection_queries
    from services.deep_retrieval_service import generate_search_queries as deep_gen

    stub = _CountingLLM('["alpha", "bravo", "charlie", "delta", "echo"]')
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)

    messages = [
        {"role": "user", "content": "what's my plan for next weekend?"},
        {"role": "assistant", "content": "let me pull that up..."},
    ]
    conversation_id = "conv-shared-cache"

    reflection_qs = await generate_reflection_queries(messages, conversation_id=conversation_id)
    deep_qs = await deep_gen(messages, conversation_id=conversation_id)

    assert reflection_qs, "reflection should have gotten non-empty queries"
    assert deep_qs, "deep retrieval should have gotten non-empty queries"
    assert stub.calls == 1, (
        f"reflection + deep retrieval together must make exactly 1 LLM call "
        f"via DreamContext cache, got {stub.calls}."
    )


async def test_uses_build_background_llm(monkeypatch):
    """Verify the GPT/OpenAI routing — patching build_background_llm must
    be enough to intercept every LLM call, proving nothing else
    instantiates a client directly.
    """
    from services.dream.context import generate_search_queries

    factory_called = 0

    def _factory(*args, **kwargs):
        nonlocal factory_called
        factory_called += 1
        stub = _CountingLLM('["a", "b", "c", "d", "e"]')
        return stub

    monkeypatch.setattr("services.llm_utils.build_background_llm", _factory)

    messages = [{"role": "user", "content": "what?"}]
    await generate_search_queries("conv-route", messages, n=5)

    assert factory_called == 1, (
        "generate_search_queries must route through build_background_llm "
        "(GPT) — not ChatAnthropic or any other client."
    )
