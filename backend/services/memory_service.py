"""
Memory service for Edward's long-term memory capabilities.

Handles:
- Embedding generation using sentence-transformers (all-MiniLM-L6-v2)
- Hybrid search (70% vector similarity + 30% BM25 keyword)
- Memory extraction from conversations using LLM
"""

import json
import math
import uuid
import re
from typing import List, Optional
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, text, func, update
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage

from services.database import async_session, MemoryModel

# Lazy load the embedding model to avoid loading at import time
_embedding_model = None


def get_embedding_model():
    """Lazy load the sentence-transformers model."""
    global _embedding_model
    if _embedding_model is None:
        from sentence_transformers import SentenceTransformer
        _embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
    return _embedding_model


@dataclass
class Memory:
    """A memory retrieved from or to be stored in the database."""
    id: Optional[str]
    content: str
    memory_type: str  # fact, preference, context, instruction
    importance: float
    source_conversation_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None  # Track when memory was last modified
    last_accessed: Optional[datetime] = None
    access_count: int = 0
    temporal_nature: str = "timeless"  # timeless, temporary, evolving
    tier: str = "observation"  # observation, belief, knowledge
    reinforcement_count: int = 0
    user_id: Optional[str] = None  # For future multi-user support
    score: float = 0.0  # Relevance score during retrieval


def get_embedding(text: str) -> List[float]:
    """Generate embedding vector for text using local sentence-transformers."""
    model = get_embedding_model()
    embedding = model.encode(text, convert_to_numpy=True)
    return embedding.tolist()


def compute_temporal_boost(
    temporal_nature: str,
    last_accessed: Optional[datetime],
    access_count: int,
) -> float:
    """
    Compute a multiplicative temporal boost in [0.7, 1.3] based on recency,
    frequency, and temporal nature.

    - timeless: boost stays ~1.0 (barely affected)
    - temporary: strong recency weight, decays to 0.7x when stale
    - evolving: moderate blend of recency + frequency
    """
    now = datetime.now(timezone.utc)

    # Recency: exponential decay with 90-day half-life
    if last_accessed:
        la = last_accessed if last_accessed.tzinfo else last_accessed.replace(tzinfo=timezone.utc)
        days_since = max((now - la).total_seconds() / 86400, 0)
    else:
        days_since = 365  # assume very old if never accessed

    recency = math.exp(-0.693 * days_since / 90)  # 0.693 = ln(2)

    # Frequency: log-normalized to [0, 1] (caps at ~150 accesses)
    frequency = min(math.log(1 + access_count) / 5.0, 1.0)

    if temporal_nature == "timeless":
        # Barely affected by recency/frequency — stays near 1.0
        boost = 1.0 + 0.05 * (recency - 0.5)
    elif temporal_nature == "temporary":
        # Strong recency weight — decays noticeably when stale
        boost = 0.7 + 0.6 * recency  # range: 0.7 (stale) to 1.3 (fresh)
    elif temporal_nature == "evolving":
        # Moderate blend of recency + frequency
        boost = 0.8 + 0.3 * recency + 0.2 * frequency
    else:
        boost = 1.0

    return max(0.7, min(1.3, boost))


def _compute_tier(reinforcement_count: int) -> str:
    """Determine memory tier based on reinforcement count."""
    if reinforcement_count >= 5:
        return "knowledge"
    elif reinforcement_count >= 2:
        return "belief"
    return "observation"


async def retrieve_memories(
    query: str,
    limit: int = 5,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3,
    min_score: float | None = None,
    memory_types: list[str] | None = None,
    update_access: bool = True,
) -> List[Memory]:
    """
    Retrieve relevant memories using hybrid search.

    Combines:
    - Vector similarity search (cosine distance)
    - BM25 keyword search (full-text)

    Args:
        query: Search query text
        limit: Maximum number of memories to return
        vector_weight: Weight for vector similarity (default 0.7)
        keyword_weight: Weight for keyword matching (default 0.3)
        min_score: Minimum combined score threshold (optional)
        memory_types: Filter to specific memory types e.g. ["fact", "preference"] (optional)

    Returns:
        List of Memory objects sorted by relevance
    """
    query_embedding = get_embedding(query)
    # Format embedding as PostgreSQL array literal
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    # Build optional type filter clause
    type_filter = ""
    type_params = {}
    if memory_types:
        placeholders = ", ".join(f":mtype_{i}" for i in range(len(memory_types)))
        type_filter = f" AND memory_type IN ({placeholders})"
        type_params = {f"mtype_{i}": t for i, t in enumerate(memory_types)}

    async with async_session() as session:
        # Simplified hybrid search using separate queries and Python-side combination
        # First, do vector search
        vector_query = text(f"""
            SELECT
                id, content, memory_type, importance,
                source_conversation_id, created_at, updated_at, last_accessed, access_count,
                temporal_nature, tier, reinforcement_count, user_id,
                1 - (embedding <=> CAST(:embedding AS vector)) as vector_score
            FROM memories
            WHERE embedding IS NOT NULL{type_filter}
            ORDER BY embedding <=> CAST(:embedding AS vector)
            LIMIT :limit
        """)

        vector_result = await session.execute(
            vector_query,
            {"embedding": embedding_str, "limit": limit * 2, **type_params}
        )
        vector_rows = {row.id: row for row in vector_result.fetchall()}

        # Then do keyword search
        keyword_query = text(f"""
            SELECT
                id,
                ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', :query)) as keyword_score
            FROM memories
            WHERE to_tsvector('english', content) @@ plainto_tsquery('english', :query){type_filter}
            ORDER BY keyword_score DESC
            LIMIT :limit
        """)

        keyword_result = await session.execute(
            keyword_query,
            {"query": query, "limit": limit * 2, **type_params}
        )
        keyword_scores = {row.id: row.keyword_score for row in keyword_result.fetchall()}

        # Combine scores
        memories = []
        for id, row in vector_rows.items():
            vector_score = row.vector_score or 0
            keyword_score = keyword_scores.get(id, 0)
            base_score = (
                vector_weight * vector_score +
                keyword_weight * keyword_score
            ) * (1 + (row.importance or 0.5) * 0.2)

            tn = getattr(row, 'temporal_nature', 'timeless') or 'timeless'
            temporal_boost = compute_temporal_boost(
                tn,
                row.last_accessed,
                row.access_count or 0,
            )
            combined_score = base_score * temporal_boost

            # Tier boost
            tier_val = getattr(row, 'tier', 'observation') or 'observation'
            tier_multiplier = {"observation": 1.0, "belief": 1.1, "knowledge": 1.2}.get(tier_val, 1.0)
            combined_score *= tier_multiplier

            # Context-observation penalty: stale context observations decay faster
            if (row.memory_type == "context" and tier_val == "observation" and temporal_boost < 0.85):
                combined_score *= 0.8

            memory = Memory(
                id=row.id,
                content=row.content,
                memory_type=row.memory_type,
                importance=row.importance or 0.5,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=getattr(row, 'updated_at', None),
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                temporal_nature=tn,
                tier=tier_val,
                reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                user_id=getattr(row, 'user_id', None),
                score=combined_score
            )
            memories.append(memory)

            # Update access tracking (skip for reflection/deep retrieval queries)
            if update_access:
                await session.execute(
                    update(MemoryModel)
                    .where(MemoryModel.id == row.id)
                    .values(
                        access_count=MemoryModel.access_count + 1,
                        last_accessed=func.now()
                    )
                )

        if update_access:
            await session.commit()

        # Spreading activation: boost memories connected to other results
        try:
            from services.consolidation_service import get_connected_memory_ids
            result_ids = {m.id for m in memories}
            for memory in memories:
                connected = await get_connected_memory_ids(memory.id)
                for conn_id, strength in connected:
                    if conn_id in result_ids:
                        memory.score += 0.1 * strength
        except Exception:
            pass  # Consolidation service may not exist yet

        # Flag annotation: append unresolved flags to content
        try:
            from services.consolidation_service import get_memory_flags
            for memory in memories:
                flags = await get_memory_flags(memory.id)
                for flag in flags:
                    memory.content += f" [FLAG: {flag['description']}]"
        except Exception:
            pass  # Consolidation service may not exist yet

        # Sort by combined score and return top results
        memories.sort(key=lambda m: m.score, reverse=True)
        if min_score is not None:
            memories = [m for m in memories if m.score >= min_score]
        return memories[:limit]


