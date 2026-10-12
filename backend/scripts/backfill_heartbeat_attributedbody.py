#!/usr/bin/env python3
"""One-shot backfill: re-decode old heartbeat iMessage rows that were stored
before ``services.heartbeat.imessage_decoder`` landed.

Upgrade helper: only an install that has been running since the March
2026 public release (its schema and rows) needs this; a fresh install
has nothing to backfill.

Algorithm:

1. Walk ``heartbeat_events`` where ``source='imessage'`` and ``raw_data`` is
   present.
2. Parse ``raw_data.rowid``; look that ROWID up in ``~/Library/Messages/chat.db``
   and fetch the ``attributedBody`` blob.
3. Re-decode with the new plistlib-based helper.
4. If the re-decoded text is non-empty AND differs from the current
   ``summary``, update both ``summary`` (capped at 200 chars to match the
   listener's shape) and ``raw_data.full_text``.

Dry-run by default. ``--apply`` writes. ``--limit N`` caps the work.
Usage:

    python scripts/backfill_heartbeat_attributedbody.py           # dry-run
    python scripts/backfill_heartbeat_attributedbody.py --apply   # commit
    python scripts/backfill_heartbeat_attributedbody.py --limit 50 --apply
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Optional

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import select, update  # noqa: E402

from services.database import HeartbeatEventModel, async_session, init_db  # noqa: E402
from services.heartbeat.imessage_decoder import decode_attributed_body  # noqa: E402

CHAT_DB_PATH = os.path.expanduser("~/Library/Messages/chat.db")

# Keys that NSKeyedArchiver leaks when the old regex decoder grabbed the wrong
# printable run. Any summary containing one of these is definitely garbled.
_PLIST_KEYS = ("$version", "$archiver", "$objects", "$top")

# iMessage reply-frame prefix bytes render as one of these printable punctuation
# characters. Seeing one directly followed by an alpha character (and no space)
# is a near-certain signal of stored framing residue.
_FRAME_PREFIXES = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")


def looks_garbled(summary: Optional[str]) -> bool:
    """Return True if the given summary looks like it came from the old
    regex-based attributedBody heuristic (either leaked plist keys or kept
    an iMessage reply-frame byte on the front)."""
    if not summary:
        return False
    if any(key in summary for key in _PLIST_KEYS):
        return True
    # Single leading punctuation byte followed by an alpha — the canonical
    # reply-frame shape: "%Sorry", "KIdk", "#Now", "'Im", "*ty", "?Im".
    if len(summary) >= 2 and summary[0] in _FRAME_PREFIXES and summary[1].isalpha():
        return True
    # Single leading capital letter glued to another letter (e.g. "KIdk")
    # without a space is always framing, never a word.
    if re.match(r"^[A-Z][a-zA-Z]", summary) and not re.match(r"^[A-Z][a-z]+\b", summary):
        # "KIdk" matches, "Idiot idiot" does not (has a space before more).
        # "Kidnapping" is "K" + "idnapping" — the [a-z]+\b guard lets it pass.
        # Require the second char be lowercase *and* the run after be a
        # legitimate word break. KIdk fails the \b guard because "dk" is
        # followed by a space — keep it False here. We re-test below.
        pass
    # More precise single-letter-glue test: CAP then CAP then lower is garbled
    # (e.g. "KI" then "dk"), whereas CAP then lower is a normal word start.
    if (
        len(summary) >= 3
        and summary[0].isupper()
        and summary[1].isupper()
        and summary[2].islower()
    ):
        return True
    return False


async def _iter_candidates(limit: Optional[int]):
    async with async_session() as session:
        stmt = (
            select(HeartbeatEventModel)
            .where(HeartbeatEventModel.source == "imessage")
            .order_by(HeartbeatEventModel.created_at.desc())
        )
        if limit:
            stmt = stmt.limit(limit)
        result = await session.execute(stmt)
        for row in result.scalars():
            yield row


def _rowid_from_raw(raw: Optional[str]) -> Optional[int]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
        rid = data.get("rowid")
        return int(rid) if rid is not None else None
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


def _fetch_attributed_body(conn: sqlite3.Connection, rowid: int) -> Optional[bytes]:
    cur = conn.execute(
        "SELECT attributedBody FROM message WHERE ROWID = ?", (rowid,)
    )
    row = cur.fetchone()
    if not row:
        return None
    return row[0]


async def main(apply: bool, limit: Optional[int]) -> None:
    if not Path(CHAT_DB_PATH).exists():
        print(f"[backfill] chat.db not at {CHAT_DB_PATH} — nothing to do")
        return

    await init_db()

    scanned = 0
    garbled = 0
    recovered = 0
    updated = 0
    skipped_no_rowid = 0
    skipped_missing_chat_row = 0
    skipped_no_attributed_body = 0
    skipped_decode_empty = 0

    conn = sqlite3.connect(f"file:{CHAT_DB_PATH}", uri=True)
    conn.execute("PRAGMA query_only = ON")
    try:
        async with async_session() as write_session:
            async for ev in _iter_candidates(limit):
                scanned += 1
                summary = ev.summary or ""
                was_garbled = looks_garbled(summary)
                if was_garbled:
                    garbled += 1

                rowid = _rowid_from_raw(ev.raw_data)
                if rowid is None:
                    skipped_no_rowid += 1
                    continue

                blob = _fetch_attributed_body(conn, rowid)
                if blob is None:
                    skipped_missing_chat_row += 1
                    continue
                if not blob:
                    skipped_no_attributed_body += 1
                    continue

                decoded = decode_attributed_body(blob)
                if not decoded:
                    skipped_decode_empty += 1
                    continue

                if decoded == summary:
                    continue  # already correct

                recovered += 1
                print(
                    f"[backfill] ev={ev.id} rowid={rowid}\n"
                    f"          old: {summary!r}\n"
                    f"          new: {decoded!r}"
                )

                if apply:
                    new_summary = decoded[:200]
                    new_raw = ev.raw_data
                    try:
                        data = json.loads(ev.raw_data) if ev.raw_data else {}
                        data["full_text"] = decoded[:1000]
                        data["backfilled_attributedbody"] = True
                        new_raw = json.dumps(data)
                    except (json.JSONDecodeError, TypeError):
                        pass
                    await write_session.execute(
                        update(HeartbeatEventModel)
                        .where(HeartbeatEventModel.id == ev.id)
                        .values(summary=new_summary, raw_data=new_raw)
                    )
                    updated += 1
            if apply:
                await write_session.commit()
    finally:
        conn.close()

    print(
        "\n[backfill] summary:\n"
        f"  scanned:                       {scanned}\n"
        f"  summaries flagged as garbled:  {garbled}\n"
        f"  rows recovered (decode diff):  {recovered}\n"
        f"  rows updated:                  {updated if apply else '0 (dry-run)'}\n"
        f"  skipped (no rowid):            {skipped_no_rowid}\n"
        f"  skipped (chat.db row gone):    {skipped_missing_chat_row}\n"
        f"  skipped (no attributedBody):   {skipped_no_attributed_body}\n"
        f"  skipped (empty decode):        {skipped_decode_empty}\n"
    )
    if not apply:
        print("[backfill] dry-run — re-run with --apply to persist changes.")


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--apply", action="store_true", help="Persist updates.")
    p.add_argument("--limit", type=int, default=None, help="Cap rows scanned.")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    asyncio.run(main(apply=args.apply, limit=args.limit))
