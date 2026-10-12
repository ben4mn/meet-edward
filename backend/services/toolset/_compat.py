"""Seams the web, browser and look tools share (2026-10-11).

The toolset's failure type (``result.ToolFailure``) and the turn's policy
(``policy.current_policy``) belong to the toolset core. These helpers use
them when they are present and fall back to today's equivalents when this
branch runs alone: a tool still raises a ``ToolFailure`` the executor
turns into ``Tool error: …`` plus ``is_error``, and "the owner is the one
asking" falls back to the turn's source (chat, voice, widget, Alexa).
"""

from __future__ import annotations

from typing import Any, Optional

try:  # the toolset core's failure type (``services/toolset/result.py``)
    from services.toolset.result import ToolFailure  # type: ignore  # noqa: F401
except ImportError:  # pragma: no cover — exercised only before the core merges
    class ToolFailure(Exception):  # type: ignore[no-redef]
        """A tool's honest failure: the executor reports it as ``Tool error: …``."""


# Turns in which the owner is the one asking (the policy's defaults;
# ``ping_owner`` is their self-chat — the policy's ``OWNER_SOURCES``).
OWNER_SOURCES = ("chat", "voice", "widget", "alexa", "ping_owner")


def current_policy() -> Any:
    """The turn's ``TurnPolicy``, or None before the core merges."""
    try:
        from services.toolset.policy import current_policy as _current  # type: ignore
    except ImportError:
        return None
    try:
        return _current()
    except Exception:  # noqa: BLE001
        return None


def turn_source() -> Optional[str]:
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001
        return None
    return getattr(rec, "source", None) if rec is not None else None


def owner_asked() -> bool:
    """Is the owner the one asking in this turn? The policy says; without
    one, the turn's source does (fail-closed: no turn is not the owner)."""
    policy = current_policy()
    if policy is not None and hasattr(policy, "owner_asked"):
        return bool(policy.owner_asked)
    return turn_source() in OWNER_SOURCES


def conversation_id() -> Optional[str]:
    try:
        from services.graph.tools import get_current_conversation_id

        return get_current_conversation_id()
    except Exception:  # noqa: BLE001
        return None


def record_failure(kind: str, action: str, exc: BaseException) -> None:
    """A ``ledger_events`` row for a failed browser action (health checks
    read where the hands fail). Never raises."""
    try:
        from services import event_ledger
        from services.turn_ledger import error_class

        event_ledger.record_event(kind=kind, source=action, outcome="failed", reason=error_class(exc))
    except Exception:  # noqa: BLE001
        pass
