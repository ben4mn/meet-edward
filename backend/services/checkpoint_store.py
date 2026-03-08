"""
Simple JSONB checkpoint store replacing LangGraph's AsyncPostgresSaver.

Provides get_messages() and save_messages() for conversation persistence.
"""

import json
from typing import Optional
from datetime import datetime, timezone

from services.database import async_session, ConversationMessagesModel


async def get_messages(conversation_id: str) -> list[dict]:
    """Load messages for a conversation.

    Returns an empty list for new conversations.
    """
    async with async_session() as session:
        record = await session.get(ConversationMessagesModel, conversation_id)
        if record and record.messages:
            data = record.messages
            if isinstance(data, str):
                return json.loads(data)
            return data
        return []


async def save_messages(
    conversation_id: str,
    messages: list[dict],
    metadata: Optional[dict] = None,
) -> None:
    """Save messages for a conversation (upsert)."""
    messages_json = json.dumps(messages, default=str)
    metadata_json = json.dumps(metadata, default=str) if metadata else None

    async with async_session() as session:
        record = await session.get(ConversationMessagesModel, conversation_id)
        if record:
            record.messages = messages_json
            if metadata_json:
                record.metadata_ = metadata_json
            record.updated_at = datetime.utcnow()
        else:
            record = ConversationMessagesModel(
                conversation_id=conversation_id,
                messages=messages_json,
                metadata_=metadata_json,
            )
            session.add(record)
        await session.commit()
