#!/usr/bin/env python3
"""Run the memory forgetting pass.

Dry run by default. Forgetting is the one memory operation whose mistakes are
invisible — a memory that should have been kept simply stops appearing, with
nothing to notice — so writing is opt-in.

Retirement stamps ``valid_until``; it never deletes. Every row one pass
retires carries the same stamp, which ``--apply`` prints (and the reflect
wake's ledger row keeps, ``detail.decay.stamp``); ``--revert-stamp`` undoes
exactly that pass. ``--revert-since`` clears every ``valid_until`` at or
after a time — which includes every horizon still in the future (open
intentions, instructions with a window), so prefer the stamp.

Usage:
    python -m scripts.run_memory_decay                      # dry run
    python -m scripts.run_memory_decay --apply
    python -m scripts.run_memory_decay --apply --limit 50   # cautious first bite
    python -m scripts.run_memory_decay --revert-stamp 2026-09-27T09:00:05.123456
    python -m scripts.run_memory_decay --revert-since 2026-08-21T00:00:00
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def _parse_stamp(stamp: str) -> datetime:
    """An ISO time as the naive UTC the column holds. asyncpg binds a
    datetime, never a string (the old ``CAST(:stamp AS timestamp)`` with a
    string argument raised DataError on every revert, 2026-09-27)."""
    parsed = datetime.fromisoformat(stamp.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


async def _revert_since(stamp: str) -> int:
    from sqlalchemy import text

    from services.database import async_session

    async with async_session() as session:
        result = await session.execute(
            text(
                "UPDATE memories SET valid_until = NULL "
                "WHERE valid_until >= :stamp"
            ),
            {"stamp": _parse_stamp(stamp)},
        )
        await session.commit()
        return result.rowcount


async def _revert_stamp(stamp: str) -> int:
    """Clear ``valid_until`` from exactly the rows one pass retired (they all
    carry its stamp, to the microsecond)."""
    from sqlalchemy import text

    from services.database import async_session

    async with async_session() as session:
        result = await session.execute(
            text("UPDATE memories SET valid_until = NULL WHERE valid_until = :stamp"),
            {"stamp": _parse_stamp(stamp)},
        )
        await session.commit()
        return result.rowcount


async def main() -> int:
    from services.memory_decay_service import (
        MIN_AGE_DAYS,
        RETENTION_FLOOR,
        decay_stale_memories,
    )

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write (default: dry run)")
    parser.add_argument("--limit", type=int, default=None, help="cap rows retired")
    parser.add_argument(
        "--revert-stamp", metavar="ISO_TS", help="clear valid_until from the rows one pass retired"
    )
    parser.add_argument(
        "--revert-since", metavar="ISO_TS",
        help="clear valid_until at/after (also clears future horizons)",
    )
    args = parser.parse_args()

    if args.revert_stamp:
        print(f"reverted: {await _revert_stamp(args.revert_stamp)} rows")
        return 0
    if args.revert_since:
        print(f"reverted: {await _revert_since(args.revert_since)} rows")
        return 0

    result = await decay_stale_memories(dry_run=not args.apply, limit=args.limit)

    pct = (
        result["candidates"] / result["scanned"] * 100 if result["scanned"] else 0.0
    )
    print(
        f"scanned {result['scanned']} live memories | "
        f"{result['candidates']} below retention floor {RETENTION_FLOOR} "
        f"({pct:.1f}%), min age {MIN_AGE_DAYS}d"
    )

    if result["breakdown"]:
        print("\nby temporal_nature/source_type:")
        for key, n in sorted(result["breakdown"].items(), key=lambda kv: -kv[1]):
            print(f"  {n:>5}  {key}")

    by_rule = {k: v for k, v in (result.get("by_rule") or {}).items() if v}
    if by_rule:
        print("\nby rule:")
        for rule, n in sorted(by_rule.items(), key=lambda kv: -kv[1]):
            print(f"  {n:>5}  {rule}")

    if result["sample"]:
        print("\nsample of what would be forgotten:")
        for content in result["sample"]:
            print(f"  - {content}")

    if args.apply:
        print(f"\nretired: {result['retired']} rows (valid_until stamped)")
        if result.get("stamp"):
            print(f"stamp: {result['stamp']}")
            print(f"undo with: --revert-stamp {result['stamp']}")
    else:
        print("\nDRY RUN — nothing written. Re-run with --apply.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
