"""
CC Manager Service — bridges Claude Code with the orchestrator task system.

Spawns tracked Claude Code sessions with separate concurrency management
(independent semaphore from internal workers).
"""

import asyncio
import traceback
import uuid
from datetime import datetime
from typing import Dict, Optional

from services.database import async_session, OrchestratorTaskModel
from services.orchestrator_service import _update_task_status, _update_task_field


# Separate semaphore for CC sessions (independent from worker semaphore)
_cc_semaphore: Optional[asyncio.Semaphore] = None
_cc_tasks: Dict[str, asyncio.Task] = {}

# Event queues for live streaming CC events to the frontend
_cc_event_queues: Dict[str, asyncio.Queue] = {}


def get_event_queue(task_id: str) -> Optional[asyncio.Queue]:
    """Get the event queue for a running CC task, if any."""
    return _cc_event_queues.get(task_id)


def init_cc_semaphore(max_sessions: int = 2) -> None:
    """Initialize or recreate the CC semaphore."""
    global _cc_semaphore
    _cc_semaphore = asyncio.Semaphore(max_sessions)


async def spawn_cc_for_task(
    task_id: str,
    parent_conversation_id: str,
    task_description: str,
    cwd: Optional[str] = None,
    allowed_tools: Optional[list] = None,
    max_turns: int = 25,
    timeout: int = 600,
) -> None:
    """
    Run a Claude Code session for an orchestrator task.

    Creates a worker conversation, acquires the CC semaphore,
    calls run_claude_code(), and updates the task record on completion.
    """
    global _cc_semaphore

    if _cc_semaphore is None:
        init_cc_semaphore()

    # Pre-create event queue so callers can drain it immediately after spawn
    queue: asyncio.Queue = asyncio.Queue()
    _cc_event_queues[task_id] = queue

    try:
        from services.conversation_service import create_conversation

        # Create worker conversation
        conversation_id = str(uuid.uuid4())
        title = f"[CC] {task_description[:50]}{'...' if len(task_description) > 50 else ''}"
        await create_conversation(conversation_id, title=title, source="orchestrator_worker")

        # Update task with worker conversation ID
        await _update_task_field(task_id, "worker_conversation_id", conversation_id)
        await _update_task_status(task_id, "running", started_at=datetime.utcnow())

        async with _cc_semaphore:
            result_text = await asyncio.wait_for(
                _run_cc_session(task_id, conversation_id, task_description, cwd, allowed_tools, max_turns, queue),
                timeout=timeout,
            )

        await _update_task_status(
            task_id, "completed",
            result_summary=result_text,
            completed_at=datetime.utcnow(),
        )

    except asyncio.TimeoutError:
        await _update_task_status(
            task_id, "failed",
            error=f"CC session timed out after {timeout}s",
            completed_at=datetime.utcnow(),
        )
    except asyncio.CancelledError:
        await _update_task_status(task_id, "cancelled", completed_at=datetime.utcnow())
    except Exception as e:
        tb = traceback.format_exc()
        await _update_task_status(
            task_id, "failed",
            error=f"{str(e)}\n{tb}",
            completed_at=datetime.utcnow(),
        )
    finally:
        _cc_tasks.pop(task_id, None)


async def _run_cc_session(
    task_id: str,
    conversation_id: str,
    task_description: str,
    cwd: Optional[str],
    allowed_tools: Optional[list],
    max_turns: int,
    queue: asyncio.Queue = None,
) -> str:
    """Execute the Claude Code session and collect output."""
    from services.claude_code_service import run_claude_code

    accumulated_text = []
    session_id = None

    # Use pre-created queue or create one (backwards compat)
    if queue is None:
        queue = asyncio.Queue()
        _cc_event_queues[task_id] = queue

    try:
        async for event in run_claude_code(
            task=task_description,
            conversation_id=conversation_id,
            cwd=cwd,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
        ):
            event_type = event.get("event_type")

            # Forward event to queue for SSE streaming
            await queue.put(event)

            if event_type == "cc_started":
                session_id = event.get("session_id")
                if session_id:
                    await _update_task_field(task_id, "cc_session_id", session_id)

            elif event_type == "cc_text":
                accumulated_text.append(event.get("text", ""))

            elif event_type == "cc_error":
                error = event.get("error", "Unknown CC error")
                raise RuntimeError(f"Claude Code error: {error}")

            elif event_type == "cc_done":
                status = event.get("status", "completed")
                if status == "failed":
                    raise RuntimeError(f"Claude Code failed: {event.get('output_summary', 'no details')}")

        result = "".join(accumulated_text)
        return result[:2000] if result else "CC session completed with no text output"
    finally:
        # Send sentinel event and clean up
        await queue.put({"event_type": "stream_end"})
        _cc_event_queues.pop(task_id, None)


async def cancel_cc_task(task_id: str) -> bool:
    """Cancel a running CC task."""
    asyncio_task = _cc_tasks.get(task_id)
    if asyncio_task:
        asyncio_task.cancel()
        try:
            await asyncio_task
        except (asyncio.CancelledError, Exception):
            pass
        return True
    return False


def get_active_cc_count() -> int:
    """Count of non-done CC asyncio tasks."""
    return sum(1 for t in _cc_tasks.values() if not t.done())
