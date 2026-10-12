"""Graphiti graph-memory service — backend-side stub.

C Phase 2 v2 architecture: Graphiti runs as a dedicated worker process
(``backend/workers/graph_worker.py``), not inside the backend. The
backend NEVER opens Kuzu directly — a Kuzu/torch segfault there would
take the backend down with it.

This module is now a thin shim:

* ``add_episode_from_memory(memory)`` — INSERTs a row into
  ``graph_ingest_queue``. That's it. Zero Kuzu, zero Graphiti,
  non-blocking. The worker picks up queued rows in its own process.

* ``search(query, limit)`` — HTTP GET to ``127.0.0.1:8001/search`` with
  a hard 5s timeout. If the worker is down / hung / disabled, returns
  ``[]`` and Edward falls back to pgvector (``remember_search``).

* ``is_enabled()`` — unchanged; gates both paths on
  ``GRAPHITI_ENABLED``.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

from services.database import async_session, GraphIngestQueueModel


logger = logging.getLogger(__name__)


_WORKER_BASE_URL = os.getenv(
    "GRAPH_WORKER_URL", "http://127.0.0.1:8001"
).rstrip("/")
_SEARCH_TIMEOUT_SECS = 5.0
_STATUS_TIMEOUT_SECS = 3.0


def is_enabled() -> bool:
    return os.getenv("GRAPHITI_ENABLED", "false").lower() in ("1", "true", "yes", "on")


async def add_episode_from_memory(memory) -> None:
    """Dual-write hook — enqueue the memory for the graph worker.

    This is a single Postgres INSERT, typically <1ms. The actual
    extraction + embedding + Kuzu write happens in the worker's
    process. If the worker is down the rows just accumulate and get
    processed when it comes back up."""
    if not is_enabled():
        return
    try:
        row = GraphIngestQueueModel(
            memory_id=getattr(memory, "id", "") or "",
            content=getattr(memory, "content", None) or str(memory),
            source_description=(
                f"Edward memory (type={getattr(memory, 'memory_type', 'unknown')}, "
                f"source={getattr(memory, 'source_type', 'unknown')})"
            ),
            reference_time=getattr(memory, "created_at", None) or datetime.now(timezone.utc).replace(tzinfo=None),
            status="pending",
        )
        async with async_session() as session:
            session.add(row)
            await session.commit()
    except Exception as exc:
        # Enqueue failure must not break pgvector writes.
        logger.warning(
            "graphiti enqueue failed for memory %s: %s",
            getattr(memory, "id", "?"),
            exc,
        )


async def search(query: str, limit: int = 5) -> list[dict[str, Any]]:
    """Multi-hop + temporal search via the worker's HTTP API.

    Returns ``[]`` on any failure — pgvector remains primary."""
    if not is_enabled():
        return []
    try:
        async with httpx.AsyncClient(timeout=_SEARCH_TIMEOUT_SECS) as client:
            resp = await client.get(
                f"{_WORKER_BASE_URL}/search",
                params={"query": query, "limit": limit},
            )
            resp.raise_for_status()
            return resp.json().get("results", [])
    except Exception as exc:
        logger.warning("graphiti search over HTTP failed: %s", exc)
        return []


async def get_worker_status() -> dict[str, Any]:
    """Proxy the worker's /status endpoint. Used by the admin router."""
    if not is_enabled():
        return {"ok": False, "enabled": False}
    try:
        async with httpx.AsyncClient(timeout=_STATUS_TIMEOUT_SECS) as client:
            resp = await client.get(f"{_WORKER_BASE_URL}/status")
            resp.raise_for_status()
            data = resp.json()
            data["enabled"] = True
            return data
    except Exception as exc:
        return {"ok": False, "enabled": True, "error": str(exc)}


async def shutdown() -> None:
    """No-op; the worker is managed by launchd independently."""
    return
