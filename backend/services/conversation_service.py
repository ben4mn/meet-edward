"""Service for managing conversation records."""

from typing import Optional, List, Tuple
from datetime import datetime
from sqlalchemy import select, update, delete, desc, or_, and_, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import async_session, ConversationModel


# Conversations Edward starts on his own. Hidden from default listings
# unless he reached out about them (notified_user) or the owner engaged (more
# than two messages). ``is_conversation_visible`` is the pure form of the
# rule; ``_visible_filter`` is the SQL form. Keep them in step.
EDWARD_OWN_SOURCES: tuple[str, ...] = ("scheduled_event", "heartbeat", "edward_thought")

# Journal entries (thought journal, 2026-09-25): an ``edward_thought`` whose
# context_pack says ``"kind": "mind_action"`` is one wake that chose to act.
# It is always visible — a fresh entry has one message, and one that only
# committed to something (an event, a goal, a stand-down) has no unread dot,
# so the rule above hid exactly what it must show: it must be obvious when
# Edward is choosing to take action. Other thoughts keep the old rule.
JOURNAL_SOURCE = "edward_thought"


def is_journal_entry(conversation) -> bool:
    from services.thought_journal import is_entry_pack

    return (getattr(conversation, "source", None) == JOURNAL_SOURCE
            and is_entry_pack(getattr(conversation, "context_pack", None)))


def _journal_entry_filter():
    """SQL form of ``is_journal_entry``: a LIKE on the pack's kind, guarded
    to thoughts (a few hundred rows, no new column to migrate)."""
    return and_(ConversationModel.source == JOURNAL_SOURCE, _entry_pack_marked())


def _entry_pack_marked():
    from services.thought_journal import ENTRY_PACK_MARKERS

    # coalesce: NOT (NULL LIKE …) is NULL, which would drop every thought
    # without a pack from the "other thoughts" page.
    pack = func.coalesce(ConversationModel.context_pack, "")
    return or_(*[pack.like(f"%{m}%") for m in ENTRY_PACK_MARKERS])


# Edward's group is paged per source; thoughts split in two so the journal
# entries have a page of their own (older thoughts and the mind's threads
# are many and would otherwise push this week's entries off it).
def _edwards_pages():
    """(label, where-clause) per page of Edward's group, in order."""
    pages = []
    for src in EDWARD_GROUP_SOURCES:
        if src == JOURNAL_SOURCE:
            pages.append(("entries", _journal_entry_filter()))
            pages.append((src, and_(ConversationModel.source == src, ~_entry_pack_marked())))
        else:
            pages.append((src, ConversationModel.source == src))
    return pages


def is_conversation_visible(conversation, include_scheduled: bool = False) -> bool:
    """Should this conversation appear in a default (thoughts-hidden) listing?"""
    if include_scheduled:
        return True
    source = getattr(conversation, "source", None) or "user"
    if source not in EDWARD_OWN_SOURCES:
        return True
    if is_journal_entry(conversation):
        return True
    count = getattr(conversation, "message_count", 0) or 0
    if count > 2:
        return True
    return bool(getattr(conversation, "notified_user", False)) and count > 1


# Sidebar groups. "yours" is what the owner started or what came in from outside;
# "edwards" is everything Edward began on his own (thoughts, threads,
# scheduled runs, workers). None means no group filter.
OWNER_SOURCES: tuple[str, ...] = ("user", "external_message")
EDWARD_GROUP_SOURCES: tuple[str, ...] = EDWARD_OWN_SOURCES + ("orchestrator_worker",)
# Long-lived threads always shown on the first page of Edward's group.
PINNED_CONVERSATION_IDS: tuple[str, ...] = ("edward-surfaces", "edward-mind")


def sources_for_group(group):
    if group == "yours":
        return OWNER_SOURCES
    if group == "edwards":
        return EDWARD_GROUP_SOURCES
    return None


def _group_filter(group):
    """SQL clause for a sidebar group, or None. A NULL source is the owner's."""
    sources = sources_for_group(group)
    if sources is None:
        return None
    clause = ConversationModel.source.in_(list(sources))
    if group == "yours":
        clause = or_(clause, ConversationModel.source.is_(None))
    return clause


def _visible_filter():
    """SQLAlchemy clause matching ``is_conversation_visible(..., False)``."""
    own = ConversationModel.source.in_(list(EDWARD_OWN_SOURCES))
    return or_(
        ~own,
        _journal_entry_filter(),
        and_(own, ConversationModel.message_count > 2),
        and_(own, ConversationModel.notified_user == True, ConversationModel.message_count > 1),  # noqa: E712
    )


