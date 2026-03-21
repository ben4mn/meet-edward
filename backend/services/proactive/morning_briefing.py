"""
Morning briefing service for Edward.

Generates and delivers a daily briefing with calendar events,
unread messages, open loops, and recent entity activity.
"""

import asyncio
from datetime import datetime, timedelta

from services.timezone_utils import now_local

_task: asyncio.Task | None = None
_briefing_time: str = "08:00"
_channel: str = "push"


def is_morning_briefing_running() -> bool:
    return _task is not None and not _task.done()


async def start_morning_briefing(config) -> None:
    global _task, _briefing_time, _channel
    if _task is not None:
        return
    _briefing_time = getattr(config, "morning_briefing_time", "08:00") or "08:00"
    _channel = getattr(config, "morning_briefing_channel", "push") or "push"
    _task = asyncio.create_task(_briefing_loop())
    print(f"Morning briefing started (time={_briefing_time}, channel={_channel})")


async def stop_morning_briefing() -> None:
    global _task
    if _task is None:
        return
    _task.cancel()
    try:
        await _task
    except asyncio.CancelledError:
        pass
    _task = None
    print("Morning briefing stopped")


def _seconds_until_next_fire(target_time: str) -> float:
    """Calculate seconds until the next occurrence of target_time (HH:MM)."""
    now = now_local()
    try:
        hour, minute = map(int, target_time.split(":"))
    except (ValueError, AttributeError):
        hour, minute = 8, 0

    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)

    return (target - now).total_seconds()


async def _briefing_loop() -> None:
    while True:
        wait_seconds = _seconds_until_next_fire(_briefing_time)
        print(f"[BRIEFING] Next briefing in {wait_seconds/3600:.1f}h")
        await asyncio.sleep(wait_seconds)

        try:
            await _generate_and_deliver()
        except Exception as e:
            print(f"[BRIEFING] Generation failed: {e}")

        # Sleep at least 60s to avoid double-fire
        await asyncio.sleep(60)


async def _generate_and_deliver() -> None:
    """Generate briefing content and deliver via configured channel."""
    from services.llm_utils import build_background_llm, make_background_system_message
    from langchain_core.messages import HumanMessage

    parts = []

    # 1. Calendar events (today)
    try:
        from services.mcp_client import is_apple_available, get_apple_mcp_tools
        if is_apple_available():
            tools = get_apple_mcp_tools()
            cal_tool = None
            for t in tools:
                if "calendar" in t.name.lower():
                    cal_tool = t
                    break
            if cal_tool:
                now = now_local()
                end_of_day = now.replace(hour=23, minute=59, second=59)
                result = await cal_tool.ainvoke({
                    "operation": "list",
                    "fromDate": now.isoformat(),
                    "toDate": end_of_day.isoformat(),
                })
                content = result.content if hasattr(result, "content") else str(result)
                if content and not content.lower().startswith("no events"):
                    parts.append(f"Today's Calendar:\n{content[:1000]}")
    except Exception as e:
        print(f"[BRIEFING] Calendar fetch failed: {e}")

    # 2. Open loops
    try:
        from services.open_loop_service import get_active_open_loops
        loops = await get_active_open_loops(limit=5)
        if loops:
            loop_text = "\n".join(f"- {l.description}" for l in loops)
            parts.append(f"Open Loops ({len(loops)} active):\n{loop_text}")
    except Exception as e:
        print(f"[BRIEFING] Open loops fetch failed: {e}")

    # 3. Unread messages summary (from heartbeat)
    try:
        from services.heartbeat.heartbeat_service import get_pending_briefing
        hb = await get_pending_briefing()
        if hb:
            parts.append(f"Recent Activity:\n{hb[:500]}")
    except Exception as e:
        print(f"[BRIEFING] Heartbeat fetch failed: {e}")

    if not parts:
        print("[BRIEFING] No content for briefing, skipping")
        return

    # Generate summary via LLM
    llm = build_background_llm(max_tokens=512)
    raw_content = "\n\n".join(parts)

    try:
        response = await asyncio.wait_for(
            llm.ainvoke([
                make_background_system_message(
                    "You are Edward, generating a concise morning briefing. "
                    "Summarize the key items for the day in 3-5 bullet points. "
                    "Be warm but efficient. Start with the most important item."
                ),
                HumanMessage(content=f"Generate a morning briefing from this data:\n\n{raw_content}"),
            ]),
            timeout=10.0,
        )
        briefing_text = response.content
        if isinstance(briefing_text, list):
            briefing_text = " ".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in briefing_text
            )
    except Exception as e:
        print(f"[BRIEFING] LLM summarization failed: {e}")
        briefing_text = raw_content[:500]

    # Deliver
    await _deliver(briefing_text.strip())
    print(f"[BRIEFING] Delivered morning briefing via {_channel}")


async def _deliver(text: str) -> None:
    """Deliver briefing via configured channel."""
    if _channel == "push":
        try:
            from services.push_service import send_push_to_all
            await send_push_to_all(
                title="Good morning",
                body=text[:200],
            )
        except Exception as e:
            print(f"[BRIEFING] Push delivery failed: {e}")
    elif _channel == "imessage":
        try:
            from services.imessage_service import send_imessage
            await send_imessage(text, recipient=None)
        except Exception as e:
            print(f"[BRIEFING] iMessage delivery failed: {e}")
    elif _channel == "chat":
        try:
            import uuid
            from services.graph import get_graph
            from services.graph.streaming import chat_with_memory
            from services.database import async_session
            from services.database import ConversationModel
            from services.settings_service import get_settings

            conversation_id = str(uuid.uuid4())
            async with async_session() as session:
                conv = ConversationModel(
                    id=conversation_id,
                    title="Morning Briefing",
                    source="scheduled_event",
                )
                session.add(conv)
                await session.commit()

            graph = await get_graph()
            settings = await get_settings()

            await chat_with_memory(
                message=f"[MORNING BRIEFING] Generate and present the morning briefing. Here is the raw data:\n\n{text}",
                conversation_id=conversation_id,
                system_prompt=settings.system_prompt if settings else "",
                model=settings.model if settings else "claude-sonnet-4-5-20250929",
                temperature=settings.temperature if settings else 0.7,
                graph=graph,
            )
        except Exception as e:
            print(f"[BRIEFING] Chat delivery failed: {e}")
