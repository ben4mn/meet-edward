"""
WhatsApp listener for Edward's heartbeat system.

With the Baileys bridge, @edward detection is push-based via webhook
(POST /api/webhook/whatsapp). No polling needed.

This module provides thread-fetching utilities used by triage_service
to build context before responding to WhatsApp mentions.
"""

import re


MENTION_PATTERN = re.compile(r"@edward\b", re.IGNORECASE)


async def get_chat_thread(chat_id: str, limit: int = 15) -> list[dict]:
    """Fetch recent messages from a WhatsApp chat for thread context."""
    from services.whatsapp_bridge_client import get_chat_messages, is_available

    if not is_available():
        return []

    try:
        return await get_chat_messages(chat_id, limit)
    except Exception as e:
        print(f"[Heartbeat] WhatsApp chat thread fetch error: {e}")
        return []


def format_chat_thread(messages: list[dict]) -> str:
    """Format WhatsApp messages into a readable thread context string.

    Note: The bridge is logged in as the user's WhatsApp account, so
    fromMe=true means the USER sent it (or Edward sent it via the bridge).
    We label fromMe messages as "You" (the user), not "Edward".
    """
    lines = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        sender = msg.get("sender_name") or msg.get("from") or "Unknown"
        is_from_me = msg.get("fromMe") or msg.get("is_from_me", False)
        if is_from_me:
            sender = "You"
        text = msg.get("text") or msg.get("body") or "(media/no text)"
        lines.append(f"{sender}: {text}")
    return "\n".join(lines)


# Start/stop are no-ops — the webhook handles everything

async def start_whatsapp_listener(config) -> None:
    """No-op: WhatsApp mentions arrive via bridge webhook."""
    print("[Heartbeat] WhatsApp listener: webhook mode (no polling needed)")


async def stop_whatsapp_listener() -> None:
    """No-op."""
    pass


def get_whatsapp_listener_status() -> str:
    """Status reflects bridge connection, not a poll loop."""
    from services.whatsapp_bridge_client import is_available
    return "running" if is_available() else "stopped"
