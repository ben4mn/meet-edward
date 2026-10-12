"""Out-of-process Graphiti ingestion + search worker (C Phase 2 v2).

Runs as its own LaunchAgent (``<prefix>.graph-worker``; the prefix is
``EDWARD_LAUNCHD_PREFIX``, default ``com.meet-edward``, installed by
``scripts/install-launchagents.sh``) with its own Python interpreter,
started by ``backend/start-graph-worker.sh``. It does nothing unless
``GRAPHITI_ENABLED=true``. Owns the Kuzu file exclusively — the main backend
never opens Kuzu directly. This isolation means C-level crashes here
(which is what we were hitting when Graphiti ran in a thread inside
the backend) only kill the worker, not the backend.

Two surfaces:

1. **Ingestion queue**: polls ``graph_ingest_queue`` in Postgres for
   pending rows, calls ``Graphiti.add_episode`` for each, marks done.
   This is fire-and-forget from the backend's perspective — it just
   inserts a queue row and moves on.

2. **FastAPI on 127.0.0.1:8001**: exposes ``/search`` and ``/status``
   for synchronous reads the backend needs (LLM tool responses, admin
   UI). Bound to localhost only — never exposed outside the host Mac.

Safe restart behavior: the launchd config has ``KeepAlive=true`` +
``ThrottleInterval=10`` so a segfault respawns this process within
10 seconds, with no impact on the main backend.
"""

from __future__ import annotations

import asyncio
import faulthandler
import json
import logging
import os
import sys
import time

# Enable native-code traceback on segfault/abort. Without this, a C-level
# crash inside Kuzu/Graphiti dies silently and launchd's KeepAlive just
# respawns us with no clue what blew up.
faulthandler.enable()
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

# Ensure ``services.*`` imports resolve — worker is launched from the
# backend directory by start-graph-worker.sh.
BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# Load env the same way the main backend does.
from dotenv import load_dotenv
load_dotenv(BACKEND_DIR.parent / ".env")
load_dotenv(BACKEND_DIR / ".env")

# Eager-load sentence-transformers on the main thread of this process,
# before any worker thread / Graphiti code touches it. Same reason as in
# the backend's lifespan — load from non-main thread can segfault.
from services.memory_service import get_embedding_model
_model = get_embedding_model()
print(f"[graph-worker] embedding model preloaded: {type(_model).__name__}", flush=True)


from fastapi import FastAPI, Query
from pydantic import BaseModel
from sqlalchemy import select, update

from services.database import (
    async_session,
    GraphIngestQueueModel,
    engine,
    init_db,
)


logger = logging.getLogger("graph-worker")


# --------------------------------------------------------------------
# Graphiti setup (this process owns Kuzu)
# --------------------------------------------------------------------


_GRAPHITI: Any = None
_INGEST_TASK: Optional[asyncio.Task] = None
_POLL_INTERVAL_SECS = 5.0
_INGEST_TIMEOUT_SECS = 180.0


def _db_path() -> str:
    root = os.getenv("GRAPHITI_DB_PATH")
    if root:
        return root
    storage = Path(os.getenv("FILE_STORAGE_ROOT", "./storage")).expanduser()
    storage.mkdir(parents=True, exist_ok=True)
    return str(storage / "graphiti.kuzu")


async def _init_graphiti() -> Any:
    from graphiti_core import Graphiti
    from graphiti_core.driver.driver import GraphProvider
    from graphiti_core.driver.kuzu_driver import KuzuDriver
    from graphiti_core.graph_queries import get_fulltext_indices

    from services.graphiti_adapters import (
        BackgroundLLMGraphitiClient,
        LocalCosineCrossEncoder,
        LocalSentenceTransformerEmbedder,
    )

    driver = KuzuDriver(db=_db_path())
    await driver.client.execute("INSTALL fts;")
    await driver.client.execute("LOAD fts;")

    for q in get_fulltext_indices(GraphProvider.KUZU):
        try:
            await driver.client.execute(q)
        except Exception as exc:
            msg = str(exc).lower()
            if "already exists" in msg or "duplicate" in msg:
                continue
            raise

    graphiti = Graphiti(
        graph_driver=driver,
        llm_client=BackgroundLLMGraphitiClient(),
        embedder=LocalSentenceTransformerEmbedder(),
        cross_encoder=LocalCosineCrossEncoder(),
    )
    print(f"[graph-worker] graphiti initialized db={_db_path()}", flush=True)
    return graphiti


# --------------------------------------------------------------------
# Ingestion queue poller
# --------------------------------------------------------------------


