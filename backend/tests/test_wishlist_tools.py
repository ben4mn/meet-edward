"""The wishlist tools replace the self-evolution tools in the registry.

Edward (and his workers) can wish for a feature; nobody can trigger a
code change from a chat turn any more.
"""

from __future__ import annotations

import pytest

from services.graph import tools as T


def test_wishlist_tool_set_and_names():
    names = {t.name for t in T.WISHLIST_TOOLS}
    assert names == {"wish_for_feature", "list_wishlist", "update_wish"}
    assert T.WISHLIST_TOOL_NAMES == names
    assert not hasattr(T, "trigger_self_evolution")
    assert not hasattr(T, "EVOLUTION_TOOLS")


def test_wishlist_prompt_section_tells_edward_he_cannot_change_his_own_code():
    text = T.get_wishlist_tools_description()
    assert text.lstrip().startswith("## Your wishlist")
    assert "wish_for_feature" in text and "list_wishlist" in text
    assert "cannot change your own code" in text
    assert "Settings → Work → Wishlist" in text
    assert "never promise" in text.lower()


async def test_registry_binds_the_wishlist_tools_and_not_evolution(monkeypatch):
    """``EDWARD_TOOLSET=legacy``; the toolset leaves the wishlist out (its
    premise — wish instead of build — is gone now he has a computer)."""
    from services import tool_registry

    monkeypatch.setenv("EDWARD_TOOLSET", "legacy")
    async def no_skills(force_refresh=False):
        return {}

    monkeypatch.setattr(tool_registry, "_get_skill_states", no_skills)
    tools = await tool_registry.get_available_tools()
    names = {t.name for t in tools}
    assert {"wish_for_feature", "list_wishlist", "update_wish"} <= names
    assert "trigger_self_evolution" not in names and "get_evolution_status" not in names

    prompt = tool_registry.get_tool_descriptions(tools)
    assert "## Your wishlist" in prompt
    assert "Self-Evolution" not in prompt


async def test_workers_get_the_wishlist_tools_too(monkeypatch):
    from services import tool_registry

    monkeypatch.setenv("EDWARD_TOOLSET", "legacy")
    async def no_skills(force_refresh=False):
        return {}

    monkeypatch.setattr(tool_registry, "_get_skill_states", no_skills)
    names = {t.name for t in await tool_registry.get_worker_tools()}
    assert {"wish_for_feature", "list_wishlist", "update_wish"} <= names
    assert "spawn_worker" not in names


async def test_the_toolset_has_no_wishlist(monkeypatch):
    from services import tool_registry

    async def no_skills(force_refresh=False):
        return {}

    monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
    monkeypatch.setattr(tool_registry, "_get_skill_states", no_skills)
    names = {t.name for t in await tool_registry.get_available_tools()}
    assert not ({"wish_for_feature", "list_wishlist", "update_wish"} & names)


@pytest.mark.integration
async def test_wish_for_feature_tool_logs_and_merges(clean_db):
    from services import wishlist_service as ws

    T.set_current_conversation_id("conv-tool")
    out = await T.wish_for_feature.ainvoke({"title": "Read PDFs", "why": "Ellis sends PDFs I cannot open."})
    assert out.startswith("Noted on the wishlist:") and "Read PDFs" in out
    out = await T.wish_for_feature.ainvoke({"title": "read pdfs", "why": "Vet invoices too."})
    assert out.startswith("Already on the list (wanted 2 times):")

    items = await ws.list_wishes()
    assert len(items) == 1 and items[0]["conversation_id"] == "conv-tool"

    listing = await T.list_wishlist.ainvoke({})
    assert "Read PDFs" in listing and items[0]["id"][:8] in listing

    out = await T.update_wish.ainvoke({"wish_id": items[0]["id"], "status": "planned", "detail": "Ellis said yes."})
    assert "planned" in out
    assert (await ws.list_wishes())[0]["status"] == "planned"
    out = await T.update_wish.ainvoke({"wish_id": "nope", "status": "done"})
    assert "No wish" in out
