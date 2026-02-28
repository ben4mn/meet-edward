"""Service for generating search tags for conversations using Claude Haiku."""

import asyncio
from typing import List, Dict

from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage

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

    llm = ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        temperature=0,
        max_tokens=150,
    )

    system = (
        "Generate 5-10 comma-separated search keywords for this conversation. "
        "Include: topics discussed, entities mentioned (people, places, products), "
        "user intent (planning, debugging, cooking, etc.), and key terms. "
        "Output ONLY the comma-separated keywords, nothing else."
    )

    response = await llm.ainvoke([
        SystemMessage(content=system),
        HumanMessage(content=conversation_text),
    ])

    tags = response.content.strip() if isinstance(response.content, str) else str(response.content).strip()

    if tags:
        await update_search_tags(conversation_id, tags)


async def generate_search_tags_safe(conversation_id: str, messages: List[Dict[str, str]]) -> None:
    """Fire-and-forget wrapper that never raises."""
    try:
        await generate_search_tags(conversation_id, messages)
    except Exception as e:
        print(f"Search tag generation failed for {conversation_id}: {e}")
