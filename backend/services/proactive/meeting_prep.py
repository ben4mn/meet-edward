"""
Meeting prep service for Edward.

Hooks into the heartbeat calendar listener to detect approaching meetings,
resolve attendee entities, and generate preparation briefs.
"""

import asyncio
import json
import re
from datetime import datetime, timedelta
from typing import Optional

from services.timezone_utils import now_local

_task: asyncio.Task | None = None
_advance_minutes: int = 30
_prepped_events: set = set()


def is_meeting_prep_running() -> bool:
    return _task is not None and not _task.done()


async def start_meeting_prep(config) -> None:
    global _task, _advance_minutes
    if _task is not None:
        return
    _advance_minutes = getattr(config, "meeting_prep_advance_minutes", 30) or 30
    _task = asyncio.create_task(_prep_loop())
    print(f"Meeting prep started (advance={_advance_minutes}m)")


async def stop_meeting_prep() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    print("Meeting prep stopped")


async def _prep_loop() -> None:
    while True:
        try:
            await _check_upcoming_events()
        except Exception as e:
            print(f"[MEETING_PREP] Check failed: {e}")
        await asyncio.sleep(120)


async def _check_upcoming_events() -> None:
    from services.mcp_client import is_apple_available, get_apple_mcp_tools

    if not is_apple_available():
        return

    tools = get_apple_mcp_tools()
    cal_tool = None
    for t in tools:
        if "calendar" in t.name.lower():
            cal_tool = t
            break

    if not cal_tool:
        return

    now = now_local()
    window_end = now + timedelta(minutes=_advance_minutes)

    try:
        result = await cal_tool.ainvoke({
            "operation": "list",
            "fromDate": now.isoformat(),
            "toDate": window_end.isoformat(),
        })
    except Exception as e:
        print(f"[MEETING_PREP] Calendar query failed: {e}")
        return

    raw = result.content if hasattr(result, "content") else str(result)
    if not raw or (isinstance(raw, str) and raw.lower().startswith("no events")):
        return

    try:
        if isinstance(raw, str):
            events_data = json.loads(raw)
        elif isinstance(raw, list):
            events_data = raw
        elif isinstance(raw, dict):
            events_data = raw.get("events", raw.get("items", [raw]))
        else:
            events_data = json.loads(str(raw))

        if isinstance(events_data, dict):
            events_data = [events_data]
    except (json.JSONDecodeError, TypeError):
        return

    for event in events_data:
        if not isinstance(event, dict):
            continue

        event_id = event.get("id") or event.get("event_id") or event.get("calendarItemIdentifier", "")
        title = event.get("title") or event.get("summary") or event.get("name") or "Untitled"

        dedup_key = f"{event_id}:{title}"
        if dedup_key in _prepped_events:
            continue

        await _generate_prep_brief(event, title)
        _prepped_events.add(dedup_key)

        if len(_prepped_events) > 100:
            _prepped_events.clear()


async def _generate_prep_brief(event: dict, title: str) -> None:
    from services.llm_utils import build_background_llm, make_background_system_message
    from langchain_core.messages import HumanMessage
    from services.entity_service import detect_entities_in_text, get_entity_memories

    attendees = event.get("attendees", event.get("participants", []))
    attendee_names = []
    if isinstance(attendees, list):
        for a in attendees:
            if isinstance(a, str):
                attendee_names.append(a)
            elif isinstance(a, dict):
                attendee_names.append(a.get("name", a.get("email", "")))
    elif isinstance(attendees, str):
        attendee_names = [n.strip() for n in attendees.split(",")]

    search_text = f"{title} {' '.join(attendee_names)}"
    entities = await detect_entities_in_text(search_text)

    entity_context = []
    for entity in entities[:5]:
        memories = await get_entity_memories(entity.id, limit=3)
        if memories:
            mem_text = "\n".join(
                f"  - {m['context_snippet'] or m['memory_id']}"
                for m in memories
            )
            entity_context.append(f"{entity.name} ({entity.entity_type}):\n{mem_text}")

    context_text = "\n\n".join(entity_context) if entity_context else "No relevant entity context found."

    llm = build_background_llm(max_tokens=512)

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                make_background_system_message(
                    "Generate a concise meeting prep brief. Include key context about attendees "
                    "and topics. Be direct and useful. 3-5 bullet points max."
                ),
                HumanMessage(content=f"""Meeting: {title}
Attendees: {', '.join(attendee_names) if attendee_names else 'Unknown'}
Event details: {json.dumps(event, default=str)[:500]}

Known context about attendees/topics:
{context_text}

Generate a brief prep summary."""),
            ]),
            timeout=10.0,
        )

        brief_text = response.content
        if isinstance(brief_text, list):
            brief_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in brief_text
            )
        brief_text = brief_text.strip()

    except Exception as e:
        print(f"[MEETING_PREP] LLM generation failed: {e}")
        brief_text = f"Upcoming: {title}"

    try:
        from services.push_service import send_push_to_all
        await send_push_to_all(
            title=f"Meeting prep: {title[:50]}",
            body=brief_text[:200],
        )
        print(f"[MEETING_PREP] Sent prep brief for: {title}")
    except Exception as e:
        print(f"[MEETING_PREP] Push delivery failed: {e}")
