#!/usr/bin/env python3
"""
Backfill entities from existing memories.

Processes memories in batches, extracts entities via LLM,
creates entity records, and links them to the source memories.
"""

import asyncio
import json
import re
import sys
import uuid
from datetime import datetime

# Force unbuffered output
sys.stdout.reconfigure(line_buffering=True)

from sqlalchemy import select, func


async def main():
    from services.database import init_db, async_session, MemoryModel
    from services.entity_service import upsert_entity, link_entity_to_memory, get_entity_count
    from services.llm_utils import build_background_llm, make_background_system_message
    from langchain_core.messages import HumanMessage

    await init_db()

    # Load memories that don't already have entity mentions
    from services.database import EntityMentionModel
    async with async_session() as session:
        # Get memory IDs that already have entity links
        linked_result = await session.execute(
            select(EntityMentionModel.memory_id).where(EntityMentionModel.memory_id.isnot(None))
        )
        already_linked = {r[0] for r in linked_result.fetchall()}

        result = await session.execute(
            select(MemoryModel).order_by(MemoryModel.created_at.asc())
        )
        all_memories = list(result.scalars().all())
        memories = [m for m in all_memories if m.id not in already_linked]

    print(f"Skipping {len(all_memories) - len(memories)} already-linked memories")

    print(f"Found {len(memories)} memories to process")
    print(f"Existing entities: {await get_entity_count()}")

    BATCH_SIZE = 8
    total_entities_created = 0
    total_links_created = 0
    batches = [memories[i:i+BATCH_SIZE] for i in range(0, len(memories), BATCH_SIZE)]

    llm = build_background_llm(max_tokens=2048)

    for batch_idx, batch in enumerate(batches):
        print(f"\nBatch {batch_idx + 1}/{len(batches)} ({len(batch)} memories)...")

        # Format memories for LLM
        memory_list = []
        for m in batch:
            memory_list.append({
                "id": m.id,
                "content": m.content,
                "type": m.memory_type,
            })

        prompt = f"""Analyze these memories and extract all entities (people, projects, places, organizations, topics).

For each entity, provide:
- name: the primary name (use real names when available, not phone numbers)
- entity_type: one of "person", "project", "place", "org", "topic"
- aliases: alternate names/references used (including phone numbers if associated with a person)
- memory_ids: list of memory IDs from below where this entity appears

Memories:
{json.dumps(memory_list, indent=2)}

Return ONLY a JSON array. Example:
[
  {{"name": "Jordan", "entity_type": "person", "aliases": [], "memory_ids": ["id1", "id2"]}},
  {{"name": "Edward", "entity_type": "project", "aliases": ["Edward AI"], "memory_ids": ["id3"]}}
]

If no entities found, return []. Do NOT create entities for generic concepts (like "weather" or "time").
Focus on specific people, projects, places, and organizations."""

        try:
            response = await asyncio.wait_for(
                llm.ainvoke([
                    make_background_system_message(
                        "Extract named entities from memories. Return ONLY a JSON array. "
                        "Be precise — only extract specific, named entities."
                    ),
                    HumanMessage(content=prompt),
                ]),
                timeout=120.0,
            )

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

            json_match = re.search(r'\[[\s\S]*\]', response_text)
            if json_match:
                response_text = json_match.group()

            entities = json.loads(response_text)
            if not isinstance(entities, list):
                print(f"  Unexpected response type: {type(entities)}")
                continue

        except asyncio.TimeoutError:
            print(f"  Batch timed out, skipping")
            continue
        except json.JSONDecodeError as e:
            print(f"  JSON parse error: {e}")
            continue
        except Exception as e:
            print(f"  Error: {e}")
            continue

        # Build memory ID -> content lookup for snippets
        mem_lookup = {m.id: m for m in batch}

        batch_entities = 0
        batch_links = 0

        for entity_data in entities:
            if not isinstance(entity_data, dict):
                continue
            name = entity_data.get("name", "").strip()
            entity_type = entity_data.get("entity_type", "topic")
            if not name or len(name) < 2:
                continue

            # Skip phone-number-only entities
            if name.startswith("+") and name[1:].isdigit():
                continue

            valid_types = ("person", "project", "place", "org", "topic")
            if entity_type not in valid_types:
                entity_type = "topic"

            aliases = entity_data.get("aliases", [])
            if not isinstance(aliases, list):
                aliases = []

            memory_ids = entity_data.get("memory_ids", [])
            if not isinstance(memory_ids, list):
                memory_ids = []

            # Upsert entity
            try:
                entity = await upsert_entity(
                    name=name,
                    entity_type=entity_type,
                    aliases=aliases,
                )
                batch_entities += 1

                # Link to memories
                for mid in memory_ids:
                    if mid in mem_lookup:
                        mem = mem_lookup[mid]
                        result = await link_entity_to_memory(
                            entity_id=entity.id,
                            memory_id=mid,
                            conversation_id=mem.source_conversation_id,
                            relationship="mentioned",
                            snippet=mem.content[:200],
                        )
                        if result:
                            batch_links += 1

            except Exception as e:
                print(f"  Error upserting {name}: {e}")

        total_entities_created += batch_entities
        total_links_created += batch_links
        print(f"  Extracted {batch_entities} entities, created {batch_links} links")

    # Final stats
    final_count = await get_entity_count()
    print(f"\n{'='*50}")
    print(f"Backfill complete!")
    print(f"  Total entities: {final_count}")
    print(f"  New entity upserts: {total_entities_created}")
    print(f"  New links created: {total_links_created}")
    print(f"{'='*50}")


if __name__ == "__main__":
    asyncio.run(main())
