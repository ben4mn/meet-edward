#!/usr/bin/env python3
"""Daily memory-health report, recorded for the health report.

Measures the memory store's 30-day health targets (docs/architecture/memory.md)
against the live database and the backend log. Runs from the
``<prefix>.memory-report`` LaunchAgent (``EDWARD_LAUNCHD_PREFIX``, default
``com.meet-edward``) at 09:05, after the deep health check; ``--push`` (the
name the LaunchAgent passes) writes one ``memory_report`` ledger row — the
verdict and the summary counts — that ``GET /api/health/report`` shows.
Nothing reaches the owner from here.

Targets (a miss flips the title to "needs a look"):
  memories/day  < 15          new live memories in the last 24h
  entity ok     >= 80%        when at least 5 calls were made
  budget        0             consolidation cycles that exhausted their LLM budget
  checkpoints   < 500 MB      the three langgraph tables
  disk          >= 10 GB free

No push since the proactivity rebuild (2026-10-11): the figures used to
become a due-now scheduled event in the day's own thread
(``memory-report-YYYY-MM-DD``) where Edward wrote an update and pushed the
owner the link — a push a day about the memory store's plumbing. The owner
is reached through ``reach_service`` now, for what matters to them; whoever
runs a health check reads the row.

Usage:
    python scripts/memory_health_report.py            # print only
    python scripts/memory_health_report.py --push     # print and record the ledger row
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import shutil
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

LOG_PATH = Path(os.getenv("EDWARD_BACKEND_LOG", "/tmp/edward-backend.log"))

TARGET_MEMORIES_PER_DAY = 15
TARGET_ENTITY_OK_RATE = 0.80
MIN_ENTITY_CALLS = 5
TARGET_CHECKPOINT_MB = 500
TARGET_DISK_FREE_GB = 10


# ── pure ──────────────────────────────────────────────────────────────


def assess(m: dict) -> dict:
    """Turn raw metrics into a verdict: ``status``, ``misses`` (what is off
    target, worst first) and ``lines`` (every figure, one per line)."""
    misses: list[str] = []
    lines: list[str] = []

    per_day = m["memories_last_24h"]
    by_src = ", ".join(f"{k} {v}" for k, v in sorted(m.get("memories_by_source", {}).items(), key=lambda kv: -kv[1]))
    line = f"memories/day {per_day} (target <{TARGET_MEMORIES_PER_DAY})" + (f": {by_src}" if by_src else "")
    if per_day >= TARGET_MEMORIES_PER_DAY:
        misses.append(line)
    lines.append(line)

    ok, to = m["entity_ok"], m["entity_timeouts"]
    total = ok + to
    if total >= MIN_ENTITY_CALLS:
        rate = ok / total
        line = f"entity ok {ok}/{total} ({rate:.0%}, target ≥{TARGET_ENTITY_OK_RATE:.0%})"
        if rate < TARGET_ENTITY_OK_RATE:
            misses.append(line)
    else:
        line = f"entity ok {ok}/{total} (too few calls to judge)"
    lines.append(line)

    line = f"budget exhausted {m['budget_exhausted']}/{m['cycles']} cycles (target 0)"
    if m["budget_exhausted"] > 0:
        misses.append(line)
    lines.append(line)

    line = f"checkpoint tables {m['checkpoint_mb']} MB (target <{TARGET_CHECKPOINT_MB})"
    if m["checkpoint_mb"] >= TARGET_CHECKPOINT_MB:
        misses.append(line)
    lines.append(line)

    line = f"disk free {m['disk_free_gb']} GB (floor {TARGET_DISK_FREE_GB})"
    if m["disk_free_gb"] < TARGET_DISK_FREE_GB:
        misses.append(line)
    lines.append(line)

    flags = m.get("open_flags", 0)
    if flags:
        lines.append(f"{flags} contradiction{'s' if flags != 1 else ''} waiting for you in Settings → Memory → Dreaming")
    lines.append(f"live {m['live']}, never used {m['unused_live']}")

    return {"status": "needs a look" if misses else "on track", "misses": misses, "lines": lines}


def format_push(verdict: dict) -> tuple[str, str]:
    """Title carries the verdict; the body leads with what is off target,
    then the rest, and stays short enough for a lock screen."""
    title = f"Memory: {verdict['status']}"
    ordered = verdict["misses"] + [l for l in verdict["lines"] if l not in verdict["misses"]]
    body = "\n".join(ordered)
    if len(body) > 400:
        body = body[:397].rstrip() + "…"
    return title, body


SUMMARY_KEYS = ("memories_last_24h", "live", "unused_live", "entity_ok", "entity_timeouts", "budget_exhausted",
                "cycles", "open_flags", "checkpoint_mb", "disk_free_gb")


def summary_counts(m: dict, verdict: dict) -> dict:
    """The ledger row's detail: the verdict, how many targets missed, and
    the raw counts (well under the ledger's 600-char detail cap)."""
    out = {"status": verdict["status"], "misses": len(verdict["misses"])}
    out.update({k: m[k] for k in SUMMARY_KEYS if k in m})
    return out


# ── collection ────────────────────────────────────────────────────────


def _log_window(hours: int = 24) -> str:
    if not LOG_PATH.exists():
        return ""
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    # Local-time prefixes in the log; a 1h skew on a 24h window is fine.
    since_local = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S")
    keep = []
    with LOG_PATH.open(errors="replace") as fh:
        for line in fh:
            if re.match(r"\d{4}-", line) and line[:19] >= since_local:
                keep.append(line)
    return "".join(keep)


async def collect() -> dict:
    from sqlalchemy import text
    from services.database import async_session

    async with async_session() as session:
        row = (await session.execute(text(
            "select count(*) filter (where valid_until is null or valid_until > (now() at time zone 'utc')) as live, "
            "count(*) filter (where (valid_until is null or valid_until > (now() at time zone 'utc')) and used_count = 0) as unused_live, "
            "count(*) filter (where created_at > (now() at time zone 'utc') - interval '24 hours') as last_24h "
            "from memories"
        ))).one()
        by_source = (await session.execute(text(
            "select coalesce(c.source, 'unknown'), count(*) from memories m "
            "left join conversations c on c.id = m.source_conversation_id "
            "where m.created_at > (now() at time zone 'utc') - interval '24 hours' group by 1"
        ))).all()
        open_flags = (await session.execute(text(
            "select count(*) from memory_flags where resolved = false"
        ))).scalar_one()
        ckpt_bytes = (await session.execute(text(
            "select coalesce(sum(pg_total_relation_size(relid)),0)::bigint from pg_stat_user_tables "
            "where relname in ('checkpoints','checkpoint_blobs','checkpoint_writes')"
        ))).scalar_one()

    window = _log_window()
    return {
        "memories_last_24h": int(row.last_24h),
        "memories_by_source": {k: int(v) for k, v in by_source},
        "live": int(row.live),
        "unused_live": int(row.unused_live),
        "entity_ok": window.count("[ENTITY] ok "),
        "entity_timeouts": window.count("[ENTITY] timed out"),
        "budget_exhausted": window.count("LLM budget exhausted"),
        "cycles": window.count("consolidation cycle complete"),
        "open_flags": int(open_flags),
        "checkpoint_mb": int(ckpt_bytes) // (1024 * 1024),
        "disk_free_gb": shutil.disk_usage("/").free // (1024 ** 3),
    }


async def record(m: dict, verdict: dict) -> Optional[int]:
    """One ``memory_report`` ledger row (``source="script"``, ``outcome="ok"``,
    the verdict as its reason). Returns the row id, None if the write failed."""
    from services import event_ledger

    return await event_ledger.record_event_now(
        kind="memory_report", source="script", outcome="ok", reason=verdict["status"],
        detail=summary_counts(m, verdict),
    )


async def main_async(push: bool) -> int:
    from dotenv import load_dotenv
    load_dotenv(BACKEND_DIR.parent / ".env")

    metrics = await collect()
    verdict = assess(metrics)
    title, body = format_push(verdict)
    print(title)
    print(body)

    if not push:
        print("(no --push: not recorded)")
        return 0

    row_id = await record(metrics, verdict)
    if row_id is None:
        print("could not write the memory_report ledger row", file=sys.stderr)
        return 1
    print(f"recorded as ledger_events row {row_id} (kind memory_report); /api/health/report shows it")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--push", action="store_true",
                        help="record the report as a memory_report ledger row (the name the LaunchAgent passes)")
    args = parser.parse_args()
    return asyncio.run(main_async(push=args.push))


if __name__ == "__main__":
    sys.exit(main())
