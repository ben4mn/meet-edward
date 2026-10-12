"""REST API endpoints for the memory consolidation system."""

from typing import Optional

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query

from services.consolidation_models import (
    ConsolidationStatusSchema,
    ConsolidationConfigUpdate,
    ConsolidationCycleSchema,
    MemoryConnectionSchema,
    MemoryFlagSchema,
    FlagResolveUpdate,
    FlagKeepUpdate,
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


async def _memory_texts(session, ids) -> dict:
    """{memory id: content} for the ids given (missing ones are absent)."""
    from sqlalchemy import select
    from services.database import MemoryModel

    ids = [i for i in ids if i]
    if not ids:
        return {}
    rows = await session.execute(select(MemoryModel.id, MemoryModel.content).where(MemoryModel.id.in_(ids)))
    return {r[0]: r[1] for r in rows.all()}


@router.get("/consolidation/connections")
async def get_connections(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    with_content: bool = False,
):
    """List memory connections. ``with_content`` attaches both memories'
    text, so a connection reads as what it links (review 2026-09-26)."""
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
        out = [_connection_to_schema(c) for c in connections]

        if with_content and connections:
            content = await _memory_texts(
                session, {c.memory_id_a for c in connections} | {c.memory_id_b for c in connections}
            )
            for schema in out:
                schema.memory_a_content = content.get(schema.memory_id_a)
                schema.memory_b_content = content.get(schema.memory_id_b)

        return out


@router.get("/consolidation/flags")
async def get_flags(
    resolved: Optional[bool] = None,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    with_content: bool = False,
):
    """List memory flags with optional resolved filter. ``with_content``
    attaches both memories' text so a contradiction can be judged in place."""
    from sqlalchemy import select
    from services.database import async_session, MemoryFlagModel, MemoryModel

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
        out = [_flag_to_schema(f) for f in flags]

        if with_content and flags:
            ids = {f.memory_id for f in flags} | {f.related_memory_id for f in flags if f.related_memory_id}
            rows = await session.execute(
                select(MemoryModel.id, MemoryModel.content).where(MemoryModel.id.in_(list(ids)))
            )
            content = {r[0]: r[1] for r in rows.all()}
            for schema in out:
                schema.memory_content = content.get(schema.memory_id)
                if schema.related_memory_id:
                    schema.related_memory_content = content.get(schema.related_memory_id)

        return out


@router.get("/consolidation/flags/{flag_id}")
async def get_flag(flag_id: str):
    """One flag with both memories' text — a deep link past the newest page
    of flags is read directly, never guessed "already settled"."""
    from sqlalchemy import select
    from services.database import async_session, MemoryFlagModel

    async with async_session() as session:
        flag = (
            await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id))
        ).scalar_one_or_none()
        if flag is None:
            raise HTTPException(status_code=404, detail="Flag not found")
        schema = _flag_to_schema(flag)
        content = await _memory_texts(session, {flag.memory_id, flag.related_memory_id})
        schema.memory_content = content.get(flag.memory_id)
        if flag.related_memory_id:
            schema.related_memory_content = content.get(flag.related_memory_id)
        return schema


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
        if body.max_cluster_size is not None:
            if body.max_cluster_size < 2:
                raise HTTPException(status_code=400, detail="max_cluster_size must be >= 2")
            config.max_cluster_size = body.max_cluster_size
        if body.max_pairs_per_cluster is not None:
            if body.max_pairs_per_cluster < 1:
                raise HTTPException(status_code=400, detail="max_pairs_per_cluster must be >= 1")
            config.max_pairs_per_cluster = body.max_pairs_per_cluster
        if body.max_llm_calls_per_cycle is not None:
            if body.max_llm_calls_per_cycle < 1:
                raise HTTPException(status_code=400, detail="max_llm_calls_per_cycle must be >= 1")
            config.max_llm_calls_per_cycle = body.max_llm_calls_per_cycle

        await session.commit()
        await session.refresh(config)

        return {
            "enabled": config.enabled,
            "interval_seconds": config.interval_seconds,
            "lookback_hours": config.lookback_hours,
            "max_cluster_size": config.max_cluster_size,
            "max_pairs_per_cluster": config.max_pairs_per_cluster,
            "max_llm_calls_per_cycle": config.max_llm_calls_per_cycle,
        }


@router.patch("/consolidation/flags/{flag_id}")
async def resolve_flag(flag_id: str, body: FlagResolveUpdate):
    """Mark a memory flag as resolved (or un-resolved) from the DreamsPanel UI."""
    from datetime import datetime
    from sqlalchemy import select
    from services.database import async_session, MemoryFlagModel

    async with async_session() as session:
        result = await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )
        flag = result.scalar_one_or_none()
        if flag is None:
            raise HTTPException(status_code=404, detail="flag not found")

        flag.resolved = body.resolved
        flag.resolved_at = datetime.utcnow() if body.resolved else None

        await session.commit()
        await session.refresh(flag)

        return _flag_to_schema(flag)


@router.post("/consolidation/flags/{flag_id}/resolve")
async def keep_flag_side(flag_id: str, body: FlagKeepUpdate):
    """The owner's call on an open contradiction: keep one memory, retire the other
    the same way the automatic path does (valid_until + confidence 0.2)."""
    from sqlalchemy import select
    from services.database import async_session, MemoryFlagModel
    from services.consolidation_service import retire_contradiction_loser, mark_flag_resolved

    async with async_session() as session:
        flag = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )).scalar_one_or_none()
        if flag is None:
            raise HTTPException(status_code=404, detail="flag not found")
        if not flag.related_memory_id:
            raise HTTPException(status_code=400, detail="flag has no second memory to choose between")
        retire_id = flag.related_memory_id if body.keep == "a" else flag.memory_id

    await retire_contradiction_loser(retire_id)
    await mark_flag_resolved(flag_id)

    async with async_session() as session:
        flag = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )).scalar_one()
        return _flag_to_schema(flag)


@router.post("/consolidation/trigger")
async def trigger_cycle(background_tasks: BackgroundTasks):
    """Manually trigger a consolidation cycle as a background task."""
    from services.consolidation_service import _run_consolidation_cycle

    background_tasks.add_task(_run_consolidation_cycle)
    return {"triggered": True}
