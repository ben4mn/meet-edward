"""The real toolset bound through the real ChatClaude (2026-10-11, the
area 2 integration): the loop's ``bind_for_turn`` and the adapter's own
``metadata`` resolution must agree, so

* ``bash`` and the editor go as their Anthropic-defined dicts, and as
  ordinary schemas when ``EDWARD_NATIVE_COMPUTER_TOOLS=off``;
* the web tools go in the version each model accepts — the basic server
  search and fetch on Opus/Sonnet (never the dynamic-filtering pair,
  2026-10-12), the basic search plus the LOCAL
  fetch on Haiku (never no fetch at all), the local pair when
  ``EDWARD_SERVER_WEB_TOOLS=off``.

Nothing leaves the machine: a fake Anthropic client, no request is sent.
"""

from __future__ import annotations

import pytest

from services.claude_chat_model import ChatClaude, _server_tools_for_model
from services.graph import tool_loop as tl
from services.toolset import all_tools
from tests.fixtures.fake_anthropic import FakeAnthropic


def _request_tools(model: str) -> list:
    """The ``tools`` array a request on ``model`` would carry."""
    llm = ChatClaude(client=FakeAnthropic([]), model=model, effort="medium")
    bound = tl.bind_for_turn(llm, list(all_tools().values()), "anthropic")
    return _server_tools_for_model([dict(t) for t in bound.bound_tools], model)


def _by_name(tools: list) -> dict:
    return {t.get("name"): t for t in tools}


@pytest.fixture(autouse=True)
def _flags_default(monkeypatch):
    monkeypatch.delenv("EDWARD_NATIVE_COMPUTER_TOOLS", raising=False)
    monkeypatch.delenv("EDWARD_SERVER_WEB_TOOLS", raising=False)


def test_the_computer_tools_bind_native_on_claude():
    tools = _by_name(_request_tools("claude-opus-5-5"))
    assert tools["bash"] == {"type": "bash_20250124", "name": "bash"}
    assert tools["str_replace_based_edit_tool"]["type"] == "text_editor_20250728"


def test_native_flag_off_binds_the_computer_tools_as_schemas(monkeypatch):
    monkeypatch.setenv("EDWARD_NATIVE_COMPUTER_TOOLS", "off")
    tools = _by_name(_request_tools("claude-opus-5-5"))
    assert "input_schema" in tools["bash"] and tools["bash"].get("type") in (None, "custom")
    assert "input_schema" in tools["str_replace_based_edit_tool"]


def test_opus_and_sonnet_get_the_basic_server_web_tools():
    for model in ("claude-opus-5-5", "claude-sonnet-5-5"):
        tools = _by_name(_request_tools(model))
        assert tools["web_search"]["type"] == "web_search_20250305", model
        assert tools["web_fetch"]["type"] == "web_fetch_20250910", model


def test_haiku_keeps_a_local_web_fetch():
    tools = _by_name(_request_tools("claude-haiku-5-5"))
    assert tools["web_search"]["type"] == "web_search_20250305"
    assert "web_fetch" in tools, "Haiku has no server fetch; the local one must stay bound"
    assert "input_schema" in tools["web_fetch"]


def test_server_flag_off_binds_the_local_web_tools(monkeypatch):
    monkeypatch.setenv("EDWARD_SERVER_WEB_TOOLS", "off")
    tools = _by_name(_request_tools("claude-opus-5-5"))
    assert "input_schema" in tools["web_search"] and "input_schema" in tools["web_fetch"]


def test_every_toolset_tool_is_bound_once():
    names = [t.get("name") for t in _request_tools("claude-opus-5-5")]
    assert sorted(names) == sorted(all_tools().keys())
