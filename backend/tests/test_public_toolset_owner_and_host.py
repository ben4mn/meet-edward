"""The toolset names the owner and its own hosts from the environment.

The public build serves whoever sets ``EDWARD_OWNER_NAME`` (conftest sets
"Ellis") on whatever host ``EDWARD_PUBLIC_BASE_URL`` names: the tool manual,
the tool descriptions and the refusals render the owner's name once, and
Edward's own ``/screen`` (where typing is free in any turn) is his only on
the hosts ``host_config.own_hosts()`` lists — never on a hardcoded domain.
"""

from __future__ import annotations

import pytest

from services.toolset import policy as P

pytestmark = pytest.mark.unit

ALL_ON = {"shell_interpreter": True, "brave_search": True, "imessage_applescript": True}
WAKE = P.TurnPolicy(source="mind")


def _type_on(url: str):
    return P.chrome_action_allowed("type", {"text": "hi"}, WAKE, current_url=url)


def test_edwards_screen_follows_the_public_url(monkeypatch):
    monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.org")
    monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
    hosts = P.own_screen_hosts()
    assert {"edward.example.org", "www.edward.example.org", "localhost", "127.0.0.1"} <= hosts
    assert _type_on("https://edward.example.org/screen") is None
    assert _type_on("https://www.edward.example.org/screen") is None
    # Only the stage: another page of his own app is still the owner's to type on.
    assert _type_on("https://edward.example.org/settings") is not None


def test_an_unconfigured_domain_is_not_edwards(monkeypatch):
    monkeypatch.delenv("EDWARD_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
    assert _type_on("https://edward.example.com/screen") is not None
    assert _type_on("http://localhost:3000/screen") is None


def test_the_old_constant_name_reads_the_live_hosts(monkeypatch):
    monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.org")
    assert "edward.example.org" in P.OWN_SCREEN_HOSTS
    monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://other.example.net")
    assert "edward.example.org" not in P.OWN_SCREEN_HOSTS


def test_the_refusals_name_the_owner():
    out = _type_on("https://mail.google.com/")
    assert "Ellis's Chrome acts as Ellis" in out and "when they ask" in out
    assert "Ellis does those themselves" in P.PAYMENT_REFUSAL
    assert "Ellis's call" in P.not_asked_refusal("Kai")


def test_the_manual_names_the_owner_and_this_checkout():
    from services.host_config import repo_root
    from services.toolset.manual import TOOL_MANUAL

    assert "Ellis's real Chrome on this Mac" in TOOL_MANUAL
    assert f"Your live code at {repo_root()} is read-only" in TOOL_MANUAL
    assert "{OWNER}" not in TOOL_MANUAL and "{LIVE_REPO}" not in TOOL_MANUAL
    assert "Mac mini" not in TOOL_MANUAL


def test_every_tool_description_is_rendered():
    from services.toolset import build_toolset

    for t in build_toolset(ALL_ON):
        assert "{OWNER}" not in (t.description or ""), t.name
    from services.toolset import memory, messaging, schedule, tell_owner

    assert "Reach Ellis:" in tell_owner.tell_owner.description
    assert "from Ellis's number" in messaging.send_message.description
    assert "Ellis's calendar and Reminders" in schedule.schedule.description
    assert "only when Ellis asks" in memory.memory.description
    assert "Ellis's call" not in schedule.CHANGE_REFUSAL and "Ellis's calendar" in schedule.CHANGE_REFUSAL


async def test_never_text_the_owner_reads_every_owner_handle(monkeypatch):
    """``graph/tools`` keeps the iMessage-only list (``_owner_handles``) and
    the full one, emails included (``_owner_all_handles``); the send guard
    reads the full one."""
    from services.graph import tools as legacy
    from services.toolset import messaging

    async def every():
        return ["+15550001111", "me@example.org"]

    async def imessage_only():
        return ["+15550001111"]

    monkeypatch.setattr(legacy, "_owner_all_handles", every, raising=False)
    monkeypatch.setattr(legacy, "_owner_handles", imessage_only)
    assert await messaging._owner_handles() == ["+15550001111", "me@example.org"]


def test_the_search_country_is_configurable(monkeypatch):
    from services.toolset import web

    monkeypatch.delenv("EDWARD_SERVER_WEB_TOOLS", raising=False)
    monkeypatch.setenv("EDWARD_SEARCH_COUNTRY", "gb")
    assert web.server_tool_spec("web_search", "claude-sonnet-5-5")["user_location"]["country"] == "GB"
    monkeypatch.setenv("EDWARD_SEARCH_COUNTRY", "not a country")
    assert web.server_tool_spec("web_search", "claude-sonnet-5-5")["user_location"]["country"] == "US"
    monkeypatch.delenv("EDWARD_SEARCH_COUNTRY")
    assert web.server_tool_spec("web_search", "claude-sonnet-5-5")["user_location"]["country"] == "US"
