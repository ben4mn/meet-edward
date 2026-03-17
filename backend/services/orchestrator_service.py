"""
Orchestrator service for Edward.

Spawns lightweight worker agents (mini-Edwards) that run within Edward's
own process via chat_with_memory(). Workers have full tool access, memory
retrieval, and state persistence. Worker conversations appear in the
sidebar with a distinct icon.

Phase 1: Core infrastructure + internal workers.
Phase 2: Claude Code integration — tracked CC sessions with separate concurrency.
"""

import asyncio
import time
import traceback
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import select, update
from services.database import async_session, OrchestratorTaskModel, OrchestratorConfigModel


# Model shorthand mapping
MODEL_SHORTHAND = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-6",
}

# Global state
_worker_tasks: Dict[str, asyncio.Task] = {}
_worker_semaphore: Optional[asyncio.Semaphore] = None

# Config cache with 5s TTL
_config_cache: Optional[dict] = None
_config_cache_ts: float = 0
_CONFIG_TTL = 5


def _resolve_model(model: Optional[str], default: str) -> str:
    """Resolve model shorthand to full model ID."""
    if not model:
        return default
    return MODEL_SHORTHAND.get(model.lower(), model)


def _task_to_dict(task: OrchestratorTaskModel) -> dict:
    """Convert a SQLAlchemy model to a serializable dict."""
    return {
        "id": task.id,
        "parent_conversation_id": task.parent_conversation_id,
        "worker_conversation_id": task.worker_conversation_id,
        "task_description": task.task_description,
        "task_type": getattr(task, "task_type", None) or "internal_worker",
        "model": task.model,
        "status": task.status,
        "context_mode": task.context_mode,
        "result_summary": task.result_summary,
        "error": task.error,
        "timeout_seconds": task.timeout_seconds,
        "cc_session_id": getattr(task, "cc_session_id", None),
        "started_at": (task.started_at.isoformat() + "Z") if task.started_at else None,
        "completed_at": (task.completed_at.isoformat() + "Z") if task.completed_at else None,
        "created_at": (task.created_at.isoformat() + "Z") if task.created_at else "",
        "updated_at": (task.updated_at.isoformat() + "Z") if task.updated_at else "",
    }


