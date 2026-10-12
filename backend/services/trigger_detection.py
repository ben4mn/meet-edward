"""Single source of truth for trigger-message classification.

The heartbeat triage service emits several distinct prefix variants for
its internal trigger messages (``MENTION_TRIGGER``, ``ACT_TRIGGER``,
``REPLY_TRIGGER``, plus the calendar/email/gmail paths). The frontend
needs every one of them flagged so the chat UI renders them as a
compact TriggerCard instead of leaking the raw multi-line template
into the conversation as a user message.

Keeping the prefix logic here means new trigger variants (briefings in
phase 3, proactive nudges in phase 5) only need to register their
prefix once.
"""

from __future__ import annotations

from typing import Optional

# Add new prefixes here. Match is case-insensitive and tolerant of leading
# whitespace.
_HEARTBEAT_PREFIXES = (
    "[heartbeat event]",
    "[heartbeat — @mention]",
    "[heartbeat - @mention]",  # ASCII dash variant
    "[heartbeat — follow-up reply]",
    "[heartbeat - follow-up reply]",
    "[heartbeat - calendar]",
    "[heartbeat — calendar]",
    "[heartbeat - email]",
    "[heartbeat — email]",
    "[heartbeat - gmail]",
    "[heartbeat — gmail]",
)

_SCHEDULED_PREFIXES = (
    "[scheduled event]",
)


def detect_trigger(content: str) -> tuple[bool, Optional[str]]:
    """Classify whether ``content`` is one of Edward's internal trigger
    messages.

    Returns ``(is_trigger, trigger_type)`` where ``trigger_type`` is
    ``"heartbeat"``, ``"scheduled_event"``, or ``None``.
    """
    if not content:
        return False, None
    head = content.lstrip().lower()
    for prefix in _HEARTBEAT_PREFIXES:
        if head.startswith(prefix):
            return True, "heartbeat"
    for prefix in _SCHEDULED_PREFIXES:
        if head.startswith(prefix):
            return True, "scheduled_event"
    return False, None
