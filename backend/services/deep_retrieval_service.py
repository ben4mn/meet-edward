"""
Gated pre-turn deep retrieval service.

Replaces single-query memory retrieval with multi-query retrieval when
the user's message is a poor search query (short, ambiguous, or in a
multi-turn conversation).
"""

import asyncio
import logging
from typing import List

from langchain_core.messages import HumanMessage, SystemMessage

from services.llm_utils import build_background_llm, make_background_system_message
from services.json_parsing import extract_json_payload

from services.memory_service import retrieve_memories, Memory
from services.database import async_session, MemoryEnrichmentModel

logger = logging.getLogger(__name__)


DEEP_QUERY_PROMPT = """Given this conversation, generate exactly 5 search queries to find relevant memories. The user's latest message may be short or ambiguous — use conversation context to infer what memories would be useful.

Include diverse query strategies: direct topic, related context, names/entities, synonyms/rephrases, and broader themes.

Return ONLY a JSON array of 5 query strings.

Last 5 messages:
{conversation}"""


async def should_deep_retrieve(message: str, conversation_id: str, turn_count: int) -> bool:
    """Decide whether to use deep retrieval instead of single-query retrieval.

    Returns True if:
    - Message is short (< 20 chars) — poor search query
    - Turn count >= 3 — conversation has enough context for better queries
    - No unconsumed enrichments exist — reflection hasn't run yet
    """
    if len(message.strip()) < 20:
        return True

    if turn_count >= 3:
        return True

    # Check for unconsumed enrichments
    try:
        from sqlalchemy import select, func
        async with async_session() as session:
            result = await session.execute(
                select(func.count(MemoryEnrichmentModel.id))
                .where(
                    MemoryEnrichmentModel.conversation_id == conversation_id,
                    MemoryEnrichmentModel.consumed == False,
                )
            )
            count = result.scalar() or 0
            if count == 0:
                return True
    except Exception:
        pass

    return False


async def generate_search_queries(
    messages: list,
    conversation_id: str | None = None,
) -> List[str]:
    """Generate search queries via the shared DreamContext cache.

    See ``services.reflection_service.generate_reflection_queries`` for the
    caching contract — both call the same helper so one LLM call per turn
    serves both subsystems.
    """
    from services.dream.context import DEEP_RETRIEVAL_TIMEOUT_SECONDS, generate_search_queries as dream_gen

    # Pre-turn: the owner is waiting on first-token latency; a slow answer is
    # left running for reflection to pick up rather than cancelled.
    return await dream_gen(
        conversation_id or "deep_retrieval:anon",
        messages,
        n=5,
        window=10,
        timeout=DEEP_RETRIEVAL_TIMEOUT_SECONDS,
    )


async def deep_retrieve_memories(
    message: str,
    messages: list,
    limit: int = 10,
    conversation_id: str | None = None,
) -> List[Memory]:
    """Run multiple search queries in parallel and merge results.

    Args:
        message: The original user message (used as one query)
        messages: Recent messages for LLM query generation
        limit: Maximum memories to return
        conversation_id: Used for the DreamContext cache key so reflection
            and deep retrieval for the same turn share one LLM call.

    Returns:
        Deduplicated, re-ranked list of Memory objects
    """
    # Generate additional queries via shared DreamContext cache.
    extra_queries = await generate_search_queries(messages, conversation_id=conversation_id)

    # Run all queries in parallel: original + background-LLM-generated
    async def _search(query: str, track_access: bool) -> List[Memory]:
        try:
            return await retrieve_memories(query, limit=8, update_access=track_access)
        except Exception as e:
            logger.warning("deep retrieval query failed: %s", e)
            return []

    # Original query tracks access, background-LLM-generated queries don't
    tasks = [_search(message, True)]
    for q in extra_queries:
        tasks.append(_search(q, False))

    results = await asyncio.gather(*tasks)

    # Deduplicate by memory ID, keeping highest score
    best_memories = {}
    for memory_list in results:
        for memory in memory_list:
            if memory.id not in best_memories or memory.score > best_memories[memory.id].score:
                best_memories[memory.id] = memory

    # Sort by score and return top results
    ranked = sorted(best_memories.values(), key=lambda m: m.score, reverse=True)
    logger.info(
        "deep retrieval completed",
        extra={
            "subsystem": "deep_retrieval",
            "unique_memories": len(ranked),
            "query_count": 1 + len(extra_queries),
        },
    )
    return ranked[:limit]
