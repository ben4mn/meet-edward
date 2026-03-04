"""
Post-turn reflection service for memory enrichment.

After each conversation turn, generates multiple search queries from the
conversation context and stores additional relevant memories as enrichments
for the next turn.
"""

import asyncio
import json
import re
import uuid
from typing import List, Optional
from datetime import datetime, timedelta

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from services.database import async_session, MemoryEnrichmentModel
from services.memory_service import retrieve_memories, Memory


REFLECTION_QUERY_INSTRUCTIONS = """Analyze this conversation and generate 3-5 search queries that would find relevant context from long-term memory. Focus on:

1. People, places, or topics mentioned or implied
2. Related context the user might expect you to remember
3. Background information that would improve your next response

Return ONLY a JSON array of query strings. Example: ["Ben's work projects", "previous discussions about Python", "Ben's preferences for code style"]"""


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


async def generate_reflection_queries(messages: list, model: str = "claude-haiku-4-5-20251001") -> List[str]:
    """Use Haiku to generate search queries from recent conversation context."""
    # Format last 10 messages
    conversation_text = "\n".join([
        f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}"
        for msg in messages[-10:]
        if isinstance(msg, dict)
    ])

    if not conversation_text.strip():
        return []

    llm = ChatAnthropic(model=model, temperature=0, max_tokens=512)

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(
                    content=REFLECTION_QUERY_INSTRUCTIONS,
                    additional_kwargs={"cache_control": {"type": "ephemeral"}},
                ),
                HumanMessage(content=f"Conversation (last 10 messages):\n{conversation_text}"),
            ]),
            timeout=5.0,
        )

        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        # Extract JSON array
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            parts = response_text.split("```")
            if len(parts) >= 2:
                response_text = parts[1].strip()

        json_match = re.search(r'\[[\s\S]*?\]', response_text)
        if json_match:
            response_text = json_match.group()

        queries = json.loads(response_text)
        if isinstance(queries, list):
            return [q for q in queries if isinstance(q, str) and q.strip()][:5]

    except asyncio.TimeoutError:
        print("[REFLECTION] Query generation timed out (5s)")
    except Exception as e:
        print(f"[REFLECTION] Query generation failed: {e}")

    return []


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
    queries = await generate_reflection_queries(messages)
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
            print(f"[REFLECTION] Query timed out: {query[:50]}")
        except Exception as e:
            print(f"[REFLECTION] Query failed: {e}")

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
    print(f"[REFLECTION] Stored {count} enrichments for conversation {conversation_id}")
    return count


async def run_reflection_safe(
    conversation_id: str,
    messages: list,
    already_retrieved_ids: List[str],
) -> None:
    """Fire-and-forget wrapper that never raises."""
    try:
        await asyncio.wait_for(
            run_reflection(conversation_id, messages, already_retrieved_ids),
            timeout=10.0,
        )
    except asyncio.TimeoutError:
        print(f"[REFLECTION] Full reflection timed out (10s) for {conversation_id}")
    except Exception as e:
        print(f"[REFLECTION] Reflection failed safely: {e}")


async def load_enrichments(conversation_id: str, limit: int = 5) -> List[Memory]:
    """Load unconsumed enrichments for a conversation and mark them consumed.

    Only loads enrichments created within the last 24 hours.

    Returns:
        List of Memory objects from the enrichment buffer
    """
    cutoff = datetime.now() - timedelta(hours=24)

    async with async_session() as session:
        from sqlalchemy import select, update

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
        ]
