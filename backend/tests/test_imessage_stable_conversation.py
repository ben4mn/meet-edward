"""R/G TDD for stable iMessage chat → conversation mapping.

Today (triage_service.py:761-769), an @edward mention in an iMessage chat
gets a fresh UUID conversation_id whenever the 5-minute listening window
has expired. That means:

- A group chat like "Weekend" that Edward replied to yesterday looks
  like a brand-new conversation when Ellis pings him today.
- Edward's own past replies live in the LangGraph checkpoint of that old
  orphaned conversation_id — he can't see them.
- "Long-running multi-day discourse" is structurally impossible.

After this step lands, every message in the same iMessage chat (1:1 or
group, across restarts, across days) routes through ``get_or_create_for_chat``
and lands in the same conversation_id forever.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select


pytestmark = pytest.mark.asyncio


# --------------------------------------------------------------------------- #
# Mapping service contract                                                    #
# --------------------------------------------------------------------------- #


async def test_first_message_in_chat_creates_persistent_mapping(clean_db):
    """First time we see a chat_identifier: a new conversation is created
    AND a row in imessage_chat_conversations records the mapping."""
    from services.imessage_conversation_service import get_or_create_for_chat
    from services.database import (
        async_session,
        ConversationModel,
        IMessageChatConversationModel,
    )

    chat_id = "iMessage;+;chat42-Weekend"
    conversation_id = await get_or_create_for_chat(
        chat_id, chat_name="Weekend", is_group=True
    )

    assert conversation_id, "service must return a non-empty conversation_id"

    async with async_session() as session:
        # Mapping row exists
        mapping = await session.execute(
            select(IMessageChatConversationModel).where(
                IMessageChatConversationModel.chat_identifier == chat_id
            )
        )
        row = mapping.scalar_one_or_none()
        assert row is not None, "mapping row must be inserted"
        assert row.conversation_id == conversation_id
        assert row.is_group is True
        assert row.chat_name == "Weekend"

        # Conversation row exists too — so the LangGraph checkpoint can attach to it
        conv = await session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        conv_row = conv.scalar_one_or_none()
        assert conv_row is not None, (
            "an actual conversations row must exist so chat_with_memory can persist state"
        )


async def test_subsequent_message_in_same_chat_reuses_conversation_id(clean_db):
    """Calling the service twice for the same chat must hand back the same id."""
    from services.imessage_conversation_service import get_or_create_for_chat

    chat_id = "iMessage;+;chat42-Weekend"
    a = await get_or_create_for_chat(chat_id, chat_name="Weekend", is_group=True)
    b = await get_or_create_for_chat(chat_id, chat_name="Weekend", is_group=True)

    assert a == b, "service must return the same conversation_id for the same chat"


async def test_different_chats_get_different_conversation_ids(clean_db):
    """Two distinct chats — one group, one 1:1 — must not collapse onto the
    same conversation_id even if they share an underlying participant."""
    from services.imessage_conversation_service import get_or_create_for_chat

    group = await get_or_create_for_chat(
        "iMessage;+;chat42-Weekend", chat_name="Weekend", is_group=True
    )
    direct = await get_or_create_for_chat(
        "+15555550100", chat_name=None, is_group=False
    )

    assert group != direct


async def test_mapping_survives_listening_window_expiry(clean_db):
    """The bug today: when the in-memory 5-min listening window evicts a
    chat, the next message creates a brand-new conversation_id. With the
    persistent mapping, that path no longer affects identity — the
    conversation_id is sourced from the DB row, not the eviction cache.

    We simulate window expiry by clearing the in-memory dict; the
    service-level identity must remain stable.
    """
    from services.imessage_conversation_service import get_or_create_for_chat
    from services.heartbeat import triage_service

    chat_id = "iMessage;+;chat42-Weekend"
    first = await get_or_create_for_chat(chat_id, chat_name="Weekend", is_group=True)

    # Listening window expires / process restarts / etc.
    triage_service._active_listeners.clear()

    second = await get_or_create_for_chat(
        chat_id, chat_name="Weekend", is_group=True
    )
    assert first == second, (
        "listening window state must not affect persistent chat-conversation mapping"
    )


async def test_chat_name_update_does_not_change_conversation_id(clean_db):
    """If Ellis renames the group chat or a contact name resolves later, the
    mapping should refresh the metadata without churning the conversation."""
    from services.imessage_conversation_service import get_or_create_for_chat
    from services.database import async_session, IMessageChatConversationModel

    chat_id = "iMessage;+;chat42-Weekend"
    first = await get_or_create_for_chat(chat_id, chat_name=None, is_group=True)
    second = await get_or_create_for_chat(
        chat_id, chat_name="Weekend", is_group=True
    )

    assert first == second

    async with async_session() as session:
        result = await session.execute(
            select(IMessageChatConversationModel).where(
                IMessageChatConversationModel.chat_identifier == chat_id
            )
        )
        row = result.scalar_one()
        assert row.chat_name == "Weekend", "later metadata should be persisted"


# --------------------------------------------------------------------------- #
# Schema sanity                                                                #
# --------------------------------------------------------------------------- #


async def test_imessage_chat_conversations_model_exists(clean_db):
    """The model+table must be registered so init_db creates it."""
    from services.database import IMessageChatConversationModel

    assert IMessageChatConversationModel.__tablename__ == "imessage_chat_conversations"
    cols = {c.name for c in IMessageChatConversationModel.__table__.c}
    assert {"chat_identifier", "conversation_id", "chat_name", "is_group", "last_active"} <= cols