async def get_config() -> dict:
    """Get orchestrator config with caching."""
    global _config_cache, _config_cache_ts

    now = time.time()
    if _config_cache and (now - _config_cache_ts) < _CONFIG_TTL:
        return _config_cache

    async with async_session() as session:
        result = await session.execute(
            select(OrchestratorConfigModel).where(OrchestratorConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()
        if not config:
            config = OrchestratorConfigModel(id="default")
            session.add(config)
            await session.commit()
            await session.refresh(config)

        _config_cache = {
            "enabled": config.enabled,
            "max_concurrent_workers": config.max_concurrent_workers,
            "max_concurrent_cc_sessions": getattr(config, "max_concurrent_cc_sessions", 2) or 2,
            "default_worker_model": config.default_worker_model,
            "default_worker_timeout": config.default_worker_timeout,
        }
        _config_cache_ts = now
        return _config_cache


async def update_config(updates: dict) -> dict:
    """Update orchestrator config."""
    global _config_cache, _config_cache_ts, _worker_semaphore

    async with async_session() as session:
        result = await session.execute(
            select(OrchestratorConfigModel).where(OrchestratorConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()
        if not config:
            config = OrchestratorConfigModel(id="default")
            session.add(config)

        if "enabled" in updates and updates["enabled"] is not None:
            config.enabled = updates["enabled"]
        if "max_concurrent_workers" in updates and updates["max_concurrent_workers"] is not None:
            config.max_concurrent_workers = max(1, min(20, updates["max_concurrent_workers"]))
        if "max_concurrent_cc_sessions" in updates and updates["max_concurrent_cc_sessions"] is not None:
            config.max_concurrent_cc_sessions = max(1, min(5, updates["max_concurrent_cc_sessions"]))
        if "default_worker_model" in updates and updates["default_worker_model"] is not None:
            config.default_worker_model = _resolve_model(updates["default_worker_model"], config.default_worker_model)
        if "default_worker_timeout" in updates and updates["default_worker_timeout"] is not None:
            config.default_worker_timeout = max(30, min(1800, updates["default_worker_timeout"]))

        await session.commit()
        await session.refresh(config)

        # Recreate semaphore if max_concurrent changed
        _worker_semaphore = asyncio.Semaphore(config.max_concurrent_workers)

        # Recreate CC semaphore
        from services.cc_manager_service import init_cc_semaphore
        init_cc_semaphore(getattr(config, "max_concurrent_cc_sessions", 2) or 2)

        _config_cache = {
            "enabled": config.enabled,
            "max_concurrent_workers": config.max_concurrent_workers,
            "max_concurrent_cc_sessions": getattr(config, "max_concurrent_cc_sessions", 2) or 2,
            "default_worker_model": config.default_worker_model,
            "default_worker_timeout": config.default_worker_timeout,
        }
        _config_cache_ts = time.time()
        return _config_cache


async def start_orchestrator() -> None:
    """Initialize orchestrator on startup. Recover crashed tasks."""
    global _worker_semaphore

    config = await get_config()
    _worker_semaphore = asyncio.Semaphore(config["max_concurrent_workers"])

    # Initialize CC semaphore
    from services.cc_manager_service import init_cc_semaphore
    init_cc_semaphore(config.get("max_concurrent_cc_sessions", 2))

    # Mark any running/pending tasks as failed (they didn't survive restart)
    async with async_session() as session:
        await session.execute(
            update(OrchestratorTaskModel)
            .where(OrchestratorTaskModel.status.in_(["running", "pending"]))
            .values(status="failed", error="Server restarted", completed_at=datetime.utcnow())
        )
        await session.commit()

    print("Orchestrator initialized")


async def stop_orchestrator() -> None:
    """Cancel all running workers on shutdown."""
    for task_id, task in list(_worker_tasks.items()):
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):
            pass

    # Mark all running tasks as cancelled
    async with async_session() as session:
        await session.execute(
            update(OrchestratorTaskModel)
            .where(OrchestratorTaskModel.status.in_(["running", "pending"]))
            .values(status="cancelled", completed_at=datetime.utcnow())
        )
        await session.commit()

    _worker_tasks.clear()
    print("Orchestrator stopped")


async def spawn_worker(
    parent_conversation_id: str,
    task_description: str,
    model: Optional[str] = None,
    context_mode: str = "scoped",
    context_data: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    wait: bool = False,
) -> dict:
    """Spawn a worker agent to handle a sub-task.

    Args:
        parent_conversation_id: The conversation that spawned this worker
        task_description: What the worker should do
        model: Model shorthand (haiku/sonnet/opus) or full ID
        context_mode: "full" (all context), "scoped" (minimal + context_data), "none" (just task)
        context_data: Extra context for scoped mode
        timeout_seconds: Max execution time
        wait: If True, block until worker completes

    Returns:
        Task dict with id, status, etc.
    """
    config = await get_config()

    if not config["enabled"]:
        return {"error": "Orchestrator is disabled. Enable it in settings."}

    resolved_model = _resolve_model(model, config["default_worker_model"])
    timeout = timeout_seconds or config["default_worker_timeout"]

    # Create task record
    task_id = str(uuid.uuid4())
    async with async_session() as session:
        task_record = OrchestratorTaskModel(
            id=task_id,
            parent_conversation_id=parent_conversation_id,
            task_description=task_description,
            model=resolved_model,
            status="pending",
            context_mode=context_mode,
            context_data=context_data,
            timeout_seconds=timeout,
        )
        session.add(task_record)
        await session.commit()
        await session.refresh(task_record)

    # Spawn asyncio task
    asyncio_task = asyncio.create_task(
        _worker_lifecycle(task_id, parent_conversation_id, task_description, resolved_model, context_mode, context_data, timeout)
    )
    _worker_tasks[task_id] = asyncio_task

    if wait:
        # Wait for completion inline
        try:
            await asyncio_task
        except (asyncio.CancelledError, Exception):
            pass
        return await get_task(task_id)

    return _task_to_dict(task_record)


async def _worker_lifecycle(
    task_id: str,
    parent_conversation_id: str,
    task_description: str,
    model: str,
    context_mode: str,
    context_data: Optional[str],
    timeout: int,
) -> None:
    """Full lifecycle of a worker: acquire semaphore, run, update DB."""
    try:
        await _update_task_status(task_id, "running", started_at=datetime.utcnow())

        async with _worker_semaphore:
            result = await asyncio.wait_for(
                _run_worker_chat(task_id, parent_conversation_id, task_description, model, context_mode, context_data),
                timeout=timeout,
            )

        await _update_task_status(task_id, "completed", result_summary=result, completed_at=datetime.utcnow())
        await _maybe_notify_parent_task_completion(
            parent_conversation_id=parent_conversation_id,
            task_id=task_id,
            task_description=task_description,
            status="completed",
            result_summary=result,
        )

    except asyncio.TimeoutError:
        error = f"Timed out after {timeout}s"
        await _update_task_status(task_id, "failed", error=error, completed_at=datetime.utcnow())
        await _maybe_notify_parent_task_completion(
            parent_conversation_id=parent_conversation_id,
            task_id=task_id,
            task_description=task_description,
            status="failed",
            error=error,
        )
    except asyncio.CancelledError:
        await _update_task_status(task_id, "cancelled", completed_at=datetime.utcnow())
    except Exception as e:
        tb = traceback.format_exc()
        error = f"{str(e)}\n{tb}"
        await _update_task_status(task_id, "failed", error=error, completed_at=datetime.utcnow())
        await _maybe_notify_parent_task_completion(
            parent_conversation_id=parent_conversation_id,
            task_id=task_id,
            task_description=task_description,
            status="failed",
            error=error,
        )
    finally:
        _worker_tasks.pop(task_id, None)


async def _run_worker_chat(
    task_id: str,
    parent_conversation_id: str,
    task_description: str,
    model: str,
    context_mode: str,
    context_data: Optional[str],
) -> str:
    """Execute the worker's chat_with_memory call."""
    from services.graph import chat_with_memory
    from services.graph.tools import set_current_conversation_id
    from services.settings_service import get_settings
    from services.conversation_service import create_conversation

    settings = await get_settings()

    # Create worker conversation
    conversation_id = str(uuid.uuid4())
    title = task_description[:50] + ("..." if len(task_description) > 50 else "")
    await create_conversation(conversation_id, title=title, source="orchestrator_worker")

    # Update task with worker conversation ID
    await _update_task_field(task_id, "worker_conversation_id", conversation_id)

    # Set conversation context for tools
    set_current_conversation_id(conversation_id)

    # Determine skip_memory based on context_mode
    skip_memory = context_mode != "full"

    # Build worker system prompt
    system_prompt = _build_worker_system_prompt(settings.system_prompt, context_mode, context_data)

    # Call chat_with_memory with worker flags
    response = await chat_with_memory(
        message=task_description,
        conversation_id=conversation_id,
        system_prompt=system_prompt,
        model=model,
        temperature=settings.temperature,
        skip_memory=skip_memory,
        is_worker=True,
    )

    return str(response)[:2000] if response else "No response"


def _build_worker_system_prompt(base_prompt: str, context_mode: str, context_data: Optional[str]) -> str:
    """Build system prompt for a worker agent based on context mode."""
    worker_preamble = (
        "You are a worker agent spawned by the orchestrator. "
        "Complete the task described in the user message thoroughly and concisely. "
        "Your response will be returned to the parent conversation as a result summary. "
        "Do NOT spawn workers yourself — you do not have orchestrator tools."
    )

    if context_mode == "full":
        return f"{base_prompt}\n\n---\n{worker_preamble}"
    elif context_mode == "scoped":
        context_section = f"\n\nContext provided:\n{context_data}" if context_data else ""
        return f"{worker_preamble}{context_section}"
    else:  # "none"
        return worker_preamble


async def _update_task_status(
    task_id: str,
    status: str,
    result_summary: Optional[str] = None,
    error: Optional[str] = None,
    started_at: Optional[datetime] = None,
    completed_at: Optional[datetime] = None,
) -> None:
    """Update task status and related fields in DB."""
    async with async_session() as session:
        result = await session.execute(
            select(OrchestratorTaskModel).where(OrchestratorTaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task:
            task.status = status
            if result_summary is not None:
                task.result_summary = result_summary
            if error is not None:
                task.error = error
            if started_at is not None:
                task.started_at = started_at
            if completed_at is not None:
                task.completed_at = completed_at
            await session.commit()


async def _update_task_field(task_id: str, field: str, value) -> None:
    """Update a single field on a task."""
    async with async_session() as session:
        result = await session.execute(
            select(OrchestratorTaskModel).where(OrchestratorTaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if task:
            setattr(task, field, value)
            await session.commit()


async def _maybe_notify_parent_task_completion(
    parent_conversation_id: str,
    task_id: str,
    task_description: str,
    status: str,
    task_type: str = "internal_worker",
    result_summary: Optional[str] = None,
    error: Optional[str] = None,
) -> None:
    """Best-effort push notification for background task completion/failure."""
    try:
        from services.push_service import is_configured, send_push_notification
        from services.heartbeat.heartbeat_service import is_conversation_active
        from services.conversation_service import mark_user_notified

        if status not in {"completed", "failed"}:
            return
        if not is_configured():
            return
        if is_conversation_active(parent_conversation_id):
            return

        task_label = "Coding task" if task_type == "cc_session" else "Background task"
        short_desc = task_description[:80] + ("..." if len(task_description) > 80 else "")
        if status == "completed":
            body_detail = (result_summary or "Completed.").strip()[:120]
            title = f"{task_label} finished"
            body = f"{short_desc}: {body_detail}"
            tag = "task-complete"
        else:
            body_detail = (error or "Task failed.").strip().splitlines()[0][:120]
            title = f"{task_label} failed"
            body = f"{short_desc}: {body_detail}"
            tag = "task-failed"

        await send_push_notification(
            title=title,
            body=body,
            url=f"/chat?c={parent_conversation_id}",
            tag=tag,
        )
        await mark_user_notified(parent_conversation_id)
    except Exception as e:
        print(f"[ORCHESTRATOR] Completion notification failed for task {task_id}: {e}")


async def get_task(task_id: str) -> dict:
    """Get a single task by ID."""
    async with async_session() as session:
        result = await session.execute(
            select(OrchestratorTaskModel).where(OrchestratorTaskModel.id == task_id)
        )
        task = result.scalar_one_or_none()
        if not task:
            return {"error": f"Task {task_id} not found"}
        return _task_to_dict(task)


async def list_tasks(
    parent_conversation_id: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
) -> List[dict]:
    """List tasks with optional filters."""
    async with async_session() as session:
        query = select(OrchestratorTaskModel)

        if parent_conversation_id:
            query = query.where(OrchestratorTaskModel.parent_conversation_id == parent_conversation_id)
        if status:
            query = query.where(OrchestratorTaskModel.status == status)

        query = query.order_by(OrchestratorTaskModel.created_at.desc()).limit(limit).offset(offset)

        result = await session.execute(query)
        tasks = list(result.scalars().all())
        return [_task_to_dict(t) for t in tasks]


async def cancel_task(task_id: str) -> dict:
    """Cancel a running worker task."""
    asyncio_task = _worker_tasks.get(task_id)
    if asyncio_task:
        asyncio_task.cancel()
        try:
            await asyncio_task
        except (asyncio.CancelledError, Exception):
            pass

    await _update_task_status(task_id, "cancelled", completed_at=datetime.utcnow())
    return await get_task(task_id)


async def wait_for_tasks(task_ids: List[str], timeout: Optional[int] = None) -> List[dict]:
    """Wait for multiple tasks to complete."""
    tasks_to_wait = []
    for tid in task_ids:
        asyncio_task = _worker_tasks.get(tid)
        if asyncio_task:
            tasks_to_wait.append(asyncio_task)

    if tasks_to_wait:
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks_to_wait, return_exceptions=True),
                timeout=timeout or 600,
            )
        except asyncio.TimeoutError:
            pass

    results = []
    for tid in task_ids:
        results.append(await get_task(tid))
    return results


async def send_message_to_worker(task_id: str, message: str) -> dict:
    """Send a follow-up message to a completed worker's conversation."""
    task = await get_task(task_id)
    if task.get("error"):
        return task

    if task["status"] == "running":
        return {"error": "Cannot send message to a running worker. Wait for it to complete."}

    if not task.get("worker_conversation_id"):
        return {"error": "Worker has no conversation ID."}

    from services.graph import chat_with_memory
    from services.graph.tools import set_current_conversation_id
    from services.settings_service import get_settings

    settings = await get_settings()

    conversation_id = task["worker_conversation_id"]
    set_current_conversation_id(conversation_id)

    response = await chat_with_memory(
        message=message,
        conversation_id=conversation_id,
        system_prompt=settings.system_prompt,
        model=task.get("model", settings.model),
        temperature=settings.temperature,
        skip_memory=True,
        is_worker=True,
    )

    return {"response": str(response)[:2000] if response else "No response"}


async def get_active_tasks_briefing(parent_conversation_id: str) -> Optional[str]:
    """Get a formatted briefing of active + recently completed workers for system prompt injection."""
    async with async_session() as session:
        # Active tasks
        result = await session.execute(
            select(OrchestratorTaskModel)
            .where(
                OrchestratorTaskModel.parent_conversation_id == parent_conversation_id,
                OrchestratorTaskModel.status.in_(["pending", "running"]),
            )
        )
        active = list(result.scalars().all())

        # Recently completed (last 5 minutes)
        cutoff = datetime.utcnow() - timedelta(minutes=5)
        result = await session.execute(
            select(OrchestratorTaskModel)
            .where(
                OrchestratorTaskModel.parent_conversation_id == parent_conversation_id,
                OrchestratorTaskModel.status.in_(["completed", "failed"]),
                OrchestratorTaskModel.completed_at >= cutoff,
            )
        )
        recent = list(result.scalars().all())

    if not active and not recent:
        return None

    lines = ["## Orchestrator Workers"]

    if active:
        lines.append(f"\n**Active ({len(active)}):**")
        for t in active:
            task_type = getattr(t, "task_type", None) or "internal_worker"
            type_label = "[CC] " if task_type == "cc_session" else ""
            lines.append(f"- [{t.status}] {type_label}{t.task_description[:80]} (id: {t.id[:8]})")

    if recent:
        lines.append(f"\n**Recently completed ({len(recent)}):**")
        for t in recent:
            task_type = getattr(t, "task_type", None) or "internal_worker"
            type_label = "[CC] " if task_type == "cc_session" else ""
            summary = t.result_summary[:100] if t.result_summary else (t.error[:100] if t.error else "no output")
            lines.append(f"- [{t.status}] {type_label}{t.task_description[:60]} → {summary} (id: {t.id[:8]})")

    return "\n".join(lines)


async def spawn_cc_task(
    parent_conversation_id: str,
    task_description: str,
    cwd: Optional[str] = None,
    timeout_seconds: Optional[int] = None,
    wait: bool = True,
) -> dict:
    """Spawn a Claude Code session tracked by the orchestrator.

    Args:
        parent_conversation_id: The conversation that spawned this task
        task_description: What CC should do
        cwd: Working directory for CC
        timeout_seconds: Max execution time (default: 600)
        wait: If True, block until CC completes

    Returns:
        Task dict with id, status, etc.
    """
    config = await get_config()

    if not config["enabled"]:
        return {"error": "Orchestrator is disabled. Enable it in settings."}

    timeout = timeout_seconds or 600

    # Create task record with task_type=cc_session, model=None (CC uses its own)
    task_id = str(uuid.uuid4())
    async with async_session() as session:
        task_record = OrchestratorTaskModel(
            id=task_id,
            parent_conversation_id=parent_conversation_id,
            task_description=task_description,
            task_type="cc_session",
            model=None,
            status="pending",
            context_mode="none",
            timeout_seconds=timeout,
        )
        session.add(task_record)
        await session.commit()
        await session.refresh(task_record)

    # Spawn asyncio task via cc_manager
    from services.cc_manager_service import spawn_cc_for_task, _cc_tasks

    asyncio_task = asyncio.create_task(
        spawn_cc_for_task(
            task_id=task_id,
            parent_conversation_id=parent_conversation_id,
            task_description=task_description,
            cwd=cwd,
            timeout=timeout,
        )
    )
    _cc_tasks[task_id] = asyncio_task
    _worker_tasks[task_id] = asyncio_task  # Also track in main dict for cancel/stop

    if wait:
        try:
            await asyncio_task
        except (asyncio.CancelledError, Exception):
            pass
        return await get_task(task_id)

    return _task_to_dict(task_record)


def get_active_count() -> int:
    """Count of non-done asyncio tasks (workers + CC sessions)."""
    return sum(1 for t in _worker_tasks.values() if not t.done())
