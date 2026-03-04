"""
WhatsApp listener for Edward's heartbeat system.

Polls WhatsApp via MCP tools (whatsapp-mcp-lifeosai) for @edward mentions.
Uses a scan-first strategy: one `get_recent_chats` call per cycle to check
lastMessage previews, then drills into specific chats only when a mention
is detected.

Strategy:
  1. Call `connect` once to establish Baileys session (persistent MCP session)
  2. Call `get_recent_chats` (1 MCP call) to scan lastMessage for @edward
  3. Only call `get_chat_messages` for chats where a mention was found
  4. Store only mention-bearing messages (not all messages)
  5. Trigger immediate triage on @edward detection

Fallback: if `get_recent_chats` fails (known "Invalid time value" bug),
poll a watchlist of group chats seeded from `get_groups`.
"""

import asyncio
import json
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select

from services.database import async_session, HeartbeatEventModel

MENTION_PATTERN = re.compile(r"@edward\b", re.IGNORECASE)

_listener_task: asyncio.Task | None = None
_poll_interval: int = 30
_last_poll_time: Optional[datetime] = None
_connected: bool = False

# Scan-first state
_get_recent_chats_works: bool = True  # Optimistic, flips on first failure
_last_retry_time: Optional[datetime] = None
_RETRY_INTERVAL = 300  # Retry get_recent_chats every 5 minutes

# Watchlist for fallback mode: chat_id → name
_watchlist: dict[str, str] = {}
_watchlist_seeded: bool = False


def _find_tool(name_suffix: str):
    """Find a WhatsApp MCP tool by exact suffix match."""
    from services.mcp_client import get_whatsapp_mcp_tools, is_whatsapp_available

    if not is_whatsapp_available():
        return None

    target = f"whatsapp_{name_suffix}"
    for t in get_whatsapp_mcp_tools():
        if t.name.lower() == target:
            return t
    return None


def _parse_mcp_result(result) -> any:
    """Parse MCP tool result into Python object.

    langchain-mcp-adapters tools use response_format="content_and_artifact",
    so ainvoke() returns a tuple (content, artifacts). Handle that plus
    plain strings and objects.
    """
    raw = result
    # Unpack content_and_artifact tuple
    if isinstance(raw, tuple) and len(raw) >= 1:
        raw = raw[0]
    if hasattr(raw, "content"):
        raw = raw.content
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw or raw.lower().startswith("error"):
            return None
        return json.loads(raw)
    if isinstance(raw, (dict, list)):
        return raw
    return json.loads(str(raw))


# ===== Connection =====

async def _ensure_connected() -> bool:
    """Connect Baileys if not already connected. Uses persistent MCP session."""
    global _connected

    if _connected:
        return True

    connect_tool = _find_tool("connect")
    if not connect_tool:
        print("[Heartbeat] WhatsApp: connect tool not found")
        return False

    try:
        print("[Heartbeat] WhatsApp: connecting Baileys...")
        result = await asyncio.wait_for(connect_tool.ainvoke({}), timeout=60)
        parsed = _parse_mcp_result(result)
        if isinstance(parsed, dict):
            status = parsed.get("status", "")
            if status in ("connected", "already_connected"):
                user = parsed.get("user") or {}
                print(f"[Heartbeat] WhatsApp: connected as {user.get('name', 'unknown')} ({user.get('id', '')})")
                _connected = True
                return True
            print(f"[Heartbeat] WhatsApp connect failed: {parsed}")
        return False
    except asyncio.TimeoutError:
        print("[Heartbeat] WhatsApp: connect timed out after 60s")
        return False
    except Exception as e:
        print(f"[Heartbeat] WhatsApp connect error: {e}")
        return False


# ===== Primary path: get_recent_chats scan =====

async def _try_get_recent_chats() -> list[dict] | None:
    """Try get_recent_chats. Returns parsed list or None on failure."""
    tool = _find_tool("get_recent_chats")
    if not tool:
        return None
    try:
        result = await asyncio.wait_for(tool.ainvoke({"limit": 30}), timeout=15)
        parsed = _parse_mcp_result(result)
        if isinstance(parsed, list):
            return parsed
        return None
    except Exception as e:
        print(f"[Heartbeat] WhatsApp get_recent_chats error: {e}")
        return None


