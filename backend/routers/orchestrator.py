"""REST API endpoints for the orchestrator system."""

import asyncio
import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import StreamingResponse

from services.orchestrator_models import (
    OrchestratorConfigResponse,
    OrchestratorConfigUpdate,
    OrchestratorTaskResponse,
    OrchestratorStatusResponse,
    WorkerMessageRequest,
)

router = APIRouter()


def _task_to_response(task: dict) -> OrchestratorTaskResponse:
    """Convert a task dict to a Pydantic response."""
    return OrchestratorTaskResponse(
        id=task["id"],
        parent_conversation_id=task["parent_conversation_id"],
        worker_conversation_id=task.get("worker_conversation_id"),
        task_description=task["task_description"],
        task_type=task.get("task_type", "internal_worker"),
        model=task.get("model"),
        status=task.get("status", "pending"),
        context_mode=task.get("context_mode", "scoped"),
        result_summary=task.get("result_summary"),
        error=task.get("error"),
        timeout_seconds=task.get("timeout_seconds", 300),
        cc_session_id=task.get("cc_session_id"),
        started_at=task.get("started_at"),
        completed_at=task.get("completed_at"),
        created_at=task.get("created_at", ""),
        updated_at=task.get("updated_at", ""),
    )


@router.get("/orchestrator/status")
async def get_status():
    """Get orchestrator status including config, active count, and recent tasks."""
    from services.orchestrator_service import get_config, get_active_count, list_tasks

    config = await get_config()
    active_count = get_active_count()
    recent = await list_tasks(limit=10)

    return OrchestratorStatusResponse(
        config=OrchestratorConfigResponse(**config),
        active_count=active_count,
        recent_tasks=[_task_to_response(t) for t in recent],
    )


@router.get("/orchestrator/tasks")
async def get_tasks(
    parent_conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List orchestrator tasks with optional filters."""
    from services.orchestrator_service import list_tasks

    tasks = await list_tasks(
        parent_conversation_id=parent_conversation_id,
        status=status,
        limit=limit,
        offset=offset,
    )
    return [_task_to_response(t) for t in tasks]


@router.get("/orchestrator/tasks/{task_id}")
async def get_task(task_id: str):
    """Get a single orchestrator task."""
    from services.orchestrator_service import get_task as _get_task

    task = await _get_task(task_id)
    if "error" in task:
        raise HTTPException(status_code=404, detail=task["error"])
    return _task_to_response(task)


@router.post("/orchestrator/tasks/{task_id}/cancel")
async def cancel_task(task_id: str):
    """Cancel a running orchestrator task."""
    from services.orchestrator_service import cancel_task as _cancel

    result = await _cancel(task_id)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return _task_to_response(result)


@router.post("/orchestrator/tasks/{task_id}/message")
async def send_message(task_id: str, body: WorkerMessageRequest):
    """Send a follow-up message to a completed worker."""
    from services.orchestrator_service import send_message_to_worker

    result = await send_message_to_worker(task_id, body.message)
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result


@router.get("/orchestrator/config")
async def get_config():
    """Get orchestrator configuration."""
    from services.orchestrator_service import get_config as _get_config

    config = await _get_config()
    return OrchestratorConfigResponse(**config)


@router.patch("/orchestrator/config")
async def update_config(body: OrchestratorConfigUpdate):
    """Update orchestrator configuration."""
    from services.orchestrator_service import update_config as _update

    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No updates provided")

    config = await _update(updates)
    return OrchestratorConfigResponse(**config)


@router.get("/orchestrator/tasks/{task_id}/events")
async def stream_task_events(task_id: str):
    """
    Stream live CC events for a running task via SSE.

    Returns a text/event-stream of CC events (cc_text, cc_tool_use, cc_tool_result, etc.).
    Ends when the task completes (stream_end sentinel) or after 660s timeout.
    Returns 404 if the task has no active event queue.
    """
    from services.cc_manager_service import get_event_queue

    queue = get_event_queue(task_id)
    if queue is None:
        raise HTTPException(status_code=404, detail="No active event stream for this task")

    async def event_generator():
        timeout = 660  # 11 minutes max
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=timeout)
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'event_type': 'stream_end', 'reason': 'timeout'})}\n\n"
                    break

                yield f"data: {json.dumps(event)}\n\n"

                if event.get("event_type") == "stream_end":
                    break
        except asyncio.CancelledError:
            pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
