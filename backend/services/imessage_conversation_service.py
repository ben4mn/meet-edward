"""Persistent iMessage chat → conversation mapping.

Before this service, every @edward mention generated a fresh UUID for the
conversation_id whenever the in-memory 5-minute listening window had
expired. That meant a friend group chat Edward replied to yesterday
looked like a brand-new conversation today — Edward's own prior replies
were stranded in an orphaned LangGraph checkpoint he could never reach.

``get_or_create_for_chat`` is the single source of truth for "which
Edward conversation does this iMessage chat belong to." Same chat
identifier in → same conversation_id out, forever.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from services.database import (
    IMessageChatConversationModel,
    async_session,
)
from services.conversation_service import create_conversation


TITLE_PREFIX = "iMessage: "


def title_for_chat(chat_identifier: str, chat_name: str | None) -> str:
    """The conversation title for an iMessage chat: the contact/group name
    when known, the raw identifier otherwise. Truncation matches the
    original minting so old titles can be recognised by ``retitled_for``."""
    return TITLE_PREFIX + (chat_name or chat_identifier)[:50]


def retitled_for(chat_identifier: str, chat_name: str | None, current_title: str | None) -> str | None:
    """Return a better title once the contact name is known, else ``None``.

    Only the auto-minted bare-identifier title is ever replaced. Anything
    else (already named, or renamed by a human) is left alone.
    """
    if not chat_name:
        return None
    if current_title != title_for_chat(chat_identifier, None):
        return None
    better = title_for_chat(chat_identifier, chat_name)
    return better if better != current_title else None


async def _retitle_conversation(conversation_id: str, chat_identifier: str, chat_name: str | None) -> None:
    from services.database import ConversationModel

    async with async_session() as session:
        row = (
            await session.execute(
                select(ConversationModel).where(ConversationModel.id == conversation_id)
            )
        ).scalar_one_or_none()
        if row is None:
            return
        better = retitled_for(chat_identifier, chat_name, row.title)
        if better is None:
            return
        row.title = better
        await session.commit()


async def get_or_create_for_chat(
    chat_identifier: str,
    chat_name: str | None = None,
    is_group: bool = False,
    contact_name: str | None = None,
) -> str:
    """Return the persistent Edward conversation_id for an iMessage chat.

    On first call for a chat: creates both a ``conversations`` row and an
    ``imessage_chat_conversations`` mapping row, then returns the new id.
    On subsequent calls: returns the same id; opportunistically refreshes
    ``chat_name`` / ``is_group`` metadata if they've improved (e.g. a
    contact name resolved after the first hit).
    """
    if not chat_identifier:
        raise ValueError("chat_identifier is required")
    # 1:1 chats have no chat_name in chat.db; the contact's name is what
    # the title should carry (otherwise the sidebar shows a bare number).
    chat_name = chat_name or contact_name

    async with async_session() as session:
        result = await session.execute(
            select(IMessageChatConversationModel).where(
                IMessageChatConversationModel.chat_identifier == chat_identifier
            )
        )
        existing = result.scalar_one_or_none()
        if existing:
            updated = False
            if chat_name and existing.chat_name != chat_name:
                existing.chat_name = chat_name
                updated = True
            if is_group and not existing.is_group:
                existing.is_group = True
                updated = True
            if updated:
                await session.commit()
            if chat_name:
                # The title was minted from the bare number if the name
                # wasn't known yet; upgrade it now that it is.
                await _retitle_conversation(existing.conversation_id, chat_identifier, chat_name)
            return existing.conversation_id

        conversation_id = str(uuid.uuid4())

    # Create the conversations row before the mapping row so any future
    # FK we add wouldn't dangle. create_conversation manages its own session.
    await create_conversation(
        conversation_id,
        title=title_for_chat(chat_identifier, chat_name),
        source="heartbeat",
    )

    async with async_session() as session:
        mapping = IMessageChatConversationModel(
            chat_identifier=chat_identifier,
            conversation_id=conversation_id,
            chat_name=chat_name,
            is_group=is_group,
        )
        session.add(mapping)
        try:
            await session.commit()
        except Exception:
            # Race: another caller created the mapping between our SELECT
            # and our INSERT. Roll back and return the winner's id.
            await session.rollback()
            result = await session.execute(
                select(IMessageChatConversationModel.conversation_id).where(
                    IMessageChatConversationModel.chat_identifier == chat_identifier
                )
            )
            winner = result.scalar_one_or_none()
            if winner:
                return winner
            raise

    return conversation_id
