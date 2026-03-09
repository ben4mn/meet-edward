"""
Scheduler service for Edward.

Runs an in-process asyncio loop that polls for due scheduled events
and executes them by invoking Edward's chat_with_memory() path.

Each event creates a new visible conversation (with title from event
description) so users can click the push notification to see Edward's
response. The [SCHEDULED EVENT] trigger message is filtered out in the
conversations router so only Edward's response is visible.
"""

import asyncio
import traceback
import uuid
from datetime import datetime

from services.scheduled_events_service import (
    get_due_events,
    mark_event_completed,
    mark_event_failed,
    _format_local,
)

_scheduler_task: asyncio.Task | None = None
_POLL_INTERVAL_SECONDS = 30


async def start_scheduler() -> None:
    """Start the scheduler polling loop."""
    global _scheduler_task
    if _scheduler_task is not None:
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())
    print("Scheduler started (polling every 30s)")


async def stop_scheduler() -> None:
    """Stop the scheduler polling loop."""
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None
    print("Scheduler stopped")


async def _scheduler_loop() -> None:
    """Main polling loop — runs forever until cancelled."""
    while True:
        try:
            await _process_due_events()
        except Exception as e:
            print(f"Scheduler poll error: {e}")
        await asyncio.sleep(_POLL_INTERVAL_SECONDS)


async def _process_due_events() -> None:
    """Fetch due events and fire each concurrently."""
    events = await get_due_events()
    if not events:
        return

    print(f"Scheduler: {len(events)} due event(s) found")
    tasks = [asyncio.create_task(_execute_event(event)) for event in events]
    await asyncio.gather(*tasks, return_exceptions=True)


def _build_scheduler_system_prompt(base_system_prompt: str, event) -> str:
    """Build a system prompt for scheduled event execution.

    Wraps the user's base system prompt with scheduler-specific instructions
    that enforce the correct delivery channel.
    """
    # Channel-specific delivery rules
    channel = (event.delivery_channel or "").strip().lower()

    if channel == "sms":
        delivery_rules = (
            "- You MUST deliver via SMS using send_message(channel=\"sms\") or send_sms().\n"
            "- Do NOT just respond in chat — the user is not watching.\n"
            "- If SMS fails, report the failure but do not silently swallow it."
        )
    elif channel == "imessage":
        delivery_rules = (
            "- You MUST deliver via iMessage using send_imessage() or send_message(channel=\"imessage\").\n"
            "- Do NOT just respond in chat — the user is not watching.\n"
            "- If iMessage fails, report the failure but do not silently swallow it."
        )
    elif channel == "chat":
        delivery_rules = (
            "- Respond normally in chat. No external message is needed.\n"
            "- The response will be stored in the event record for later review."
        )
    else:
        # No channel specified — infer from description
        delivery_rules = (
            "- If the description asks you to send a message/text someone, use the appropriate messaging tool.\n"
            "- Otherwise, respond normally in chat. The response will be stored in the event record."
        )

    return (
        f"{base_system_prompt}\n\n"
        f"---\n"
        f"You are now executing a scheduled event autonomously. The user is NOT in an active conversation — "
        f"they will not see your chat response unless they check the event record.\n\n"
        f"DELIVERY RULES:\n"
        f"{delivery_rules}\n\n"
        f"After executing, provide a brief confirmation of what you did."
    )


async def _execute_event(event) -> None:
    """Execute a single scheduled event by calling chat_with_memory."""
    from services.graph import chat_with_memory
    from services.graph.tools import set_current_conversation_id
    from services.settings_service import get_settings
    from services.conversation_service import create_conversation

    try:
        settings = await get_settings()

        # Create a real conversation so user can see Edward's response in UI
        conversation_id = str(uuid.uuid4())

        # Title from event description (first 50 chars)
        title = event.description[:50] + ("..." if len(event.description) > 50 else "")
        await create_conversation(conversation_id, title=title, source="scheduled_event")

        set_current_conversation_id(conversation_id)

        # Scheduler-specific system prompt wrapping the base prompt
        system_prompt = _build_scheduler_system_prompt(settings.system_prompt, event)

        # Trigger message (delivery enforcement is in the system prompt)
        # Include conversation_id so Edward can link to it in push notifications
        now = datetime.now()
        trigger_message = (
            f"[SCHEDULED EVENT] The following event is now due.\n"
            f"Description: '{event.description}'\n"
            f"Scheduled at: {_format_local(event.scheduled_at) if event.scheduled_at else 'N/A'}\n"
            f"Current time: {now.strftime('%A, %B %d, %Y at %I:%M %p')}\n"
            f"Conversation ID: {conversation_id} (use url='/?c={conversation_id}' in push notifications to link here)\n"
            f"Execute the described action now."
        )

        response = await chat_with_memory(
            message=trigger_message,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            model=settings.model,
            temperature=settings.temperature,
        )

        result_summary = str(response)[:500] if response else "No response"
        await mark_event_completed(event.id, result_summary)
        print(f"Scheduler: Event {event.id} completed — {result_summary[:100]}")

    except Exception as e:
        error_msg = f"{str(e)}\n{traceback.format_exc()}"
        await mark_event_failed(event.id, error_msg[:1000])
        print(f"Scheduler: Event {event.id} failed — {str(e)}")
