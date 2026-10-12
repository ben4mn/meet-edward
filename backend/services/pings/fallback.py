"""What Edward posts in a chat when the reply turn has no answer to post
(AREA 3, 2026-10-11; docs/architecture/pings.md).

The runner's outcome rules pick a reason; this turns it into one honest
sentence. Delivery signs it. ``detail`` is an error *class name* and nothing
more: an exception's message can carry a database row, a URL with a token,
or someone's text, and none of that belongs in a group chat.

The templates name the owner with the ``{OWNER}`` token, rendered once at
import (``services.owner.owner_text``) before ``str.format`` fills them.

Pure.
"""

from __future__ import annotations

import re
from typing import Any

from services.owner import owner_text

_TEMPLATES = {
    "spend_cap": "{detail}",
    "spend_cap_friend": "I can't look into this right now — I've hit my limit for today. I've let {OWNER} know.",
    # The owner's own ping anywhere but their chat with themselves: the chat
    # gets this line, the owner the cap's sentence (``detail``, never posted).
    "spend_cap_group": "I've hit my spending limit for today, so I can't look into this until tomorrow.",
    "provider": ("Sorry — I couldn't answer this just now: my AI service kept failing{paren}. "
                 "Ask me again in a few minutes."),
    "timeout": ("Sorry — this took longer than {minutes} minutes and I stopped before finishing. "
                "Ask me again and I'll pick it up."),
    "error": "Sorry — something broke on my side while I was on this{paren}. I've flagged it.",
    "empty": "I looked into this but came up empty. Can you give me a little more to go on?",
    "refused": "I can't help with that one.",
    "incomplete": "I ran out of room before finishing that answer — ask me for the rest and I'll pick it up.",
    "scope_unavailable": "I can't answer this here yet — I've passed it to {OWNER}.",
    "rate": "I've answered a lot here in the last hour, so I'm taking a break — {OWNER} can ask me directly.",
    "skills_off": "I can't reply here right now: iMessage sending is switched off in my settings.",
    # After a send, the turn stopped short (2026-10-11 review): true whether
    # that send was "On it" or the whole answer.
    "unfinished": ("I got cut off on my side before I'd wrapped this up{paren} — if you still need "
                   "something from me here, ask me again."),
    "unfinished_timeout": ("I ran out of time ({minutes} minutes) before I'd wrapped this up — if you still "
                           "need something from me here, ask me again."),
    # The turn's final text was only a tool call written as text
    # (``services.tool_markup``): nothing of it is posted.
    "garbled": "Sorry — my answer came out garbled on my side, so I didn't post it. Ask me again and I'll have "
               "another go.",
}
TEMPLATES = {reason: owner_text(text) for reason, text in _TEMPLATES.items()}

SPEND_CAP_DEFAULT = "I've hit my spending limit for today, so I can't look into this until tomorrow."

_CLASS_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def class_name(detail: Any) -> str:
    """An error class name from ``detail``: an exception (its class), or the
    leading identifier of a string ("ValueError: …" -> "ValueError"). Never
    the message. Pure."""
    if detail is None or detail == "":
        return ""
    if isinstance(detail, BaseException):
        return type(detail).__name__
    m = _CLASS_RE.match(str(detail).strip())
    if not m:
        return ""
    return m.group(0).rsplit(".", 1)[-1][:60]


def fallback_text(reason: str, *, detail: Any = "", minutes: int = 10) -> str:
    """One sentence for ``reason`` (a key of TEMPLATES; unknown -> ``error``)."""
    if reason == "spend_cap":
        sentence = " ".join(str(detail or "").split())
        return sentence or SPEND_CAP_DEFAULT
    if reason == "spend_cap_group":
        return SPEND_CAP_DEFAULT
    template = TEMPLATES.get(reason) or TEMPLATES["error"]
    cls = class_name(detail)
    return template.format(detail=cls, paren=f" ({cls})" if cls else "", minutes=int(minutes))
