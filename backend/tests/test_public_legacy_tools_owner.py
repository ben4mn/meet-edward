"""The legacy tool bodies (``services/graph/tools.py``) name and recognise the
owner from the environment, never a literal.

* A send target names the owner by one of ``EDWARD_OWNER_NAME`` /
  ``_FULL_NAME`` / ``_ALIASES`` (whole), "me"/"myself", or one of the owner's
  handles — the database's ``imessage_owner_handles`` plus
  ``EDWARD_OWNER_HANDLES``.
* ``_owner_handles`` (the iMessage handles) and ``_owner_all_handles`` (those
  plus the owner's email addresses) are two functions: one calling the other
  must never recurse.
* Model-facing text is rendered once at import: no ``{OWNER}`` or
  ``{SCREEN_URL}`` token is ever left in a tool description or a manual.
"""

from __future__ import annotations

import pytest

from services.graph import tools


def test_the_owners_names_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    monkeypatch.setenv("EDWARD_OWNER_FULL_NAME", "Sam Rivera")
    monkeypatch.setenv("EDWARD_OWNER_ALIASES", "Sammy")
    assert tools.owner_target_names() == {"sam", "sam rivera", "sammy", "me", "myself", "owner", "the owner"}
    for target in ("Sam", "sam rivera", " Sammy. ", "me", "Myself"):
        assert tools.names_owner(target) is True, target
    for target in ("Samantha", "Sam Smith", "Ellis", ""):
        assert tools.names_owner(target) is False, target


def test_with_no_name_set_the_words_the_prompts_use_still_match(monkeypatch):
    # Unset, the prompts call the owner "the owner": a wake may address them so.
    for var in ("EDWARD_OWNER_NAME", "EDWARD_OWNER_FULL_NAME", "EDWARD_OWNER_ALIASES", "EDWARD_OWNER_HANDLES"):
        monkeypatch.delenv(var, raising=False)
    assert tools.owner_target_names() == {"me", "myself", "owner", "the owner"}
    for target in ("the owner", "Owner", "me"):
        assert tools.names_owner(target) is True, target
    for target in ("Ellis", "owners", "the owner's wife"):
        assert tools.names_owner(target) is False, target


def test_the_env_handles_name_the_owner(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_HANDLES", "+1 (201) 555-0100, sam@example.com")
    assert tools.names_owner("tel:+12015550100") is True
    assert tools.names_owner("Sam (+1 201 555 0100)") is True
    assert tools.names_owner("sam@example.com") is True
    assert tools.names_owner("+12015550199") is False
    monkeypatch.delenv("EDWARD_OWNER_HANDLES")
    assert tools.names_owner("tel:+12015550100") is False
    assert tools.names_owner("tel:+12015550100", ["+12015550100"]) is True


async def test_owner_handles_fall_back_to_the_env_when_the_db_is_unreadable(monkeypatch):
    from services import database

    def broken():
        raise RuntimeError("db down")

    monkeypatch.setattr(database, "async_session", broken)
    monkeypatch.setenv("EDWARD_OWNER_HANDLES", "+12015550100,sam@example.com")
    assert await tools._owner_handles() == ["+12015550100", "sam@example.com"]
    monkeypatch.delenv("EDWARD_OWNER_HANDLES")
    assert await tools._owner_handles() == []


async def test_all_handles_add_the_email_addresses_without_recursing(monkeypatch):
    from services import mail_service

    async def imessage_handles():
        return ["+12015550100", "sam@icloud.example"]

    async def addresses():
        return ["SAM@icloud.example", "sam.work@example.com"]

    monkeypatch.setattr(tools, "_owner_handles", imessage_handles)
    monkeypatch.setattr(mail_service, "owner_addresses", addresses)
    assert await tools._owner_all_handles() == ["+12015550100", "sam@icloud.example", "sam.work@example.com"]


async def test_all_handles_keep_the_imessage_handles_when_mail_fails(monkeypatch):
    from services import mail_service

    async def imessage_handles():
        return ["+12015550100"]

    async def broken():
        raise RuntimeError("mail down")

    monkeypatch.setattr(tools, "_owner_handles", imessage_handles)
    monkeypatch.setattr(mail_service, "owner_addresses", broken)
    assert await tools._owner_all_handles() == ["+12015550100"]


def test_the_refusals_name_the_owner_from_conftest():
    # conftest sets EDWARD_OWNER_NAME=Ellis before anything imports.
    assert tools.TEXT_TO_OWNER_REFUSAL == (
        "Tool error: Ellis is reached by send_push_notification (if it can't wait) or leave_note — not by text. "
        "If you meant someone else named Ellis, use their full name or number.")
    assert "{OWNER}" not in tools.HUB_LEGACY_REFUSAL and "{what}" in tools.HUB_LEGACY_REFUSAL
    assert "act as Ellis" in tools.HUB_LEGACY_REFUSAL


def _manuals():
    return {
        "memory": tools.get_memory_tools_description(),
        "contacts": tools.get_contacts_tools_description(),
        "web": tools.get_web_tools_description(),
        "documents": tools.get_document_tools_description(),
        "mind": tools.get_mind_tools_description(),
        "wishlist": tools.get_wishlist_tools_description(),
        "product": tools.get_product_tools_description(
            {"set_goal", "suggest_idea", "how_do_i_know", "forget_about"}),
        "provenance": tools.PROVENANCE_GUIDANCE,
    }


@pytest.mark.parametrize("name", sorted(_manuals()))
def test_no_manual_keeps_a_token(name):
    text = _manuals()[name]
    assert "{OWNER}" not in text and "{SCREEN_URL}" not in text


def test_no_tool_description_keeps_a_token():
    from langchain_core.tools import BaseTool

    described = [v for v in vars(tools).values() if isinstance(v, BaseTool)]
    assert len(described) > 40
    for t in described:
        d = t.description or ""
        assert "{OWNER}" not in d and "{SCREEN_URL}" not in d, t.name
    assert "Ellis's Chrome" in tools.browser_open.description
    assert "/screen" in tools.bring_up_screen.description
