#!/usr/bin/env python3
"""How Messages' attributedBody blobs decode on this Mac — counts only.

2026-10-11 (docs/architecture/heartbeat.md). The typedstream length prefix
(``0x81`` + uint16, ``0x82`` + uint32) is little-endian; the decoder once
read it big-endian, so every attributedBody-only message of 128 bytes or
more fell to the legacy regex. This prints, for the last
``--limit`` messages that carry a body:

    bodies, prefix_0x81, prefix_0x81_le_fits, prefix_0x81_be_fits,
    prefix_0x82, decoded, undecoded

It opens chat.db read-only (``mode=ro``, ``query_only``) and prints counts
only — never a message, a handle or a chat name. Run it from the checkout
after a deploy, with a python that holds Full Disk Access:

    backend/.venv/bin/python backend/scripts/verify_typedstream.py

Exit 2 when the database is missing or unreadable.
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_DB = os.path.expanduser("~/Library/Messages/chat.db")
MARKER = b"NSString\x01\x94\x84\x01+"


def count(db_path: str, limit: int) -> dict:
    from services.heartbeat.imessage_decoder import decode_attributed_body

    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        conn.execute("PRAGMA query_only = ON")
        rows = conn.execute(
            "SELECT attributedBody FROM message WHERE attributedBody IS NOT NULL ORDER BY ROWID DESC LIMIT ?",
            (int(limit),)).fetchall()
    finally:
        conn.close()
    out = {"bodies": 0, "prefix_0x81": 0, "prefix_0x81_le_fits": 0, "prefix_0x81_be_fits": 0, "prefix_0x82": 0,
           "decoded": 0, "undecoded": 0}
    for (blob,) in rows:
        blob = bytes(blob or b"")
        if not blob:
            continue
        out["bodies"] += 1
        idx = blob.find(MARKER)
        if idx >= 0:
            pos = idx + len(MARKER)
            sentinel = blob[pos] if pos < len(blob) else None
            if sentinel == 0x81 and pos + 3 <= len(blob):
                out["prefix_0x81"] += 1
                remaining = len(blob) - (pos + 3)
                if int.from_bytes(blob[pos + 1:pos + 3], "little") <= remaining:
                    out["prefix_0x81_le_fits"] += 1
                if int.from_bytes(blob[pos + 1:pos + 3], "big") <= remaining:
                    out["prefix_0x81_be_fits"] += 1
            elif sentinel == 0x82:
                out["prefix_0x82"] += 1
        if decode_attributed_body(blob):
            out["decoded"] += 1
        else:
            out["undecoded"] += 1
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--db", default=DEFAULT_DB, help="chat.db path (read-only)")
    parser.add_argument("--limit", type=int, default=5000, help="the newest N messages with a body")
    args = parser.parse_args(argv)
    if not Path(args.db).is_file():
        print(f"no chat.db at {args.db}", file=sys.stderr)
        return 2
    try:
        counts = count(args.db, args.limit)
    except sqlite3.Error as exc:
        print(f"chat.db unreadable: {type(exc).__name__}", file=sys.stderr)
        return 2
    for key, value in counts.items():
        print(f"{key}: {value}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
