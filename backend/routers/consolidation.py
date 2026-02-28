"""REST API endpoints for the memory consolidation system."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from services.consolidation_models import (
    ConsolidationStatusSchema,
    ConsolidationConfigUpdate,
    ConsolidationCycleSchema,
    MemoryConnectionSchema,
    MemoryFlagSchema,
)

router = APIRouter()


def _cycle_to_schema(cycle) -> ConsolidationCycleSchema:
    """Convert a SQLAlchemy ConsolidationCycleModel to a Pydantic schema."""
    return ConsolidationCycleSchema(
        id=cycle.id,
        memories_reviewed=cycle.memories_reviewed or 0,
        clusters_found=cycle.clusters_found or 0,
        connections_created=cycle.connections_created or 0,
        flags_created=cycle.flags_created or 0,
        contradictions_found=cycle.contradictions_found or 0,
        haiku_calls=cycle.haiku_calls or 0,
        duration_ms=cycle.duration_ms or 0,
        created_at=(cycle.created_at.isoformat() + "Z") if cycle.created_at else "",
    )


def _connection_to_schema(conn) -> MemoryConnectionSchema:
    """Convert a SQLAlchemy MemoryConnectionModel to a Pydantic schema."""
    return MemoryConnectionSchema(
        id=conn.id,
        memory_id_a=conn.memory_id_a,
        memory_id_b=conn.memory_id_b,
        connection_type=conn.connection_type,
        strength=conn.strength or 0.5,
        created_at=(conn.created_at.isoformat() + "Z") if conn.created_at else "",
    )


def _flag_to_schema(flag) -> MemoryFlagSchema:
    """Convert a SQLAlchemy MemoryFlagModel to a Pydantic schema."""
    return MemoryFlagSchema(
        id=flag.id,
        memory_id=flag.memory_id,
        flag_type=flag.flag_type,
        description=flag.description,
        related_memory_id=flag.related_memory_id,
        resolved=flag.resolved or False,
        resolved_at=(flag.resolved_at.isoformat() + "Z") if flag.resolved_at else None,
        created_at=(flag.created_at.isoformat() + "Z") if flag.created_at else "",
    )


@router.get("/consolidation/status")
async def get_status():
    """Get current consolidation system status."""
    from services.consolidation_service import get_consolidation_status

    status = await get_consolidation_status()
    return status


@router.get("/consolidation/cycles")
async def get_cycles(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """List consolidation cycles ordered by most recent."""
    from sqlalchemy import select
    from services.database import async_session, ConsolidationCycleModel

    async with async_session() as session:
        result = await session.execute(
            select(ConsolidationCycleModel)
            .order_by(ConsolidationCycleModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        cycles = list(result.scalars().all())

        return [_cycle_to_schema(c) for c in cycles]


@router.get("/consolidation/connections")
async def get_connections(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List memory connections."""
    from sqlalchemy import select
    from services.database import async_session, MemoryConnectionModel

    async with async_session() as session:
        result = await session.execute(
            select(MemoryConnectionModel)
            .order_by(MemoryConnectionModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
        connections = list(result.scalars().all())

        return [_connection_to_schema(c) for c in connections]


@router.get("/consolidation/flags")
async def get_flags(
    resolved: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List memory flags with optional resolved filter."""
    from sqlalchemy import select
    from services.database import async_session, MemoryFlagModel

    async with async_session() as session:
        query = select(MemoryFlagModel)

        if resolved is not None:
            query = query.where(MemoryFlagModel.resolved == resolved)

        query = (
            query.order_by(MemoryFlagModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await session.execute(query)
        flags = list(result.scalars().all())

        return [_flag_to_schema(f) for f in flags]


@router.patch("/consolidation/config")
async def update_config(body: ConsolidationConfigUpdate):
    """Update consolidation configuration."""
    from sqlalchemy import select
    from services.database import async_session, ConsolidationConfigModel

    async with async_session() as session:
        result = await session.execute(
            select(ConsolidationConfigModel).where(
                ConsolidationConfigModel.id == "default"
            )
        )
        config = result.scalar_one_or_none()

        if not config:
            config = ConsolidationConfigModel(id="default")
            session.add(config)

        if body.enabled is not None:
            config.enabled = body.enabled
        if body.interval_seconds is not None:
            if body.interval_seconds < 60:
                raise HTTPException(
                    status_code=400,
                    detail="Interval must be at least 60 seconds",
                )
            config.interval_seconds = body.interval_seconds
        if body.lookback_hours is not None:
            if body.lookback_hours < 1:
                raise HTTPException(
                    status_code=400,
                    detail="Lookback must be at least 1 hour",
                )
            config.lookback_hours = body.lookback_hours

        await session.commit()
        await session.refresh(config)

        return {
            "enabled": config.enabled,
            "interval_seconds": config.interval_seconds,
            "lookback_hours": config.lookback_hours,
        }


@router.post("/consolidation/trigger")
async def trigger_cycle(background_tasks: BackgroundTasks):
    """Manually trigger a consolidation cycle as a background task."""
    from services.consolidation_service import _run_consolidation_cycle

    background_tasks.add_task(_run_consolidation_cycle)
    return {"triggered": True}
