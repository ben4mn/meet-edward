"""Secrets already written to the access log can be scrubbed in place
(pre-deploy review, 2026-09-27).

L10's redacting log config (``services.access_log``) protects lines
written after uvicorn restarts with it. The 23+ ``GET /api/widget?token=…``
lines already in /tmp/edward-backend.log stay: launchd appends to the
same file, so a redeploy does not truncate it, and the next rotation in
``scripts/health_check.sh`` copies them into a .gz archive. The token
authenticates ``POST /api/widget/chat`` — a full chat turn, exempt from the
taint gate — through the public tunnel.

``scripts/scrub_log_secrets.py`` overwrites each secret query value in
place with a mask of the same length: the file keeps its size and inode,
so launchd's descriptor (O_APPEND) keeps writing at the end and nothing it
appends meanwhile is lost or shifted. A rotated ``.gz`` archive, which
nobody holds open, is rewritten whole.
"""

from __future__ import annotations

import gzip
import os
import subprocess
import sys
from pathlib import Path

import pytest

from services.access_log import mask_query, redact_query

pytestmark = pytest.mark.unit

BACKEND = Path(__file__).resolve().parents[1]
SCRIPT = BACKEND / "scripts" / "scrub_log_secrets.py"

TOKEN = "0123456789abcdefABCDEF_-token"
LINES = [
    '2026-09-27T09:00:01 INFO:     127.0.0.1:5123 - "GET /api/widget?token=%s HTTP/1.1" 200\n' % TOKEN,
    '2026-09-27T09:00:02 INFO:     127.0.0.1:5124 - "GET /api/today HTTP/1.1" 200\n',
    '2026-09-27T09:00:03 INFO:     127.0.0.1:5125 - "GET /api/presence/stream?device_id=p&token=%s HTTP/1.1" 200\n'
    % TOKEN,
    "[memory] stored 3 rows\n",
]


@pytest.mark.parametrize("text", [line.rstrip("\n") for line in LINES] + ["/api/x?key=k", "/api/x?token="])
def test_the_mask_keeps_the_length_and_hides_what_redaction_hides(text):
    masked = mask_query(text)
    assert len(masked) == len(text)
    assert TOKEN not in masked
    # Exactly the parameters redact_query would hide, nothing else.
    assert (masked == text) == (redact_query(text) == text)


def _run(*args):
    return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], capture_output=True, text=True,
                          cwd=str(BACKEND))


def test_a_live_log_is_scrubbed_in_place(tmp_path):
    log = tmp_path / "edward-backend.log"
    log.write_text("".join(LINES))
    before = os.stat(log)
    with open(log, "a") as held_open:  # launchd's descriptor
        done = _run(log)
        held_open.write("appended after the scrub\n")
    assert done.returncode == 0, done.stderr
    after = os.stat(log)
    assert after.st_ino == before.st_ino
    text = log.read_text()
    assert TOKEN not in text
    assert text.endswith("appended after the scrub\n")
    assert LINES[1] in text and LINES[3] in text
    assert "2 secret" in done.stdout


def test_a_rotated_archive_is_rewritten(tmp_path):
    archive = tmp_path / "edward-backend.log.20260927-090000.gz"
    with gzip.open(archive, "wt") as fh:
        fh.write("".join(LINES))
    done = _run(archive)
    assert done.returncode == 0, done.stderr
    with gzip.open(archive, "rt") as fh:
        text = fh.read()
    assert TOKEN not in text and LINES[1] in text


def test_dry_run_counts_and_changes_nothing(tmp_path):
    log = tmp_path / "edward-backend.log"
    log.write_text("".join(LINES))
    done = _run("--dry-run", log)
    assert done.returncode == 0 and "2 secret" in done.stdout
    assert log.read_text() == "".join(LINES)
