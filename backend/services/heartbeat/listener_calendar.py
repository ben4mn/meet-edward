"""
Calendar listener for Edward's heartbeat system.

Polls Apple Calendar via MCP tools for upcoming events.
Writes HeartbeatEventModel rows to PostgreSQL, deduped by source_id.
"""

import asyncio
import json
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy import select

from services.database import async_session, HeartbeatEventModel

_listener_task: asyncio.Task | None = None
_poll_interval: int = 300
_lookahead_minutes: int = 30


def _find_calendar_tool():
    """Find the calendar list/upcoming tool from Apple MCP tools."""
    from services.mcp_client import get_apple_mcp_tools, is_apple_available

    if not is_apple_available():
        return None

    tools = get_apple_mcp_tools()
    # Look for a tool that reads calendar events — tool may be named simply "calendar"
    for t in tools:
        name = t.name.lower()
        if "calendar" in name:
            return t
    return None


async def _poll_once() -> None:
    """Single poll iteration — fetch upcoming calendar events."""
    tool = _find_calendar_tool()
    if tool is None:
        return

    now = datetime.now(timezone.utc)
    lookahead_end = now + timedelta(minutes=_lookahead_minutes)

    try:
        result = await tool.ainvoke({
            "operation": "list",
            "fromDate": now.isoformat(),
            "toDate": lookahead_end.isoformat(),
        })
    except Exception as e:
        print(f"[Heartbeat] Calendar poll error calling MCP tool: {e}")
        return

    # Parse response — MCP tools may return ToolMessage content strings
    events_data = []
    try:
        raw = result
        # Extract string content from ToolMessage or similar wrappers
        if hasattr(result, "content"):
            raw = result.content
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw or raw.lower().startswith("no events"):
                print(f"[Heartbeat] Calendar poll: {raw or 'empty response'}")
                return
            parsed = json.loads(raw)
        elif isinstance(raw, dict):
            parsed = raw
        elif isinstance(raw, list):
            parsed = raw
        else:
            parsed = json.loads(str(raw))

        if isinstance(parsed, list):
            events_data = parsed
        elif isinstance(parsed, dict):
            # Could be wrapped in an "events" key or similar
            events_data = parsed.get("events", parsed.get("items", []))
            if not isinstance(events_data, list):
                events_data = [parsed]
    except (json.JSONDecodeError, TypeError, ValueError) as e:
        print(f"[Heartbeat] Calendar response parse error: {e} | raw type={type(result).__name__} preview={str(result)[:200]}")
        return

    if not events_data:
        print("[Heartbeat] Calendar poll: no upcoming events in window")
        return

    stored = 0
    async with async_session() as session:
        for event in events_data:
            if not isinstance(event, dict):
                continue

            # Extract event fields — handle various key naming conventions
            event_id = (
                event.get("id")
                or event.get("event_id")
                or event.get("calendarItemIdentifier")
                or ""
            )
            title = (
                event.get("title")
                or event.get("summary")
                or event.get("name")
                or "Untitled"
            )
            start_str = (
                event.get("start_date")
                or event.get("startDate")
                or event.get("start")
                or ""
            )
            end_str = (
                event.get("end_date")
                or event.get("endDate")
                or event.get("end")
                or ""
            )
            is_all_day = event.get("is_all_day", event.get("isAllDay", False))

            # Build source_id for dedup
            source_id = f"calendar:{event_id}:{start_str}"

            # Dedup check
            existing = await session.execute(
                select(HeartbeatEventModel.id).where(
                    HeartbeatEventModel.source_id == source_id
                )
            )
            if existing.scalar_one_or_none():
                continue

            # Determine if event is starting soon (within 15 minutes)
            starting_soon = False
            try:
                if start_str:
                    start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)
                    minutes_until = (start_dt - now).total_seconds() / 60
                    if 0 <= minutes_until <= 15:
                        starting_soon = True
            except (ValueError, TypeError):
                pass

            # Format summary
            if starting_soon:
                summary = f"[STARTING SOON] {title} — starts at {start_str}"
            elif start_str and end_str:
                summary = f"{title} — {start_str} to {end_str}"
            else:
                summary = title

            hb_event = HeartbeatEventModel(
                source="calendar",
                event_type="calendar_upcoming",
                sender=None,
                contact_name=None,
                chat_identifier=None,
                chat_name=None,
                summary=summary[:200],
                raw_data=json.dumps(event),
                source_id=source_id,
                is_from_user=False,
            )
            session.add(hb_event)
            stored += 1

        if stored:
            await session.commit()
            print(f"[Heartbeat] Calendar listener: stored {stored} new events")

    # Fast-track starting-soon events → trigger immediate triage
    if any(
        isinstance(e, dict)
        and _is_starting_soon(e, now)
        for e in events_data
    ):
        print("[Heartbeat] Starting-soon calendar event detected — triggering immediate triage")
        from services.heartbeat.heartbeat_service import trigger_immediate_triage
        await trigger_immediate_triage()


def _is_starting_soon(event: dict, now: datetime) -> bool:
    """Check if a calendar event starts within 15 minutes."""
    start_str = (
        event.get("start_date")
        or event.get("startDate")
        or event.get("start")
        or ""
    )
    if not start_str:
        return False
    try:
        start_dt = datetime.fromisoformat(start_str.replace("Z", "+00:00"))
        if start_dt.tzinfo is None:
            start_dt = start_dt.replace(tzinfo=timezone.utc)
        minutes_until = (start_dt - now).total_seconds() / 60
        return 0 <= minutes_until <= 15
    except (ValueError, TypeError):
        return False


async def _listener_loop() -> None:
    """Main polling loop."""
    print(f"[Heartbeat] Calendar listener started (poll every {_poll_interval}s, lookahead {_lookahead_minutes}m)")

    while True:
        try:
            await _poll_once()
        except Exception as e:
            print(f"[Heartbeat] Calendar poll error: {e}")
        await asyncio.sleep(_poll_interval)


async def start_calendar_listener(config) -> None:
    """Start the calendar listener with config-driven poll interval and lookahead."""
    global _listener_task, _poll_interval, _lookahead_minutes

    if _listener_task is not None:
        return

    from services.mcp_client import is_apple_available

    if not is_apple_available():
        print("[Heartbeat] Calendar listener skipped: Apple MCP not available")
        return

    _poll_interval = getattr(config, "calendar_poll_seconds", 300)
    _lookahead_minutes = getattr(config, "calendar_lookahead_minutes", 30)

    _listener_task = asyncio.create_task(_listener_loop())


async def stop_calendar_listener() -> None:
    """Stop the calendar listener."""
    global _listener_task
    if _listener_task is None:
        return
    _listener_task.cancel()
    try:
        await _listener_task
    except asyncio.CancelledError:
        pass
    _listener_task = None
    print("[Heartbeat] Calendar listener stopped")


def get_calendar_listener_status() -> str:
    """Get the current listener status."""
    if _listener_task is None:
        return "stopped"
    if _listener_task.done():
        return "error"
    return "running"