async def create_conversation(
    conversation_id: str,
    title: str = "New Conversation",
    source: str = "user",
    channel: str = "text",
    initial_message: Optional[str] = None,
    context_pack: Optional[str] = None,
) -> ConversationModel:
    """Create a new conversation record.

    Args:
        conversation_id: Unique identifier for the conversation
        title: Display title (truncated to 100 chars)
        source: Origin of conversation - "user", "scheduled_event", "external_message", "edward_thought"
        channel: Input method - "text" or "voice"
        initial_message: For Edward-initiated thought conversations, the
            narrative that should render as his opening assistant message.
            The conversations API surfaces it when the checkpoint is empty.
        context_pack: JSON-encoded signal pack from the wake-up tick (or
            other context carrier) so that follow-up turns can recover
            what Edward was reasoning about.
    """
    async with async_session() as session:
        conversation = ConversationModel(
            id=conversation_id,
            title=title[:100],  # Truncate to 100 chars
            source=source,
            channel=channel,
            message_count=1,
            initial_message=initial_message,
            context_pack=context_pack,
        )
        session.add(conversation)
        await session.commit()
        await session.refresh(conversation)
        return conversation


async def get_conversations(
    limit: int = 50,
    offset: int = 0,
    include_scheduled: bool = False,
    group: Optional[str] = None,
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

        if group == "edwards":
            # Thoughts arrive every half hour and would crowd threads,
            # workers and scheduled runs off any single newest-first page.
            # Take a page *per source* and merge so each sidebar section
            # has something to show; offset/limit apply per source.
            merged: List[ConversationModel] = []
            any_more = False
            for src, where in _edwards_pages():
                if src == "heartbeat":
                    # Threads order by the latest *message*, not updated_at:
                    # a retitle or a nudge bumps updated_at without anyone
                    # having said anything. last_activity_at rides along on
                    # the row for the API to expose.
                    from services.database import HeartbeatEventModel, IMessageChatConversationModel

                    last_evt = (
                        select(func.max(HeartbeatEventModel.created_at))
                        .where(HeartbeatEventModel.chat_identifier == IMessageChatConversationModel.chat_identifier)
                        .correlate(IMessageChatConversationModel)
                        .scalar_subquery()
                    )
                    activity = func.coalesce(last_evt, ConversationModel.updated_at).label("last_activity")
                    q = (
                        select(ConversationModel, activity)
                        .outerjoin(
                            IMessageChatConversationModel,
                            IMessageChatConversationModel.conversation_id == ConversationModel.id,
                        )
                        .where(ConversationModel.source == src)
                        .order_by(desc(activity))
                        .limit(limit + 1)
                        .offset(offset)
                    )
                    pairs = list((await session.execute(q)).all())
                    if len(pairs) > limit:
                        any_more = True
                    rows = []
                    for conv, act in pairs[:limit]:
                        conv.last_activity_at = act
                        rows.append(conv)
                    merged.extend(rows)
                    continue
                q = (
                    select(ConversationModel)
                    .where(where)
                    .order_by(desc(ConversationModel.updated_at))
                    .limit(limit + 1)
                    .offset(offset)
                )
                rows = list((await session.execute(q)).scalars().all())
                if len(rows) > limit:
                    any_more = True
                merged.extend(rows[:limit])
            # Edward's long-lived threads (the surfaces pulse, the mind) are
            # pinned on the first page whatever their recency.
            if offset == 0:
                for pinned_id in PINNED_CONVERSATION_IDS:
                    if any(c.id == pinned_id for c in merged):
                        continue
                    pinned = (await session.execute(
                        select(ConversationModel).where(ConversationModel.id == pinned_id)
                    )).scalar_one_or_none()
                    if pinned is not None:
                        merged.append(pinned)
            merged.sort(
                key=lambda c: getattr(c, "last_activity_at", None) or c.updated_at or datetime.min,
                reverse=True,
            )
            return merged, any_more

        # Hide Edward's own thoughts unless explicitly requested (see
        # is_conversation_visible for the rule).
        if not include_scheduled:
            query = query.where(_visible_filter())
        gf = _group_filter(group)
        if gf is not None:
            query = query.where(gf)

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
    include_scheduled: bool = False,
    group: Optional[str] = None,
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
        if not include_scheduled and group != "edwards":
            filters.append(_visible_filter())
        gf = _group_filter(group)
        if gf is not None:
            filters.append(gf)

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


async def clear_user_notified(conversation_id: str) -> bool:
    """Clear the notified_user flag (mark as read)."""
    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel).where(ConversationModel.id == conversation_id)
        )
        conversation = result.scalar_one_or_none()
        if not conversation or not conversation.notified_user:
            return False

        conversation.notified_user = False
        await session.commit()
        return True