async def store_memory(memory: Memory) -> Memory:
    """Store a single memory in the database."""
    async with async_session() as session:
        memory_id = memory.id or str(uuid.uuid4())
        embedding = get_embedding(memory.content)

        db_memory = MemoryModel(
            id=memory_id,
            content=memory.content,
            embedding=embedding,
            memory_type=memory.memory_type,
            importance=memory.importance,
            temporal_nature=memory.temporal_nature,
            tier=memory.tier,
            reinforcement_count=memory.reinforcement_count,
            source_conversation_id=memory.source_conversation_id
        )

        session.add(db_memory)
        await session.commit()
        await session.refresh(db_memory)

        memory.id = db_memory.id
        memory.created_at = db_memory.created_at
        return memory


async def store_memories(memories: List[Memory]) -> List[Memory]:
    """Store multiple memories in the database."""
    stored = []
    for memory in memories:
        stored.append(await store_memory(memory))
    return stored


MERGE_INSTRUCTION = "Merge these two related memories into one clear, concise memory. Return ONLY the merged text, nothing else."


async def _llm_merge_content(content_a: str, content_b: str, model: str = "claude-haiku-4-5-20251001") -> str:
    """Use Haiku to merge two memory contents into one clean version."""
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=256)
    response = await llm.ainvoke([
        SystemMessage(
            content=MERGE_INSTRUCTION,
            additional_kwargs={"cache_control": {"type": "ephemeral"}},
        ),
        HumanMessage(content=f"Memory A: {content_a}\nMemory B: {content_b}"),
    ])
    text = response.content
    if isinstance(text, list):
        text = " ".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in text)
    return text.strip()


