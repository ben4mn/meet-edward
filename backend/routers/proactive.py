"""REST API endpoints for the proactive intelligence system."""

from typing import Optional
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query

from services.proactive_models import (
    ProactiveConfigSchema,
    ProactiveConfigUpdate,
    ProactiveStatusSchema,
    OpenLoopSchema,
    OpenLoopUpdate,
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


@router.get("/proactive/config")
async def get_config():
    """Get proactive intelligence configuration."""
    from sqlalchemy import select
    from services.database import async_session, ProactiveConfigModel

    async with async_session() as session:
        result = await session.execute(
            select(ProactiveConfigModel).where(ProactiveConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()

    if not config:
        return ProactiveConfigSchema().model_dump()

    return ProactiveConfigSchema(
        morning_briefing_enabled=config.morning_briefing_enabled,
        morning_briefing_time=config.morning_briefing_time or "08:00",
        morning_briefing_channel=config.morning_briefing_channel or "push",
        meeting_prep_enabled=config.meeting_prep_enabled,
        meeting_prep_advance_minutes=config.meeting_prep_advance_minutes or 30,
        pattern_detection_enabled=config.pattern_detection_enabled,
        open_loop_nudge_enabled=config.open_loop_nudge_enabled,
        open_loop_nudge_days=config.open_loop_nudge_days or 7,
        extraction_depth=config.extraction_depth or "standard",
    ).model_dump()


@router.patch("/proactive/config")
async def update_config(body: ProactiveConfigUpdate):
    """Update proactive intelligence configuration."""
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

        if body.morning_briefing_enabled is not None:
            config.morning_briefing_enabled = body.morning_briefing_enabled
        if body.morning_briefing_time is not None:
            config.morning_briefing_time = body.morning_briefing_time
        if body.morning_briefing_channel is not None:
            if body.morning_briefing_channel not in ("push", "imessage", "chat"):
                raise HTTPException(400, "Channel must be push, imessage, or chat")
            config.morning_briefing_channel = body.morning_briefing_channel
        if body.meeting_prep_enabled is not None:
            config.meeting_prep_enabled = body.meeting_prep_enabled
        if body.meeting_prep_advance_minutes is not None:
            if body.meeting_prep_advance_minutes < 5:
                raise HTTPException(400, "Advance minutes must be at least 5")
            config.meeting_prep_advance_minutes = body.meeting_prep_advance_minutes
        if body.pattern_detection_enabled is not None:
            config.pattern_detection_enabled = body.pattern_detection_enabled
        if body.open_loop_nudge_enabled is not None:
            config.open_loop_nudge_enabled = body.open_loop_nudge_enabled
        if body.open_loop_nudge_days is not None:
            if body.open_loop_nudge_days < 1:
                raise HTTPException(400, "Nudge days must be at least 1")
            config.open_loop_nudge_days = body.open_loop_nudge_days
        if body.extraction_depth is not None:
            if body.extraction_depth not in ("light", "standard", "deep"):
                raise HTTPException(400, "Extraction depth must be light, standard, or deep")
            config.extraction_depth = body.extraction_depth

        await session.commit()
        await session.refresh(config)

        return ProactiveConfigSchema(
            morning_briefing_enabled=config.morning_briefing_enabled,
            morning_briefing_time=config.morning_briefing_time or "08:00",
            morning_briefing_channel=config.morning_briefing_channel or "push",
            meeting_prep_enabled=config.meeting_prep_enabled,
            meeting_prep_advance_minutes=config.meeting_prep_advance_minutes or 30,
            pattern_detection_enabled=config.pattern_detection_enabled,
            open_loop_nudge_enabled=config.open_loop_nudge_enabled,
            open_loop_nudge_days=config.open_loop_nudge_days or 7,
            extraction_depth=config.extraction_depth or "standard",
        ).model_dump()


@router.get("/proactive/status")
async def get_status():
    """Get status of all proactive services."""
    from sqlalchemy import select
    from services.database import async_session, ProactiveConfigModel
    from services.entity_service import get_entity_count
    from services.open_loop_service import get_open_loop_count

    async with async_session() as session:
        result = await session.execute(
            select(ProactiveConfigModel).where(ProactiveConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()

    entity_count = await get_entity_count()
    open_loop_count = await get_open_loop_count()

    from services.proactive import (
        is_morning_briefing_running,
        is_meeting_prep_running,
        is_pattern_detection_running,
        is_open_loop_nudge_running,
    )

    config_schema = ProactiveConfigSchema()
    if config:
        config_schema = ProactiveConfigSchema(
            morning_briefing_enabled=config.morning_briefing_enabled,
            morning_briefing_time=config.morning_briefing_time or "08:00",
            morning_briefing_channel=config.morning_briefing_channel or "push",
            meeting_prep_enabled=config.meeting_prep_enabled,
            meeting_prep_advance_minutes=config.meeting_prep_advance_minutes or 30,
            pattern_detection_enabled=config.pattern_detection_enabled,
            open_loop_nudge_enabled=config.open_loop_nudge_enabled,
            open_loop_nudge_days=config.open_loop_nudge_days or 7,
            extraction_depth=config.extraction_depth or "standard",
        )

    return ProactiveStatusSchema(
        config=config_schema,
        entity_count=entity_count,
        open_loop_count=open_loop_count,
        morning_briefing_running=is_morning_briefing_running(),
        meeting_prep_running=is_meeting_prep_running(),
        pattern_detection_running=is_pattern_detection_running(),
        open_loop_nudge_running=is_open_loop_nudge_running(),
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
