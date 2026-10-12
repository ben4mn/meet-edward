"""Regression test for the triage-loop error handler.

Production logs once showed thousands of:
    [Heartbeat] Triage loop error: Object of type datetime is not JSON serializable

Root cause: the legacy engine's end-of-cycle ``json.dumps(classifications)``
had no ``default=str``. That serializer left with the legacy engine (no
triage results are written any more), but the loop's error handler also
logged no traceback, hiding the offending line for weeks — that part stays.
"""

import io
import traceback
from contextlib import redirect_stdout

from services.heartbeat.heartbeat_service import _log_triage_loop_error


def test_triage_loop_error_logs_traceback():
    """The loop error handler must include a traceback, not just str(e)."""
    buf = io.StringIO()
    try:
        raise TypeError("Object of type datetime is not JSON serializable")
    except TypeError as e:
        with redirect_stdout(buf):
            _log_triage_loop_error(e)
    out = buf.getvalue()
    assert "Object of type datetime" in out
    assert "Traceback" in out
