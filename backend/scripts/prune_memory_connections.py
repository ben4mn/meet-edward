#!/usr/bin/env python3
"""Backfill real strengths onto memory connections and prune the surplus.

The live graph was written with a flat ``strength = 0.5`` on all 21,326
edges, so the weight dimension carried no information — retrieval's
``score += 0.1 * strength`` was a constant. Median degree is 11 and max is
300, while spreading activation pulls in at most 5 memories per query.

This does two things:
  1. Restamps every edge with the strength its connection_type deserves.
  2. Drops edges beyond MAX_CONNECTIONS_PER_MEMORY per memory, keeping the
     strongest.

Unlike memories, connections are deleted rather than retired: an edge has no
independent content, and consolidation rebuilds the graph each cycle.

Usage:
    python -m scripts.prune_memory_connections            # dry run
    python -m scripts.prune_memory_connections --apply
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


async def main() -> int:
    from sqlalchemy import text

    from services.consolidation_service import (
        CONNECTION_TYPE_STRENGTH,
        MAX_CONNECTIONS_PER_MEMORY,
        select_surplus_connections,
        strength_for_connection_type,
    )
    from services.database import async_session

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    args = parser.parse_args()

    async with async_session() as session:
        rows = (
            await session.execute(
                text(
                    "SELECT id, memory_id_a, memory_id_b, connection_type, strength "
                    "FROM memory_connections"
                )
            )
        ).fetchall()

    edges = [
        {
            "id": r.id,
            "memory_id_a": r.memory_id_a,
            "memory_id_b": r.memory_id_b,
            "connection_type": r.connection_type,
            "strength": strength_for_connection_type(r.connection_type),
        }
        for r in rows
    ]

    surplus = select_surplus_connections(edges)
    surplus_ids = [e["id"] for e in surplus]

    by_type: dict[str, int] = {}
    for e in surplus:
        t = e["connection_type"] or "unknown"
        by_type[t] = by_type.get(t, 0) + 1

    print(f"edges: {len(edges)}  cap: {MAX_CONNECTIONS_PER_MEMORY}/memory")
    print(f"strengths: {CONNECTION_TYPE_STRENGTH}")
    print(f"surplus: {len(surplus)}  keeping: {len(edges) - len(surplus)}")
    if by_type:
        print("\nsurplus by type:")
        for t, n in sorted(by_type.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>6}  {t}")

    if not args.apply:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
        return 0

    async with async_session() as session:
        for connection_type in set(e["connection_type"] for e in edges):
            await session.execute(
                text(
                    "UPDATE memory_connections SET strength = :s "
                    "WHERE connection_type = :t"
                ),
                {"s": strength_for_connection_type(connection_type), "t": connection_type},
            )
        deleted = 0
        for i in range(0, len(surplus_ids), 5000):
            chunk = surplus_ids[i : i + 5000]
            result = await session.execute(
                text("DELETE FROM memory_connections WHERE id = ANY(:ids)"),
                {"ids": chunk},
            )
            deleted += result.rowcount
        await session.commit()

    print(f"\nrestamped strengths; deleted {deleted} surplus edges")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
