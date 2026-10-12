"""Backfill Edward's existing memories into Graphiti (C Phase 2).

For an install that turns the knowledge graph on (``GRAPHITI_ENABLED=true``)
after it already has memories. Iterates every row in ``memories`` and emits
a Graphiti episode for each so the knowledge-graph side catches up with what
pgvector already knows. Runs via ``build_background_llm()`` (the
``background`` route: Claude under ``EDWARD_LLM_PROVIDER=anthropic``, else
the Codex fallback), no OpenAI API key required, and uses the local
sentence-transformers embedder. Per-episode extraction
is cached by Graphiti, so re-running after a crash is safe — episodes
already in the graph are skipped.

Usage (run from ``backend/``):

    source .venv/bin/activate
    GRAPHITI_ENABLED=true python -m scripts.backfill_graphiti \\
        --limit 50 \\
        --since 2025-01-01

Flags:
    --limit N     process at most N memories (default: all)
    --since YYYY-MM-DD   only memories created on/after this date
    --dry-run     count what would be processed without writing
    --concurrency K   max episodes in flight at once (default: 1;
                      Kuzu AsyncConnection serializes writes anyway)
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

# macOS + Python 3.12 + sentence-transformers multiprocessing = silent
# subprocess death after the first ingestion. Set these early (before any
# multiprocessing-using lib imports) so the HF model's worker pool
# uses spawn instead of fork. Prefer the POST /api/memory/graph/backfill
# endpoint in the live backend — it sidesteps this entire class of issue
# because sentence-transformers is loaded once at backend startup.
os.environ.setdefault("OBJC_DISABLE_INITIALIZE_FORK_SAFETY", "YES")
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
try:
    import multiprocessing
    multiprocessing.set_start_method("spawn", force=True)
except RuntimeError:
    pass
from datetime import datetime, timezone
from pathlib import Path

# Make ``services.*`` imports resolve when run from the repo root.
BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _flush_print(*args, **kwargs):
    """Always flush — Python aggressively buffers stdout when a worker
    thread pool is active (sentence-transformers, Kuzu executor) and
    progress lines get swallowed until process exit, making the run
    look hung."""
    kwargs.setdefault("flush", True)
    print(*args, **kwargs)


async def _fetch_memories(limit: int | None, since: datetime | None):
    from sqlalchemy import select
    from services.database import async_session, MemoryModel

    async with async_session() as session:
        stmt = select(MemoryModel).order_by(MemoryModel.created_at.asc())
        if since is not None:
            stmt = stmt.where(MemoryModel.created_at >= since.replace(tzinfo=None))
        if limit is not None:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        return list(result.scalars().all())


async def _ingest_one(graphiti, memory) -> tuple[bool, str]:
    from graphiti_core.nodes import EpisodeType

    try:
        await graphiti.add_episode(
            name=f"memory_{memory.id[:8]}",
            episode_body=memory.content,
            source=EpisodeType.text,
            source_description=(
                f"Edward memory (type={memory.memory_type or 'unknown'}, "
                f"source={memory.source_type or 'unknown'}, "
                f"importance={memory.importance or 0.5:.2f})"
            ),
            reference_time=memory.created_at or datetime.now(timezone.utc),
        )
        return True, ""
    except Exception as exc:
        return False, str(exc)


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--since", type=str, default=None, help="YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--concurrency", type=int, default=1)
    args = parser.parse_args()

    since = None
    if args.since:
        since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc)

    # Ensure feature flag is on for this script regardless of parent env.
    os.environ["GRAPHITI_ENABLED"] = "true"

    memories = await _fetch_memories(args.limit, since)
    _flush_print(f"[backfill] scope: {len(memories)} memories")

    if args.dry_run:
        for m in memories[:5]:
            _flush_print(
                f"  sample id={m.id[:8]} type={m.memory_type} "
                f"content={(m.content or '')[:60]!r}"
            )
        return

    from services.graph_memory_service import get_graphiti, shutdown

    graphiti = await get_graphiti()
    if graphiti is None:
        _flush_print("[backfill] Graphiti init failed — aborting")
        sys.exit(2)

    ok = 0
    fail = 0
    start = time.time()
    sem = asyncio.Semaphore(max(1, args.concurrency))

    # Sequential loop (concurrency collapsed to 1 in practice anyway — Kuzu
    # AsyncConnection serializes writes and asyncio.gather with a
    # Semaphore=1 adds no throughput). Sequential is also easier to debug
    # and produces linear progress logs.
    for i, m in enumerate(memories, 1):
        _flush_print(f"[backfill] ({i}/{len(memories)}) ingesting id={m.id[:8]} type={m.memory_type}")
        success, err = await _ingest_one(graphiti, m)
        if success:
            ok += 1
        else:
            fail += 1
            _flush_print(f"[backfill] fail id={m.id[:8]} err={err[:200]}")
        if i % 10 == 0:
            elapsed = time.time() - start
            rate = i / elapsed if elapsed else 0
            _flush_print(
                f"[backfill] checkpoint {i}/{len(memories)} "
                f"ok={ok} fail={fail} ({rate:.2f}/s)"
            )

    elapsed = time.time() - start
    _flush_print(
        f"[backfill] done in {elapsed:.1f}s: "
        f"{ok} succeeded, {fail} failed (of {len(memories)})"
    )
    await shutdown()
    # Dispose the SQLAlchemy engine so its asyncpg pool closes cleanly —
    # otherwise the worker threads linger holding PG connections past
    # process exit and we exhaust ``max_connections`` over time.
    try:
        from services.database import engine
        await engine.dispose()
    except Exception:
        pass


if __name__ == "__main__":
    try:
        asyncio.run(main())
    finally:
        # Final belt-and-suspenders cleanup in case main() raised.
        try:
            import asyncio as _a
            from services.database import engine as _eng
            _a.run(_eng.dispose())
        except Exception:
            pass
