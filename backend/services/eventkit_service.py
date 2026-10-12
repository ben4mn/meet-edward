"""The EventKit helper, from Python (2026-10-11; docs/architecture/computer.md).

``edward-eventkit`` is one small signed binary over Apple's EventKit — the
one write path to the owner's calendar and Reminders (``schedule``'s event_*
and reminder_* actions, when the owner asks). Reads stay in ``calendar_service`` and
``reminders_service`` (the databases, milliseconds, no permission prompt).

The source is ``backend/tools/eventkit/edward_eventkit.swift``; the deploy
builds it (``backend/scripts/build_eventkit.sh``) into
``~/edward-eventkit/bin/edward-eventkit``, or ``EDWARD_EVENTKIT`` names it
(read per call). Until it is built every write answers honestly that the
helper isn't installed.

The protocol: ``edward-eventkit <command>`` with one JSON object on stdin
(never argv — ``ps`` would show an event's title and notes) and one JSON
line back. Exit 0 ok; 2 bad input; 3 a failure in the helper's own words
(``not_found``, ``read_only``, ``ambiguous``, ``save_failed``); 4 macOS's
access, which becomes the System Settings sentence the owner needs. A helper that
never started is ``EventKitUnavailable`` (nothing changed); one that started
and gave no answer — a timeout, a crash — is ``EventKitOutcomeUnknown``,
because a write may already be saved. TCC attributes the access to the
responsible process: the backend's python (``python3.12`` on the original
host).
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

from services.owner import owner_name

DEFAULT_PATH = "~/edward-eventkit/bin/edward-eventkit"
RUN_TIMEOUT_S = 20
ACCESS_TIMEOUT_S = 5
ACCESS_TTL_S = 600


class EventKitError(RuntimeError):
    """The helper didn't do it (and why)."""


class EventKitUnavailable(EventKitError):
    """The helper never started (not installed, or it wouldn't launch):
    nothing changed."""


class EventKitOutcomeUnknown(EventKitError):
    """The helper started and gave no answer — it timed out, crashed or
    printed nothing readable. A write may already have reached Calendar or
    Reminders, so this is "I don't know", never "it didn't happen" (a retry
    on "didn't happen" duplicates the event; review 2026-10-11)."""


class EventKitDenied(EventKitError):
    """macOS hasn't given access to ``entity`` (event | reminder)."""

    def __init__(self, entity: str, status: str = "denied"):
        self.entity = entity if entity in ("event", "reminder") else "event"
        self.status = status or "denied"
        super().__init__(denied_sentence(self.entity, self.status))


class EventKitFailed(EventKitError):
    """The helper ran and said no: ``code`` and its own sentence."""

    def __init__(self, code: str, error: str):
        self.code = code or "failed"
        self.error = error or "the EventKit helper failed"
        super().__init__(self.error)


ASK_SCRIPT = "backend/scripts/eventkit_request_access.sh"


def _python_name() -> str:
    """The name System Settings lists the backend's python under (TCC
    attributes access to the responsible process): ``python3.12`` for a
    Homebrew python@3.12 venv."""
    try:
        return os.path.basename(os.path.realpath(sys.executable)) or "python3"
    except Exception:  # noqa: BLE001
        return "python3"


