"""A turn's tool-round cap is a parameter; a research wake gets 50 (followups
L11, 2026-09-27).

Both tool loops hard-coded 30 rounds (100 while a plan is active). The first
research wake used all 30 in 285 s of its 600 s and stopped mid-search
("it ran out of tool iterations (30)"): a research turn reads a page per
round, and the cap, not the clock, ended it. ``chat_with_memory`` and
``stream_with_memory_events`` now take ``max_tool_iterations`` (default
``EDWARD_MAX_TOOL_ROUNDS``, 60, since the toolset; a plan no longer raises
it), and ``mind_service`` asks 50 for a ``research`` wake only.
"""

from __future__ import annotations

import pytest

from services.graph import streaming
from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io, run_stream


def _endless_search(monkeypatch):
    """A model that asks for one more distinct search every round."""
    tool = FakeTool("web_search", lambda a: f"results for {a.get('query')}")
    script = [ai("", call("web_search", query=f"q{i}")) for i in range(200)]
    h = patch_loop_io(monkeypatch, tools=[tool], script=script)
    return h, tool


async def test_a_turn_stops_at_60_rounds_by_default(monkeypatch):
    """60 since the toolset (tools-spec §5.4): rounds are a poor proxy for
    cost; the per-turn dollar cap is the real bound."""
    monkeypatch.delenv("EDWARD_MAX_TOOL_ROUNDS", raising=False)
    _, tool = _endless_search(monkeypatch)
    out = await streaming.chat_with_memory(message="look it up", turn_source="mind", skip_extraction=True,
                                           **base_kwargs(FakeGraph()))
    assert len(tool.calls) == 60
    assert "60" in out


async def test_chat_with_memory_takes_a_higher_cap(monkeypatch):
    _, tool = _endless_search(monkeypatch)
    await streaming.chat_with_memory(message="look it up", turn_source="mind", skip_extraction=True,
                                     max_tool_iterations=50, **base_kwargs(FakeGraph()))
    assert len(tool.calls) == 50


async def test_the_streaming_loop_takes_it_too(monkeypatch):
    _, tool = _endless_search(monkeypatch)
    events = await run_stream(message="look it up", max_tool_iterations=50, **base_kwargs(FakeGraph()))
    assert any(e.get("type") == "done" for e in events)
    assert len(tool.calls) == 50


async def test_the_streaming_default_is_60_too(monkeypatch):
    monkeypatch.delenv("EDWARD_MAX_TOOL_ROUNDS", raising=False)
    _, tool = _endless_search(monkeypatch)
    await run_stream(message="look it up", **base_kwargs(FakeGraph()))
    assert len(tool.calls) == 60


@pytest.mark.parametrize("reason", ["self", "mention", "nightly", "manual"])
async def test_no_wake_asks_for_more_rounds_since_research_went(monkeypatch, reason):
    """The research wake (50 rounds) went with the surplus queue
    (2026-10-11): every wake takes the turn's default."""
    import services.mind_service as ms
    from tests.test_mind_service import _const, _stub

    seen = []

    async def chat(**kw):
        seen.append(kw.get("max_tool_iterations"))
        return "Looked into it."

    _stub(monkeypatch, ms, chat)
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "nightly_has_material", _const(True))
    monkeypatch.setattr(ms, "_after_nightly", _const({}))
    ms.reset_state()
    await ms.wake(reason)
    assert seen == [None]
    assert not hasattr(ms, "tool_iterations_for")
