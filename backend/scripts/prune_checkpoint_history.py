"""Reclaim old LangGraph checkpoint versions.

The checkpoint trim (``_trim_checkpoint_history``) bounds what each new
checkpoint holds, but langgraph-checkpoint-postgres keeps every earlier
checkpoint, its blob versions and its pending writes forever. On
2026-09-14 that was 2,908 unreferenced blob versions (1.9 GB logical,
~800 MB on disk) and 766 MB of writes, most of it the pulse thread's
history before the trim ever worked.

Keeps, per thread: the latest checkpoint, the blob versions it references,
and its writes. Deletes everything older. Time travel to earlier
checkpoints is not something Edward uses.

    .venv/bin/python scripts/prune_checkpoint_history.py --dry-run   # report only
    .venv/bin/python scripts/prune_checkpoint_history.py --apply     # delete, then VACUUM

It runs against whatever DATABASE_URL names (the live database by default);
pass --apply deliberately. The URL is printed without its password.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

LATEST = """
with latest as (
  select distinct on (thread_id, checkpoint_ns) thread_id, checkpoint_ns, checkpoint_id, checkpoint
  from checkpoints order by thread_id, checkpoint_ns, checkpoint_id desc
), referenced as (
  select l.thread_id, l.checkpoint_ns, kv.key as channel, kv.value as version
  from latest l, jsonb_each_text(l.checkpoint->'channel_versions') kv
)
"""

MEASURE = LATEST + """
select
  (select count(*) from checkpoint_blobs b left join referenced r
     on r.thread_id=b.thread_id and r.checkpoint_ns=b.checkpoint_ns and r.channel=b.channel and r.version=b.version
   where r.thread_id is null) as blob_versions,
  (select coalesce(sum(octet_length(b.blob)),0) from checkpoint_blobs b left join referenced r
     on r.thread_id=b.thread_id and r.checkpoint_ns=b.checkpoint_ns and r.channel=b.channel and r.version=b.version
   where r.thread_id is null) as blob_bytes,
  (select count(*) from checkpoints c left join latest l
     on l.thread_id=c.thread_id and l.checkpoint_ns=c.checkpoint_ns and l.checkpoint_id=c.checkpoint_id
   where l.thread_id is null) as old_checkpoints,
  (select count(*) from checkpoint_writes w left join latest l
     on l.thread_id=w.thread_id and l.checkpoint_ns=w.checkpoint_ns and l.checkpoint_id=w.checkpoint_id
   where l.thread_id is null) as old_writes
"""

DELETE_BLOBS = LATEST + """
delete from checkpoint_blobs b
using (select b2.thread_id, b2.checkpoint_ns, b2.channel, b2.version
       from checkpoint_blobs b2 left join referenced r
         on r.thread_id=b2.thread_id and r.checkpoint_ns=b2.checkpoint_ns and r.channel=b2.channel and r.version=b2.version
       where r.thread_id is null) stale
where b.thread_id=stale.thread_id and b.checkpoint_ns=stale.checkpoint_ns and b.channel=stale.channel and b.version=stale.version
"""

DELETE_WRITES = LATEST + """
delete from checkpoint_writes w
using (select w2.thread_id, w2.checkpoint_ns, w2.checkpoint_id from checkpoint_writes w2 left join latest l
         on l.thread_id=w2.thread_id and l.checkpoint_ns=w2.checkpoint_ns and l.checkpoint_id=w2.checkpoint_id
       where l.thread_id is null) stale
where w.thread_id=stale.thread_id and w.checkpoint_ns=stale.checkpoint_ns and w.checkpoint_id=stale.checkpoint_id
"""

DELETE_CHECKPOINTS = LATEST + """
delete from checkpoints c
using (select c2.thread_id, c2.checkpoint_ns, c2.checkpoint_id from checkpoints c2 left join latest l
         on l.thread_id=c2.thread_id and l.checkpoint_ns=c2.checkpoint_ns and l.checkpoint_id=c2.checkpoint_id
       where l.thread_id is null) stale
where c.thread_id=stale.thread_id and c.checkpoint_ns=stale.checkpoint_ns and c.checkpoint_id=stale.checkpoint_id
"""


async def main(apply: bool) -> int:
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    from services.database import DATABASE_URL, engine

    print(f"database: {make_url(DATABASE_URL).render_as_string(hide_password=True)}")
    async with engine.connect() as conn:
        row = (await conn.execute(text(MEASURE))).one()
        print(f"unreferenced blob versions: {row.blob_versions} ({row.blob_bytes/1048576:.0f} MB logical)")
        print(f"old checkpoints: {row.old_checkpoints}   old pending writes: {row.old_writes}")
    if not apply:
        print("dry run — nothing deleted (pass --apply)")
        return 0
    async with engine.begin() as conn:
        b = (await conn.execute(text(DELETE_BLOBS))).rowcount
        w = (await conn.execute(text(DELETE_WRITES))).rowcount
        c = (await conn.execute(text(DELETE_CHECKPOINTS))).rowcount
        print(f"deleted: {b} blob versions, {w} writes, {c} checkpoints")
    async with engine.connect() as conn:
        # AsyncConnection.execution_options is a coroutine (the 2026-09-27
        # reset's --apply deleted, then crashed here before VACUUM).
        conn = await conn.execution_options(isolation_level="AUTOCOMMIT")
        await conn.execute(text("VACUUM (ANALYZE) checkpoint_blobs, checkpoint_writes, checkpoints"))
        print("vacuumed")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(main(apply=args.apply)))
