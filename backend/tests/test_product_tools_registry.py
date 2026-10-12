"""The Phase 5 tools are bound, described, and routed (plan Phase 5, 2026-09-24).

Goals, provenance, forget and ideas are always bound in chat; workers do
not get ``forget_about`` (a sub-agent must not delete Ellis's memories);
the manual gets one short section with a line per bound tool, and the
provenance line — the Muse lesson — ships whenever ``how_do_i_know`` does.
"""

from __future__ import annotations

import pytest

PRODUCT = {"how_do_i_know", "forget_about"}
# Unit G retired goals and ideas (Ellis's answer #3, 2026-10-11) and deleted
# goals_service: the tools must not stay bound (review 2026-10-11 — the
# manual still said "set_goal when Ellis wants to work toward something", and
# set_goal raised ModuleNotFoundError), and suggest_idea wrote notes Today
# no longer shows.
RETIRED = {"set_goal", "update_goal", "list_goals", "suggest_idea"}


@pytest.mark.integration
async def test_product_tools_are_always_bound_and_described(clean_db, monkeypatch):
    """``EDWARD_TOOLSET=legacy`` (the old registry, kept for rollback)."""
    from services import tool_registry

    monkeypatch.setenv("EDWARD_TOOLSET", "legacy")

    tools = await tool_registry.get_available_tools("tell me a joke")
    names = {t.name for t in tools}
    assert PRODUCT <= names
    assert not RETIRED & names, RETIRED & names
    manual = tool_registry.get_tool_descriptions(tools)
    assert "how_do_i_know" in manual and "Never describe your own data access from memory" in manual
    assert "forget_about" in manual
    assert "set_goal" not in manual and "suggest_idea" not in manual

    workers = {t.name for t in await tool_registry.get_worker_tools()}
    assert "forget_about" not in workers
    assert "how_do_i_know" in workers and not RETIRED & workers


@pytest.mark.integration
async def test_no_bound_tool_imports_a_module_that_is_gone(clean_db):
    """A tool that imports a deleted service at call time fails only when
    the model calls it; catch it here instead."""
    import importlib.util
    import inspect
    import re

    from services import tool_registry

    tools = list(await tool_registry.get_available_tools("tell me a joke")) + list(
        await tool_registry.get_worker_tools())
    missing = set()
    for t in tools:
        fn = getattr(t, "coroutine", None) or getattr(t, "func", None)
        if fn is None:
            continue
        try:
            src = inspect.getsource(fn)
        except (OSError, TypeError):
            continue
        mods = set(re.findall(r"from services\.(\w+)(?:\.\w+)* import", src))
        mods |= set(re.findall(r"from services import ([\w, ]+)", src) and
                    [m.strip().split(" as ")[0] for grp in re.findall(r"from services import ([\w, ]+)", src)
                     for m in grp.split(",")])
        for m in mods:
            if m and importlib.util.find_spec(f"services.{m}") is None:
                missing.add((t.name, m))
    assert not missing, sorted(missing)


def test_manual_lines_follow_the_bound_tools():
    from services.graph.tools import PRODUCT_TOOL_NAMES, get_product_tools_description

    assert not RETIRED & PRODUCT_TOOL_NAMES
    manual = get_product_tools_description()
    assert "how_do_i_know" in manual and "forget_about" in manual
    assert "set_goal" not in manual and "suggest_idea" not in manual and "goals" not in manual.lower()
    assert get_product_tools_description({"remember"}) == ""


def test_tool_docstrings_say_when_to_use_them():
    from services.graph.tools import forget_about, how_do_i_know

    assert "how you know" in how_do_i_know.description and "Never describe" in how_do_i_know.description
    assert "confirm=true" in forget_about.description or "confirm=True" in forget_about.description


def test_routes_are_mounted():
    from main import app

    paths = {getattr(r, "path", "") for r in app.routes}
    # The goal and idea routes went with unit G (2026-10-11).
    for p in ("/api/memories/{memory_id}/provenance", "/api/memories/forget"):
        assert p in paths, p


@pytest.mark.integration
async def test_under_the_toolset_why_and_forget_are_memory_actions(clean_db, monkeypatch):
    """v2 (2026-10-11): provenance and forget are ``memory(why)`` and
    ``memory(forget)``; goals and ideas left the toolset (0 goals, 1 idea)."""
    from services import tool_registry

    monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
    names = {t.name for t in await tool_registry.get_available_tools()}
    assert "memory" in names and not (PRODUCT & names)
    memory = next(t for t in await tool_registry.get_available_tools() if t.name == "memory")
    assert "why" in memory.description and "forget" in memory.description
