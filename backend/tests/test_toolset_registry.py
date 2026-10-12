"""One toolset, identical on every surface (tools-spec §0.1, §4.2, §13).

The 116-tool chat set, ``MIND_TOOLS``, ``PULSE_TOOLS`` and the other
allowlists are replaced by 14 tools in one order, so every same-model turn
shares one prompt-cache entry and the mind can finally see, browse and run
code (R6). ``EDWARD_TOOLSET=legacy`` restores the old registry — minus the
MCP sends that bypassed the send guard and the keyword gating that hid
tools (08-25).
"""

from __future__ import annotations

import pytest

from services.toolset import names
from services.toolset import policy as P

pytestmark = pytest.mark.unit

ALL_ON = {k: True for k in ("shell_interpreter", "brave_search", "imessage_applescript", "ios_widget",
                            "push_notifications", "twilio_sms", "twilio_whatsapp", "whatsapp_mcp",
                            "apple_services", "contacts_lookup", "find_my", "html_hosting", "orchestrator",
                            "code_interpreter", "javascript_interpreter", "sql_interpreter", "alexa_outbound",
                            "claude_session")}


@pytest.fixture
def skills(monkeypatch):
    from services import tool_registry

    states = dict(ALL_ON)

    async def _states(force_refresh=False):
        return states

    monkeypatch.setattr(tool_registry, "_get_skill_states", _states)
    monkeypatch.setattr(tool_registry, "_get_custom_mcp_tools", lambda: [])
    return states


class TestV2:
    async def test_exactly_the_fourteen_in_order(self, skills, monkeypatch):
        from services import tool_registry

        monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
        tools = await tool_registry.get_available_tools()
        assert [t.name for t in tools] == list(names.TOOL_ORDER)

    async def test_identical_on_every_surface(self, skills, monkeypatch):
        from services import tool_registry

        monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
        chat = await tool_registry.get_available_tools(message="text Parker and book a table")
        bare = await tool_registry.get_available_tools()
        worker = await tool_registry.get_worker_tools()
        assert [t.name for t in chat] == [t.name for t in bare] == [t.name for t in worker]
        assert all(a is b for a, b in zip(chat, bare))

    async def test_skill_toggles_remove_their_tools(self, skills, monkeypatch):
        from services import tool_registry

        monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
        skills.update(shell_interpreter=False, brave_search=False, imessage_applescript=False)
        got = [t.name for t in await tool_registry.get_available_tools()]
        for gone in ("bash", "str_replace_based_edit_tool", "web_search", "web_fetch", "messages", "send_message"):
            assert gone not in got
        for kept in ("memory", "schedule", "screen", "look", "plan", "tell_owner", "browser", "chrome"):
            assert kept in got

    async def test_owner_installed_mcp_tools_are_appended(self, skills, monkeypatch):
        from types import SimpleNamespace

        from services import tool_registry

        monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
        mcp = SimpleNamespace(name="notion_search", description="search notion")
        monkeypatch.setattr(tool_registry, "_get_custom_mcp_tools", lambda: [mcp])
        got = await tool_registry.get_available_tools()
        assert [t.name for t in got][:14] == list(names.TOOL_ORDER) and got[-1] is mcp

    async def test_the_manual_is_the_tool_description(self, skills, monkeypatch):
        from services import tool_registry
        from services.toolset.manual import TOOL_MANUAL

        monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
        tools = await tool_registry.get_available_tools()
        assert tool_registry.get_tool_descriptions(tools) == TOOL_MANUAL

    def test_every_tool_carries_its_metadata(self):
        from services.toolset import build_toolset

        for t in build_toolset(ALL_ON):
            md = t.metadata or {}
            assert "timeout_s" in md, t.name
            if t.name in names.ACTION_TOOLS:
                assert md.get("action_arg") == "action", t.name


class TestLegacy:
    async def test_legacy_restores_the_old_registry_without_the_mcp_sends(self, skills, monkeypatch):
        from types import SimpleNamespace

        from services import mcp_client, tool_registry

        monkeypatch.setenv("EDWARD_TOOLSET", "legacy")
        apple = SimpleNamespace(name="messages", description="apple-mcp")
        wa = SimpleNamespace(name="whatsapp_send_message", description="wa")
        monkeypatch.setattr(mcp_client, "get_apple_mcp_tools", lambda: [apple], raising=False)
        monkeypatch.setattr(mcp_client, "is_apple_available", lambda: True, raising=False)
        monkeypatch.setattr(mcp_client, "get_whatsapp_mcp_tools", lambda: [wa], raising=False)
        monkeypatch.setattr(mcp_client, "is_whatsapp_available", lambda: True, raising=False)
        got = [t.name for t in await tool_registry.get_available_tools(message="whatsapp my calendar")]
        assert "send_imessage" in got and "remember_search" in got and "compose_screen" in got
        assert "messages" not in got and "whatsapp_send_message" not in got
        for retired in ("execute_code", "execute_shell", "spawn_cc_worker", "add_mcp_server", "create_hosted_page"):
            assert retired not in got

    async def test_legacy_has_no_keyword_gating(self, skills, monkeypatch):
        from services import tool_registry

        monkeypatch.setenv("EDWARD_TOOLSET", "legacy")
        hi = [t.name for t in await tool_registry.get_available_tools(message="hi")]
        full = [t.name for t in await tool_registry.get_available_tools()]
        assert hi == full and "web_search" in hi


