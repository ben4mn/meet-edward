"""
LangChain tools for WhatsApp Bridge integration.

These tools wrap the WhatsApp bridge REST API and are bound to the LLM
when the whatsapp_mcp skill is enabled. They send/read as the user via
Baileys (not Twilio).
"""

import json

from langchain_core.tools import tool


@tool
async def whatsapp_send_message(chat_id: str, message: str) -> str:
    """Send a WhatsApp message to a specific chat as the user.

    Use this to reply in WhatsApp conversations. The message is sent from
    the user's own WhatsApp account (not a bot number).

    Args:
        chat_id: The WhatsApp chat ID (e.g. '1234567890@s.whatsapp.net' for
                 individuals or 'groupid@g.us' for groups). This is provided
                 in heartbeat event context.
        message: The text message to send.
    """
    from services.whatsapp_bridge_client import send_message, is_available

    if not is_available():
        return "WhatsApp bridge is not connected."
    try:
        result = await send_message(chat_id, message)
        msg_id = result.get("message_id", "")
        return f"Message sent to {chat_id} (id: {msg_id})"
    except Exception as e:
        return f"Failed to send WhatsApp message: {e}"


@tool
async def whatsapp_get_chat_messages(chat_id: str, limit: int = 15) -> str:
    """Get recent messages from a WhatsApp chat.

    Args:
        chat_id: The WhatsApp chat ID.
        limit: Maximum number of messages to retrieve (default: 15).
    """
    from services.whatsapp_bridge_client import get_chat_messages, is_available

    if not is_available():
        return "WhatsApp bridge is not connected."
    try:
        messages = await get_chat_messages(chat_id, limit)
        if not messages:
            return "No messages found in this chat."
        lines = []
        for msg in messages:
            sender = msg.get("sender_name") or msg.get("from", "Unknown")
            if msg.get("fromMe"):
                sender = "You"
            text = msg.get("text", "(no text)")
            lines.append(f"{sender}: {text}")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to get chat messages: {e}"


@tool
async def whatsapp_get_contacts() -> str:
    """List WhatsApp contacts."""
    from services.whatsapp_bridge_client import get_contacts, is_available

    if not is_available():
        return "WhatsApp bridge is not connected."
    try:
        contacts = await get_contacts()
        if not contacts:
            return "No contacts found."
        lines = [f"- {c.get('name', c.get('id', '?'))} ({c.get('id', '')})" for c in contacts[:50]]
        total = len(contacts)
        if total > 50:
            lines.append(f"... and {total - 50} more")
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to get contacts: {e}"


@tool
async def whatsapp_get_groups() -> str:
    """List WhatsApp groups the user is part of."""
    from services.whatsapp_bridge_client import get_groups, is_available

    if not is_available():
        return "WhatsApp bridge is not connected."
    try:
        groups = await get_groups()
        if not groups:
            return "No groups found."
        lines = [
            f"- {g.get('name', g.get('id', '?'))} ({g.get('participants', '?')} members) [id: {g.get('id', '')}]"
            for g in groups
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to get groups: {e}"


@tool
async def whatsapp_get_recent_chats(limit: int = 30) -> str:
    """List recent WhatsApp chats with last message preview.

    Args:
        limit: Maximum number of chats to retrieve (default: 30).
    """
    from services.whatsapp_bridge_client import get_recent_chats, is_available

    if not is_available():
        return "WhatsApp bridge is not connected."
    try:
        chats = await get_recent_chats(limit)
        if not chats:
            return "No recent chats found."
        lines = [
            f"- {c.get('name', c.get('id', '?'))} [id: {c.get('id', '')}]"
            for c in chats
        ]
        return "\n".join(lines)
    except Exception as e:
        return f"Failed to get recent chats: {e}"


# Collected tools for binding
WHATSAPP_BRIDGE_TOOLS = [
    whatsapp_send_message,
    whatsapp_get_chat_messages,
    whatsapp_get_contacts,
    whatsapp_get_groups,
    whatsapp_get_recent_chats,
]
