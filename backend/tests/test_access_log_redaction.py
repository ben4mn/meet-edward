"""The access log never carries a secret from a query string (2026-09-27, L10).

uvicorn logs every request line, query string included, into the
world-readable /tmp/edward-backend.log: the Scriptable widget's
``GET /api/widget?token=…`` put the widget token there several times a
minute, and ``/api/presence/stream?token=…`` does the same. The access
logger now runs every record through ``services.access_log`` before it is
written, and ``backend/start.sh`` starts uvicorn with ``log_config.json``,
which installs it.
"""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from services.access_log import RedactSecretsFilter, redact_query

BACKEND = Path(__file__).resolve().parents[1]
START_SH = BACKEND / "start.sh"
LOG_CONFIG = BACKEND / "log_config.json"


@pytest.mark.parametrize(
    "target, expected",
    [
        ("/api/widget?token=abc123", "/api/widget?token=[redacted]"),
        ("/api/presence/stream?token=t0k&device_id=phone", "/api/presence/stream?token=[redacted]&device_id=phone"),
        ("/api/x?key=k&api_key=a&code=c&password=p&secret=s", "/api/x?key=[redacted]&api_key=[redacted]&code=[redacted]&password=[redacted]&secret=[redacted]"),
        ("/api/x?TOKEN=abc", "/api/x?TOKEN=[redacted]"),
        ("/api/x?access_token=a&client_secret=b&refresh_token=c", "/api/x?access_token=[redacted]&client_secret=[redacted]&refresh_token=[redacted]"),
        ("/api/x?api%5Fkey=abc", "/api/x?api%5Fkey=[redacted]"),
        ("/api/x?days=7&limit=20", "/api/x?days=7&limit=20"),
        ("/api/x?token=", "/api/x?token="),
        ("/api/x", "/api/x"),
        ("/api/x?c=conv-1&token=abc#frag", "/api/x?c=conv-1&token=[redacted]#frag"),
        # A name that merely contains a secret word is not a secret.
        ("/api/x?keyword=kayak&monkey=1&codename=x", "/api/x?keyword=kayak&monkey=1&codename=x"),
        # Without a query string there is nothing to redact.
        ("token=abc", "token=abc"),
    ],
)
def test_redact_query(target, expected):
    assert redact_query(target) == expected


def test_the_gmail_callbacks_code_and_state_are_redacted():
    # Google redirects the browser to the public callback with both; the
    # state is the one thing that makes a code Ellis's (2026-10-11).
    target = "/api/gmail/oauth/callback?state=s7aTe-v4lue&code=4/0AbCd&scope=https://www.googleapis.com/auth/gmail.readonly"
    assert redact_query(target) == (
        "/api/gmail/oauth/callback?state=[redacted]&code=[redacted]"
        "&scope=https://www.googleapis.com/auth/gmail.readonly"
    )


def test_redact_query_inside_a_request_line():
    line = 'GET /api/widget?token=abc123 HTTP/1.1'
    assert redact_query(line) == 'GET /api/widget?token=[redacted] HTTP/1.1'


def _access_record(path: str) -> logging.LogRecord:
    # Exactly the shape uvicorn's httptools/h11 protocols log (0.44).
    return logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1,
        '%s - "%s %s HTTP/%s" %d',
        ("1.2.3.4:0", "GET", path, "1.1", 200),
        None,
    )


def test_filter_redacts_an_uvicorn_access_record():
    from uvicorn.logging import AccessFormatter

    record = _access_record("/api/widget?token=abc123&x=1")
    assert RedactSecretsFilter().filter(record) is True
    line = AccessFormatter('%(client_addr)s - "%(request_line)s" %(status_code)s').format(record)
    assert "abc123" not in line
    assert '"GET /api/widget?token=[redacted]&x=1 HTTP/1.1" 200 OK' in line


def test_filter_leaves_other_records_alone():
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "%s items", (3,), None)
    RedactSecretsFilter().filter(record)
    assert record.getMessage() == "3 items"
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "opened /a?token=abc", None, None)
    RedactSecretsFilter().filter(record)
    assert record.getMessage() == "opened /a?token=[redacted]"


def test_start_sh_starts_uvicorn_with_the_log_config():
    # By absolute path (the venv's own uvicorn, review 2026-10-12), still a
    # command line restart.sh's pattern finds.
    exec_lines = [l for l in START_SH.read_text().splitlines()
                  if re.match(r'\s*exec "\$VENV"/bin/uvicorn main:app', l)]
    assert len(exec_lines) == 1, exec_lines
    assert re.search(r"--log-config\s+log_config\.json\b", exec_lines[0]), exec_lines[0]
    # restart.sh finds stragglers with `uvicorn main:app.*--port 8000`.
    assert re.search(r"uvicorn main:app.*--port 8000", exec_lines[0])
    assert LOG_CONFIG.is_file()
    json.loads(LOG_CONFIG.read_text())


def test_uvicorn_loading_the_log_config_redacts_and_stamps_access_lines():
    """Load log_config.json the way `uvicorn --log-config` does and log a request.

    In a subprocess: dictConfig replaces the process's logging handlers.
    """
    script = r"""
import logging, sys
import uvicorn
uvicorn.Config("main:app", log_config="log_config.json")
logging.getLogger("uvicorn.access").info(
    '%s - "%s %s HTTP/%s" %d', "1.2.3.4:0", "GET", "/api/widget?token=abc123", "1.1", 200)
logging.getLogger("uvicorn.error").info(
    '%s - "WebSocket %s" [accepted]', "1.2.3.4:0", "/api/voice/ws?token=abc123")
sys.stdout.flush(); sys.stderr.flush()
"""
    env = {k: v for k, v in os.environ.items() if k != "DATABASE_URL"}
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=BACKEND, env=env,
        capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    out = result.stdout + result.stderr
    assert "abc123" not in out
    access = [l for l in result.stdout.splitlines() if "/api/widget" in l]
    assert len(access) == 1, result.stdout
    # The same leading stamp as every other line in the log, so the
    # watchdog can place a request in time (scripts/health_check.sh).
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} INFO: +1\.2\.3\.4:0 - \"GET /api/widget\?token=\[redacted\] HTTP/1\.1\" 200 OK$", access[0]), access[0]
    assert "/api/voice/ws?token=[redacted]" in result.stderr
