#!/usr/bin/env python3
"""Garbage-collect superseded LangGraph checkpoints.

``langgraph-checkpoint-postgres`` never deletes anything. Every turn on every
thread adds a row to ``checkpoints``, blobs for the channels that changed,
and the turn's pending writes. Edward only ever reads the latest checkpoint
per thread (nothing calls ``aget_state_history``), so everything older than
the newest ``--keep`` checkpoints is dead weight. On one install
(2026-09-05) that was 2.3 GB across three tables, with a single busy
contact's heartbeat thread alone holding 1.6 GB of writes, on a disk that
had already gone down once from being full.

Three deletes, in dependency order:
  1. checkpoints outside the newest N per (thread_id, checkpoint_ns)
  2. checkpoint_writes whose checkpoint no longer exists
  3. checkpoint_blobs not referenced by any surviving checkpoint's
     ``channel_versions`` (a blob is shared by every checkpoint in which that
     channel did not change, so this is computed, not inferred from age)

Dry run by default. Plain ``VACUUM (ANALYZE)`` after ``--apply --vacuum``
marks the space reusable but does not return it to the OS; run one manual
``VACUUM FULL checkpoint_blobs, checkpoint_writes`` during a backend restart
window after the first big apply.

Usage:
    python -m scripts.gc_checkpoints                 # dry run, report only
    python -m scripts.gc_checkpoints --apply
    python -m scripts.gc_checkpoints --apply --vacuum --keep 20
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from collections import defaultdict
from pathlib import Path
from typing import Iterable

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_KEEP = 20
DELETE_BATCH = 5000

CheckpointKey = tuple[str, str, str]           # thread_id, checkpoint_ns, checkpoint_id
BlobKey = tuple[str, str, str, str]            # thread_id, checkpoint_ns, channel, version


# ── pure selection ────────────────────────────────────────────────────


def select_checkpoints_to_drop(rows: Iterable[CheckpointKey], keep: int = DEFAULT_KEEP) -> list[CheckpointKey]:
    """Everything but the newest ``keep`` checkpoints per (thread, namespace).

    checkpoint_id is a uuid6 — time-ordered and lexically sortable — so the
    newest is the lexical max. The latest checkpoint is never dropped, even
    with ``keep=1``.
    """
    keep = max(1, int(keep))
    by_thread: dict[tuple[str, str], list[str]] = defaultdict(list)
    for thread_id, ns, checkpoint_id in rows:
        by_thread[(thread_id, ns)].append(checkpoint_id)
    drop: list[CheckpointKey] = []
    for (thread_id, ns), ids in by_thread.items():
        ids.sort(reverse=True)
        drop.extend((thread_id, ns, cid) for cid in ids[keep:])
    return drop


def select_orphan_blob_keys(
    surviving_checkpoints: Iterable[tuple[str, str, dict]],
    blob_keys: Iterable[BlobKey],
) -> list[BlobKey]:
    """Blobs no surviving checkpoint points at.

    ``channel_versions`` values are strings in the current layout and ints in
    the v1 layout; ``checkpoint_blobs.version`` is text either way, so compare
    as strings.
    """
    referenced: set[BlobKey] = set()
    for thread_id, ns, channel_versions in surviving_checkpoints:
        for channel, version in (channel_versions or {}).items():
            referenced.add((thread_id, ns, channel, str(version)))
    return [key for key in blob_keys if (key[0], key[1], key[2], str(key[3])) not in referenced]


def select_orphan_write_keys(
    surviving_checkpoints: set[CheckpointKey],
    write_keys: Iterable[CheckpointKey],
) -> list[CheckpointKey]:
    return [key for key in write_keys if key not in surviving_checkpoints]


# ── database ──────────────────────────────────────────────────────────


async def run_gc(keep: int = DEFAULT_KEEP, apply: bool = False, vacuum: bool = False) -> dict:
    from sqlalchemy import text
    from services.database import async_session, engine

    report: dict = {"keep": keep, "applied": bool(apply)}

    async with async_session() as session:
        rows = (await session.execute(
            text("SELECT thread_id, checkpoint_ns, checkpoint_id FROM checkpoints")
        )).all()
        all_keys: list[CheckpointKey] = [(r[0], r[1], r[2]) for r in rows]
        drop_checkpoints = select_checkpoints_to_drop(all_keys, keep=keep)
        dropped = set(drop_checkpoints)
        surviving = {k for k in all_keys if k not in dropped}

        write_rows = (await session.execute(
            text("SELECT thread_id, checkpoint_ns, checkpoint_id, octet_length(blob) FROM checkpoint_writes")
        )).all()
        write_bytes = {}
        for r in write_rows:
            write_bytes[(r[0], r[1], r[2])] = write_bytes.get((r[0], r[1], r[2]), 0) + (r[3] or 0)
        drop_writes = select_orphan_write_keys(surviving, list(write_bytes))

        # Blobs: referenced by surviving checkpoints only.
        surviving_cv = (await session.execute(
            text("SELECT thread_id, checkpoint_ns, checkpoint_id, checkpoint->'channel_versions' FROM checkpoints")
        )).all()
        surviving_cv = [(r[0], r[1], r[3] or {}) for r in surviving_cv if (r[0], r[1], r[2]) in surviving]
        blob_rows = (await session.execute(
            text("SELECT thread_id, checkpoint_ns, channel, version, octet_length(blob) FROM checkpoint_blobs")
        )).all()
        blob_bytes = {(r[0], r[1], r[2], r[3]): (r[4] or 0) for r in blob_rows}
        drop_blobs = select_orphan_blob_keys(surviving_cv, list(blob_bytes))

        report.update(
            checkpoints_total=len(all_keys),
            checkpoints_to_drop=len(drop_checkpoints),
            writes_total=len(write_rows),
            writes_to_drop=len(drop_writes),
            writes_bytes_to_drop=sum(write_bytes[k] for k in drop_writes),
            blobs_total=len(blob_rows),
            blobs_to_drop=len(drop_blobs),
            blobs_bytes_to_drop=sum(blob_bytes[k] for k in drop_blobs),
            threads_touched=len({(t, ns) for t, ns, _ in drop_checkpoints}),
        )

        if not apply:
            return report

        for batch in _batches(drop_checkpoints):
            await session.execute(
                text("DELETE FROM checkpoints WHERE thread_id = :t AND checkpoint_ns = :ns AND checkpoint_id = :c"),
                [{"t": t, "ns": ns, "c": c} for t, ns, c in batch],
            )
        for batch in _batches(drop_writes):
            await session.execute(
                text("DELETE FROM checkpoint_writes WHERE thread_id = :t AND checkpoint_ns = :ns AND checkpoint_id = :c"),
                [{"t": t, "ns": ns, "c": c} for t, ns, c in batch],
            )
        for batch in _batches(drop_blobs):
            await session.execute(
                text("DELETE FROM checkpoint_blobs WHERE thread_id = :t AND checkpoint_ns = :ns AND channel = :ch AND version = :v"),
                [{"t": t, "ns": ns, "ch": ch, "v": v} for t, ns, ch, v in batch],
            )
        await session.commit()

    if vacuum:
        # VACUUM cannot run inside a transaction block.
        async with engine.connect() as conn:
            conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
            await conn.execute(text("VACUUM (ANALYZE) checkpoints, checkpoint_writes, checkpoint_blobs"))
        report["vacuumed"] = True
    return report


def _batches(items: list, size: int = DELETE_BATCH):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apply", action="store_true", help="delete (default: dry run)")
    parser.add_argument("--vacuum", action="store_true", help="VACUUM (ANALYZE) the three tables after --apply")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"checkpoints to keep per thread (default {DEFAULT_KEEP})")
    args = parser.parse_args()

    report = asyncio.run(run_gc(keep=args.keep, apply=args.apply, vacuum=args.apply and args.vacuum))
    print(
        f"checkpoints: {report['checkpoints_to_drop']} of {report['checkpoints_total']} to drop "
        f"across {report['threads_touched']} threads (keep {report['keep']})\n"
        f"writes:      {report['writes_to_drop']} of {report['writes_total']} ({report['writes_bytes_to_drop'] / 1e6:.0f} MB)\n"
        f"blobs:       {report['blobs_to_drop']} of {report['blobs_total']} ({report['blobs_bytes_to_drop'] / 1e6:.0f} MB)"
    )
    if report["applied"]:
        print("APPLIED" + (" + vacuumed" if report.get("vacuumed") else ""))
    else:
        print("DRY RUN — nothing deleted. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