async def _process_one_pending() -> bool:
    """Grab the oldest pending queue row, run add_episode, mark done.
    Returns True if a row was processed (caller can poll immediately
    for the next one), False if queue was empty."""
    from graphiti_core.nodes import EpisodeType

    async with async_session() as session:
        # ``FOR UPDATE SKIP LOCKED`` so multiple workers (should we ever
        # run more than one) don't grab the same row.
        row_q = await session.execute(
            select(GraphIngestQueueModel)
            .where(GraphIngestQueueModel.status == "pending")
            .order_by(GraphIngestQueueModel.enqueued_at.asc())
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        row = row_q.scalar_one_or_none()
        if row is None:
            return False
        row.status = "processing"
        row.attempts = (row.attempts or 0) + 1
        await session.commit()
        queue_id = row.id
        memory_id = row.memory_id
        content = row.content
        source_desc = row.source_description
        ref_time = row.reference_time

    t0 = time.time()
    print(
        f"[graph-worker] ingest START queue_id={queue_id[:8]} "
        f"memory_id={memory_id[:8]}",
        flush=True,
    )
    ok = False
    err_msg = None
    try:
        await asyncio.wait_for(
            _GRAPHITI.add_episode(
                name=f"memory_{memory_id[:8]}",
                episode_body=content,
                source=EpisodeType.text,
                source_description=source_desc or "",
                reference_time=ref_time or datetime.now(timezone.utc),
            ),
            timeout=_INGEST_TIMEOUT_SECS,
        )
        ok = True
    except asyncio.TimeoutError:
        err_msg = f"timeout after {_INGEST_TIMEOUT_SECS}s"
    except Exception as exc:
        err_msg = f"{type(exc).__name__}: {exc}"

    # Fold the WAL into the main DB file. Kuzu only persists on graceful
    # shutdown or explicit CHECKPOINT, and this worker tends to die via
    # SIGSEGV (never graceful). Without this, the WAL grew to 11 MB over
    # ~4 weeks while the main file stayed at 2.9 MB; each respawn replayed
    # the entire WAL and a single bad offset in that replay segfaulted the
    # next NodeTable::update. Skip on failure — folding a partially-aborted
    # transaction is exactly the corruption we're avoiding.
    if ok:
        try:
            await _GRAPHITI.driver.client.execute("CHECKPOINT;")
        except Exception as exc:
            print(
                f"[graph-worker] CHECKPOINT after queue_id={queue_id[:8]} failed: "
                f"{type(exc).__name__}: {exc}",
                flush=True,
            )

    async with async_session() as session:
        await session.execute(
            update(GraphIngestQueueModel)
            .where(GraphIngestQueueModel.id == queue_id)
            .values(
                status="done" if ok else "failed",
                error_msg=err_msg,
                processed_at=datetime.utcnow(),
            )
        )
        await session.commit()

    print(
        f"[graph-worker] ingest {'OK' if ok else 'FAIL'} "
        f"queue_id={queue_id[:8]} ({time.time() - t0:.1f}s) "
        f"{('err=' + err_msg) if err_msg else ''}",
        flush=True,
    )
    return True


async def _ingest_loop() -> None:
    """Long-running loop: drain the queue, then sleep."""
    while True:
        try:
            while True:
                processed = await _process_one_pending()
                if not processed:
                    break
                # Yield briefly between rows so the FastAPI endpoints
                # can still be served in a timely fashion.
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            print(f"[graph-worker] ingest loop error: {exc}", flush=True)
        await asyncio.sleep(_POLL_INTERVAL_SECS)


# --------------------------------------------------------------------
# FastAPI surface
# --------------------------------------------------------------------


class SearchResult(BaseModel):
    fact: Optional[str] = None
    valid_at: Optional[str] = None
    invalid_at: Optional[str] = None
    source_node_uuid: Optional[str] = None
    target_node_uuid: Optional[str] = None


class SearchResponse(BaseModel):
    results: list[SearchResult]


class StatusResponse(BaseModel):
    ok: bool
    db_path: str
    episodes: int
    entities: int
    edges: int
    queue_pending: int
    queue_processing: int
    queue_done: int
    queue_failed: int


MAX_RECOVERY_ATTEMPTS = 3


async def _recover_stuck_processing() -> int:
    """Any rows in 'processing' at startup are leftovers from a worker
    that was killed mid-ingest (segfault or SIGKILL).

    Rows under the attempt cap go back to 'pending' for retry. Rows AT
    or above the cap are quarantined to 'failed' so a poison pill (e.g.
    a row whose ingest reliably segfaults Kuzu) can't loop forever — we
    saw this happen 6+ times in 4 minutes before this guard existed.

    Returns the count of rows re-pended (NOT including quarantined ones).
    """
    async with async_session() as session:
        # Quarantine first so the recoverable count is accurate.
        quarantined = await session.execute(
            update(GraphIngestQueueModel)
            .where(
                (GraphIngestQueueModel.status == "processing")
                & (GraphIngestQueueModel.attempts >= MAX_RECOVERY_ATTEMPTS)
            )
            .values(
                status="failed",
                error_msg=(
                    f"auto-quarantined after {MAX_RECOVERY_ATTEMPTS}+ attempts "
                    "(worker crashed mid-ingest each time — likely a Kuzu "
                    "segfault or other native crash on this row). Investigate "
                    "before re-queueing."
                ),
                processed_at=datetime.utcnow(),
            )
        )
        recovered = await session.execute(
            update(GraphIngestQueueModel)
            .where(GraphIngestQueueModel.status == "processing")
            .values(status="pending")
        )
        await session.commit()
        if quarantined.rowcount:
            print(
                f"[graph-worker] auto-quarantined {quarantined.rowcount} "
                f"row(s) at attempts >= {MAX_RECOVERY_ATTEMPTS} "
                "(would crash-loop)",
                flush=True,
            )
        return int(recovered.rowcount or 0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _GRAPHITI, _INGEST_TASK
    await init_db()  # ensures schema is up-to-date; cheap idempotent op
    recovered = await _recover_stuck_processing()
    if recovered:
        print(
            f"[graph-worker] recovered {recovered} row(s) "
            f"stuck in 'processing' from a prior crash",
            flush=True,
        )
    _GRAPHITI = await _init_graphiti()
    # Gate the ingestion loop separately from the HTTP surface so the
    # worker can stay alive (serving /search, /status, /health) even
    # while we're diagnosing the ingestion crash. Flip
    # GRAPHITI_INGEST_ENABLED=true to drain the queue.
    if os.getenv("GRAPHITI_INGEST_ENABLED", "false").lower() in ("1", "true", "yes", "on"):
        _INGEST_TASK = asyncio.create_task(_ingest_loop())
        print("[graph-worker] ingest loop started", flush=True)
    else:
        print(
            "[graph-worker] ingest loop DISABLED (set GRAPHITI_INGEST_ENABLED=true)",
            flush=True,
        )
    yield
    if _INGEST_TASK is not None:
        _INGEST_TASK.cancel()
        try:
            await _INGEST_TASK
        except (asyncio.CancelledError, Exception):
            pass
    try:
        close = getattr(_GRAPHITI, "close", None)
        if close:
            result = close()
            if asyncio.iscoroutine(result):
                await result
    except Exception:
        pass
    await engine.dispose()


app = FastAPI(title="Edward Graph Worker", lifespan=lifespan)


@app.get("/health")
async def health():
    return {"ok": _GRAPHITI is not None}


@app.get("/search", response_model=SearchResponse)
async def search(query: str, limit: int = Query(5, ge=1, le=50)):
    if _GRAPHITI is None:
        return SearchResponse(results=[])
    try:
        edges = await _GRAPHITI.search(query=query, num_results=limit)
    except Exception as exc:
        print(f"[graph-worker] search error: {exc}", flush=True)
        return SearchResponse(results=[])

    out: list[SearchResult] = []
    for edge in edges or []:
        out.append(SearchResult(
            fact=getattr(edge, "fact", None) or str(edge),
            valid_at=(
                getattr(edge, "valid_at", None).isoformat()
                if getattr(edge, "valid_at", None) is not None
                else None
            ),
            invalid_at=(
                getattr(edge, "invalid_at", None).isoformat()
                if getattr(edge, "invalid_at", None) is not None
                else None
            ),
            source_node_uuid=getattr(edge, "source_node_uuid", None),
            target_node_uuid=getattr(edge, "target_node_uuid", None),
        ))
    return SearchResponse(results=out)


async def _count(cypher: str) -> int:
    if _GRAPHITI is None:
        return 0
    try:
        res = await _GRAPHITI.driver.client.execute(cypher)
        while res.has_next():
            row = res.get_next()
            return int(row[0])
    except Exception as exc:
        print(f"[graph-worker] count query failed: {exc}", flush=True)
    return -1


@app.get("/status", response_model=StatusResponse)
async def status():
    from sqlalchemy import func as sa_func

    async with async_session() as session:
        rows = await session.execute(
            select(GraphIngestQueueModel.status, sa_func.count())
            .group_by(GraphIngestQueueModel.status)
        )
        counts = {s: int(c) for s, c in rows.all()}

    return StatusResponse(
        ok=_GRAPHITI is not None,
        db_path=_db_path(),
        episodes=await _count("MATCH (n:Episodic) RETURN count(*)"),
        entities=await _count("MATCH (n:Entity) RETURN count(*)"),
        edges=await _count("MATCH ()-[r:RELATES_TO]->() RETURN count(*)"),
        queue_pending=counts.get("pending", 0),
        queue_processing=counts.get("processing", 0),
        queue_done=counts.get("done", 0),
        queue_failed=counts.get("failed", 0),
    )
