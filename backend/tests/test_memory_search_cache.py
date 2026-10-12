"""
N2: cache remember_search results within the same conversation so
repeated queries don't re-hit pgvector + BM25 each time.

The LLM often searches for the same entity multiple times in one
turn (or across rapid turns) — once mid-reasoning, again to verify
before answering. Each call costs ~50ms of DB I/O and is identical.
A simple per-conversation LRU+TTL cache turns the second call into
~10us in-memory.

Cache scope: keyed by (conversation_id, query, memory_type). New
conversation → fresh queries. Same conversation, repeat query →
cache hit. TTL keeps results fresh enough that recent memory writes
in another tab don't go stale-forever.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.asyncio
async def test_repeat_search_in_same_conversation_hits_cache(monkeypatch):
    """Two remember_search calls with identical args within the same
    conversation must call search_memories exactly once."""
    from services.graph import tools as graph_tools
    from services import memory_service

    call_count = {"n": 0}

    async def fake_search_memories(*args, **kwargs):
        call_count["n"] += 1
        # Mimic the (memories, total) tuple shape
        return ([], 0)

    monkeypatch.setattr(memory_service, "search_memories", fake_search_memories)
    # Reset cache between tests
    if hasattr(graph_tools, "_clear_memory_search_cache"):
        graph_tools._clear_memory_search_cache()

    graph_tools.set_current_conversation_id("conv-cache-1")

    out1 = await graph_tools.remember_search.ainvoke({"query": "Sasha"})
    out2 = await graph_tools.remember_search.ainvoke({"query": "Sasha"})

    assert out1 == out2
    assert call_count["n"] == 1, (
        f"second call should hit cache; got {call_count['n']} DB queries"
    )


@pytest.mark.asyncio
async def test_different_conversation_does_not_share_cache(monkeypatch):
    """Cache is scoped per conversation — switching conversations must
    re-query. Otherwise stale data leaks between sessions."""
    from services.graph import tools as graph_tools
    from services import memory_service

    call_count = {"n": 0}

    async def fake_search_memories(*args, **kwargs):
        call_count["n"] += 1
        return ([], 0)

    monkeypatch.setattr(memory_service, "search_memories", fake_search_memories)
    if hasattr(graph_tools, "_clear_memory_search_cache"):
        graph_tools._clear_memory_search_cache()

    graph_tools.set_current_conversation_id("conv-A")
    await graph_tools.remember_search.ainvoke({"query": "Sasha"})

    graph_tools.set_current_conversation_id("conv-B")
    await graph_tools.remember_search.ainvoke({"query": "Sasha"})

    assert call_count["n"] == 2, (
        f"different conversations must not share cache; got {call_count['n']}"
    )


@pytest.mark.asyncio
async def test_different_memory_type_does_not_share_cache(monkeypatch):
    from services.graph import tools as graph_tools
    from services import memory_service

    call_count = {"n": 0}

    async def fake_search_memories(*args, **kwargs):
        call_count["n"] += 1
        return ([], 0)

    monkeypatch.setattr(memory_service, "search_memories", fake_search_memories)
    if hasattr(graph_tools, "_clear_memory_search_cache"):
        graph_tools._clear_memory_search_cache()

    graph_tools.set_current_conversation_id("conv-types")
    await graph_tools.remember_search.ainvoke({"query": "X"})
    await graph_tools.remember_search.ainvoke({"query": "X", "memory_type": "fact"})

    assert call_count["n"] == 2
