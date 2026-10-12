"""
Post-turn implication reasoning service for Edward.

After memory extraction, analyzes stored memories to identify:
1. Open loops — unresolved items to track
2. Implications — inferred facts (stored as low-confidence memories)

Follows reflection_service.py pattern: fire-and-forget, run_*_safe() wrapper.
"""

import asyncio
import uuid
from typing import List, Optional

from langchain_core.messages import HumanMessage

from services.llm_utils import build_background_llm, make_background_system_message
from services.json_parsing import extract_json_payload


IMPLICATION_PROMPT = """Given these recently stored memories from a conversation, identify:

1. **Open loops** — unresolved items, commitments, things to follow up on (e.g., "I need to call the dentist", "I'll send that report tomorrow", "waiting for a response from X")
2. **Implications** — things that can be inferred but weren't explicitly stated (e.g., if someone mentions a meeting with a client, they likely have an ongoing business relationship)

Recently stored memories:
{memories}

Recent conversation context:
{conversation}

Return ONLY valid JSON:
{{
  "open_loops": [
    {{"description": "what needs follow-up", "priority": 0.5, "entity_name": "optional related person/project"}}
  ],
  "implications": [
    {{"content": "inferred fact", "memory_type": "fact", "confidence": 0.3}}
  ]
}}

If nothing found, return {{"open_loops": [], "implications": []}}"""


async def run_implication_analysis(
    conversation_id: str,
    messages: list,
    stored_memory_ids: List[str],
) -> dict:
    """Analyze stored memories for open loops and implications.

    Args:
        conversation_id: Current conversation
        messages: Recent messages for context
        stored_memory_ids: IDs of memories just stored by extraction

    Returns:
        Dict with counts of loops and implications created
    """
    if not stored_memory_ids:
        return {"open_loops": 0, "implications": 0}

    # Load the stored memories
    from services.memory_service import get_memory_by_id
    memory_texts = []
    for mid in stored_memory_ids[:10]:
        mem = await get_memory_by_id(mid)
        if mem:
            memory_texts.append(f"- [{mem.memory_type}] {mem.content}")

    if not memory_texts:
        return {"open_loops": 0, "implications": 0}

    conversation_text = "\n".join([
        f"{msg.get('role', 'unknown').upper()}: {msg.get('content', '')}"
        for msg in messages[-5:]
        if isinstance(msg, dict)
    ])

    llm = build_background_llm(max_tokens=1024)

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                make_background_system_message(
                    "Analyze memories for open loops and implications. Return ONLY valid JSON.",
                ),
                HumanMessage(content=IMPLICATION_PROMPT.format(
                    memories="\n".join(memory_texts),
                    conversation=conversation_text,
                )),
            ]),
            timeout=8.0,
        )

        response_text = response.content
        if isinstance(response_text, list):
            response_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in response_text
            )
        response_text = response_text.strip()

        data = extract_json_payload(
            response_text, expected="object", subsystem="implication_analysis"
        )

    except asyncio.TimeoutError:
        print("[IMPLICATION] Analysis timed out (8s)")
        return {"open_loops": 0, "implications": 0}
    except Exception as e:
        print(f"[IMPLICATION] Analysis failed: {e}")
        return {"open_loops": 0, "implications": 0}

    loops_created = 0
    implications_created = 0

    # Store open loops
    open_loops = data.get("open_loops", [])
    if open_loops:
        from services.open_loop_service import create_open_loop
        from services.entity_service import resolve_entity

        for loop in open_loops[:5]:
            if not isinstance(loop, dict) or not loop.get("description"):
                continue
            try:
                entity_id = None
                entity_name = loop.get("entity_name")
                if entity_name:
                    entity = await resolve_entity(entity_name)
                    if entity:
                        entity_id = entity.id

                await create_open_loop(
                    description=loop["description"],
                    source_conversation_id=conversation_id,
                    source_memory_id=stored_memory_ids[0] if stored_memory_ids else None,
                    entity_id=entity_id,
                    priority=float(loop.get("priority", 0.5)),
                )
                loops_created += 1
            except Exception as e:
                print(f"[IMPLICATION] Failed to create open loop: {e}")

    # Store implications as low-confidence memories
    implications = data.get("implications", [])
    if implications:
        from services.memory_service import store_memory, Memory

        for imp in implications[:3]:
            if not isinstance(imp, dict) or not imp.get("content"):
                continue
            try:
                confidence = float(imp.get("confidence", 0.3))
                confidence = max(0.1, min(0.5, confidence))

                memory = Memory(
                    id=None,
                    content=imp["content"],
                    memory_type=imp.get("memory_type", "fact"),
                    importance=0.3,
                    source_type="inferred",
                    confidence=confidence,
                    source_conversation_id=conversation_id,
                )
                await store_memory(memory)
                implications_created += 1
            except Exception as e:
                print(f"[IMPLICATION] Failed to store implication: {e}")

    if loops_created or implications_created:
        print(f"[IMPLICATION] Created {loops_created} open loops, {implications_created} implications")

    return {"open_loops": loops_created, "implications": implications_created}


async def run_implication_safe(
    conversation_id: str,
    messages: list,
    stored_memory_ids: List[str],
) -> None:
    """Fire-and-forget wrapper that never raises."""
    try:
        await asyncio.wait_for(
            run_implication_analysis(conversation_id, messages, stored_memory_ids),
            timeout=15.0,
        )
    except asyncio.TimeoutError:
        print(f"[IMPLICATION] Full analysis timed out (15s) for {conversation_id}")
    except Exception as e:
        print(f"[IMPLICATION] Analysis failed safely: {e}")
