"""One-off: stamp ``origin`` / ``about`` on memories that predate the columns.

Upgrade helper: only an install that has been running since the March
2026 public release (its schema and rows) needs this; a fresh install
has nothing to backfill.

Mind rebuild Phase 1 (2026-09-18). Every existing row got the defaults
``origin='heard'``, ``about='owner'`` from the migration. Two groups are
knowably different and cheap to fix:

  * rows whose ``source_conversation_id`` is a heartbeat (iMessage) thread
    were read by Edward, not said by the owner -> ``origin='read'``;
  * rows from the surfaces pulse (``edward-surfaces``) are Edward's own
    observations -> ``origin='thought'``, ``about='self'``.

Whom a heartbeat row is *about* cannot be told from the row alone, so
``about`` is left at ``owner`` there; retrieval never filters on it unless a
caller asks, and the mind's own reads (``about='self'``) are unaffected.

Dry run by default. ``--apply`` writes. Read-only SQL otherwise, per the
repo rule about touching production from scripts.

    cd backend && source .venv/bin/activate
    python -m scripts.backfill_memory_origin            # report
    python -m scripts.backfill_memory_origin --apply    # write
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import text  # noqa: E402

from services.database import async_session  # noqa: E402

HEARTBEAT_READ_SQL = """
    UPDATE memories m
       SET origin = 'read'
     WHERE m.origin = 'heard'
       AND m.source_conversation_id IN (SELECT conversation_id FROM imessage_chat_conversations)
"""
HEARTBEAT_COUNT_SQL = """
    SELECT count(*) FROM memories m
     WHERE m.origin = 'heard'
       AND m.source_conversation_id IN (SELECT conversation_id FROM imessage_chat_conversations)
"""
PULSE_THOUGHT_SQL = """
    UPDATE memories
       SET origin = 'thought', about = 'self'
     WHERE origin = 'heard' AND about = 'owner'
       AND source_conversation_id = 'edward-surfaces'
"""
PULSE_COUNT_SQL = """
    SELECT count(*) FROM memories
     WHERE origin = 'heard' AND about = 'owner'
       AND source_conversation_id = 'edward-surfaces'
"""


def plan_backfill(heartbeat_rows: int, pulse_rows: int) -> list[str]:
    """Pure: the report lines for a dry run or an apply."""
    return [
        f"heartbeat threads -> origin=read: {heartbeat_rows} row(s)",
        f"edward-surfaces  -> origin=thought, about=self: {pulse_rows} row(s)",
    ]


async def run(apply: bool) -> dict[str, int]:
    async with async_session() as session:
        hb = (await session.execute(text(HEARTBEAT_COUNT_SQL))).scalar() or 0
        pulse = (await session.execute(text(PULSE_COUNT_SQL))).scalar() or 0
        for line in plan_backfill(hb, pulse):
            print(line)
        if not apply:
            print("dry run; pass --apply to write")
            return {"heartbeat": hb, "pulse": pulse, "applied": 0}
        r1 = await session.execute(text(HEARTBEAT_READ_SQL))
        r2 = await session.execute(text(PULSE_THOUGHT_SQL))
        await session.commit()
        applied = (r1.rowcount or 0) + (r2.rowcount or 0)
        print(f"applied: {applied} row(s) updated")
        return {"heartbeat": hb, "pulse": pulse, "applied": applied}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--apply", action="store_true", help="write the changes (default: dry run)")
    args = parser.parse_args()
    asyncio.run(run(apply=args.apply))


if __name__ == "__main__":
    main()
