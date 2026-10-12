"""Edward-initiated thought conversations.

The design rule: every push Edward sends should land on a
real conversation where you can read his reasoning and reply. This
service is the single primitive that creates those conversations.

A thought conversation:

* has ``source="edward_thought"`` so it stands apart from user-initiated
  chats and heartbeat reply threads
* stores Edward's narrative in ``initial_message`` so the conversations
  API can synthesize it as a first assistant message even before any
  real checkpoint state exists
* shows up in the sidebar with the short ``title`` (a heading like "3
  pings while you were busy"); the full narrative appears as the first
  message in the chat view
"""

from __future__ import annotations

import logging
import uuid
from typing import Optional

from services.conversation_service import create_conversation

logger = logging.getLogger(__name__)

GENERIC_TITLES = {"", "edward", "edward's thought", "thought"}
TITLE_MAX = 64


def thought_title(title, narrative) -> str:
    """A sidebar title that says what the thought was.

    The explicit title wins unless it is missing or the generic "Edward";
    then the narrative's first sentence, trimmed on a word boundary.
    """
    t = (title or "").strip()
    if t and t.lower() not in GENERIC_TITLES:
        return t[:100]
    text = " ".join((narrative or "").split())
    if not text:
        return "Edward's thought"
    import re

    first = re.split(r"(?<=[.!?])\s+", text, maxsplit=1)[0]
    # "Three threads worth clearing: - Rowan…" → stop before the list.
    first = re.split(r":\s+(?:[-•*]\s|\d+[.)]\s)", first, maxsplit=1)[0]
    first = first.rstrip(".!?:").strip()
    if len(first) <= TITLE_MAX:
        return first or "Edward's thought"
    cut = first[: TITLE_MAX - 1]
    if " " in cut:
        cut = cut[: cut.rfind(" ")]
    return cut.rstrip(" ,;:") + "…"


async def create_thought_conversation(
    title: str,
    narrative: str,
    *,
    source: str = "edward_thought",
    related_chat_identifier: Optional[str] = None,
    context_pack: Optional[dict] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """Create a fresh conversation that the owner can tap into from a push.

    ``context_pack`` is the signal pack (wake-up's inputs, e.g. the
    actual unread messages) so when the owner replies, the chat node can
    recover what Edward was reasoning about. Without it the thread is
    amnesiac the moment the owner types.

    ``conversation_id`` writes it under an id handed out earlier — a wake
    allocates its journal entry's id before the turn so a push or note it
    sends can link to the entry (``thought_journal.begin_entry``,
    2026-09-25). A fresh uuid otherwise.

    Returns the new conversation_id. Never raises — on persistence
    failure returns a UUID for which the conversation doesn't exist;
    the caller should treat that as a soft failure and either fall back
    to no URL or retry later.
    """
    import json as _json

    conversation_id = conversation_id or str(uuid.uuid4())
    # Short title for sidebar; full narrative for the chat body.
    safe_title = thought_title(title, narrative)
    narrative_text = (narrative or "").strip()[:4000]
    pack_json: Optional[str] = None
    if context_pack is not None:
        try:
            pack_json = _json.dumps(context_pack, default=str)[:16000]
        except (TypeError, ValueError):
            pack_json = None

    try:
        await create_conversation(
            conversation_id,
            title=safe_title,
            source=source,
            initial_message=narrative_text or None,
            context_pack=pack_json,
        )
    except TypeError:
        # Backwards-compat path: older create_conversation signature
        # doesn't accept initial_message. Fall back to writing the
        # column ourselves after create.
        await create_conversation(
            conversation_id, title=safe_title, source=source
        )
        try:
            from sqlalchemy import update
            from services.database import (
                ConversationModel,
                async_session,
            )
            async with async_session() as session:
                await session.execute(
                    update(ConversationModel)
                    .where(ConversationModel.id == conversation_id)
                    .values(
                        initial_message=narrative_text or None,
                        context_pack=pack_json,
                    )
                )
                await session.commit()
        except Exception as exc:
            logger.warning(
                "thought conversation: initial_message update failed: %s", exc
            )
    except Exception as exc:
        logger.exception("thought conversation create failed: %s", exc)
        return conversation_id

    return conversation_id


def thought_conversation_url(conversation_id: str) -> str:
    """Helper so callers don't have to remember the deep-link shape."""
    return f"/chat?c={conversation_id}"