async def store_memories_with_conflict_detection(
    memories: List[Memory],
    similarity_threshold: float = 0.75
) -> List[Memory]:
    """
    Store memories with semantic conflict detection and reinforcement.

    For each memory:
    - Similarity >= 0.92: Pure reinforcement (increment count, recompute tier, no LLM)
    - Similarity 0.75-0.92: LLM conflict resolution (UPDATE/MERGE/KEEP_BOTH/DISCARD)
    - No match: Store as new observation

    Args:
        memories: List of memories to store
        similarity_threshold: Minimum similarity to trigger conflict check

    Returns:
        List of stored/updated Memory objects
    """
    stored = []

    for memory in memories:
        try:
            # Find similar existing memories across full database
            similar = await find_similar_memories(
                memory.content,
                threshold=similarity_threshold,
                limit=5
            )

            if not similar:
                # No conflicts, store as new observation
                memory.tier = "observation"
                memory.reinforcement_count = 0
                stored.append(await store_memory(memory))
                print(f"Memory stored (new observation): {memory.content[:50]}...")
                continue

            # Handle the most similar memory
            existing, similarity = similar[0]
            print(f"Found similar memory (score={similarity:.2f}): {existing.content[:50]}...")

            if similarity >= 0.92:
                # Pure reinforcement — no LLM needed
                new_count = (existing.reinforcement_count or 0) + 1
                new_tier = _compute_tier(new_count)
                updated = await update_memory(
                    existing.id,
                    reinforcement_count=new_count,
                    tier=new_tier,
                    importance=max(existing.importance, memory.importance)
                )
                if updated:
                    stored.append(updated)
                    print(f"Memory reinforced (count={new_count}, tier={new_tier}): {updated.content[:50]}...")
            else:
                # Similarity 0.75-0.92 — use LLM conflict resolution
                resolution = await resolve_memory_conflict(existing, memory, similarity)
                print(f"Conflict resolution: {resolution.action} - {resolution.reason}")

                if resolution.action == "UPDATE":
                    new_count = (existing.reinforcement_count or 0) + 1
                    updated = await update_memory(
                        existing.id,
                        content=memory.content,
                        memory_type=memory.memory_type,
                        importance=max(existing.importance, memory.importance),
                        reinforcement_count=new_count,
                        tier=_compute_tier(new_count),
                    )
                    if updated:
                        stored.append(updated)
                        print(f"Memory updated (reinforced): {updated.content[:50]}...")

                elif resolution.action == "MERGE":
                    merged_content = await _llm_merge_content(existing.content, memory.content)
                    new_count = (existing.reinforcement_count or 0) + 1
                    updated = await update_memory(
                        existing.id,
                        content=merged_content,
                        importance=max(existing.importance, memory.importance),
                        reinforcement_count=new_count,
                        tier=_compute_tier(new_count),
                    )
                    if updated:
                        stored.append(updated)
                        print(f"Memory merged (reinforced): {updated.content[:50]}...")

                elif resolution.action == "KEEP_BOTH":
                    memory.tier = "observation"
                    memory.reinforcement_count = 0
                    stored.append(await store_memory(memory))
                    print(f"Memory stored (keeping both): {memory.content[:50]}...")

                elif resolution.action == "DISCARD":
                    print(f"Memory discarded: {memory.content[:50]}...")

        except Exception as e:
            print(f"Error processing memory with conflict detection: {e}")
            # Fallback: store without conflict detection
            try:
                memory.tier = "observation"
                memory.reinforcement_count = 0
                stored.append(await store_memory(memory))
            except Exception as e2:
                print(f"Fallback storage also failed: {e2}")

    return stored


MEMORY_EXTRACTION_INSTRUCTIONS = """Analyze the following conversation and extract important information that should be remembered for future conversations.

Focus on:
1. **Personal facts** - Name, job, location, family, hobbies, etc.
2. **Preferences** - Likes, dislikes, communication style preferences
3. **Context** - Ongoing projects, situations, goals, challenges
4. **Instructions** - How the user wants the assistant to behave, format responses, etc.

For each piece of information, assess its importance (0-1 scale):
- 1.0: Critical identity info or explicit instructions
- 0.7-0.9: Important preferences or ongoing context
- 0.4-0.6: Useful background information
- 0.1-0.3: Minor details that might be relevant

Also classify each memory's temporal nature:
- "timeless": Permanent facts unlikely to change (identity, allergies, family relationships, fixed preferences)
- "temporary": Time-bound context that expires (current projects, upcoming events, temporary situations)
- "evolving": Things that change over time and may be superseded (favorite restaurant, current job, tools in use)

Return ONLY a valid JSON array. If nothing worth remembering, return [].

Example output:
[{{"content": "Chee Loon Chen (CL) is a close friend who lives in Singapore, uses WhatsApp (+6598587940) since he's international", "memory_type": "fact", "importance": 0.8, "temporal_nature": "timeless"}},
{{"content": "Ben is training for the Austin marathon in March", "memory_type": "context", "importance": 0.7, "temporal_nature": "temporary"}},
{{"content": "User's name is Ben", "memory_type": "fact", "importance": 0.9, "temporal_nature": "timeless"}}]

IMPORTANT:
- Return ONLY the JSON array, no other text
- Make each memory self-contained and understandable without context
- Every memory about a person MUST include their name. Instead of "Lives in Singapore", write "Chee Loon lives in Singapore". Instead of "Communication method is WhatsApp", write "Chee Loon uses WhatsApp".
- Group closely related facts about the same person into a single memory when possible. Prefer fewer, richer memories over many fragmented ones.
- Don't store trivial greetings or small talk
- Don't duplicate information already known
- Be concise but complete"""


