"""
Entity extraction and management service for Edward.

Handles:
- LLM-based entity extraction from conversations
- Entity resolution (name/alias matching)
- Entity-to-memory linking
- Fast substring detection for retrieval-time entity matching
"""

import asyncio
import json
import re
import uuid
from typing import List, Optional, Tuple
from datetime import datetime

from sqlalchemy import select, func, or_, text, update
from langchain_core.messages import HumanMessage

from services.llm_utils import build_background_llm, make_background_system_message
from services.json_parsing import extract_json_payload
from services.database import async_session, EntityModel, EntityMentionModel

import os
import time

# 27 of 29 calls in the day before 2026-09-05 hit the old 8 s budget on
# Terra. Entity extraction now runs off the critical path (see
# memory_service._link_entities_in_background), so the budget can be
# generous, and it rides Luna — the fast tier — by default. 30 s was still
# under the call's p90 (2026-09-25 audit: 48 % timed out, each a paid
# request thrown away): 90 s, low effort, and each message clipped.
ENTITY_TIMEOUT_SECONDS = 90
ENTITY_REASONING_EFFORT = "low"
ENTITY_MESSAGE_MAX_CHARS = 4000
ENTITY_MODEL_DEFAULT = "gpt-5.6-luna"


def entity_model() -> str:
    return os.getenv("EDWARD_ENTITY_MODEL", ENTITY_MODEL_DEFAULT)


ENTITY_EXTRACTION_PROMPT = """Analyze this conversation and extract entities (people, projects, places, organizations, topics).

For each entity, provide:
- name: the primary name used
- entity_type: one of "person", "project", "place", "org", "topic"
- aliases: list of alternate names/references used (empty list if none)
- relationship: how this entity relates to the conversation (e.g., "subject", "mentioned", "discussed")

Return ONLY a JSON array. Example:
[
  {{"name": "Sarah", "entity_type": "person", "aliases": ["Sarah Chen"], "relationship": "discussed"}},
  {{"name": "Project Atlas", "entity_type": "project", "aliases": ["Atlas"], "relationship": "subject"}}
]

If no entities found, return [].

Conversation:
{conversation}"""


def _clip_message(content) -> str:
    """One message's text, at most ``ENTITY_MESSAGE_MAX_CHARS``. Pure."""
    text_value = content if isinstance(content, str) else str(content or "")
    if len(text_value) <= ENTITY_MESSAGE_MAX_CHARS:
        return text_value
    return text_value[:ENTITY_MESSAGE_MAX_CHARS] + " …[clipped]"


async def extract_entities(messages: list) -> List[dict]:
    """Use LLM to identify entities from conversation messages.

    Args:
        messages: List of message dicts with 'role' and 'content' keys

    Returns:
        List of entity dicts with name, entity_type, aliases, relationship
    """
    conversation_text = "\n".join([
        f"{msg.get('role', 'unknown').upper()}: {_clip_message(msg.get('content', ''))}"
        for msg in messages[-10:]
        if isinstance(msg, dict)
    ])

    if not conversation_text.strip():
        return []

    llm = build_background_llm(max_tokens=1024, model=entity_model(), effort=ENTITY_REASONING_EFFORT)
    started = time.monotonic()

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                make_background_system_message(
                    "Extract entities (people, projects, places, orgs, topics) from conversations. Return ONLY a JSON array.",
                ),
                HumanMessage(content=ENTITY_EXTRACTION_PROMPT.format(conversation=conversation_text)),
            ]),
            timeout=ENTITY_TIMEOUT_SECONDS,
        )

        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        entities = extract_json_payload(
            response_text, expected="array", subsystem="entity_extraction"
        )
        if isinstance(entities, list):
            # Validate each entity has required fields
            valid = []
            for e in entities:
                if isinstance(e, dict) and e.get("name") and e.get("entity_type"):
                    if e["entity_type"] not in ("person", "project", "place", "org", "topic"):
                        e["entity_type"] = "topic"
                    valid.append(e)
            print(f"[ENTITY] ok {len(valid)} entities in {time.monotonic() - started:.1f}s")
            return valid

    except asyncio.TimeoutError:
        print(f"[ENTITY] timed out after {ENTITY_TIMEOUT_SECONDS}s")
    except Exception as e:
        print(f"[ENTITY] Extraction failed: {e}")

    return []


async def resolve_entity(name: str, entity_type: Optional[str] = None) -> Optional[EntityModel]:
    """Case-insensitive lookup by name or aliases.

    Checks exact name match first, then searches aliases JSON array.
    """
    async with async_session() as session:
        # Exact name match (case-insensitive)
        query = select(EntityModel).where(
            func.lower(EntityModel.name) == name.lower()
        )
        if entity_type:
            query = query.where(EntityModel.entity_type == entity_type)

        result = await session.execute(query)
        entities = list(result.scalars().all())
        if entities:
            return entities[0]  # Return first match if multiple

        # Search aliases — aliases stored as JSON array text
        # Use SQL to check if lowered name appears in lowered aliases
        result = await session.execute(
            select(EntityModel).where(
                func.lower(EntityModel.aliases).contains(name.lower())
            )
        )
        entities = list(result.scalars().all())
        return entities[0] if entities else None


