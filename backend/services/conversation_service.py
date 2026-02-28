"""Service for managing conversation records."""

from typing import Optional, List, Tuple
from datetime import datetime
from sqlalchemy import select, update, delete, desc, or_, and_, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import async_session, ConversationModel


async def create_conversation(
    conversation_id: str,
    title: str = "New Conversation",
    source: str = "user",
    channel: str = "text"
) -> ConversationModel:
    """Create a new conversation record.

    Args:
        conversation_id: Unique identifier for the conversation
        title: Display title (truncated to 100 chars)
        source: Origin of conversation - "user", "scheduled_event", or "external_message"
        channel: Input method - "text" or "voice"
    """
    async with async_session() as session:
        conversation = ConversationModel(
            id=conversation_id,
            title=title[:100],  # Truncate to 100 chars
            source=source,
            channel=channel,
            message_count=1
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        return conversation


async def get_conversations(
    limit: int = 50,
    offset: int = 0,
    include_scheduled: bool = False
) -> Tuple[List[ConversationModel], bool]:
    """Get list of conversations ordered by updated_at descending.

    Args:
        limit: Maximum number of conversations to return
        offset: Number of conversations to skip
        include_scheduled: If False, hide scheduled_event "thoughts" (where notified_user=false)

    Returns:
        Tuple of (conversations, has_more) where has_more indicates more results exist.

    The distinction is:
    - "Thoughts": Edward did something autonomously without trying to get user attention
    - "Conversations": User-initiated, or Edward tried to engage user (push notification, message)

    When include_scheduled=False, we hide scheduled_event conversations unless user engaged (message_count > 2).
    When include_scheduled=True, we show everything.
    """
    async with async_session() as session:
        query = select(ConversationModel)

        # Filter out scheduled event "thoughts" unless explicitly requested
        # A "thought" is: source='scheduled_event' AND message_count <= 2 (Edward's initial + response only)
        # User engagement means message_count > 2 (user replied at least once)
        if not include_scheduled:
            query = query.where(
                or_(
                    ~ConversationModel.source.in_(["scheduled_event", "heartbeat"]),
                    and_(
                        ConversationModel.source.in_(["scheduled_event", "heartbeat"]),
                        ConversationModel.message_count > 2
                    ),
                    and_(
                        ConversationModel.source == "heartbeat",
                        ConversationModel.notified_user == True
                    ),
                )
            )

        # Fetch one extra row to determine if more results exist
        query = query.order_by(desc(ConversationModel.updated_at)).limit(limit + 1).offset(offset)
        result = await session.execute(query)
        rows = list(result.scalars().all())

        has_more = len(rows) > limit
        return rows[:limit], has_more


async def get_conversation(conversation_id: str) -> Optional[ConversationModel]:
    """Get a single conversation by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        return result.scalar_one_or_none()


async def update_conversation(conversation_id: str, title: Optional[str] = None) -> Optional[ConversationModel]:
    """Update a conversation's title."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return None

        if title is not None:
            conversation.title = title[:100]
        conversation.updated_at = datetime.utcnow()

        await session.commit()
        await session.refresh(conversation)
        return conversation


async def delete_conversation(conversation_id: str) -> bool:
    """Delete a conversation record."""
    async with async_session() as session:
        result = await session.execute(
            delete(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        await session.commit()
        return result.rowcount > 0


async def increment_message_count(conversation_id: str) -> Optional[ConversationModel]:
    """Increment message count and update timestamp for a conversation."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return None

        conversation.message_count += 1
        conversation.updated_at = datetime.utcnow()

        await session.commit()
        await session.refresh(conversation)
        return conversation


async def conversation_exists(conversation_id: str) -> bool:
    """Check if a conversation exists."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel.id).where(ConversationModel.id == conversation_id)
        )
        return result.scalar_one_or_none() is not None


async def search_conversations(
    query: str,
    limit: int = 20,
    offset: int = 0,
    include_scheduled: bool = False
) -> Tuple[List[ConversationModel], int]:
    """Search conversations using PostgreSQL full-text search.

    Args:
        query: Search query string
        limit: Maximum results to return
        offset: Number of results to skip
        include_scheduled: If False, hide scheduled_event "thoughts"

    Returns:
        Tuple of (matching conversations sorted by relevance, total count)
    """
    async with async_session() as session:
        tsquery = func.plainto_tsquery("english", query)
        tsvector = func.to_tsvector(
            "english",
            func.coalesce(ConversationModel.title, "") + " " + func.coalesce(ConversationModel.search_tags, "")
        )
        rank = func.ts_rank_cd(tsvector, tsquery)

        base_filter = tsvector.op("@@")(tsquery)

        filters = [base_filter]
        if not include_scheduled:
            filters.append(
                or_(
                    ~ConversationModel.source.in_(["scheduled_event", "heartbeat"]),
                    and_(
                        ConversationModel.source.in_(["scheduled_event", "heartbeat"]),
                        ConversationModel.message_count > 2
                    ),
                    and_(
                        ConversationModel.source == "heartbeat",
                        ConversationModel.notified_user == True
                    ),
                )
            )

        # Count total matches
        count_query = select(func.count()).select_from(ConversationModel).where(*filters)
        total = (await session.execute(count_query)).scalar() or 0

        # Get results sorted by relevance
        results_query = (
            select(ConversationModel)
            .where(*filters)
            .order_by(rank.desc(), desc(ConversationModel.updated_at))
            .limit(limit)
            .offset(offset)
        )
        result = await session.execute(results_query)
        return list(result.scalars().all()), total


async def update_search_tags(conversation_id: str, tags: str) -> bool:
    """Update the search_tags column for a conversation.

    Args:
        conversation_id: The conversation to update
        tags: Comma-separated keyword tags

    Returns:
        True if the conversation was updated, False if not found.
    """
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return False

        conversation.search_tags = tags
        await session.commit()
        return True


async def mark_user_notified(conversation_id: str) -> bool:
    """Mark a conversation as having notified the user (push notification or outbound message).

    This is used to distinguish between:
    - "Thoughts": Edward doing background tasks silently
    - "Conversations": Edward trying to engage the user

    Returns True if the conversation was updated, False if not found.
    """
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation:
            return False

        conversation.notified_user = True
        await session.commit()
        return True
