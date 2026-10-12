#!/usr/bin/env python3
"""Mask secret query values already written to a log (pre-deploy review,
2026-09-27).

The redacting access-log config (``services.access_log``,
``log_config.json``) only covers lines written after uvicorn restarts with
it. Lines already in /tmp/edward-backend.log — the widget token in
``GET /api/widget?token=…`` — stay, because launchd appends to the same
file across restarts, and the health check's rotation copies them into a
.gz archive.

    backend/.venv/bin/python backend/scripts/scrub_log_secrets.py [--dry-run] \\
        /tmp/edward-backend.log /tmp/edward-backend.log.*.gz

A plain file is scrubbed in place: each secret value is overwritten by
``*`` of the same length, so the file keeps its size and inode and the
process appending to it (O_APPEND) loses nothing. A ``.gz`` archive, which
nobody holds open, is rewritten whole. Run it once the redacting config is
live, so nothing new arrives unredacted behind it.

Stdlib plus ``services.access_log`` (itself stdlib only).
"""

from __future__ import annotations

import argparse
import gzip
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.access_log import count_secrets, mask_query  # noqa: E402

# latin-1 maps every byte to one character and back: a masked line keeps
# its length in bytes whatever the log holds.
ENCODING = "latin-1"


def scrub_in_place(path: Path, dry_run: bool = False) -> int:
    found = 0
    with open(path, "rb" if dry_run else "r+b") as fh:
        offset = 0
        while True:
            fh.seek(offset)
            line = fh.readline()
            if not line:
                break
            text = line.decode(ENCODING)
            n = count_secrets(text)
            if n:
                found += n
                if not dry_run:
                    fh.seek(offset)
                    fh.write(mask_query(text).encode(ENCODING))
            offset += len(line)
    return found


def scrub_archive(path: Path, dry_run: bool = False) -> int:
    with gzip.open(path, "rb") as fh:
        text = fh.read().decode(ENCODING)
    found = sum(count_secrets(line) for line in text.splitlines())
    if found and not dry_run:
        masked = "".join(mask_query(line) for line in text.splitlines(keepends=True))
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}.")
        try:
            with os.fdopen(fd, "wb") as raw, gzip.GzipFile(fileobj=raw, mode="wb") as out:
                out.write(masked.encode(ENCODING))
            os.chmod(tmp, os.stat(path).st_mode & 0o777)
            os.replace(tmp, path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise
    return found


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count, change nothing")
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args(argv)
    verb = "would mask" if args.dry_run else "masked"
    for path in args.paths:
        if not path.is_file():
            print(f"{path}: not a file, skipped")
            continue
        scrub = scrub_archive if path.suffix == ".gz" else scrub_in_place
        n = scrub(path, dry_run=args.dry_run)
        print(f"{path}: {n} secret value{'s' if n != 1 else ''} {verb}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