class TestNames:
    def test_tiers(self):
        assert names.tier("send_message", {"message": "x"}) == "outward"
        assert names.tier("tell_owner", {}) == "outward"
        assert names.tier("chrome", {"action": "type"}) == "outward"
        assert names.tier("chrome", {"action": "read"}) is None
        assert names.tier("schedule", {"action": "add"}) == "commitment"
        assert names.tier("schedule", {"action": "calendar"}) is None
        assert names.tier("messages", {"action": "stand_down"}) == "commitment"
        assert names.tier("messages", {"action": "recent"}) is None
        assert names.tier("screen", {"action": "compose"}) == "surface"
        assert names.tier("screen", {"action": "state"}) is None
        assert names.tier("memory", {"action": "remember"}) == "internal"
        assert names.tier("memory", {"action": "search"}) is None
        assert names.tier("bash", {"command": "pip install yt-dlp"}) == "commitment"
        assert names.tier("bash", {"command": "nohup python fetch.py > jobs/f.log 2>&1 &"}) == "commitment"
        assert names.tier("bash", {"command": "ls -la && echo ok"}) is None
        assert names.tier("send_imessage", {}) is None  # not a toolset name

    def test_reaches_and_surfaces(self):
        assert names.reaches_people("send_message", {})
        assert names.reaches_people("tell_owner", {"urgent": True})
        assert not names.reaches_people("tell_owner", {"urgent": False})
        assert names.surface_of("screen", {"action": "widget"}) == "widget"
        assert names.surface_of("screen", {"action": "say"}) == "screen"
        assert names.surface_of("screen", {"action": "state"}) is None

    @pytest.mark.parametrize("old,args,new,new_args", [
        ("send_imessage", {"contact": "Parker", "message": "hi"}, "send_message", {"to": "Parker", "message": "hi"}),
        ("send_imessage", {"contact": "Parker", "chat_id": "chat1", "message": "hi"}, "send_message",
         {"chat_id": "chat1", "message": "hi"}),
        ("send_message", {"recipient": "Parker", "message": "hi", "channel": "imessage"}, "send_message",
         {"to": "Parker", "message": "hi"}),
        ("send_push_notification", {"title": "T", "body": "B", "urgency": "high"}, "tell_owner",
         {"message": "B", "title": "T", "urgent": True}),
        ("leave_note", {"title": "T", "body": "B", "needs_you": True}, "tell_owner", {"message": "B", "title": "T"}),
        ("wake_me", {"when": "2026-10-12T09:00:00", "why": "check"}, "schedule",
         {"action": "add", "kind": "wake", "when": "2026-10-12T09:00:00", "what": "check"}),
        ("stand_down", {"chat_id": "c1"}, "messages", {"action": "stand_down", "chat_id": "c1"}),
        ("remember_search", {"query": "q"}, "memory", {"action": "search", "query": "q"}),
        ("compose_screen", {"pieces": [1]}, "screen", {"action": "compose", "pieces": [1]}),
        ("fetch_page_content", {"url": "https://x"}, "web_fetch", {"url": "https://x"}),
    ])
    def test_retired_names_alias_to_the_new_tool(self, old, args, new, new_args):
        assert names.alias_call(old, args) == (new, new_args)

    def test_new_names_do_not_alias(self):
        assert names.alias_call("send_message", {"chat_id": "c", "message": "m"}) is None
        assert names.alias_call("memory", {"action": "search"}) is None


class TestPolicyShim:
    def test_area_one_shims(self):
        from services.graph import tool_policy

        assert tool_policy.REACH_TOOLS == names.REACH_TOOLS
        assert tool_policy.agent_tools() == names.TOOL_ORDER

    def test_reach_tools(self):
        assert names.REACH_TOOLS == {"send_message", "tell_owner", "chrome", "screen"}
        assert P.legacy_policy("mind", allowed_tools=[t for t in names.TOOL_ORDER if t not in names.REACH_TOOLS]
                               ).may_reach_people is False
