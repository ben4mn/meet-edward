"""
N3: as the LLM streams its response, a background watcher scans for
entity-shaped tokens (capitalized names) and kicks off async memory
queries. Results land in the N2 cache. When the LLM later tool-calls
remember_search for that entity, it returns instantly.

Two seams:
  1. extract_entity_candidates(text) — pure function. Picks out
     plausible entity names. Conservative: false positives are
     fine (extra cache writes), false negatives are costly.
  2. SpeculativeRecaller.observe(text) — async. Calls
     extract_entity_candidates, dedupes against the conversation's
     in-flight queries, and submits async search_memories tasks
     whose results populate the N2 cache.
"""

from __future__ import annotations

import asyncio

import pytest


@pytest.mark.parametrize(
    "text,must_include,must_not_include",
    [
        # Mid-sentence proper nouns
        (
            "I'll check Sasha's calendar for the Lisbon trip.",
            {"Sasha", "Lisbon"},
            {"I'll", "check"},
        ),
        # Sentence-initial capitals are NOT entities
        (
            "The weather is nice today.",
            set(),
            {"The", "Weather"},
        ),
        # All-caps acronyms (skip — too noisy)
        (
            "Send via SMS to LAX",
            {"LAX"},  # 3+ letter all-caps that aren't common words is fine
            {"SMS"},  # SMS is too generic — exclude common acronyms
        ),
        # Multi-word entities — single-word extraction is fine for now
        (
            "Harbor Inn in San Diego",
            {"Harbor", "Inn", "San", "Diego"},
            set(),
        ),
        # Lowercase common words
        (
            "tomorrow morning at the office",
            set(),
            {"tomorrow", "morning", "office"},
        ),
    ],
)
def test_extract_entity_candidates(text, must_include, must_not_include):
    from services.speculative_recall import extract_entity_candidates

    found = set(extract_entity_candidates(text))

    missing = must_include - found
    assert not missing, f"text={text!r}: missing entities {missing}, got {found}"

    accidental = must_not_include & found
    assert not accidental, f"text={text!r}: false positives {accidental}, got {found}"


@pytest.mark.asyncio
async def test_observe_kicks_off_memory_queries_per_entity(monkeypatch):
    """When SpeculativeRecaller observes text containing entities, it
    schedules async search_memories calls and they populate the N2 cache."""
    from services import memory_service
    from services.graph import tools as graph_tools
    from services.speculative_recall import SpeculativeRecaller

    queries_fired = []

    async def fake_search_memories(*args, **kwargs):
        queries_fired.append(kwargs.get("query") or (args[0] if args else None))
        return ([], 0)

    monkeypatch.setattr(memory_service, "search_memories", fake_search_memories)
    if hasattr(graph_tools, "_clear_memory_search_cache"):
        graph_tools._clear_memory_search_cache()

    graph_tools.set_current_conversation_id("conv-spec-1")

    recaller = SpeculativeRecaller(conversation_id="conv-spec-1")
    await recaller.observe("I'll check Sasha and meet at the Lisbon hotel.")

    # Let scheduled tasks run
    await recaller.drain()

    # Both entities should have triggered queries
    assert "Sasha" in queries_fired, (
        f"expected speculative query for 'Sasha', got fired={queries_fired}"
    )
    assert "Lisbon" in queries_fired


@pytest.mark.asyncio
async def test_observe_dedupes_repeat_entities(monkeypatch):
    """Seeing the same entity twice in one stream must not fire two
    queries — the first one is enough."""
    from services import memory_service
    from services.graph import tools as graph_tools
    from services.speculative_recall import SpeculativeRecaller

    fire_counts = {}

    async def fake_search_memories(query=None, **kwargs):
        fire_counts[query] = fire_counts.get(query, 0) + 1
        return ([], 0)

    monkeypatch.setattr(memory_service, "search_memories", fake_search_memories)
    if hasattr(graph_tools, "_clear_memory_search_cache"):
        graph_tools._clear_memory_search_cache()
    graph_tools.set_current_conversation_id("conv-spec-2")

    recaller = SpeculativeRecaller(conversation_id="conv-spec-2")
    await recaller.observe("Sasha is great. Sasha works at the library. Sasha.")
    await recaller.drain()

    assert fire_counts.get("Sasha", 0) == 1, (
        f"expected 1 query for 'Sasha', got {fire_counts}"
    )
