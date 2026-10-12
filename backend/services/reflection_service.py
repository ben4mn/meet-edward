"""
Post-turn reflection service for memory enrichment.

After each conversation turn, generates multiple search queries from the
conversation context and stores additional relevant memories as enrichments
for the next turn.
"""

import asyncio
import logging
import uuid
from typing import List, Optional
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage

from services.llm_utils import build_background_llm, make_background_system_message
from services.json_parsing import extract_json_payload

from services.database import async_session, MemoryEnrichmentModel, MemoryModel
from services.memory_service import retrieve_memories, Memory
from services.owner import owner_text

logger = logging.getLogger(__name__)


REFLECTION_QUERY_PROMPT = owner_text("""Analyze this conversation and generate 3-5 search queries that would find relevant context from long-term memory. Focus on:

1. People, places, or topics mentioned or implied
2. Related context the user might expect you to remember
3. Background information that would improve your next response

Return ONLY a JSON array of query strings. Example: ["{OWNER}'s work projects", "previous discussions about Python", "{OWNER}'s preferences for code style"]

Conversation (last 10 messages):
{conversation}""")


def should_reflect(messages: list, turn_count: int) -> bool:
    """Gate: decide whether to run reflection after this turn.

    Skip if:
    - Less than 2 turns in the conversation
    - Last message is very short AND turn_count < 3 (early trivial exchange)
    """
    if turn_count < 2:
        return False

    # Check last human message length
    last_human = None
    for msg in reversed(messages):
        role = msg.get("role", "") if isinstance(msg, dict) else getattr(msg, "type", "")
        if role in ("human", "user"):
            content = msg.get("content", "") if isinstance(msg, dict) else getattr(msg, "content", "")
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") if isinstance(block, dict) else str(block)
                    for block in content
                )
            last_human = content
            break

    if last_human and len(last_human) < 10 and turn_count < 3:
        return False

    return True


async def generate_reflection_queries(
    messages: list,
    conversation_id: str | None = None,
) -> List[str]:
    """Generate search queries via the shared DreamContext cache.

    When ``conversation_id`` is provided, back-to-back callers for the same
    turn (e.g. reflection + deep retrieval) share one underlying LLM call
    for 60 s. When it's None, a per-call fallback ID is used and caching
    essentially disabled — retained for legacy callers that don't have a
    conversation scope.
    """
    from services.dream.context import REFLECTION_TIMEOUT_SECONDS, generate_search_queries

    # Post-turn: nobody is waiting, so give the model the time it needs.
    return await generate_search_queries(
        conversation_id or "reflection:anon",
        messages,
        n=5,
        window=10,
        timeout=REFLECTION_TIMEOUT_SECONDS,
    )


async def run_reflection(
    conversation_id: str,
    messages: list,
    already_retrieved_ids: List[str],
) -> int:
    """Run reflection queries and store enrichments.

    Args:
        conversation_id: Current conversation ID
        messages: Recent messages for query generation
        already_retrieved_ids: Memory IDs already in context (to deduplicate)

    Returns:
        Number of enrichments stored
    """
    queries = await generate_reflection_queries(messages, conversation_id=conversation_id)
    if not queries:
        return 0

    seen_ids = set(already_retrieved_ids)
    enrichments_to_store = []

    for query in queries:
        try:
            memories = await asyncio.wait_for(
                retrieve_memories(query, limit=3, update_access=False),
                timeout=3.0,
            )
            for memory in memories:
                if memory.id not in seen_ids:
                    seen_ids.add(memory.id)
                    enrichments_to_store.append((memory, query))
        except asyncio.TimeoutError:
            logger.warning("reflection query timed out: %s", query[:50])
        except Exception as e:
            logger.warning("reflection query failed: %s", e)

    if not enrichments_to_store:
        return 0

    # Store enrichments in database
    async with async_session() as session:
        for memory, query in enrichments_to_store:
            enrichment = MemoryEnrichmentModel(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                memory_id=memory.id,
                memory_content=memory.content,
                memory_type=memory.memory_type,
                importance=memory.importance,
                temporal_nature=memory.temporal_nature,
                query_source=query,
                score=memory.score,
                consumed=False,
            )
            session.add(enrichment)
        await session.commit()

    count = len(enrichments_to_store)
    logger.info(
        "reflection stored enrichments",
        extra={"subsystem": "reflection", "conversation_id": conversation_id, "count": count},
    )
    return count


# What the whole reflection may take beyond its query budget: the memory
# searches (3 s each) and the enrichment write.
REFLECTION_OUTER_MARGIN_SECONDS = 15.0


def reflection_outer_timeout() -> float:
    """The bound on one whole reflection: the query budget
    (``dream.context.REFLECTION_TIMEOUT_SECONDS``, read per call) plus
    ``REFLECTION_OUTER_MARGIN_SECONDS``. It was a hard-coded 10 s under a
    20 s query budget (2026-09-25 audit), so a reflection whose queries
    took 11-20 s was cancelled after the model had answered."""
    from services.dream import context

    return float(context.REFLECTION_TIMEOUT_SECONDS) + REFLECTION_OUTER_MARGIN_SECONDS


async def run_reflection_safe(
    conversation_id: str,
    messages: list,
    already_retrieved_ids: List[str],
) -> None:
    """Fire-and-forget wrapper that never raises."""
    bound = reflection_outer_timeout()
    try:
        await asyncio.wait_for(
            run_reflection(conversation_id, messages, already_retrieved_ids),
            timeout=bound,
        )
    except asyncio.TimeoutError:
        logger.warning("full reflection timed out (%.0fs) for %s", bound, conversation_id)
    except Exception as e:
        logger.warning("reflection failed safely: %s", e)


async def load_enrichments(conversation_id: str, limit: int = 5) -> List[Memory]:
    """Load unconsumed enrichments for a conversation and mark them consumed.

    Only loads enrichments created within the last 24 hours.

    Returns:
        List of Memory objects from the enrichment buffer
    """
    cutoff = datetime.now() - timedelta(hours=24)

    async with async_session() as session:
        from sqlalchemy import or_, select, update

        result = await session.execute(
            select(MemoryEnrichmentModel)
            .where(
                MemoryEnrichmentModel.conversation_id == conversation_id,
                MemoryEnrichmentModel.consumed == False,
                MemoryEnrichmentModel.created_at >= cutoff,
            )
            .order_by(MemoryEnrichmentModel.score.desc())
            .limit(limit)
        )
        rows = result.scalars().all()

        if not rows:
            return []

        # A row retired since it was found (valid_until passed) is consumed
        # but not replayed: recall is live-only (memory audit, 2026-09-27).
        live_ids = set((await session.execute(
            select(MemoryModel.id).where(
                MemoryModel.id.in_([row.memory_id for row in rows]),
                or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > datetime.utcnow()),
            )
        )).scalars().all())

        # Mark as consumed
        ids = [row.id for row in rows]
        await session.execute(
            update(MemoryEnrichmentModel)
            .where(MemoryEnrichmentModel.id.in_(ids))
            .values(consumed=True)
        )
        await session.commit()

        return [
            Memory(
                id=row.memory_id,
                content=row.memory_content,
                memory_type=row.memory_type,
                importance=row.importance,
                temporal_nature=row.temporal_nature,
                score=row.score,
            )
            for row in rows
            if row.memory_id in live_ids
        ]
