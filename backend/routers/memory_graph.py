"""Graph-memory admin endpoints (C Phase 2 v2).

The backend never touches Kuzu directly anymore — all graph ops go
through the dedicated worker daemon over HTTP. This router:

  * ``POST /api/memory/graph/backfill`` — bulk-enqueues existing
    memories into ``graph_ingest_queue``. The worker picks them up at
    its own pace.
  * ``GET /api/memory/graph/backfill/status`` — reads in-memory state
    (what the last /backfill request enqueued, how many are left in
    the pending queue).
  * ``GET /api/memory/graph/status`` — proxies to the worker's
    /status endpoint so we get live episode/entity/edge counts +
    queue counters in one call.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func

from services.database import async_session, GraphIngestQueueModel, MemoryModel
from services.graph_memory_service import (
    get_worker_status,
    is_enabled,
)


router = APIRouter(prefix="/api/memory/graph", tags=["memory-graph"])


_STATE: dict = {
    "started_at": None,
    "total_enqueued": 0,
    "already_queued_or_done": 0,
    "since": None,
    "limit": None,
}


class BackfillResponse(BaseModel):
    accepted: bool
    enqueued: int
    skipped_existing: int
    message: str


class BackfillStatusResponse(BaseModel):
    started_at: Optional[str]
    total_enqueued: int
    already_queued_or_done: int
    queue_pending: int
    queue_processing: int
    queue_done: int
    queue_failed: int
    since: Optional[str]
    limit: Optional[int]


class GraphStatusResponse(BaseModel):
    enabled: bool
    ok: bool
    episodes: int = 0
    entities: int = 0
    edges: int = 0
    queue_pending: int = 0
    queue_processing: int = 0
    queue_done: int = 0
    queue_failed: int = 0
    db_path: Optional[str] = None
    error: Optional[str] = None


async def _queue_counts() -> dict[str, int]:
    async with async_session() as session:
        rows = await session.execute(
            select(GraphIngestQueueModel.status, func.count())
            .group_by(GraphIngestQueueModel.status)
        )
        counts = {s: int(c) for s, c in rows.all()}
    return {
        "queue_pending": counts.get("pending", 0),
        "queue_processing": counts.get("processing", 0),
        "queue_done": counts.get("done", 0),
        "queue_failed": counts.get("failed", 0),
    }


@router.post("/backfill", response_model=BackfillResponse, status_code=202)
async def start_backfill(
    limit: Optional[int] = Query(None, gt=0, le=5000),
    since: Optional[str] = Query(None, description="ISO date or datetime"),
):
    """Bulk-enqueue memories for the worker. No Kuzu calls; just
    inserts rows into ``graph_ingest_queue``. The worker drains the
    queue at its own pace."""
    if not is_enabled():
        raise HTTPException(
            status_code=400,
            detail="Graph memory is disabled (GRAPHITI_ENABLED=false)",
        )

    since_dt = None
    if since:
        try:
            since_dt = datetime.fromisoformat(since.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(status_code=400, detail=f"bad since: {since}")

    async with async_session() as session:
        stmt = select(MemoryModel).order_by(MemoryModel.created_at.asc())
        if since_dt:
            stmt = stmt.where(
                MemoryModel.created_at >= since_dt.replace(tzinfo=None)
            )
        if limit:
            stmt = stmt.limit(limit)
        memories = list((await session.execute(stmt)).scalars().all())

        # Skip rows that are already in the queue (pending/processing/done)
        # so repeat calls are cheap + idempotent.
        existing_ids = set()
        if memories:
            mem_ids = [m.id for m in memories]
            existing_q = await session.execute(
                select(GraphIngestQueueModel.memory_id).where(
                    GraphIngestQueueModel.memory_id.in_(mem_ids)
                )
            )
            existing_ids = {row[0] for row in existing_q.all()}

        enqueued = 0
        for m in memories:
            if m.id in existing_ids:
                continue
            session.add(GraphIngestQueueModel(
                memory_id=m.id,
                content=m.content,
                source_description=(
                    f"Edward memory (type={m.memory_type or 'unknown'}, "
                    f"source={m.source_type or 'unknown'}, "
                    f"importance={m.importance or 0.5:.2f})"
                ),
                reference_time=m.created_at or datetime.now(timezone.utc).replace(tzinfo=None),
                status="pending",
            ))
            enqueued += 1
        await session.commit()

    _STATE.update({
        "started_at": datetime.now(timezone.utc).isoformat(),
        "total_enqueued": enqueued,
        "already_queued_or_done": len(memories) - enqueued,
        "since": since_dt.isoformat() if since_dt else None,
        "limit": limit,
    })

    return BackfillResponse(
        accepted=True,
        enqueued=enqueued,
        skipped_existing=len(memories) - enqueued,
        message=(
            f"Enqueued {enqueued} new memories "
            f"(skipped {len(memories) - enqueued} already queued/done). "
            f"Worker will process them in the background."
        ),
    )


@router.get("/backfill/status", response_model=BackfillStatusResponse)
async def backfill_status():
    counts = await _queue_counts()
    return BackfillStatusResponse(
        started_at=_STATE.get("started_at"),
        total_enqueued=_STATE.get("total_enqueued", 0),
        already_queued_or_done=_STATE.get("already_queued_or_done", 0),
        since=_STATE.get("since"),
        limit=_STATE.get("limit"),
        **counts,
    )


@router.get("/status", response_model=GraphStatusResponse)
async def graph_status():
    if not is_enabled():
        return GraphStatusResponse(enabled=False, ok=False)
    worker = await get_worker_status()
    counts = await _queue_counts()
    return GraphStatusResponse(
        enabled=True,
        ok=bool(worker.get("ok")),
        episodes=worker.get("episodes", 0),
        entities=worker.get("entities", 0),
        edges=worker.get("edges", 0),
        db_path=worker.get("db_path"),
        error=worker.get("error"),
        **counts,
    )
