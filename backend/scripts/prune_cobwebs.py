"""Clear the cobwebs: month-old leftovers nobody engaged with.

  * conversations from cron fires (scheduled_event) and Edward's own
    thoughts (edward_thought) older than 30 days, never engaged
    (message_count <= 2, not notified) — plus their LangGraph checkpoints,
    which the app's delete endpoint leaves behind
  * scheduled_events rows finished/cancelled/failed more than 30 days ago
  * self_observations still 'open' after 21 days -> 'superseded'
  * orchestrator_tasks cancelled/failed more than 30 days ago
  * ledger_events kind='mind' (the mind's wake rows) older than 30 days —
    turn_ledger.prune() does the same at RETENTION_DAYS; this makes the
    count visible in the same report

Never touches: user or inbound conversations, heartbeat threads (they map
to live iMessage chats), the pinned edward-surfaces conversation,
memories, documents.

Run from backend/:  python scripts/prune_cobwebs.py [--dry-run] [--days 30]
"""
import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import text  # noqa: E402

from services.database import async_session  # noqa: E402

PRUNE_SOURCES = ("scheduled_event", "edward_thought")


async def main(dry_run: bool, days: int, obs_days: int) -> None:
    async with async_session() as session:
        conv_ids = [
            r[0]
            for r in (
                await session.execute(
                    text(
                        """
                        SELECT id FROM conversations
                        WHERE source = ANY(:sources)
                          AND id <> 'edward-surfaces'
                          AND updated_at < now() - make_interval(days => :days)
                          AND coalesce(message_count, 0) <= 2
                          AND coalesce(notified_user, false) = false
                          -- journal entries are the record of what he chose to do (2026-09-25)
                          AND coalesce(context_pack, '') NOT LIKE '%"kind": "mind_action"%'
                          AND coalesce(context_pack, '') NOT LIKE '%"kind":"mind_action"%'
                        """
                    ),
                    {"sources": list(PRUNE_SOURCES), "days": days},
                )
            ).all()
        ]
        n_events = (
            await session.execute(
                text(
                    "SELECT count(*) FROM scheduled_events WHERE status IN ('completed','cancelled','failed') "
                    "AND coalesce(updated_at, created_at) < now() - make_interval(days => :days)"
                ),
                {"days": days},
            )
        ).scalar()
        n_obs = (
            await session.execute(
                text("SELECT count(*) FROM self_observations WHERE status='open' AND created_at < now() - make_interval(days => :d)"),
                {"d": obs_days},
            )
        ).scalar()
        n_tasks = (
            await session.execute(
                text(
                    "SELECT count(*) FROM orchestrator_tasks WHERE status IN ('cancelled','failed') "
                    "AND created_at < now() - make_interval(days => :days)"
                ),
                {"days": days},
            )
        ).scalar()
        n_mind = (
            await session.execute(
                text("SELECT count(*) FROM ledger_events WHERE kind='mind' AND created_at < now() - make_interval(days => :days)"),
                {"days": days},
            )
        ).scalar()
        n_ckpt = 0
        if conv_ids:
            n_ckpt = (
                await session.execute(
                    text("SELECT count(*) FROM checkpoints WHERE thread_id = ANY(:ids)"), {"ids": conv_ids}
                )
            ).scalar()

        verb = "would remove" if dry_run else "removing"
        print(f"{verb} {len(conv_ids)} conversations ({n_ckpt} checkpoints), {n_events} finished event rows, "
              f"{n_tasks} dead tasks, {n_mind} old mind wake rows; "
              f"{'would mark' if dry_run else 'marking'} {n_obs} stale observations superseded")
        if dry_run:
            return

        if conv_ids:
            for tbl in ("checkpoint_writes", "checkpoint_blobs", "checkpoints"):
                await session.execute(text(f"DELETE FROM {tbl} WHERE thread_id = ANY(:ids)"), {"ids": conv_ids})
            await session.execute(text("DELETE FROM conversations WHERE id = ANY(:ids)"), {"ids": conv_ids})
        await session.execute(
            text(
                "DELETE FROM scheduled_events WHERE status IN ('completed','cancelled','failed') "
                "AND coalesce(updated_at, created_at) < now() - make_interval(days => :days)"
            ),
            {"days": days},
        )
        await session.execute(
            text("UPDATE self_observations SET status='superseded' WHERE status='open' AND created_at < now() - make_interval(days => :d)"),
            {"d": obs_days},
        )
        await session.execute(
            text(
                "DELETE FROM orchestrator_tasks WHERE status IN ('cancelled','failed') "
                "AND created_at < now() - make_interval(days => :days)"
            ),
            {"days": days},
        )
        await session.execute(
            text("DELETE FROM ledger_events WHERE kind='mind' AND created_at < now() - make_interval(days => :days)"),
            {"days": days},
        )
        await session.commit()
        print("done")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--observation-days", type=int, default=21)
    a = ap.parse_args()
    asyncio.run(main(a.dry_run, a.days, a.observation_days))
