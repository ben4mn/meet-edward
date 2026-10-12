"""
Regression: list_chat_participants crashed on every chat that had participants.

    Tool list_chat_participants result: Tool error: 'str' object has no attribute 'get'

Found 2026-08-22. A name-shadowing bug inside services/graph/tools.py:

    line 552:  from services.contacts_service import lookup_phone   # -> dict
    line 594:      result = lookup_phone(handle); result.get("success")
    line 852:  @tool
               def lookup_phone(phone_number: str) -> str:          # -> str

The module-level import is rebound by the tool definition further down the same
file. ``resolve()`` looks the name up in module globals at call time, so it gets
the LangChain tool — which returns a formatted string — and .get() explodes.

The fix must bind the contacts helper under a name the tool cannot shadow. These
tests pin the behaviour (participants resolve to names) rather than the spelling,
so they keep working however the import is renamed.
"""

from __future__ import annotations

import pytest

import services.graph.tools as tools_mod


class TestNoShadowing:
    def test_module_level_name_is_the_langchain_tool(self):
        """Document the hazard: the tool DOES own this name at module level."""
        assert hasattr(tools_mod.lookup_phone, "name")
        assert tools_mod.lookup_phone.name == "lookup_phone"

    def test_participant_resolution_does_not_use_the_shadowed_name(self, monkeypatch):
        """If resolution reaches the @tool, it gets a str and crashes."""
        monkeypatch.setattr(
            "services.heartbeat.listener_imessage.get_chat_participants",
            lambda chat_identifier: [{"handle": "+15555550171"}],
        )

        def fake_lookup(phone_number):
            return {"success": True, "matches": [{"name": "Rowan (Ro) Hale"}]}

        # Participants are named through lookup_handle (2026-10-11).
        monkeypatch.setattr(
            "services.contacts_service.lookup_handle", fake_lookup, raising=True
        )

        import asyncio

        out = asyncio.run(
            tools_mod.list_chat_participants.ainvoke(
                {"chat_identifier": "chat100000000000000001"}
            )
        )
        assert "str' object has no attribute" not in out
        assert "Rowan (Ro) Hale" in out
        assert "+15555550171" in out


class TestGracefulDegradation:
    @pytest.mark.asyncio
    async def test_unmatched_handle_falls_back_to_raw_number(self, monkeypatch):
        monkeypatch.setattr(
            "services.heartbeat.listener_imessage.get_chat_participants",
            lambda chat_identifier: [{"handle": "+15550001111"}],
        )
        monkeypatch.setattr(
            "services.contacts_service.lookup_phone",
            lambda phone_number: {"success": False},
            raising=True,
        )
        out = await tools_mod.list_chat_participants.ainvoke(
            {"chat_identifier": "chat1"}
        )
        assert "+15550001111" in out
        assert "no Contacts match" in out

    @pytest.mark.asyncio
    async def test_lookup_raising_does_not_kill_the_roster(self, monkeypatch):
        monkeypatch.setattr(
            "services.heartbeat.listener_imessage.get_chat_participants",
            lambda chat_identifier: [{"handle": "+15550002222"}],
        )

        def boom(phone_number):
            raise RuntimeError("Contacts.app unavailable")

        monkeypatch.setattr(
            "services.contacts_service.lookup_phone", boom, raising=True
        )
        out = await tools_mod.list_chat_participants.ainvoke(
            {"chat_identifier": "chat1"}
        )
        assert "+15550002222" in out

    @pytest.mark.asyncio
    async def test_empty_roster_message_is_unchanged(self, monkeypatch):
        monkeypatch.setattr(
            "services.heartbeat.listener_imessage.get_chat_participants",
            lambda chat_identifier: [],
        )
        out = await tools_mod.list_chat_participants.ainvoke(
            {"chat_identifier": "nope"}
        )
        assert "no participants found" in out
