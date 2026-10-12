"""Edward's computer: his home directory, a persistent sandboxed shell per
thread, an in-process file editor, and the kernel rails that keep all of it
off production.

This is the backend of AREA 2 (the toolset): ``bash`` and the file editor
run here, under a macOS Seatbelt (``sandbox-exec``) profile generated from
one shared rails table (:mod:`services.computer.rails`), so the in-process
editor and the shell deny exactly the same things.

Design decision (2026-10-11): Edward gets broad access. The rails that hold
are the ones that stop real, unattended harm — never a second Google
Chrome, no AppleScript control of other apps, no writes to the live repo or
the releases (nor renaming anything above them), no signalling processes he
didn't start, never a database of Edward's (:5432, nor the test cluster on
:5433), Edward's own API or the WhatsApp bridge. Reads are allowed
everywhere but Edward's own credentials; command output is scanned and
redacted before the model sees it. The sandbox does not isolate the owner's
home or Homebrew's prefix (docs/architecture/computer.md): for strong
isolation, run Edward as a separate macOS user.

``startup()``/``shutdown()`` are called from ``main.py``'s lifespan.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from services.computer import home, profile, shell

logger = logging.getLogger(__name__)


async def startup() -> None:
    """Prepare the computer at backend boot: lay out ``~/edward-home``, write
    the Seatbelt profile, and kill any shell process groups left recorded by
    a previous run. Never raises — a broken computer must not stop boot.
    """
    try:
        home.ensure_home()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("computer: ensure_home failed: %s", exc)
    try:
        if sandbox_enabled():
            profile.write_profile()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("computer: write_profile failed: %s", exc)
    try:
        shell.sweep_stale_sessions()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("computer: session sweep failed: %s", exc)
    logger.info("computer: ready (sandbox=%s, home=%s)", sandbox_enabled(), home.edward_home())


async def shutdown() -> None:
    """Close every live shell session (kills their process groups)."""
    try:
        await shell.shutdown_all()
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("computer: shutdown failed: %s", exc)


def sandbox_enabled() -> bool:
    """Whether the Seatbelt sandbox is in force. ``EDWARD_COMPUTER_SANDBOX=off``
    is an emergency switch that runs the shell unsandboxed; every command then
    logs ``[computer] UNSANDBOXED``.
    """
    return os.getenv("EDWARD_COMPUTER_SANDBOX", "on").strip().lower() != "off"


def status() -> Dict[str, Any]:
    """A snapshot for the skills panel and the health report: ``sessions``
    is the count (``session_list`` the detail); ``status``/``status_message``
    are set when the shell can't start (fail-closed: sandbox-exec missing)
    or runs unsandboxed (the emergency switch), so the Settings row says so."""
    sessions = shell.session_snapshot()
    out: Dict[str, Any] = {
        "sandbox": sandbox_enabled(),
        "home": str(home.edward_home()),
        "sessions": len(sessions),
        "session_list": sessions,
    }
    n = len(sessions)
    open_line = f"{n} shell session{'s' if n != 1 else ''} open"
    if not out["sandbox"]:
        out["status"] = "connected"
        out["status_message"] = f"UNSANDBOXED (EDWARD_COMPUTER_SANDBOX=off) — {open_line}"
    elif not os.path.exists(shell._SANDBOX_EXEC):
        out["status"] = "error"
        out["status_message"] = f"my computer is unavailable ({shell._SANDBOX_EXEC} — sandbox-exec is missing)"
    return out