async def extract_and_store_memories(
    messages: List[dict],
    conversation_id: str,
    model: str = "claude-haiku-4-5-20251001",
    existing_memories: List[Memory] = None
) -> List[Memory]:
    """
    Extract memorable information from conversation and store it.

    Uses a fast model (Haiku) to analyze the conversation and identify
    information worth remembering.

    Args:
        messages: List of message dicts with 'role' and 'content'
        conversation_id: ID of the source conversation
        model: Model to use for extraction (default: haiku for speed/cost)
        existing_memories: Previously retrieved memories to avoid duplicates

    Returns:
        List of newly stored Memory objects
    """
    if not messages:
        print("Memory extraction: no messages, skipping")
        return []

    print(f"Memory extraction: processing {len(messages)} messages")

    # Format conversation for analysis
    conversation_text = "\n".join([
        f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}"
        for msg in messages[-10:]  # Last 10 messages for context
    ])

    print(f"Memory extraction: formatted conversation ({len(conversation_text)} chars)")

    # Format existing memories
    existing_text = "None"
    if existing_memories:
        existing_text = "\n".join([
            f"- [{m.memory_type}] {m.content}"
            for m in existing_memories
        ])

    print(f"Memory extraction: existing memories text: {existing_text[:100]}")

    # Call LLM to extract memories
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=2048)

    dynamic_data = f"Conversation:\n{conversation_text}\n\nExisting memories (don't duplicate these):\n{existing_text}"

    print(f"Memory extraction: prompt ready ({len(dynamic_data)} chars)")

    try:
        print(f"Memory extraction: calling LLM with {len(messages)} messages")
        response = await llm.ainvoke([
            SystemMessage(
                content=MEMORY_EXTRACTION_INSTRUCTIONS,
                additional_kwargs={"cache_control": {"type": "ephemeral"}},
            ),
            HumanMessage(content=dynamic_data),
        ])
        print(f"Memory extraction: got LLM response")

        # Handle both string and list content (Claude sometimes returns list of content blocks)
        if isinstance(response.content, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response.content
            )
        else:
            response_text = response.content

        response_text = response_text.strip()
        print(f"Memory extraction raw response: {response_text[:500]}")

        # Extract JSON from response - handle various formats
        # Remove markdown code blocks if present
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            parts = response_text.split("```")
            if len(parts) >= 2:
                response_text = parts[1].strip()

        # Try to find JSON array in the response
        json_match = re.search(r'\[[\s\S]*?\]', response_text)
        if json_match:
            response_text = json_match.group()

        print(f"Memory extraction parsed JSON: {response_text[:300]}")

        extracted = json.loads(response_text)

        if not isinstance(extracted, list):
            print(f"Memory extraction: expected list, got {type(extracted)}")
            return []

        # Convert to Memory objects and store
        memories_to_store = []
        for item in extracted:
            if not isinstance(item, dict):
                continue
            if not item.get("content") or not item.get("memory_type"):
                continue

            # Validate memory_type
            valid_types = ["fact", "preference", "context", "instruction"]
            memory_type = item["memory_type"]
            if memory_type not in valid_types:
                memory_type = "context"  # Default to context

            # Validate temporal_nature
            valid_natures = ["timeless", "temporary", "evolving"]
            temporal_nature = item.get("temporal_nature", "timeless")
            if temporal_nature not in valid_natures:
                temporal_nature = "timeless"

            memory = Memory(
                id=None,
                content=item["content"],
                memory_type=memory_type,
                importance=float(item.get("importance", 0.5)),
                temporal_nature=temporal_nature,
                source_conversation_id=conversation_id
            )
            memories_to_store.append(memory)

        print(f"Memory extraction: found {len(memories_to_store)} memories to store")

        if memories_to_store:
            stored = await store_memories_with_conflict_detection(memories_to_store)
            print(f"Memory extraction: stored {len(stored)} memories")
            return stored

        return []

    except json.JSONDecodeError as e:
        print(f"Memory extraction JSON parse error: {e}")
        print(f"Attempted to parse: {response_text[:200] if 'response_text' in locals() else 'N/A'}")
        return []
    except Exception as e:
        import traceback
        print(f"Memory extraction failed: {type(e).__name__}: {e}")
        print(f"Traceback: {traceback.format_exc()}")
        return []


async def get_memory_stats() -> dict:
    """Get statistics about stored memories."""
    async with async_session() as session:
        # Total count
        total_result = await session.execute(
            select(func.count(MemoryModel.id))
        )
        total = total_result.scalar() or 0

        # Count by type
        type_result = await session.execute(
            select(
                MemoryModel.memory_type,
                func.count(MemoryModel.id)
            ).group_by(MemoryModel.memory_type)
        )
        by_type = {row[0]: row[1] for row in type_result.fetchall()}

        # Count by tier
        tier_result = await session.execute(
            select(
                MemoryModel.tier,
                func.count(MemoryModel.id)
            ).group_by(MemoryModel.tier)
        )
        by_tier = {(row[0] or "observation"): row[1] for row in tier_result.fetchall()}

        # Average importance
        avg_result = await session.execute(
            select(func.avg(MemoryModel.importance))
        )
        avg_importance = float(avg_result.scalar() or 0)

        return {
            "total": total,
            "by_type": by_type,
            "by_tier": by_tier,
            "average_importance": round(avg_importance, 2)
        }