async def _scan_chats_for_mentions(chats: list[dict], since: datetime) -> None:
    """Scan lastMessage from get_recent_chats for @edward. Drill into matches."""
    chats_with_mentions = []

    for chat in chats:
        if not isinstance(chat, dict):
            continue
        last_msg = chat.get("lastMessage") or ""
        chat_id = chat.get("id") or ""
        chat_name = chat.get("name") or chat_id

        if not chat_id:
            continue

        if MENTION_PATTERN.search(last_msg):
            chats_with_mentions.append((chat_id, chat_name))
            # Add to watchlist so fallback mode can also monitor this chat
            _watchlist[chat_id] = chat_name

    if not chats_with_mentions:
        return

    print(f"[Heartbeat] WhatsApp: @edward detected in {len(chats_with_mentions)} chat(s), fetching details")

    stored = 0
    for chat_id, chat_name in chats_with_mentions:
        count = await _fetch_and_store_mentions(chat_id, chat_name, since)
        stored += count

    if stored > 0:
        print(f"[Heartbeat] WhatsApp: stored {stored} mention event(s), triggering triage")
        from services.heartbeat.heartbeat_service import trigger_immediate_triage
        await trigger_immediate_triage()


async def _fetch_and_store_mentions(chat_id: str, chat_name: str, since: datetime) -> int:
    """Fetch messages from a specific chat, store only @edward mentions."""
    messages_tool = _find_tool("get_chat_messages")
    if not messages_tool:
        return 0

    try:
        result = await asyncio.wait_for(
            messages_tool.ainvoke({"chat_id": chat_id, "limit": 10}), timeout=10
        )
        msgs_data = _parse_mcp_result(result)
    except Exception as e:
        print(f"[Heartbeat] WhatsApp: error fetching {chat_name}: {e}")
        return 0

    if not isinstance(msgs_data, list):
        return 0

    stored = 0
    async with async_session() as session:
        for msg in msgs_data:
            if not isinstance(msg, dict):
                continue

            text = msg.get("text") or msg.get("body") or msg.get("content") or ""

            # Only store @edward mentions
            if not MENTION_PATTERN.search(text):
                continue

            msg_id = msg.get("id") or ""
            sender = msg.get("from") or chat_id
            is_from_me = msg.get("fromMe", False)

            # Timestamp filter — skip old messages
            timestamp = msg.get("timestamp")
            if timestamp:
                try:
                    if isinstance(timestamp, (int, float)):
                        msg_time = datetime.fromtimestamp(timestamp, tz=timezone.utc)
                    else:
                        msg_time = datetime.fromisoformat(
                            str(timestamp).replace("Z", "+00:00")
                        )
                        if msg_time.tzinfo is None:
                            msg_time = msg_time.replace(tzinfo=timezone.utc)
                    if msg_time < since:
                        continue
                except (ValueError, TypeError, OSError):
                    pass

            if not msg_id:
                msg_id = f"{chat_id}_{hash(text)}"

            source_id = f"whatsapp:{msg_id}"

            # Dedup check
            existing = await session.execute(
                select(HeartbeatEventModel.id).where(
                    HeartbeatEventModel.source_id == source_id
                )
            )
            if existing.scalar_one_or_none():
                continue

            hb_event = HeartbeatEventModel(
                source="whatsapp",
                event_type="message_received",
                sender=sender,
                contact_name=chat_name,
                chat_identifier=chat_id,
                chat_name=chat_name,
                summary=text[:200],
                raw_data=json.dumps(msg),
                source_id=source_id,
                is_from_user=bool(is_from_me),
            )
            session.add(hb_event)
            stored += 1

        if stored:
            await session.commit()

    return stored


# ===== Fallback path: watchlist polling =====

async def _seed_watchlist_from_groups() -> None:
    """Seed the watchlist with group chats (@mentions are most common in groups)."""
    global _watchlist_seeded

    if _watchlist_seeded:
        return
    _watchlist_seeded = True

    groups_tool = _find_tool("get_groups")
    if not groups_tool:
        return

    try:
        result = await asyncio.wait_for(groups_tool.ainvoke({}), timeout=15)
        parsed = _parse_mcp_result(result)
        if isinstance(parsed, list):
            for group in parsed:
                if isinstance(group, dict) and group.get("id"):
                    _watchlist[group["id"]] = group.get("subject") or group.get("name") or group["id"]
            if _watchlist:
                print(f"[Heartbeat] WhatsApp: seeded watchlist with {len(_watchlist)} groups")
    except Exception as e:
        print(f"[Heartbeat] WhatsApp: group seed failed (non-critical): {e}")
        _watchlist_seeded = False  # Allow retry next cycle


