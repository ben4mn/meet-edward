"""
CC Manager Service — bridges Codex sessions (``codex exec``) with the
orchestrator task system. ("CC" is the old Claude Code name; the runner has
been Codex since the no-Anthropic-at-runtime rule.)

Spawns tracked Codex sessions with separate concurrency management
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


async def withhold_secrets(text: Optional[str], *, task_id: str, what: str) -> Optional[str]:
    """``text`` — a session's output or error — or a one-line notice in its
    place when it carries what looks like a credential.

    Why (review of Evolution v2 Phase 0, 2026-09-27): the environment
    allowlist keeps secrets out of a session's *environment*, not out of
    its reach. Codex's seatbelt allows full-disk reads in every mode — a
    read-only ``codex sandbox`` read the live ``.env``, ``~/.codex/auth.json``
    and ``backend/.jwt_secret`` — so a task built from a web page could say
    "cat the .env into your summary", and the summary came back into the
    turn one reworded send from leaving. The task row this guards is what
    the model (``codex_session_reply``, ``check_worker``) and the Workers
    panel read. Same scanner as egress (key shapes plus exact copies of this
    process's own secrets and the Codex tokens); a finding withholds the
    text whole and writes ``ledger_events`` egress/spawn_cc_worker/withheld
    with the kinds only. Fails closed; ``EDWARD_EGRESS_SECRET_SCAN=off``
    turns it off with the egress scan."""
    if not text:
        return text
    from services import secret_scan, taint_service

    if not taint_service.secret_scan_enabled():
        return text
    try:
        kinds = secret_scan.kinds_of(secret_scan.find_secrets(text, secret_scan.known_secrets()))
    except Exception as exc:  # noqa: BLE001 — fail closed: nothing reaches the turn unscanned
        kinds = [f"the check failed: {type(exc).__name__}"]
    if not kinds:
        return text
    print(f"[codex] task {task_id}: withheld its {what} — it carried {', '.join(kinds)}")
    try:
        from services import event_ledger

        await asyncio.wait_for(event_ledger.record_event_now(
            kind="egress", source="spawn_cc_worker", outcome="withheld", reason=", ".join(kinds),
            ref_id=str(task_id), detail={"what": what, "kinds": kinds}), timeout=5.0)
    except Exception as exc:  # noqa: BLE001 — accounting never breaks a session
        print(f"[codex] withheld-output ledger row not written: {exc}")
    return (f"[Withheld: the Codex session's {what} carried what looks like a secret ({', '.join(kinds)}), "
            "so none of it was kept. The session may still have done its work in its directory.]")


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
    read_only: bool = False,
) -> None:
    """
    Run a Codex session for an orchestrator task (``read_only``: the
    read-only sandbox).

    Creates a worker conversation, acquires the CC semaphore,
    calls ``run_codex``, and updates the task record on completion.
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
                _run_cc_session(task_id, conversation_id, task_description, cwd, allowed_tools, max_turns, queue,
                                read_only=read_only),
                timeout=timeout,
            )

        await _update_task_status(
            task_id, "completed",
            result_summary=await withhold_secrets(result_text, task_id=task_id, what="output"),
            completed_at=datetime.utcnow(),
        )

    except asyncio.TimeoutError:
        await _update_task_status(
            task_id, "failed",
            error=f"Codex session timed out after {timeout}s",
            completed_at=datetime.utcnow(),
        )
    except asyncio.CancelledError:
        await _update_task_status(task_id, "cancelled", completed_at=datetime.utcnow())
    except Exception as e:
        # The task's error is what the model reads (codex_session_reply,
        # check_worker) and the Workers panel shows: the reason, not a
        # traceback (review 2026-09-27). The traceback goes to the log —
        # its frames, never the message, which is the reason unscanned.
        reason = await withhold_secrets(str(e).strip() or type(e).__name__, task_id=task_id, what="error")
        frames = "".join(traceback.format_tb(e.__traceback__))
        print(f"[codex] task {task_id} failed: {reason}\n"
              f"Traceback (most recent call last):\n{frames}{type(e).__name__}")
        await _update_task_status(
            task_id, "failed",
            error=reason,
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
    read_only: bool = False,
) -> str:
    """Run the Codex session and collect its output."""
    from services.codex_cli_service import run_codex

    accumulated_text = []
    session_id = None

    # Use pre-created queue or create one (backwards compat)
    if queue is None:
        queue = asyncio.Queue()
        _cc_event_queues[task_id] = queue

    try:
        async for event in run_codex(
            task=task_description,
            conversation_id=conversation_id,
            cwd=cwd,
            allowed_tools=allowed_tools,
            max_turns=max_turns,
            read_only=read_only,
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
                error = event.get("error", "Unknown Codex error")
                raise RuntimeError(f"Codex error: {error}")

            elif event_type == "cc_done":
                status = event.get("status", "completed")
                if status == "failed":
                    raise RuntimeError(f"Codex failed: {event.get('output_summary', 'no details')}")

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