# ADAPT: calendar and Reminders writes need macOS privacy (TCC) grants for the backend's python.
#   How it works here: access is attributed to the python running the backend; Calendar was granted
#   Full Access in System Settings and Reminders once through eventkit_request_access.sh at the Mac.
#   To adapt: build the helper (backend/scripts/build_eventkit.sh), then run
#   backend/scripts/eventkit_request_access.sh while sitting at the Mac and click Allow.
def denied_sentence(entity: str, status: str = "denied") -> str:
    """What the owner does about it. Measured 2026-10-11 under the backend's
    python3.12: Calendar write-only, Reminders never asked — and an app
    macOS never asked isn't in System Settings' list, so that case names
    the one-time request from this Mac's screen instead."""
    what = "Reminders" if entity == "reminder" else "calendar"
    who = owner_name()
    py = _python_name()
    if status == "notDetermined":
        return (f"I can't change {who}'s {what} yet — macOS hasn't asked for access. At this Mac, {who} runs "
                f"bash {ASK_SCRIPT} (in the Edward checkout) and clicks Allow.")
    if entity == "reminder":
        return (f"I can't change {who}'s Reminders — macOS hasn't given me access. On this Mac: System Settings → "
                f"Privacy & Security → Reminders → turn on {py}.")
    if status == "writeOnly":
        return (f"I can only add events to {who}'s default calendar — macOS gave me write-only access. To pick a "
                f"calendar, move, change or delete: System Settings → Privacy & Security → Calendars → {py} → "
                "Full Access.")
    return (f"I can't change {who}'s calendar — macOS hasn't given me access. On this Mac: System Settings → "
            f"Privacy & Security → Calendars → {py} → Full Access.")


def binary_path() -> Path:
    return Path(os.path.expanduser(os.getenv("EDWARD_EVENTKIT") or DEFAULT_PATH))


def available() -> bool:
    p = binary_path()
    return p.is_file() and os.access(p, os.X_OK)


def _run(command: str, payload: Optional[Dict[str, Any]] = None, timeout_s: float = RUN_TIMEOUT_S) -> Dict[str, Any]:
    """Run one helper command; its JSON reply, or an ``EventKitError``.
    The seam tests stub."""
    if not available():
        raise EventKitUnavailable(f"the EventKit helper isn't installed at {binary_path()} "
                                  "(backend/scripts/build_eventkit.sh builds it)")
    data = json.dumps(payload or {}).encode("utf-8")
    try:
        r = subprocess.run([str(binary_path()), command], input=data, capture_output=True, timeout=timeout_s)
    except subprocess.TimeoutExpired:
        raise EventKitOutcomeUnknown(f"the EventKit helper took longer than {timeout_s:g}s") from None
    except OSError as exc:
        raise EventKitUnavailable(f"the EventKit helper wouldn't start ({exc})") from exc
    try:
        out = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        out = None
    if not isinstance(out, dict):
        raise EventKitOutcomeUnknown(f"the EventKit helper gave no answer (exit {r.returncode})")
    if r.returncode == 0 and out.get("ok"):
        return out
    if r.returncode == 4:
        raise EventKitDenied(str(out.get("entity") or "event"), str(out.get("status") or "denied"))
    raise EventKitFailed(str(out.get("code") or "failed"),
                         str(out.get("error") or f"the EventKit helper failed (exit {r.returncode})"))


async def _call(command: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return await asyncio.to_thread(_run, command, payload)


async def add_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("event_add", payload)


async def update_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("event_update", payload)


async def delete_event(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("event_delete", payload)


async def add_reminder(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("reminder_add", payload)


async def complete_reminder(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("reminder_complete", payload)


async def delete_reminder(payload: Dict[str, Any]) -> Dict[str, Any]:
    return await _call("reminder_delete", payload)


_access_cache: Dict[str, Any] = {}


def access_status() -> Dict[str, Any]:
    """What macOS has granted (the helper's ``access``, which never
    prompts) — the health report's ``calendar.eventkit`` line. Cached ten
    minutes; never raises. A missing helper is said without running
    anything."""
    if not available():
        return {"installed": False, "path": str(binary_path())}
    now = time.monotonic()
    if _access_cache and now - _access_cache.get("at", 0.0) < ACCESS_TTL_S:
        return dict(_access_cache["value"])
    try:
        out = _run("access", {}, timeout_s=ACCESS_TIMEOUT_S)
        value: Dict[str, Any] = {"installed": True, "calendar": out.get("calendar"), "reminders": out.get("reminders")}
    except EventKitError as exc:
        value = {"installed": True, "error": str(exc)[:200]}
    except Exception as exc:  # noqa: BLE001 — a health line never raises
        value = {"installed": True, "error": f"{type(exc).__name__}: {exc}"[:200]}
    _access_cache.update(at=now, value=value)
    return dict(value)