async def upsert_entity(
    name: str,
    entity_type: str,
    aliases: Optional[List[str]] = None,
    metadata: Optional[dict] = None,
) -> EntityModel:
    """Resolve then create or update an entity. Increments mention_count."""
    existing = await resolve_entity(name, entity_type)

    async with async_session() as session:
        if existing:
            # Update existing entity
            result = await session.execute(
                select(EntityModel).where(EntityModel.id == existing.id)
            )
            entity = result.scalar_one()
            entity.mention_count = (entity.mention_count or 0) + 1
            entity.last_seen = datetime.utcnow()

            # Merge aliases
            if aliases:
                current_aliases = json.loads(entity.aliases) if entity.aliases else []
                current_lower = {a.lower() for a in current_aliases}
                for alias in aliases:
                    if alias.lower() not in current_lower and alias.lower() != entity.name.lower():
                        current_aliases.append(alias)
                        current_lower.add(alias.lower())
                entity.aliases = json.dumps(current_aliases) if current_aliases else None

            # Merge metadata
            if metadata:
                current_meta = json.loads(entity.metadata_json) if entity.metadata_json else {}
                current_meta.update(metadata)
                entity.metadata_json = json.dumps(current_meta)

            await session.commit()
            await session.refresh(entity)
            return entity
        else:
            # Create new entity
            entity = EntityModel(
                id=str(uuid.uuid4()),
                name=name,
                entity_type=entity_type,
                aliases=json.dumps(aliases) if aliases else None,
                metadata_json=json.dumps(metadata) if metadata else None,
                mention_count=1,
            )
            session.add(entity)
            await session.commit()
            await session.refresh(entity)
            print(f"[ENTITY] Created new entity: {name} ({entity_type})")
            return entity


async def link_entity_to_memory(
    entity_id: str,
    memory_id: Optional[str],
    conversation_id: Optional[str] = None,
    relationship: Optional[str] = None,
    snippet: Optional[str] = None,
) -> Optional[EntityMentionModel]:
    """Create an EntityMention linking entity to memory. Deduplicates by (entity_id, memory_id)."""
    if not memory_id:
        return None

    async with async_session() as session:
        # Check for existing link
        existing = await session.execute(
            select(EntityMentionModel).where(
                EntityMentionModel.entity_id == entity_id,
                EntityMentionModel.memory_id == memory_id,
            )
        )
        if existing.scalar_one_or_none():
            return None

        mention = EntityMentionModel(
            id=str(uuid.uuid4()),
            entity_id=entity_id,
            memory_id=memory_id,
            conversation_id=conversation_id,
            relationship=relationship,
            context_snippet=snippet[:500] if snippet else None,
        )
        session.add(mention)
        await session.commit()
        return mention


async def get_entity_memories(entity_id: str, limit: int = 10, *, live_only: bool = False) -> List[dict]:
    """Load all memories linked to an entity via entity_mentions.

    ``live_only`` keeps only mentions of rows that exist and have not
    lapsed (``valid_until``), before the limit (2026-09-27): filtered
    after it, retired mentions read first crowded out the live ones.

    Returns list of dicts with memory_id, relationship, context_snippet.
    """
    query = (
        select(EntityMentionModel)
        .where(EntityMentionModel.entity_id == entity_id)
        .where(EntityMentionModel.memory_id.isnot(None))
    )
    if live_only:
        from services.database import MemoryModel

        query = query.join(MemoryModel, MemoryModel.id == EntityMentionModel.memory_id).where(
            or_(MemoryModel.valid_until.is_(None), MemoryModel.valid_until > datetime.utcnow())
        )
    async with async_session() as session:
        result = await session.execute(query.order_by(EntityMentionModel.id.desc()).limit(limit))
        mentions = list(result.scalars().all())

    return [
        {
            "memory_id": m.memory_id,
            "relationship": m.relationship,
            "context_snippet": m.context_snippet,
        }
        for m in mentions
    ]


async def detect_entities_in_text(text_input: str) -> List[EntityModel]:
    """Fast path: substring match against known entity names and aliases.

    Used during retrieval for quick entity detection without LLM calls.
    """
    if not text_input or len(text_input) < 2:
        return []

    text_lower = text_input.lower()
    matched = []

    async with async_session() as session:
        # Load all entities (typically small set — hundreds at most)
        result = await session.execute(select(EntityModel))
        all_entities = list(result.scalars().all())

    for entity in all_entities:
        # Check name
        if entity.name.lower() in text_lower:
            matched.append(entity)
            continue

        # Check aliases
        if entity.aliases:
            try:
                aliases = json.loads(entity.aliases)
                if any(alias.lower() in text_lower for alias in aliases):
                    matched.append(entity)
            except (json.JSONDecodeError, TypeError):
                pass

    return matched


async def get_entity_by_id(entity_id: str) -> Optional[EntityModel]:
    """Get a single entity by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(EntityModel).where(EntityModel.id == entity_id)
        )
        return result.scalar_one_or_none()


async def list_entities(
    entity_type: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[EntityModel]:
    """List entities with optional type filter."""
    async with async_session() as session:
        query = select(EntityModel)
        if entity_type:
            query = query.where(EntityModel.entity_type == entity_type)
        query = query.order_by(EntityModel.mention_count.desc()).limit(limit).offset(offset)
        result = await session.execute(query)
        return list(result.scalars().all())


async def get_entity_count() -> int:
    """Get total entity count."""
    async with async_session() as session:
        result = await session.execute(select(func.count()).select_from(EntityModel))
        return result.scalar() or 0
