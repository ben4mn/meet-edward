"""Fakes for driving the chat tool loops end to end without I/O.

``patch_loop_io`` stubs every read the two loops make around the model
(memory, briefings, tools, the ledger's DB writes, post-turn learners) so a
test scripts only what matters: what the model answers on each call and
what each tool returns. ``FakeGraph`` applies LangGraph's real
``add_messages`` reducer, so ids, merges and ``RemoveMessage`` behave as
they do against Postgres.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any, Callable, Dict, List, Optional

from langchain_core.messages import AIMessage, AIMessageChunk


def ai(text: str = "", *calls: Dict[str, Any], **kw) -> AIMessage:
    """An assistant turn: text plus tool calls given as {"name", "args"[, "id"]}."""
    tool_calls = []
    for i, c in enumerate(calls):
        tool_calls.append({"id": c.get("id") or f"call_{c['name']}_{id(c)}_{i}", "name": c["name"],
                           "args": c.get("args", {}), "type": "tool_call"})
    return AIMessage(content=text, tool_calls=tool_calls, **kw)


def call(name: str, **args) -> Dict[str, Any]:
    return {"name": name, "args": args}


class FakeTool:
    def __init__(self, name: str, result: Any = "ok", delay: float = 0.0):
        self.name = name
        self.description = f"fake {name}"
        self._result = result
        self._delay = delay
        self.calls: List[dict] = []

    async def ainvoke(self, args):
        self.calls.append(dict(args or {}))
        if self._delay:
            await asyncio.sleep(self._delay)
        r = self._result
        if callable(r):
            r = r(args)
        if isinstance(r, BaseException):
            raise r
        return r


class FakeLLM:
    """Scripted model. ``script`` items are AIMessages, callables taking the
    messages sent, exceptions to raise, or ``("sleep", seconds, item)``."""

    def __init__(self, script: list, calls: list, *, tool_choice=None, bound=None, build_kwargs=None):
        self.script = script
        self.calls = calls
        self.tool_choice = tool_choice
        self.bound = bound or []
        self.build_kwargs = build_kwargs or {}

    def bind_tools(self, tools, **kw):
        return FakeLLM(self.script, self.calls, tool_choice=kw.get("tool_choice"),
                       bound=[t.name for t in tools], build_kwargs=self.build_kwargs)

    async def _next(self, messages):
        self.calls.append({"messages": list(messages), "tool_choice": self.tool_choice,
                           "tools": list(self.bound), "build": dict(self.build_kwargs)})
        item = self.script.pop(0) if self.script else AIMessage(content="(script exhausted)")
        if isinstance(item, tuple) and item and item[0] == "sleep":
            await asyncio.sleep(item[1])
            item = item[2]
        if callable(item) and not isinstance(item, AIMessage):
            item = item(messages)
        if isinstance(item, BaseException):
            raise item
        return item

    async def ainvoke(self, messages):
        return await self._next(messages)

    async def astream(self, messages):
        msg = await self._next(messages)
        yield AIMessageChunk(
            content=msg.content,
            tool_calls=list(msg.tool_calls or []),
            response_metadata=dict(msg.response_metadata or {}),
            usage_metadata=getattr(msg, "usage_metadata", None),
            additional_kwargs=dict(msg.additional_kwargs or {}),
        )


class FakeGraph:
    def __init__(self, messages: Optional[list] = None):
        self.state: Dict[str, Any] = {"messages": list(messages or [])}
        self.updates: List[tuple] = []

    async def aget_state(self, config):
        return SimpleNamespace(values=dict(self.state) if self.state.get("messages") else {})

    async def aupdate_state(self, config, values, as_node=None):
        from langgraph.graph.message import add_messages

        self.updates.append((values, as_node))
        values = dict(values)
        if "messages" in values:
            self.state["messages"] = add_messages(self.state.get("messages", []), values.pop("messages"))
        self.state.update(values)

    @property
    def messages(self) -> list:
        return list(self.state.get("messages", []))


class LoopHarness(SimpleNamespace):
    """What a test inspects after a turn."""


def patch_loop_io(monkeypatch, *, tools: List[FakeTool], script: list) -> LoopHarness:
    from services.graph import streaming
    from services import turn_ledger

    h = LoopHarness(calls=[], builds=[], learners=[], usage=[], post_turn=[], script=script)

    def _build_llm(model, temperature, max_tokens=16384, provider="openai", **kw):
        h.builds.append({"model": model, "provider": provider, **kw})
        return FakeLLM(script, h.calls, build_kwargs={"model": model, "provider": provider, **kw})

    async def _tools(message=None):
        return list(tools)

    async def _none(*a, **k):
        return None

    async def _empty(*a, **k):
        return []

    async def _ctx(**kw):
        return {"entity_memories": [], "enriched_memories": [], "relevant_documents": [],
                "state_documents": [], "briefing": None, "session_briefing_context": "",
                "open_loops": [], "tools": list(tools)}

    async def _search_tags(conversation_id, msgs):
        h.learners.append(("search_tags", conversation_id))

    async def _reflection(conversation_id, msgs, ids):
        h.learners.append(("reflection", conversation_id))

    async def _record_usage(**kw):
        h.usage.append(kw)

    async def _post_turn(**kw):
        h.post_turn.append(kw)

    class _Recaller:
        def __init__(self, **kw):
            pass

        async def observe(self, text):
            return None

    monkeypatch.setattr(streaming, "_build_llm", _build_llm)
    monkeypatch.setattr(streaming, "get_available_tools", _tools)
    monkeypatch.setattr(streaming, "build_memory_context", lambda *a, **k: "")
    monkeypatch.setattr(streaming, "_collect_llm_context", _ctx)
    # No conversation row to open with (a test that wants the real lookup
    # patches ``_load_entry_row`` back).
    monkeypatch.setattr(streaming, "_load_entry_row", _none)
    monkeypatch.setattr(turn_ledger, "_schedule", lambda coro: coro.close())

    import services.heartbeat.heartbeat_service as hb
    monkeypatch.setattr(hb, "get_pending_briefing", _none)
    monkeypatch.setattr(hb, "consume_ambient_notes", lambda cid: [])
    import services.session_briefing_service as sb
    monkeypatch.setattr(sb, "generate_briefing", _none)
    import services.orchestrator_service as orch
    monkeypatch.setattr(orch, "get_active_tasks_briefing", _none)
    import services.search_tag_service as st
    monkeypatch.setattr(st, "generate_search_tags_safe", _search_tags)
    import services.reflection_service as rs
    monkeypatch.setattr(rs, "should_reflect", lambda msgs, n: True)
    monkeypatch.setattr(rs, "run_reflection_safe", _reflection)
    monkeypatch.setattr(rs, "load_enrichments", _empty)
    import services.document_service as ds
    monkeypatch.setattr(ds, "retrieve_relevant_documents", _empty)
    monkeypatch.setattr(ds, "get_state_documents", _empty)
    import services.usage_service as us
    monkeypatch.setattr(us, "record_usage", _record_usage)
    import services.memory_service as ms
    monkeypatch.setattr(ms, "retrieve_memories", _empty)
    monkeypatch.setattr(ms, "extract_and_store_memories", _empty)
    import services.dream.speculative as spec

    async def _spec(**kw):
        return [], False

    monkeypatch.setattr(spec, "speculative_deep_retrieve", _spec)
    import services.speculative_recall as sr
    monkeypatch.setattr(sr, "SpeculativeRecaller", _Recaller)

    async def _deep(*a, **k):
        return None

    monkeypatch.setattr(streaming, "_run_deep_retrieval_safe", _deep)
    return h


def human_texts(messages) -> List[str]:
    from langchain_core.messages import HumanMessage

    return [m.content for m in messages if isinstance(m, HumanMessage) and isinstance(m.content, str)]


async def run_stream(**kwargs) -> List[dict]:
    from services.graph.streaming import stream_with_memory_events

    return [e async for e in stream_with_memory_events(**kwargs)]


def base_kwargs(graph, **over) -> Dict[str, Any]:
    kw: Dict[str, Any] = dict(conversation_id="conv-test", system_prompt="be edward", model="gpt-6-astra",
                              temperature=0.5, graph=graph)
    kw.update(over)
    return kw


Script = List[Any]
Callback = Callable[..., Any]
