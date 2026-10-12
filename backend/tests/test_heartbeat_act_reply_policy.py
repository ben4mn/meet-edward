"""Heartbeat reply policy: the tool-exclusion mechanism stays available.

History: a non-mention heartbeat turn replied twice (as the owner) to casual
banter in one of the owner's chats. First response was a hard mute
(outbound tools stripped from non-mention turns); the owner rolled that
back — Edward MAY reply on his own judgment, but only when the reply serves
a clear purpose.

The legacy ACT respond path and its trigger left with the legacy triage
engine; the mind's prompt carries those rules now. What stays, and is under
test: the generic ``excluded_tools`` mechanism in ``chat_with_memory``.
"""

import inspect

from types import SimpleNamespace

from services.graph import streaming


def _fake_tools(*names):
    return [SimpleNamespace(name=n) for n in names]


def test_apply_tool_exclusions_mechanism_still_works():
    tools = _fake_tools("send_imessage", "remember_search")

    kept = streaming._apply_tool_exclusions(tools, ["send_imessage"])
    assert [t.name for t in kept] == ["remember_search"]

    assert streaming._apply_tool_exclusions(tools, None) == tools


def test_chat_with_memory_keeps_excluded_tools_param():
    assert "excluded_tools" in inspect.signature(streaming.chat_with_memory).parameters
