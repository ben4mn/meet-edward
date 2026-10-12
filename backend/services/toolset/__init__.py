"""Edward's toolset: 14 tools, identical on every surface
(docs/architecture/tool-loop.md; 2026-10-11).

``build_toolset(skill_states)`` returns them in ``TOOL_ORDER``. Seven are
this package's own (``messages``, ``send_message``, ``tell_owner``,
``memory``, ``schedule``, ``screen``, ``plan``); ``bash`` and the editor
come from ``computer_tools`` and the web tools, the two browsers and
``look`` from ``web``, ``browsers`` and ``look``, each imported when
present and replaced by a stand-in (``fallbacks``) when it is not. What a turn may do with them is ``TurnPolicy`` (``policy``), not a
shorter list. ``EDWARD_TOOLSET=legacy`` (read per call) restores the old
registry; see ``services/tool_registry.py``.

Each tool's ``metadata``: ``timeout_s`` (its deadline in the loop),
``action_arg`` for action tools (the work rail's "{action}: {detail}"),
``parallel_safe`` for reads, and ``anthropic_tool`` (set by the computer
and web modules) for a native or server tool the Claude adapter binds as is.
"""

from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Dict, List, Optional

from services.toolset.names import EXTERNAL_TOOLS, TOOL_ORDER, TOOLSET_NAMES

logger = logging.getLogger(__name__)

TOOLSET_ENV = "EDWARD_TOOLSET"

# Deadlines for this package's tools (the loop reads ``metadata.timeout_s``).
_OWN_METADATA: Dict[str, Dict[str, Any]] = {
    "messages": {"timeout_s": 60, "action_arg": "action", "parallel_safe": True},
    # Up to 30 s of osascript per send, plus attachments.
    "send_message": {"timeout_s": 150},
    "tell_owner": {"timeout_s": 60},
    "memory": {"timeout_s": 120, "action_arg": "action"},
    "schedule": {"timeout_s": 60, "action_arg": "action"},
    "screen": {"timeout_s": 90, "action_arg": "action"},
    "plan": {"timeout_s": 15},
}
_FALLBACK_METADATA: Dict[str, Dict[str, Any]] = {
    "bash": {"timeout_s": 195},
    "str_replace_based_edit_tool": {"timeout_s": 60},
    "web_search": {"timeout_s": 45, "parallel_safe": True},
    "web_fetch": {"timeout_s": 45, "parallel_safe": True},
    "browser": {"timeout_s": 45, "action_arg": "action"},
    "chrome": {"timeout_s": 90, "action_arg": "action"},
    "look": {"timeout_s": 60, "parallel_safe": True},
}


def toolset_mode() -> str:
    """``v2`` (default) or ``legacy`` (``EDWARD_TOOLSET``), read per call."""
    return "legacy" if (os.getenv(TOOLSET_ENV) or "").strip().lower() == "legacy" else "v2"


def is_v2() -> bool:
    return toolset_mode() == "v2"


def _with_metadata(t: Any, defaults: Dict[str, Any]) -> Any:
    md = dict(getattr(t, "metadata", None) or {})
    changed = False
    for k, v in defaults.items():
        if k not in md:
            md[k] = v
            changed = True
    if changed:
        try:
            t.metadata = md
        except Exception:  # noqa: BLE001 — a frozen tool keeps its own
            pass
    return t


_own_cache: Dict[str, Any] = {}


def _own_tools() -> Dict[str, Any]:
    if not _own_cache:
        from services.toolset import memory, messaging, plan, schedule, screen, tell_owner

        for t in (messaging.messages, messaging.send_message, tell_owner.tell_owner, memory.memory,
                  schedule.schedule, screen.screen, plan.plan):
            _own_cache[t.name] = _with_metadata(t, _OWN_METADATA[t.name])
    return _own_cache


def _external_tool(name: str) -> Any:
    module = EXTERNAL_TOOLS[name]
    try:
        mod = importlib.import_module(f"services.toolset.{module}")
    except ImportError:
        mod = None
    except Exception as exc:  # noqa: BLE001 — a broken module must not take the toolset down
        logger.warning("toolset: %s failed to import (%s); using the stand-in for %s", module, exc, name)
        mod = None
    t = getattr(mod, name, None) if mod is not None else None
    if t is None:
        from services.toolset import fallbacks

        t = fallbacks.FALLBACKS[name]
    return _with_metadata(t, _FALLBACK_METADATA.get(name, {}))


def all_tools() -> Dict[str, Any]:
    """Every toolset tool by name, in ``TOOL_ORDER``."""
    own = _own_tools()
    return {name: (own[name] if name in own else _external_tool(name)) for name in TOOL_ORDER}


def build_toolset(skill_states: Optional[Dict[str, bool]] = None) -> List[Any]:
    """The toolset for a turn: ``TOOL_ORDER``, minus the tools whose skill
    toggle is explicitly off (``skills.REGISTRY_SKILLS``)."""
    from services.toolset.skills import REGISTRY_SKILLS

    states = skill_states or {}
    out = []
    for name, t in all_tools().items():
        skill = REGISTRY_SKILLS.get(name)
        if skill and states.get(skill) is False:
            continue
        out.append(t)
    return out


__all__ = ["TOOL_ORDER", "TOOLSET_NAMES", "build_toolset", "all_tools", "toolset_mode", "is_v2"]
