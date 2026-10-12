"""Skill toggles the toolset reads at call time (docs/architecture/integrations.md).

Six toggles gate the new tools: ``shell_interpreter`` → bash + the editor,
``brave_search`` → web_search + web_fetch, ``imessage_applescript`` →
messages + send_message (the registry drops them), ``ios_widget`` → the
screen tool's widget actions and ``push_notifications`` →
``tell_owner(urgent)`` (the tools refuse them). ``memory``, ``schedule``,
``screen``, ``look``, ``plan``, ``tell_owner``, ``browser`` and ``chrome`` are
always on.
"""

from __future__ import annotations

REGISTRY_SKILLS = {
    "bash": "shell_interpreter",
    "str_replace_based_edit_tool": "shell_interpreter",
    "web_search": "brave_search",
    "web_fetch": "brave_search",
    "messages": "imessage_applescript",
    "send_message": "imessage_applescript",
}


async def skill_enabled(skill_id: str) -> bool:
    """The registry's cached state (one DB read per 30 s at most); True when
    it cannot be read — the tool's own checks still apply."""
    try:
        from services.tool_registry import _get_skill_states

        states = await _get_skill_states()
    except Exception:  # noqa: BLE001
        return True
    return bool(states.get(skill_id, True))
