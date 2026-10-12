"""``_trim_checkpoint_history`` must actually reach the checkpoint.

2026-09-14: zero "trimmed N stale messages" lines in the whole backend log
since the trim was added on 2026-09-12 — every call failed with LangGraph's
"Ambiguous update, specify as_node", so every thread kept its full history
on disk (edward-surfaces: 2,042 messages, 1.38 GB of blob versions).
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage, HumanMessage


class FakeGraph:
    def __init__(self):
        self.calls = []

    async def aupdate_state(self, config, state, **kwargs):
        self.calls.append((config, state, kwargs))


@pytest.mark.asyncio
async def test_trim_names_the_node_and_honours_the_window():
    from services.graph.streaming import _trim_checkpoint_history

    msgs = []
    for i in range(40):
        msgs.append(HumanMessage(content="x" * 1000, id=f"h{i}"))
        msgs.append(AIMessage(content="y" * 1000, id=f"a{i}"))
    graph = FakeGraph()
    removed = await _trim_checkpoint_history(graph, {"configurable": {"thread_id": "t"}}, msgs, max_chars=6_000)
    assert removed > 60
    config, state, kwargs = graph.calls[0]
    assert kwargs.get("as_node"), "an update without as_node is rejected as ambiguous and silently drops the trim"
    assert len(state["messages"]) == removed
