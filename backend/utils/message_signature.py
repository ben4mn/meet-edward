"""
Message signature enforcement for Edward's outgoing messages.

Ensures every outgoing message is signed with "- Edward 🤖" so recipients
know the message came from the AI assistant, not from Ben directly.
"""

import re


SIGNATURE = "- Edward 🤖"

# Patterns that count as an existing signature (case-insensitive, at end of message)
_SIGNATURE_PATTERNS = [
    r"-\s*Edward\s*🤖\s*$",
    r"-\s*Edward\s*$",
    r"—\s*Edward\s*🤖\s*$",
    r"—\s*Edward\s*$",
]


def ensure_message_signature(message: str) -> str:
    """
    Ensure the message ends with Edward's signature.

    If the message already contains a recognizable Edward signature at the end,
    it is returned as-is. Otherwise, appends "\\n\\n- Edward 🤖".

    Args:
        message: The outgoing message text

    Returns:
        The message with signature guaranteed to be present
    """
    stripped = message.rstrip()

    for pattern in _SIGNATURE_PATTERNS:
        if re.search(pattern, stripped, re.IGNORECASE):
            return message

    return f"{stripped}\n\n{SIGNATURE}"
