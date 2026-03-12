"""Service for generating search tags for conversations using Claude Haiku."""

import asyncio
from typing import List, Dict

from services.llm_client import haiku_call
from services.conversation_service import update_search_tags


async def generate_search_tags(conversation_id: str, messages: List[Dict[str, str]]) -> None:
    """Generate search tags for a conversation using Claude Haiku.

    Args:
        conversation_id: The conversation to tag
        messages: Recent messages in the format [{"role": "human"/"assistant", "content": "..."}]
    """
    if not messages:
        return

    # Take last 5 messages for context
    recent = messages[-5:]
    conversation_text = "\n".join(
        f"{m['role']}: {m['content'][:500]}" for m in recent
        if isinstance(m.get("content"), str)
    )

    if not conversation_text.strip():
        return

    system = (
        "Generate 5-10 comma-separated search keywords for this conversation. "
        "Include: topics discussed, entities mentioned (people, places, products), "
        "user intent (planning, debugging, cooking, etc.), and key terms. "
        "Output ONLY the comma-separated keywords, nothing else."
    )

    tags = await haiku_call(system=system, message=conversation_text, max_tokens=150)
    tags = tags.strip()

    if tags:
        await update_search_tags(conversation_id, tags)


async def generate_search_tags_safe(conversation_id: str, messages: List[Dict[str, str]]) -> None:
    """Fire-and-forget wrapper that never raises."""
    try:
        await generate_search_tags(conversation_id, messages)
    except Exception as e:
        print(f"Search tag generation failed for {conversation_id}: {e}")