async def get_all_memories(limit: int = 100, offset: int = 0) -> List[Memory]:
    """Get all memories with pagination."""
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel)
            .order_by(MemoryModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        rows = result.scalars().all()
        return [
            Memory(
                id=row.id,
                content=row.content,
                memory_type=row.memory_type,
                importance=row.importance or 0.5,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                temporal_nature=row.temporal_nature or "timeless",
                tier=getattr(row, 'tier', 'observation') or 'observation',
                reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                user_id=row.user_id
            )
            for row in rows
        ]


async def get_memory_by_id(memory_id: str) -> Optional[Memory]:
    """Get a single memory by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel).where(MemoryModel.id == memory_id)
        )
        row = result.scalar_one_or_none()
        if not row:
            return None
        return Memory(
            id=row.id,
            content=row.content,
            memory_type=row.memory_type,
            importance=row.importance or 0.5,
            source_conversation_id=row.source_conversation_id,
            created_at=row.created_at,
            updated_at=row.updated_at,
            last_accessed=row.last_accessed,
            access_count=row.access_count or 0,
            temporal_nature=row.temporal_nature or "timeless",
            tier=getattr(row, 'tier', 'observation') or 'observation',
            reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
            user_id=row.user_id
        )


async def delete_memory(memory_id: str) -> bool:
    """
    Delete a memory by ID.

    Args:
        memory_id: The ID of the memory to delete

    Returns:
        True if deleted, False if not found
    """
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel).where(MemoryModel.id == memory_id)
        )
        memory = result.scalar_one_or_none()
        if not memory:
            return False

        await session.delete(memory)
        await session.commit()
        return True


async def update_memory(
    memory_id: str,
    content: Optional[str] = None,
    memory_type: Optional[str] = None,
    importance: Optional[float] = None,
    temporal_nature: Optional[str] = None,
    tier: Optional[str] = None,
    reinforcement_count: Optional[int] = None,
) -> Optional[Memory]:
    """
    Update an existing memory. Re-generates embedding if content changes.

    Args:
        memory_id: ID of the memory to update
        content: New content (optional)
        memory_type: New type (optional)
        importance: New importance score (optional)
        temporal_nature: New temporal nature (optional)

    Returns:
        Updated Memory object or None if not found
    """
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel).where(MemoryModel.id == memory_id)
        )
        memory = result.scalar_one_or_none()
        if not memory:
            return None

        # Update fields if provided
        if content is not None and content != memory.content:
            memory.content = content
            # Re-generate embedding for new content
            memory.embedding = get_embedding(content)

        if memory_type is not None:
            valid_types = ["fact", "preference", "context", "instruction"]
            if memory_type in valid_types:
                memory.memory_type = memory_type

        if importance is not None:
            memory.importance = max(0.0, min(1.0, importance))

        if temporal_nature is not None:
            valid_natures = ["timeless", "temporary", "evolving"]
            if temporal_nature in valid_natures:
                memory.temporal_nature = temporal_nature

        if tier is not None:
            valid_tiers = ["observation", "belief", "knowledge"]
            if tier in valid_tiers:
                memory.tier = tier

        if reinforcement_count is not None:
            memory.reinforcement_count = reinforcement_count

        await session.commit()
        await session.refresh(memory)

        return Memory(
            id=memory.id,
            content=memory.content,
            memory_type=memory.memory_type,
            importance=memory.importance or 0.5,
            source_conversation_id=memory.source_conversation_id,
            created_at=memory.created_at,
            updated_at=memory.updated_at,
            last_accessed=memory.last_accessed,
            access_count=memory.access_count or 0,
            temporal_nature=memory.temporal_nature or "timeless",
            tier=getattr(memory, 'tier', 'observation') or 'observation',
            reinforcement_count=getattr(memory, 'reinforcement_count', 0) or 0,
            user_id=memory.user_id
        )


async def search_memories(
    query: Optional[str] = None,
    memory_type: Optional[str] = None,
    min_importance: Optional[float] = None,
    temporal_nature: Optional[str] = None,
    tier: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    vector_weight: float = 0.7,
    keyword_weight: float = 0.3
) -> tuple[List[Memory], int]:
    """
    Search and filter memories with pagination using hybrid search.

    Combines:
    - Vector similarity search (cosine distance)
    - BM25 keyword search (full-text)

    Args:
        query: Text search query (optional)
        memory_type: Filter by type (optional)
        min_importance: Minimum importance threshold (optional)
        temporal_nature: Filter by temporal nature (optional)
        limit: Maximum results to return
        offset: Number of results to skip
        vector_weight: Weight for vector similarity (default 0.7)
        keyword_weight: Weight for keyword matching (default 0.3)

    Returns:
        Tuple of (list of memories, total count matching filters)
    """
    async with async_session() as session:
        # Build base query for non-search case
        base_query = select(MemoryModel)
        count_query = select(func.count(MemoryModel.id))

        # Apply filters
        if memory_type:
            base_query = base_query.where(MemoryModel.memory_type == memory_type)
            count_query = count_query.where(MemoryModel.memory_type == memory_type)

        if min_importance is not None:
            base_query = base_query.where(MemoryModel.importance >= min_importance)
            count_query = count_query.where(MemoryModel.importance >= min_importance)

        if temporal_nature:
            base_query = base_query.where(MemoryModel.temporal_nature == temporal_nature)
            count_query = count_query.where(MemoryModel.temporal_nature == temporal_nature)

        if tier:
            base_query = base_query.where(MemoryModel.tier == tier)
            count_query = count_query.where(MemoryModel.tier == tier)

        # If query provided, use hybrid search (vector + keyword)
        if query:
            query_embedding = get_embedding(query)
            embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

            # Build filter conditions for SQL
            filter_conditions = "embedding IS NOT NULL"
            params = {"embedding": embedding_str, "query": query}

            if memory_type:
                filter_conditions += " AND memory_type = :memory_type"
                params["memory_type"] = memory_type

            if min_importance is not None:
                filter_conditions += " AND importance >= :min_importance"
                params["min_importance"] = min_importance

            if temporal_nature:
                filter_conditions += " AND temporal_nature = :temporal_nature"
                params["temporal_nature"] = temporal_nature

            if tier:
                filter_conditions += " AND tier = :tier"
                params["tier"] = tier

            # Vector search - get more results than needed for combining
            fetch_limit = (limit + offset) * 2
            params["fetch_limit"] = fetch_limit

            vector_sql = text(f"""
                SELECT
                    id, content, memory_type, importance,
                    source_conversation_id, created_at, updated_at, last_accessed, access_count,
                    temporal_nature, tier, reinforcement_count, user_id,
                    1 - (embedding <=> CAST(:embedding AS vector)) as vector_score
                FROM memories
                WHERE {filter_conditions}
                ORDER BY embedding <=> CAST(:embedding AS vector)
                LIMIT :fetch_limit
            """)

            vector_result = await session.execute(vector_sql, params)
            vector_rows = {row.id: row for row in vector_result.fetchall()}

            # Keyword search (BM25)
            keyword_sql = text(f"""
                SELECT
                    id,
                    ts_rank_cd(to_tsvector('english', content), plainto_tsquery('english', :query)) as keyword_score
                FROM memories
                WHERE {filter_conditions}
                  AND to_tsvector('english', content) @@ plainto_tsquery('english', :query)
                ORDER BY keyword_score DESC
                LIMIT :fetch_limit
            """)

            keyword_result = await session.execute(keyword_sql, params)
            keyword_scores = {row.id: row.keyword_score for row in keyword_result.fetchall()}

            # Combine scores from both searches
            all_ids = set(vector_rows.keys()) | set(keyword_scores.keys())
            scored_memories = []

            for memory_id in all_ids:
                if memory_id in vector_rows:
                    row = vector_rows[memory_id]
                    vector_score = row.vector_score or 0
                    keyword_score = keyword_scores.get(memory_id, 0)

                    # Combined score with importance multiplier
                    combined_score = (
                        vector_weight * vector_score +
                        keyword_weight * keyword_score
                    ) * (1 + (row.importance or 0.5) * 0.2)

                    scored_memories.append(Memory(
                        id=row.id,
                        content=row.content,
                        memory_type=row.memory_type,
                        importance=row.importance or 0.5,
                        source_conversation_id=row.source_conversation_id,
                        created_at=row.created_at,
                        updated_at=row.updated_at,
                        last_accessed=row.last_accessed,
                        access_count=row.access_count or 0,
                        temporal_nature=getattr(row, 'temporal_nature', 'timeless') or 'timeless',
                        tier=getattr(row, 'tier', 'observation') or 'observation',
                        reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                        user_id=row.user_id,
                        score=combined_score
                    ))

            # Sort by combined score and apply pagination
            scored_memories.sort(key=lambda m: m.score, reverse=True)
            total = len(scored_memories)
            paginated = scored_memories[offset:offset + limit]

            return paginated, total

        # No query - just filter and paginate
        base_query = base_query.order_by(MemoryModel.created_at.desc()).limit(limit).offset(offset)

        result = await session.execute(base_query)
        rows = result.scalars().all()

        count_result = await session.execute(count_query)
        total = count_result.scalar() or 0

        return [
            Memory(
                id=row.id,
                content=row.content,
                memory_type=row.memory_type,
                importance=row.importance or 0.5,
                source_conversation_id=row.source_conversation_id,
                created_at=row.created_at,
                updated_at=row.updated_at,
                last_accessed=row.last_accessed,
                access_count=row.access_count or 0,
                temporal_nature=row.temporal_nature or "timeless",
                tier=getattr(row, 'tier', 'observation') or 'observation',
                reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                user_id=row.user_id
            )
            for row in rows
        ], total


# ===== SEMANTIC CONFLICT DETECTION =====

async def find_similar_memories(content: str, threshold: float = 0.85, limit: int = 5) -> List[tuple[Memory, float]]:
    """
    Find memories semantically similar to the given content.

    Args:
        content: Content to find similar memories for
        threshold: Minimum similarity score (0-1, default 0.85)
        limit: Maximum number of similar memories to return

    Returns:
        List of (Memory, similarity_score) tuples
    """
    query_embedding = get_embedding(content)
    embedding_str = "[" + ",".join(str(x) for x in query_embedding) + "]"

    async with async_session() as session:
        sql = text("""
            SELECT
                id, content, memory_type, importance,
                source_conversation_id, created_at, updated_at, last_accessed, access_count,
                temporal_nature, tier, reinforcement_count, user_id,
                1 - (embedding <=> CAST(:embedding AS vector)) as similarity
            FROM memories
            WHERE embedding IS NOT NULL
              AND 1 - (embedding <=> CAST(:embedding AS vector)) >= :threshold
            ORDER BY similarity DESC
            LIMIT :limit
        """)

        result = await session.execute(sql, {
            "embedding": embedding_str,
            "threshold": threshold,
            "limit": limit
        })

        rows = result.fetchall()
        return [
            (
                Memory(
                    id=row.id,
                    content=row.content,
                    memory_type=row.memory_type,
                    importance=row.importance or 0.5,
                    source_conversation_id=row.source_conversation_id,
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                    last_accessed=row.last_accessed,
                    access_count=row.access_count or 0,
                    temporal_nature=getattr(row, 'temporal_nature', 'timeless') or 'timeless',
                    tier=getattr(row, 'tier', 'observation') or 'observation',
                    reinforcement_count=getattr(row, 'reinforcement_count', 0) or 0,
                    user_id=row.user_id,
                    score=row.similarity
                ),
                row.similarity
            )
            for row in rows
        ]


CONFLICT_RESOLUTION_INSTRUCTIONS = """You are analyzing two memories to determine how to handle a potential conflict or update.

Determine the best action:
- UPDATE: The new memory is an update/correction to the existing one (replace old with new)
- MERGE: Both contain complementary information (combine into one comprehensive memory)
- KEEP_BOTH: They are related but distinct facts that should both be kept
- DISCARD: The new memory is redundant or less accurate (keep existing, discard new)

If MERGE, provide the merged content.

Return ONLY a valid JSON object with this structure:
{{"action": "UPDATE|MERGE|KEEP_BOTH|DISCARD", "merged_content": "...", "reason": "brief explanation"}}

The merged_content field is only required when action is MERGE."""


@dataclass
class ConflictResolution:
    """Result of conflict resolution between memories."""
    action: str  # UPDATE, MERGE, KEEP_BOTH, DISCARD
    merged_content: Optional[str] = None
    reason: str = ""


async def resolve_memory_conflict(
    existing: Memory,
    new: Memory,
    similarity: float,
    model: str = "claude-haiku-4-5-20251001"
) -> ConflictResolution:
    """
    Use LLM to decide how to handle conflicting/similar memories.

    Args:
        existing: The existing memory in database
        new: The new memory to be stored
        similarity: Similarity score between them
        model: Model to use for resolution

    Returns:
        ConflictResolution with action and optional merged content
    """
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=512)

    dynamic_data = f"""EXISTING MEMORY:
- Content: {existing.content}
- Type: {existing.memory_type}
- Importance: {existing.importance}

NEW MEMORY:
- Content: {new.content}
- Type: {new.memory_type}
- Importance: {new.importance}

Similarity score: {similarity:.2f}"""

    try:
        response = await llm.ainvoke([
            SystemMessage(
                content=CONFLICT_RESOLUTION_INSTRUCTIONS,
                additional_kwargs={"cache_control": {"type": "ephemeral"}},
            ),
            HumanMessage(content=dynamic_data),
        ])

        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        # Extract JSON
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            parts = response_text.split("```")
            if len(parts) >= 2:
                response_text = parts[1].strip()

        json_match = re.search(r'\{[\s\S]*?\}', response_text)
        if json_match:
            response_text = json_match.group()

        result = json.loads(response_text)

        action = result.get("action", "KEEP_BOTH").upper()
        if action not in ["UPDATE", "MERGE", "KEEP_BOTH", "DISCARD"]:
            action = "KEEP_BOTH"

        return ConflictResolution(
            action=action,
            merged_content=result.get("merged_content"),
            reason=result.get("reason", "")
        )

    except Exception as e:
        print(f"Conflict resolution failed: {e}")
        # Default to keeping both on error
        return ConflictResolution(action="KEEP_BOTH", reason=f"Resolution error: {e}")


# ===== TEMPORAL NATURE BACKFILL =====

BACKFILL_INSTRUCTIONS = """Classify each memory's temporal nature. Ben is the user.

- "timeless": Permanent facts unlikely to change (identity, allergies, family relationships, fixed preferences)
- "temporary": Time-bound context that expires (current projects, upcoming events, temporary situations)
- "evolving": Things that change over time and may be superseded (favorite restaurant, current job, tools in use)

Return ONLY a valid JSON array of {{"id": "...", "temporal_nature": "..."}} for each memory."""


async def backfill_temporal_nature(
    model: str = "claude-haiku-4-5-20251001",
    batch_size: int = 20,
) -> dict:
    """
    Classify all existing memories that still have the default 'timeless' temporal_nature.

    Sends memories to Haiku in batches for classification and updates the DB.

    Returns:
        Summary dict with counts of each classification.
    """
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=2048)
    summary = {"total": 0, "timeless": 0, "temporary": 0, "evolving": 0, "errors": 0}

    async with async_session() as session:
        # Fetch all memories with default temporal_nature
        result = await session.execute(
            select(MemoryModel)
            .where(MemoryModel.temporal_nature == "timeless")
            .order_by(MemoryModel.created_at.asc())
        )
        all_memories = result.scalars().all()
        summary["total"] = len(all_memories)

        if not all_memories:
            return summary

        # Process in batches
        for i in range(0, len(all_memories), batch_size):
            batch = all_memories[i:i + batch_size]
            memories_for_prompt = [
                {"id": m.id, "content": m.content, "memory_type": m.memory_type}
                for m in batch
            ]

            try:
                response = await llm.ainvoke([
                    SystemMessage(
                        content=BACKFILL_INSTRUCTIONS,
                        additional_kwargs={"cache_control": {"type": "ephemeral"}},
                    ),
                    HumanMessage(content=f"Memories:\n{json.dumps(memories_for_prompt, indent=2)}"),
                ])
                response_text = response.content
                if isinstance(response_text, list):
                    response_text = " ".join(
                        block.get("text", "") if isinstance(block, dict) else str(block)
                        for block in response_text
                    )
                response_text = response_text.strip()

                # Extract JSON
                if "```json" in response_text:
                    response_text = response_text.split("```json")[1].split("```")[0].strip()
                elif "```" in response_text:
                    parts = response_text.split("```")
                    if len(parts) >= 2:
                        response_text = parts[1].strip()

                json_match = re.search(r'\[[\s\S]*?\]', response_text)
                if json_match:
                    response_text = json_match.group()

                classifications = json.loads(response_text)

                valid_natures = {"timeless", "temporary", "evolving"}
                for item in classifications:
                    mem_id = item.get("id")
                    nature = item.get("temporal_nature", "timeless")
                    if nature not in valid_natures:
                        nature = "timeless"

                    await session.execute(
                        update(MemoryModel)
                        .where(MemoryModel.id == mem_id)
                        .values(temporal_nature=nature)
                    )
                    summary[nature] = summary.get(nature, 0) + 1
                    print(f"Backfill: {mem_id[:8]}... -> {nature}")

                await session.commit()

            except Exception as e:
                print(f"Backfill batch error: {e}")
                summary["errors"] += 1

    return summary


# ===== TIER MIGRATION (ONE-TIME DEDUP + REINFORCE) =====

DEDUP_CLASSIFY_INSTRUCTIONS = """Review these groups of similar memories. For each group, decide the action:

- MERGE: These are duplicates or near-duplicates. Provide a single clean merged version.
- KEEP_ALL: These are genuinely distinct facts that should remain separate.

For MERGE groups, also classify the merged memory's temporal_nature:
- "timeless": Permanent facts (identity, relationships, fixed preferences)
- "temporary": Time-bound context (current projects, upcoming events)
- "evolving": Changes over time (current job, favorite restaurant)

Return ONLY valid JSON:
{
  "groups": [
    {
      "action": "MERGE",
      "keep_id": "id of the best/most complete memory to keep",
      "absorb_ids": ["ids to delete after merging"],
      "merged_content": "single clean merged text",
      "temporal_nature": "timeless|temporary|evolving"
    },
    {
      "action": "KEEP_ALL",
      "memory_ids": ["id1", "id2"]
    }
  ]
}"""


async def backfill_memory_tiers(
    similarity_threshold: float = 0.78,
    batch_size: int = 50,
    model: str = "claude-haiku-4-5-20251001",
    dry_run: bool = False,
) -> dict:
    """
    One-time migration: deduplicate all memories and set proper tiers.

    Scans the entire memory store for near-duplicates, merges them via Haiku,
    sums reinforcement counts, and computes tiers.

    Args:
        similarity_threshold: Minimum similarity to consider as potential duplicate
        batch_size: Number of memories to process per iteration
        model: LLM model for merge decisions
        dry_run: If True, report what would happen without making changes

    Returns:
        Summary dict with merge/promotion stats
    """

    summary = {
        "total_memories": 0,
        "duplicates_found": 0,
        "merges_performed": 0,
        "memories_deleted": 0,
        "promotions": 0,
        "haiku_calls": 0,
        "errors": 0,
        "dry_run": dry_run,
    }

    # Step 1: Load all memory IDs and embeddings
    async with async_session() as session:
        result = await session.execute(
            select(MemoryModel)
            .where(MemoryModel.embedding.isnot(None))
            .order_by(MemoryModel.created_at.asc())
        )
        all_memories = list(result.scalars().all())

    summary["total_memories"] = len(all_memories)
    if len(all_memories) < 2:
        print(f"[Tier Migration] Only {len(all_memories)} memories, nothing to dedup")
        return summary

    print(f"[Tier Migration] Scanning {len(all_memories)} memories for duplicates...")

    # Step 2: For each memory, find similar ones via embedding search
    # Track which memories have been absorbed so we skip them
    absorbed = set()
    merge_groups = []  # List of group dicts for LLM review

    for i, mem in enumerate(all_memories):
        if mem.id in absorbed:
            continue

        # Find similar memories using SQL vector search
        similar = await find_similar_memories(
            mem.content,
            threshold=similarity_threshold,
            limit=10,
        )

        # Filter out self and already-absorbed
        candidates = [
            (s_mem, sim) for s_mem, sim in similar
            if s_mem.id != mem.id and s_mem.id not in absorbed
        ]

        if not candidates:
            continue

        # Pure reinforcement for very high similarity (>= 0.92)
        pure_reinforce = [(s, sim) for s, sim in candidates if sim >= 0.92]
        llm_review = [(s, sim) for s, sim in candidates if sim < 0.92]

        # Handle pure reinforcement without LLM
        for s_mem, sim in pure_reinforce:
            summary["duplicates_found"] += 1
            if not dry_run:
                new_count = (mem.reinforcement_count or 0) + (s_mem.reinforcement_count or 0) + 1
                await update_memory(
                    mem.id,
                    reinforcement_count=new_count,
                    tier=_compute_tier(new_count),
                    importance=max(mem.importance or 0.5, s_mem.importance or 0.5),
                )
                await delete_memory(s_mem.id)
                absorbed.add(s_mem.id)
                summary["merges_performed"] += 1
                summary["memories_deleted"] += 1
                print(f"[Tier Migration] Pure reinforce: {s_mem.id[:8]}... -> {mem.id[:8]}... (sim={sim:.2f})")
            else:
                absorbed.add(s_mem.id)
                print(f"[Tier Migration] [DRY RUN] Would reinforce: {s_mem.content[:50]}... into {mem.content[:50]}... (sim={sim:.2f})")

        # Collect LLM-review candidates into groups for batch processing
        if llm_review:
            group_contents = [{"id": mem.id, "content": mem.content}]
            for s_mem, sim in llm_review:
                group_contents.append({"id": s_mem.id, "content": s_mem.content, "similarity": round(sim, 2)})
            merge_groups.append(group_contents)

        # Process LLM groups in batches
        if len(merge_groups) >= batch_size or (i == len(all_memories) - 1 and merge_groups):
            await _process_merge_groups(merge_groups, summary, model, dry_run, absorbed)
            merge_groups = []

    # Process any remaining groups
    if merge_groups:
        await _process_merge_groups(merge_groups, summary, model, dry_run, absorbed)

    # Step 3: Auto-promote all memories based on final reinforcement counts
    if not dry_run:
        async with async_session() as session:
            result = await session.execute(select(MemoryModel))
            remaining = list(result.scalars().all())

        for m in remaining:
            expected = _compute_tier(m.reinforcement_count or 0)
            current = m.tier or "observation"
            if current != expected:
                await update_memory(m.id, tier=expected)
                summary["promotions"] += 1
                print(f"[Tier Migration] Promoted: {m.id[:8]}... {current} -> {expected}")

    print(f"[Tier Migration] Complete: {summary}")
    return summary


async def _process_merge_groups(
    groups: list,
    summary: dict,
    model: str,
    dry_run: bool,
    absorbed: set,
) -> None:
    """Process a batch of merge groups via Haiku."""
    llm = ChatAnthropic(model=model, temperature=0, max_tokens=4096)

    try:
        response = await llm.ainvoke([
            SystemMessage(
                content=DEDUP_CLASSIFY_INSTRUCTIONS,
                additional_kwargs={"cache_control": {"type": "ephemeral"}},
            ),
            HumanMessage(content=f"Memory groups:\n{json.dumps(groups, indent=2)}"),
        ])
        summary["haiku_calls"] += 1

        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        # Extract JSON
        if "```json" in response_text:
            response_text = response_text.split("```json")[1].split("```")[0].strip()
        elif "```" in response_text:
            parts = response_text.split("```")
            if len(parts) >= 2:
                response_text = parts[1].strip()

        json_match = re.search(r'\{[\s\S]*\}', response_text)
        if json_match:
            response_text = json_match.group()

        result_data = json.loads(response_text)
        decisions = result_data.get("groups", [])

        for decision in decisions:
            action = decision.get("action", "KEEP_ALL")

            if action == "MERGE":
                keep_id = decision.get("keep_id")
                absorb_ids = decision.get("absorb_ids", [])
                merged_content = decision.get("merged_content")

                if not keep_id or not absorb_ids or not merged_content:
                    continue

                summary["duplicates_found"] += len(absorb_ids)

                if dry_run:
                    print(f"[Tier Migration] [DRY RUN] Would merge {len(absorb_ids)} memories into {keep_id[:8]}...")
                    for aid in absorb_ids:
                        absorbed.add(aid)
                    continue

                # Get current state of surviving memory
                surviving = await get_memory_by_id(keep_id)
                if not surviving:
                    continue

                # Sum reinforcement counts from all absorbed memories
                total_reinforcement = surviving.reinforcement_count or 0
                for aid in absorb_ids:
                    absorbed_mem = await get_memory_by_id(aid)
                    if absorbed_mem:
                        total_reinforcement += (absorbed_mem.reinforcement_count or 0) + 1
                        await delete_memory(aid)
                        absorbed.add(aid)
                        summary["memories_deleted"] += 1

                new_tier = _compute_tier(total_reinforcement)
                temporal = decision.get("temporal_nature")
                update_kwargs = {
                    "content": merged_content,
                    "reinforcement_count": total_reinforcement,
                    "tier": new_tier,
                }
                if temporal and temporal in ("timeless", "temporary", "evolving"):
                    update_kwargs["temporal_nature"] = temporal

                await update_memory(keep_id, **update_kwargs)
                summary["merges_performed"] += 1
                print(f"[Tier Migration] Merged {len(absorb_ids)} -> {keep_id[:8]}... (count={total_reinforcement}, tier={new_tier})")

    except Exception as e:
        print(f"[Tier Migration] Batch error: {e}")
        summary["errors"] += 1
