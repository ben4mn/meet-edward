"""The chat SSE stream is closed when Ellis leaves (review of batch B,
2026-09-27).

Stop in the PWA, iOS killing a hidden tab or a dropped network only aborts
the fetch. Starlette cancels the response, but ``_yield_with_heartbeats``
ran each step of the turn in a task behind ``asyncio.shield`` (so the 10 s
heartbeat timeout would not cancel it) and never cancelled it: the step —
an LLM call of up to 300 s, a tool of up to 120 s — ran on, holding the
thread's slot (``services/thread_turns.py``), and Ellis's next message in the
thread waited for it showing "thinking". A turn stopped mid-call writes no
checkpoint, so the wait protected nothing there. Now the in-flight step is
cancelled when the consumer goes away, and the stream's own ``finally``
frees the thread at once — except the turn's last step, the save of an
answer Ellis has already read, which runs to completion (below).

``_wrap_chat_stream`` catches everything to turn a crash into an explained
answer; a close (``GeneratorExit``) and a cancellation because Ellis left are
not crashes: no explainer call (a Codex request nobody reads), no
"async generator ignored GeneratorExit".
"""

from __future__ import annotations

import asyncio

import pytest

from tests.test_thread_serialization import _post_then_disconnect, _sse_app, _stream


@pytest.fixture(autouse=True)
def _no_ledger_writes(monkeypatch):
    from services import turn_ledger

    monkeypatch.setattr(turn_ledger, "_schedule", lambda coro: coro.close())


def _slow_first_turn(monkeypatch, step, cancelled):
    from services.graph import streaming

    async def impl(*, message, conversation_id, **kw):
        yield {"type": "progress", "step": "memory_search", "status": "started", "conversation_id": conversation_id}
        if message == "first":
            try:
                await asyncio.sleep(step)  # the LLM call in flight when Ellis presses Stop
            except asyncio.CancelledError:
                cancelled.append(message)
                raise
        yield {"type": "content", "content": f"re: {message}", "conversation_id": conversation_id}
        yield {"type": "done", "conversation_id": conversation_id}

    monkeypatch.setattr(streaming, "_stream_with_memory_events_impl", impl)


async def test_stop_cancels_the_step_in_flight_and_frees_the_thread_at_once(monkeypatch):
    from routers import chat as chat_router
    from services.mind_service import thread_lock

    cid, step, cancelled, explained = "chat-stop-now", 3.0, [], []

    async def explainer(**kw):
        explained.append(kw)
        return "explained"

    _slow_first_turn(monkeypatch, step, cancelled)
    monkeypatch.setattr(chat_router, "_format_chat_error_explanation", explainer)

    await _post_then_disconnect(_sse_app(cid, step), after_s=0.1)
    await asyncio.sleep(0)
    assert cancelled == ["first"], "the step Ellis stopped does not run on"
    assert not thread_lock(cid).locked(), "the thread is free as the stream closes"
    loop = asyncio.get_running_loop()
    started = loop.time()
    events = await asyncio.wait_for(_stream("second", conversation_id=cid), timeout=2)
    assert events[-1]["type"] == "done"
    assert loop.time() - started < 0.2
    assert explained == [], "Ellis leaving is not a crash to explain"


async def test_a_heartbeat_timeout_still_does_not_cancel_the_step():
    """The shield stays for what it was for: a quiet stretch longer than the
    heartbeat interval is not a disconnect."""
    from routers.chat import _yield_with_heartbeats

    async def slow_stream():
        await asyncio.sleep(0.25)
        yield {"type": "content", "content": "late"}

    chunks = [c async for c in _yield_with_heartbeats(slow_stream(), heartbeat_interval=0.1)]
    assert chunks[:2] == [": heartbeat\n\n", ": heartbeat\n\n"]
    assert chunks[-1].startswith("data: ") and "late" in chunks[-1]


async def test_a_consumer_that_leaves_cancels_the_step():
    from routers.chat import _yield_with_heartbeats

    cancelled = []

    async def stream():
        yield {"type": "progress"}
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.append(True)
            raise
        yield {"type": "done"}

    gen = _yield_with_heartbeats(stream(), heartbeat_interval=10.0)
    assert (await gen.__anext__()).startswith("data: ")
    pending = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.05)
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending
    await asyncio.sleep(0)
    assert cancelled == [True]


