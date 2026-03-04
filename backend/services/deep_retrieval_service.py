"""
Gated pre-turn deep retrieval service.

Replaces single-query memory retrieval with multi-query retrieval when
the user's message is a poor search query (short, ambiguous, or in a
multi-turn conversation).
"""

import asyncio
import json
import re
from typing import List

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from services.memory_service import retrieve_memories, Memory
from services.database import async_session, MemoryEnrichmentModel


DEEP_QUERY_INSTRUCTIONS = """Given this conversation, generate exactly 3 search queries to find relevant memories. The user's latest message may be short or ambiguous — use conversation context to infer what memories would be useful.

Return ONLY a JSON array of 3 query strings."""


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


async def generate_search_queries(messages: list, model: str = "claude-haiku-4-5-20251001") -> List[str]:
    """Use Haiku to generate search queries from recent conversation context."""
    conversation_text = "\n".join([
        f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}"
        for msg in messages[-5:]
        if isinstance(msg, dict)
    ])

    if not conversation_text.strip():
        return []

    llm = ChatAnthropic(model=model, temperature=0, max_tokens=256)

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                SystemMessage(
                    content=DEEP_QUERY_INSTRUCTIONS,
                    additional_kwargs={"cache_control": {"type": "ephemeral"}},
                ),
                HumanMessage(content=f"Last 5 messages:\n{conversation_text}"),
            ]),
            timeout=3.0,
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
            return [q for q in queries if isinstance(q, str) and q.strip()][:3]

    except asyncio.TimeoutError:
        print("[DEEP RETRIEVAL] Query generation timed out (3s)")
    except Exception as e:
        print(f"[DEEP RETRIEVAL] Query generation failed: {e}")

    return []


async def deep_retrieve_memories(
    message: str,
    messages: list,
    limit: int = 10,
) -> List[Memory]:
    """Run multiple search queries in parallel and merge results.

    Args:
        message: The original user message (used as one query)
        messages: Recent messages for Haiku query generation
        limit: Maximum memories to return

    Returns:
        Deduplicated, re-ranked list of Memory objects
    """
    # Generate additional queries from conversation context
    extra_queries = await generate_search_queries(messages)

    # Run all queries in parallel: original + Haiku-generated
    async def _search(query: str, track_access: bool) -> List[Memory]:
        try:
            return await retrieve_memories(query, limit=5, update_access=track_access)
        except Exception as e:
            print(f"[DEEP RETRIEVAL] Query failed: {e}")
            return []

    # Original query tracks access, Haiku queries don't
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
    print(f"[DEEP RETRIEVAL] Found {len(ranked)} unique memories from {1 + len(extra_queries)} queries")
    return ranked[:limit]
