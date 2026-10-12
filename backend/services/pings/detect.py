"""What counts as a ping (AREA 3, 2026-10-11; docs/architecture/pings.md). Pure.

A ping is someone asking Edward something in a chat, where his answer
belongs in that chat. First match wins:

1. Edward's own send echoed back (``is_echo``) is never a ping.
2. The owner in their self-chat (from-me, 1:1, the chat is one of their
   own handles): ``owner_direct``, no ``@`` needed. **Breaker:** while an
   Edward send into that chat is unconfirmed (its chat.db row was never
   found, ``unconfirmed_send``), a self-chat line without ``@edward`` is not
   a ping, so Edward cannot answer himself in a loop.
3. ``@edward`` anywhere in the whole text, from anyone, in any chat — the
   owner's own included (the identity filter used to drop it): ``mention``.
4. An inline reply to one of Edward's own bubbles (``thread_originator_guid``
   is a guid the transport recorded): ``reply_to_edward``. Must-answer only
   from the owner: to friends that bubble came from the owner's number.

Tapbacks and other associated messages (stickers on a bubble, edits) are
never pings: a tapback on "@edward look" reads 'Loved “@edward look”'. The
listener does not ingest them; ``classify`` refuses them as well.

Not pings: "Edward Jones", "Thanks Edward!", "foo@edward.com", "edward, …"
(the vocative is P1 and off by default), and a link: ``youtube.com/@edward``,
a URL balloon's ``[link: "Edward (@edward) / X" …]`` preview or a photo's
description in its ``[image: … attachment:N — "…"]`` placeholder (review
2026-10-11). The listener's caption (the text without placeholders) is what
is read when the message carries one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import AbstractSet, Any, Mapping, Optional

from services.pings.hooks import chat_key, must_answer

MENTION_RE = re.compile(r"(?<![\w.@/])@edward\b", re.I)
_URL_RE = re.compile(r"(?:https?://|www\.)\S+", re.I)
# The listener's media placeholders: ``[image: IMG.heic attachment:449321]``,
# ``[link: "<title>" (<site>) attachment:<rowid>]``, with a description inside
# once the describer has one.
_PLACEHOLDER_RE = re.compile(r"\[[^\[\]]*attachment:\d+[^\[\]]*\]")

KINDS = ("mention", "owner_direct", "reply_to_edward")


@dataclass(frozen=True)
class PingVerdict:
    kind: str            # mention | owner_direct | reply_to_edward
    from_owner: bool
    must_answer: bool    # pings.hooks.must_answer(kind, from_owner)


def has_mention(text: Optional[str]) -> bool:
    """``@edward`` addressed to Edward: never inside a link or a media
    placeholder. Pure."""
    if not text:
        return False
    plain = _URL_RE.sub(" ", _PLACEHOLDER_RE.sub(" ", str(text)))
    return MENTION_RE.search(plain) is not None


def is_self_chat(msg: Mapping[str, Any], owner_handles: AbstractSet[str]) -> bool:
    """The owner's own 1:1 with themselves: from-me, not a group, and the
    chat is one of their handles (compared exactly, then by ``chat_key`` so a
    phone spelled with or without the country code still matches)."""
    if not owner_handles or not msg.get("is_from_me") or msg.get("is_group"):
        return False
    chat = str(msg.get("chat_identifier") or "").strip()
    if not chat:
        return False
    if chat in owner_handles:
        return True
    key = chat_key(chat)
    return bool(key) and any(key == chat_key(h) for h in owner_handles if h)


def _verdict(kind: str, from_owner: bool) -> PingVerdict:
    return PingVerdict(kind=kind, from_owner=from_owner, must_answer=must_answer(kind, from_owner))


def classify(msg: Mapping[str, Any], *, is_echo: bool, owner_handles: AbstractSet[str],
             edward_guids: AbstractSet[str], unconfirmed_send: bool) -> Optional[PingVerdict]:
    """The ping verdict for one chat.db message, or None for an ordinary one."""
    if is_echo:
        return None
    if int(msg.get("associated_message_type") or 0) != 0:
        return None
    caption = msg.get("caption")
    text = str(caption if caption is not None else (msg.get("text") or ""))
    # ADAPT: Edward sends and reads iMessages as the owner's own Apple ID.
    #   How it works here: Messages on this Mac is signed in to the owner's Apple ID, so chat.db's
    #   is_from_me means "the owner wrote it" (Edward's own sends are told apart by ROWID and signature).
    #   To adapt: with a separate Apple ID for Edward, from-me is Edward only; ask Claude to decide
    #   from_owner by the sender handle (owner.owner_handles(), EDWARD_OWNER_HANDLES) instead.
    from_owner = bool(msg.get("is_from_me"))
    mentioned = has_mention(text)
    if is_self_chat(msg, owner_handles):
        if unconfirmed_send and not mentioned:
            return None
        return _verdict("mention" if unconfirmed_send else "owner_direct", True)
    if mentioned:
        return _verdict("mention", from_owner)
    thread = msg.get("thread_originator_guid")
    if thread and edward_guids and str(thread) in edward_guids:
        return _verdict("reply_to_edward", from_owner)
    return None