# ------------------------------------------- the answer Ellis already has -------
# Review of this change (2026-09-27): the cancel reached the turn's last step
# too — the trace, the checkpoint write, the trim and the post-turn learners
# run after the final content and before ``done``. Ellis has read the whole
# answer while Stop is still on screen; tapping it, switching apps or losing
# the network then cancelled the save, and his message and Edward's answer
# were gone on reload, with no extraction, search tags or reflection. The
# non-streaming loop already shields its save; the streaming one now lets
# that step finish, and holds the thread until it has.


class _SlowSaveGraph:
    """FakeGraph whose final checkpoint write (``is_complete``) takes 0.3 s."""

    def __init__(self, conversation_id):
        from tests.fixtures.tool_loop import FakeGraph

        self._inner = FakeGraph()
        self._cid = conversation_id
        self.final_save = {"started": False, "finished": False, "cancelled": False, "held": None}

    async def aget_state(self, config):
        return await self._inner.aget_state(config)

    async def aupdate_state(self, config, values, as_node=None):
        from services.mind_service import thread_lock

        if values.get("is_complete"):
            self.final_save["started"] = True
            try:
                await asyncio.sleep(0.3)  # a real thread's checkpoint write
            except asyncio.CancelledError:
                self.final_save["cancelled"] = True
                raise
            await self._inner.aupdate_state(config, values, as_node=as_node)
            self.final_save["finished"] = True
            # Ellis's next turn must not load the thread before this lands.
            self.final_save["held"] = thread_lock(self._cid).locked()
            return
        await self._inner.aupdate_state(config, values, as_node=as_node)

    @property
    def messages(self):
        return self._inner.messages


def _app_for(graph, conversation_id):
    from fastapi import FastAPI
    from starlette.middleware.base import BaseHTTPMiddleware
    from starlette.responses import StreamingResponse

    from routers import chat as chat_router

    class _PassThrough(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            return await call_next(request)

    app = FastAPI()
    app.add_middleware(_PassThrough)

    @app.post("/api/chat")
    async def chat_endpoint():
        async def generate():
            inner = chat_router._wrap_chat_stream(
                message="what's up", conversation_id=conversation_id, system_prompt="be edward",
                model="gpt-6-astra", temperature=0.4, graph=graph, attachments=None, provider="openai",
            )
            async for chunk in chat_router._yield_with_heartbeats(inner, heartbeat_interval=10.0):
                yield chunk
        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


async def _post_and_leave_on(app, marker: bytes):
    """POST /api/chat; the client goes away once a chunk carrying
    ``marker`` has been sent."""
    body = [{"type": "http.request", "body": b"{}", "more_body": False}]
    gone = asyncio.Event()
    sent = []

    async def receive():
        if body:
            return body.pop(0)
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message.get("type") == "http.response.body":
            chunk = message.get("body", b"")
            sent.append(chunk)
            if marker in chunk:
                gone.set()

    scope = {"type": "http", "method": "POST", "path": "/api/chat", "raw_path": b"/api/chat", "root_path": "",
             "query_string": b"", "headers": [(b"content-type", b"application/json")], "scheme": "http",
             "server": ("test", 80), "client": ("owner", 1), "http_version": "1.1", "asgi": {"version": "3.0"}}
    await asyncio.wait_for(app(scope, receive, send), timeout=5)
    return sent


async def test_leaving_after_the_answer_still_saves_it(monkeypatch):
    from langchain_core.messages import AIMessage, HumanMessage

    from services.mind_service import thread_lock
    from tests.fixtures.tool_loop import patch_loop_io

    cid = "chat-leave-after-answer"
    h = patch_loop_io(monkeypatch, tools=[], script=[AIMessage(content="all good here")])
    graph = _SlowSaveGraph(cid)

    # "Response complete" is the last event before the save; Ellis has the
    # whole answer and leaves before ``done``.
    sent = await _post_and_leave_on(_app_for(graph, cid), b"Response complete")
    await asyncio.sleep(0.6)

    assert any(b"all good here" in s for s in sent), "Ellis saw the whole answer"
    assert not any(b'"type": "done"' in s for s in sent), "he left before done"
    assert graph.final_save["started"] and not graph.final_save["cancelled"], graph.final_save
    assert graph.final_save["finished"], "the final save ran to completion"
    assert graph.final_save["held"] is True, "the thread stayed held until the write landed"
    assert [m.content for m in graph.messages if isinstance(m, HumanMessage)] == ["what's up"]
    assert [m.content for m in graph.messages if isinstance(m, AIMessage)] == ["all good here"]
    assert ("search_tags", cid) in h.learners, "the post-turn learners still ran"
    assert not thread_lock(cid).locked(), "and the thread is free afterwards"