async def _poll_watchlist(since: datetime) -> None:
    """Fallback: poll only watchlisted chats for @mentions."""
    global _get_recent_chats_works, _last_retry_time

    # Periodically retry get_recent_chats
    now = datetime.now(timezone.utc)
    if (_last_retry_time is None
            or (now - _last_retry_time).total_seconds() > _RETRY_INTERVAL):
        _last_retry_time = now
        chats = await _try_get_recent_chats()
        if chats is not None:
            _get_recent_chats_works = True
            print("[Heartbeat] WhatsApp: get_recent_chats recovered, switching back to scan mode")
            await _scan_chats_for_mentions(chats, since)
            return

    if not _watchlist:
        await _seed_watchlist_from_groups()

    if not _watchlist:
        return

    stored = 0
    for chat_id, chat_name in list(_watchlist.items()):
        count = await _fetch_and_store_mentions(chat_id, chat_name, since)
        stored += count

    if stored > 0:
        print(f"[Heartbeat] WhatsApp: stored {stored} mention event(s) from watchlist, triggering triage")
        from services.heartbeat.heartbeat_service import trigger_immediate_triage
        await trigger_immediate_triage()


# ===== Main poll loop =====

async def _poll_once() -> None:
    """Single poll: scan get_recent_chats for @mentions, drill into matches only."""
    global _last_poll_time, _get_recent_chats_works

    if not await _ensure_connected():
        return

    now = datetime.now(timezone.utc)

    # On first poll, only look back 5 minutes
    if _last_poll_time is None:
        since = now - timedelta(minutes=5)
    else:
        since = _last_poll_time
    _last_poll_time = now

    # Primary path: single get_recent_chats call
    if _get_recent_chats_works:
        chats = await _try_get_recent_chats()
        if chats is not None:
            await _scan_chats_for_mentions(chats, since)
            return
        # Failed — switch to watchlist mode
        _get_recent_chats_works = False
        print("[Heartbeat] WhatsApp: get_recent_chats failed, switching to watchlist mode")

    # Fallback path: poll watchlist
    await _poll_watchlist(since)


# ===== Thread context (used by triage service) =====

async def get_chat_thread(chat_id: str, limit: int = 15) -> list[dict]:
    """Fetch recent messages from a WhatsApp chat for thread context."""
    tool = _find_tool("get_chat_messages")
    if tool is None:
        return []

    try:
        result = await tool.ainvoke({"chat_id": chat_id, "limit": limit})
        parsed = _parse_mcp_result(result)
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return parsed.get("messages", parsed.get("items", []))
        return []
    except Exception as e:
        print(f"[Heartbeat] WhatsApp chat thread fetch error: {e}")
        return []


def format_chat_thread(messages: list[dict]) -> str:
    """Format WhatsApp messages into a readable thread context string."""
    lines = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        sender = msg.get("from") or "Unknown"
        is_from_me = msg.get("fromMe", False)
        if is_from_me:
            sender = "Edward"
        text = msg.get("text") or msg.get("body") or "(media/no text)"
        lines.append(f"{sender}: {text}")
    return "\n".join(lines)


# ===== Start/Stop =====

async def _listener_loop() -> None:
    """Main polling loop."""
    print(f"[Heartbeat] WhatsApp listener started (poll every {_poll_interval}s)")

    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[Heartbeat] WhatsApp poll error: {e}")
        await asyncio.sleep(_poll_interval)


async def start_whatsapp_listener(config) -> None:
    """Start the WhatsApp listener with config-driven poll interval."""
    global _listener_task, _poll_interval

    print("[Heartbeat] WhatsApp listener start requested")

    if _listener_task is not None:
        print("[Heartbeat] WhatsApp listener already running, skipping")
        return

    from services.mcp_client import is_whatsapp_available, get_whatsapp_mcp_tools

    available = is_whatsapp_available()
    tool_count = len(get_whatsapp_mcp_tools())
    print(f"[Heartbeat] WhatsApp MCP available={available}, tools={tool_count}")

    if not available:
        print("[Heartbeat] WhatsApp listener skipped: WhatsApp MCP not available")
        return

    _poll_interval = getattr(config, "whatsapp_poll_seconds", 30)

    _listener_task = asyncio.create_task(_listener_loop())
    print(f"[Heartbeat] WhatsApp listener task created (poll every {_poll_interval}s)")


async def stop_whatsapp_listener() -> None:
    """Stop the WhatsApp listener."""
    global _listener_task, _connected
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
    _connected = False
    print("[Heartbeat] WhatsApp listener stopped")


def get_whatsapp_listener_status() -> str:
    """Get the current listener status."""
    if _listener_task is None:
        return "stopped"
    if _listener_task.done():
        return "error"
    return "running"
