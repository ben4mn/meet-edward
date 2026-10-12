"""REST API endpoints for what is left of the proactive system.

Since mind rebuild Phase 7 the five templated jobs are wakes
(``/proactive/status`` lists them); the owner's data — open loops, entities —
and the dossier thresholds stay here.
"""

from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from services.proactive_models import (
    ProactiveConfigSchema,
    ProactiveConfigUpdate,
    ProactiveStatusSchema,
    OpenLoopSchema,
    OpenLoopUpdate,
    WakeSchema,
)

router = APIRouter()


def _dt_to_str(dt) -> Optional[str]:
    if dt is None:
        return None
    return dt.isoformat() + "Z" if isinstance(dt, datetime) else str(dt)


def _loop_to_schema(loop) -> OpenLoopSchema:
    return OpenLoopSchema(
        id=loop.id,
        description=loop.description,
        source_memory_id=loop.source_memory_id,
        source_conversation_id=loop.source_conversation_id,
        entity_id=loop.entity_id,
        status=loop.status or "open",
        priority=loop.priority or 0.5,
        detected_at=_dt_to_str(loop.detected_at),
        last_nudged_at=_dt_to_str(loop.last_nudged_at),
        resolved_at=_dt_to_str(loop.resolved_at),
        snooze_until=_dt_to_str(loop.snooze_until),
        nudge_count=loop.nudge_count or 0,
    )


def _config_schema(config) -> ProactiveConfigSchema:
    if not config:
        return ProactiveConfigSchema()
    return ProactiveConfigSchema(
        dossier_enabled=getattr(config, "dossier_enabled", True),
        dossier_min_acted_pushes=getattr(config, "dossier_min_acted_pushes", 2) or 2,
        dossier_min_inbound_threads=getattr(config, "dossier_min_inbound_threads", 3) or 3,
        dossier_window_days=getattr(config, "dossier_window_days", 7) or 7,
    )


@router.get("/proactive/config")
async def get_config():
    """The dossier thresholds — the only proactive knobs left."""
    from sqlalchemy import select
    from services.database import async_session, ProactiveConfigModel

    async with async_session() as session:
        result = await session.execute(
            select(ProactiveConfigModel).where(ProactiveConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()
    return _config_schema(config).model_dump()


@router.patch("/proactive/config")
async def update_config(body: ProactiveConfigUpdate):
    """Update the dossier thresholds."""
    from sqlalchemy import select
    from services.database import async_session, ProactiveConfigModel

    async with async_session() as session:
        result = await session.execute(
            select(ProactiveConfigModel).where(ProactiveConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()

        if not config:
            config = ProactiveConfigModel(id="default")
            session.add(config)

        if body.dossier_enabled is not None:
            config.dossier_enabled = body.dossier_enabled
        if body.dossier_min_acted_pushes is not None:
            if body.dossier_min_acted_pushes < 1:
                raise HTTPException(400, "Dossier acted-push threshold must be at least 1")
            config.dossier_min_acted_pushes = body.dossier_min_acted_pushes
        if body.dossier_min_inbound_threads is not None:
            if body.dossier_min_inbound_threads < 1:
                raise HTTPException(400, "Dossier inbound-thread threshold must be at least 1")
            config.dossier_min_inbound_threads = body.dossier_min_inbound_threads
        if body.dossier_window_days is not None:
            if body.dossier_window_days < 1:
                raise HTTPException(400, "Dossier window must be at least 1 day")
            config.dossier_window_days = body.dossier_window_days

        await session.commit()
        await session.refresh(config)
        return _config_schema(config).model_dump()


@router.get("/proactive/status")
async def get_status():
    """Counts, the dossier config, and the wakes Edward takes on his own."""
    from sqlalchemy import select
    from services.database import async_session, ProactiveConfigModel
    from services.entity_service import get_entity_count
    from services.open_loop_service import get_open_loop_count
    from services.scheduler_service import WAKEUP_TICKS

    async with async_session() as session:
        result = await session.execute(
            select(ProactiveConfigModel).where(ProactiveConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()

    entity_count = await get_entity_count()
    open_loop_count = await get_open_loop_count()

    return ProactiveStatusSchema(
        config=_config_schema(config),
        entity_count=entity_count,
        open_loop_count=open_loop_count,
        wakes=[WakeSchema(kind=kind, cron=cron) for kind, cron in WAKEUP_TICKS],
    ).model_dump()


@router.get("/proactive/open-loops")
async def list_open_loops(
    status: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List open loops with optional status filter."""
    from services.open_loop_service import get_open_loops

    loops = await get_open_loops(status=status, limit=limit, offset=offset)
    return [_loop_to_schema(loop) for loop in loops]


@router.patch("/proactive/open-loops/{loop_id}")
async def update_open_loop(loop_id: str, body: OpenLoopUpdate):
    """Resolve or snooze an open loop."""
    if body.status == "resolved":
        from services.open_loop_service import resolve_open_loop
        loop = await resolve_open_loop(loop_id)
        if not loop:
            raise HTTPException(404, "Open loop not found")
        return _loop_to_schema(loop)
    elif body.status == "snoozed":
        if not body.snooze_until:
            raise HTTPException(400, "snooze_until required for snoozed status")
        from services.open_loop_service import snooze_open_loop
        try:
            until = datetime.fromisoformat(body.snooze_until.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "Invalid snooze_until datetime")
        loop = await snooze_open_loop(loop_id, until)
        if not loop:
            raise HTTPException(404, "Open loop not found")
        return _loop_to_schema(loop)
    else:
        raise HTTPException(400, "Status must be 'resolved' or 'snoozed'")


@router.get("/proactive/entities")
async def list_entities(
    entity_type: Optional[str] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List entities with optional type filter."""
    from services.entity_service import list_entities as _list_entities
    import json

    entities = await _list_entities(entity_type=entity_type, limit=limit, offset=offset)
    return [
        {
            "id": e.id,
            "name": e.name,
            "entity_type": e.entity_type,
            "aliases": json.loads(e.aliases) if e.aliases else [],
            "mention_count": e.mention_count or 0,
            "first_seen": _dt_to_str(e.first_seen),
            "last_seen": _dt_to_str(e.last_seen),
        }
        for e in entities
    ]
