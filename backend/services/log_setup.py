"""Timestamped process logging for the backend.

Regression origin (2026-09-02): the backend log had no timestamps at all —
every line in the file came from ``print()`` and nothing had ever
configured ``logging``, so ``logger.info`` calls were dropped by Python's
last-resort handler (WARNING and above only). Incidents could only be
placed in time by cross-referencing the database.

Two things happen here:

* ``configure_logging`` installs a root ``StreamHandler`` so structured
  ``logger.info`` lines become visible, each prefixed with a timestamp.
* ``TimestampingStream`` wraps stdout/stderr so the thousands of existing
  ``print()`` call sites get the same prefix per *line* without being
  touched. Partial writes (``print(..., end="")``) are tracked so a line is
  stamped exactly once.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from typing import Callable, TextIO, Tuple

_original_stdout: TextIO | None = None
_original_stderr: TextIO | None = None


def default_stamp() -> str:
    """Local-time ISO stamp with a trailing space, e.g. ``2026-09-02T22:01:37 ``."""
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S ")


def prefix_lines(text: str, stamp: str, at_line_start: bool) -> Tuple[str, bool]:
    """Prefix every line *start* in ``text`` with ``stamp``.

    ``at_line_start`` says whether the previous write ended with a newline
    (so the first character of this write begins a new line). Returns the
    rewritten text and the new ``at_line_start`` state.
    """
    if not text:
        return "", at_line_start
    out = []
    for i, chunk in enumerate(text.split("\n")):
        is_last = i == len(text.split("\n")) - 1
        if i > 0:
            out.append("\n")
            at_line_start = True
        if chunk and at_line_start:
            out.append(stamp)
            at_line_start = False
        out.append(chunk)
        if is_last and text.endswith("\n"):
            at_line_start = True
    return "".join(out), at_line_start


class TimestampingStream:
    """A text stream wrapper that stamps each line as it is written."""

    def __init__(self, inner: TextIO, stamp: Callable[[], str] = default_stamp):
        self._inner = inner
        self._stamp = stamp
        self._at_line_start = True

    def write(self, text: str) -> int:
        out, self._at_line_start = prefix_lines(text, self._stamp(), self._at_line_start)
        return self._inner.write(out)

    def flush(self) -> None:
        self._inner.flush()

    def __getattr__(self, name):  # fileno, isatty, encoding, ...
        return getattr(self._inner, name)


class _StampFormatter(logging.Formatter):
    def __init__(self, stamp: Callable[[], str]):
        super().__init__("%(levelname)s %(name)s %(message)s")
        self._stamp = stamp

    def format(self, record: logging.LogRecord) -> str:
        return self._stamp() + super().format(record)


def configure_logging(
    stream: TextIO | None = None,
    level: int = logging.INFO,
    stamp: Callable[[], str] = default_stamp,
    wrap_stdio: bool = False,
) -> None:
    """Make ``logger.info`` visible and, optionally, timestamp stdout/stderr.

    * ``wrap_stdio=False``: install a root handler writing stamped lines to
      ``stream`` (default: current stdout).
    * ``wrap_stdio=True``: replace ``sys.stdout``/``sys.stderr`` with
      ``TimestampingStream`` wrappers around ``stream`` (default: the real
      stdout/stderr) and point the root handler at the wrapped stdout. The
      wrapper stamps every line, so the handler adds no stamp of its own.
      ``configure_logging.restore_stdio()`` undoes the swap (tests).

    Idempotent: re-running replaces the handler this module installed.
    """
    global _original_stdout, _original_stderr

    root = logging.getLogger()
    for h in list(root.handlers):
        if getattr(h, "_edward_log_setup", False):
            root.removeHandler(h)

    if wrap_stdio:
        if _original_stdout is None:
            _original_stdout, _original_stderr = sys.stdout, sys.stderr
        sys.stdout = TimestampingStream(stream or _original_stdout, stamp)
        sys.stderr = TimestampingStream(stream or _original_stderr, stamp)
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(_StampFormatter(lambda: ""))
    else:
        handler = logging.StreamHandler(stream or sys.stdout)
        handler.setFormatter(_StampFormatter(stamp))

    handler._edward_log_setup = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    # uvicorn installs its own handlers on these loggers; stop them from
    # also propagating to the root handler (double lines).
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logging.getLogger(name).propagate = False


def _restore_stdio() -> None:
    global _original_stdout, _original_stderr
    if _original_stdout is not None:
        sys.stdout, sys.stderr = _original_stdout, _original_stderr
        _original_stdout = _original_stderr = None


configure_logging.restore_stdio = _restore_stdio  # type: ignore[attr-defined]
