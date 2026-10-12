"""One turn at a time in a thread (followups M3, 2026-09-27).

A turn reads the thread's checkpoint when it starts and writes it when it
ends. Only the mind's wakes took ``mind_service.thread_lock``; chat and
voice streams, scheduled events (in Ellis's threads or falling back to
``edward-mind``) and heartbeat turns ran unlocked, so two overlapping turns
in one thread each wrote their own copy and the last write won. Both turn
wrappers in ``services/graph/streaming.py`` now hold the thread's lock
(``services/thread_turns.py``) — without ever waiting on themselves: a wake
already holds it when it calls ``chat_with_memory``.

The turn bodies are faked here the way they use the checkpoint: read at the
start, write at the end. Without the lock the second write drops the first
turn's messages.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
CID = "chat-serial-1"


@pytest.fixture(autouse=True)
def _no_ledger_writes(monkeypatch):
    from services import turn_ledger

    monkeypatch.setattr(turn_ledger, "_schedule", lambda coro: coro.close())


class _Thread:
    """A stand-in checkpoint store plus a log of when turns ran."""

    def __init__(self):
        self.messages: dict = {}
        self.log: list = []

    async def turn(self, conversation_id, message, delay=0.05):
        self.log.append(("start", message))
        before = list(self.messages.get(conversation_id, []))
        await asyncio.sleep(delay)
        self.messages[conversation_id] = before + [message, f"re: {message}"]
        self.log.append(("end", message))
        return f"re: {message}"


def _fake_chat_impl(thread, inner=None):
    async def impl(*, message, conversation_id, **kw):
        if inner is not None:
            await inner(conversation_id)
        return await thread.turn(conversation_id, message)
    return impl


def _fake_stream_impl(thread, inner=None):
    async def impl(*, message, conversation_id, **kw):
        yield {"type": "progress", "step": "memory_search", "status": "started", "conversation_id": conversation_id}
        if inner is not None:
            await inner(conversation_id)
        await thread.turn(conversation_id, message)
        yield {"type": "content", "content": f"re: {message}", "conversation_id": conversation_id}
        yield {"type": "done", "conversation_id": conversation_id}
    return impl


def _install(monkeypatch, thread, *, chat_inner=None, stream_inner=None):
    from services.graph import streaming

    monkeypatch.setattr(streaming, "_chat_with_memory_impl", _fake_chat_impl(thread, chat_inner))
    monkeypatch.setattr(streaming, "_stream_with_memory_events_impl", _fake_stream_impl(thread, stream_inner))


async def _chat(message, conversation_id=CID, turn_source="chat"):
    from services.graph import streaming

    return await streaming.chat_with_memory(
        message=message, conversation_id=conversation_id, system_prompt="be edward", model="gpt-6-astra",
        temperature=0.4, graph=object(), turn_source=turn_source,
    )


async def _stream(message, conversation_id=CID, turn_source="chat"):
    from services.graph import streaming

    events = []
    async for event in streaming.stream_with_memory_events(
        message, conversation_id, "be edward", "gpt-6-astra", 0.4, object(), turn_source=turn_source,
    ):
        events.append(event)
    return events


def _serial(log):
    """Every turn ended before the next one started."""
    return all(log[i][0] == "start" and log[i + 1] == ("end", log[i][1]) for i in range(0, len(log), 2))


# ---------------------------------------------------------------- pure ------

def test_needs_lock():
    from services.thread_turns import CALLER_SERIALIZED_SOURCES, needs_lock

    assert CALLER_SERIALIZED_SOURCES == frozenset({"mind", "pulse"})
    for source in ("chat", "voice", "scheduled_event", "heartbeat", "worker", "widget", "alexa", "background"):
        assert needs_lock("c1", source)
    assert not needs_lock("c1", "mind"), "a wake's caller already holds the lock"
    assert not needs_lock("c1", "pulse")
    assert not needs_lock("", "chat") and not needs_lock(None, "chat")
    assert not needs_lock("c1", "chat", frozenset({"c1"})), "a nested turn never waits on its own thread"
    assert needs_lock("c1", "chat", frozenset({"c2"}))


# ------------------------------------------------------- one at a time ------

async def test_two_turns_on_one_thread_run_one_after_the_other(monkeypatch):
    thread = _Thread()
    _install(monkeypatch, thread)
    await asyncio.wait_for(asyncio.gather(_chat("from the phone"), _chat("from the mac")), timeout=5)
    assert _serial(thread.log), thread.log
    assert len(thread.messages[CID]) == 4, "both turns land in the checkpoint"


async def test_two_streamed_turns_on_one_thread_run_one_after_the_other(monkeypatch):
    thread = _Thread()
    _install(monkeypatch, thread)
    a, b = await asyncio.wait_for(asyncio.gather(_stream("phone"), _stream("mac")), timeout=5)
    assert _serial(thread.log), thread.log
    assert len(thread.messages[CID]) == 4
    assert a[-1]["type"] == "done" and b[-1]["type"] == "done"


async def test_a_chat_stream_and_a_scheduled_event_share_the_thread(monkeypatch):
    thread = _Thread()
    _install(monkeypatch, thread)
    await asyncio.wait_for(asyncio.gather(
        _stream("what's for dinner"), _chat("[SCHEDULED EVENT] text me at 5", turn_source="scheduled_event"),
    ), timeout=5)
    assert _serial(thread.log), thread.log
    assert len(thread.messages[CID]) == 4


async def test_a_scheduled_event_in_edward_mind_waits_for_a_wake(monkeypatch):
    from services.mind_service import thread_lock

    thread = _Thread()
    _install(monkeypatch, thread)

    async def wake_like():
        async with thread_lock("edward-mind"):
            await thread.turn("edward-mind", "wake pack")

    await asyncio.wait_for(asyncio.gather(
        wake_like(), _chat("memory report", conversation_id="edward-mind", turn_source="scheduled_event"),
    ), timeout=5)
    assert _serial(thread.log), thread.log
    assert len(thread.messages["edward-mind"]) == 4


async def test_different_threads_still_run_side_by_side(monkeypatch):
    thread = _Thread()
    _install(monkeypatch, thread)
    await asyncio.wait_for(asyncio.gather(
        _chat("a", conversation_id="thread-a"), _stream("b", conversation_id="thread-b"),
    ), timeout=5)
    assert [kind for kind, _ in thread.log[:2]] == ["start", "start"], thread.log


# -------------------------------------------------------- no deadlock -------

async def test_a_wake_holding_the_lock_runs_its_turn(monkeypatch):
    from services.mind_service import thread_lock

    thread = _Thread()
    _install(monkeypatch, thread)
    for source, cid in (("mind", "edward-mind"), ("pulse", "edward-surfaces")):
        async with thread_lock(cid):
            # mind_service._wake_once: the lock, then the turn under wait_for.
            out = await asyncio.wait_for(_chat("pack", conversation_id=cid, turn_source=source), timeout=2)
        assert out == "re: pack"


async def test_the_real_wake_path_completes(monkeypatch):
    import services.mind_service as ms
    from tests.test_mind_service import _S, _const

    thread = _Thread()
    _install(monkeypatch, thread)
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    ms.reset_state()
    try:
        out = await asyncio.wait_for(ms.wake("manual", message="look around", record=False), timeout=5)
        pulse = await asyncio.wait_for(
            ms.wake("surfaces", message="pulse", conversation_id="edward-surfaces", tools=("update_widget",),
                    system_prompt="S", turn_source="pulse", skip_extraction=True, record=False), timeout=5)
    finally:
        ms.reset_state()
    assert out["ok"] is True and pulse["ok"] is True
    assert not ms.thread_lock("edward-mind").locked()


async def test_a_turn_started_inside_a_streamed_turn_on_its_thread_does_not_wait(monkeypatch):
    from services.graph import streaming

    thread = _Thread()
    nested = []

    async def inner(conversation_id):
        nested.append(await _chat("nested", conversation_id=conversation_id, turn_source="worker"))

    monkeypatch.setattr(streaming, "_chat_with_memory_impl", _fake_chat_impl(thread))
    monkeypatch.setattr(streaming, "_stream_with_memory_events_impl", _fake_stream_impl(thread, inner))
    await asyncio.wait_for(_stream("outer"), timeout=5)
    assert nested == ["re: nested"]


async def test_a_turn_started_inside_a_turn_on_its_thread_does_not_wait(monkeypatch):
    from services.graph import streaming

    thread = _Thread()
    nested = []
    depth = {"n": 0}

    async def impl(*, message, conversation_id, **kw):
        depth["n"] += 1
        if depth["n"] == 1:
            nested.append(await _chat("in the same task", conversation_id=conversation_id))
            # A task the turn creates and awaits sees what its creator holds.
            nested.append(await asyncio.create_task(_chat("in a child task", conversation_id=conversation_id)))
        return await thread.turn(conversation_id, message)

    monkeypatch.setattr(streaming, "_chat_with_memory_impl", impl)
    await asyncio.wait_for(_chat("outer"), timeout=5)
    assert nested == ["re: in the same task", "re: in a child task"]


async def test_the_held_marker_never_leaks_out_of_a_stream(monkeypatch):
    from services import thread_turns
    from services.graph import streaming

    thread = _Thread()
    _install(monkeypatch, thread)
    seen = []
    async for _ in streaming.stream_with_memory_events("hi", CID, "p", "gpt-6-astra", 0.4, object()):
        seen.append(thread_turns.held_threads())
    assert seen and all(h == frozenset() for h in seen), seen
    assert thread_turns.held_threads() == frozenset()


async def test_a_stream_the_consumer_leaves_after_done_frees_the_thread(monkeypatch):
    from services.graph import streaming
    from services.mind_service import thread_lock

    thread = _Thread()
    _install(monkeypatch, thread)
    gen = streaming.stream_with_memory_events("voice turn", CID, "p", "gpt-6-astra", 0.4, object(),
                                              turn_source="voice")
    async for event in gen:
        if event["type"] == "done":
            break  # routers/voice_ws.py; the generator is closed later
    assert not thread_lock(CID).locked(), "the thread is free once the last event has passed"
    assert await asyncio.wait_for(_chat("next question"), timeout=2) == "re: next question"
    await gen.aclose()


async def test_a_turn_cancelled_while_waiting_holds_nothing(monkeypatch):
    from services.mind_service import thread_lock

    thread = _Thread()
    _install(monkeypatch, thread)
    lock = thread_lock(CID)
    await lock.acquire()
    waiting = asyncio.create_task(_chat("stop pressed"))
    await asyncio.sleep(0.05)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    lock.release()
    assert await asyncio.wait_for(_chat("again"), timeout=2) == "re: again"
    assert not lock.locked()
    assert thread.log == [("start", "again"), ("end", "again")]


# --------------------------------------------------- the lock holders -------

def _calls_named(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            if fname == name:
                yield node


def test_every_wake_runs_its_turn_as_a_caller_serialized_source():
    """``mind_service._wake_once`` holds ``thread_lock`` when it calls
    ``chat_with_memory(turn_source=turn_source)``. A wake whose turn source
    were anything else would wait on the lock its own caller holds."""
    from services.thread_turns import CALLER_SERIALIZED_SOURCES

    offenders = []
    for root in ("services", "routers", "scripts"):
        for path in (BACKEND / root).rglob("*.py"):
            tree = ast.parse(path.read_text())
            for call in _calls_named(tree, "wake"):
                for kw in call.keywords:
                    if kw.arg == "turn_source":
                        if not (isinstance(kw.value, ast.Constant) and kw.value.value in CALLER_SERIALIZED_SOURCES):
                            if not (isinstance(kw.value, ast.Name) and path.name == "mind_service.py"):
                                offenders.append(f"{path.relative_to(BACKEND)}:{call.lineno}")
    assert not offenders, offenders


def test_the_wake_takes_the_thread_lock_before_its_turn():
    src = (BACKEND / "services/mind_service.py").read_text()
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.AsyncFunctionDef)}
    body = ast.get_source_segment(src, fns["_wake_once"])
    assert body.index("thread_lock(conversation_id)") < body.index("chat_with_memory(")
    assert "turn_source=turn_source" in body
    defaults = {a.arg: d for a, d in zip(fns["wake"].args.kwonlyargs, fns["wake"].args.kw_defaults)}
    assert defaults["turn_source"].value == "mind"


# ----------------------------------------------------- the scheduler --------

@pytest.mark.integration
async def test_a_failed_event_writes_its_note_under_the_thread_lock(clean_db, monkeypatch):
    import services.graph as graph_pkg
    import services.settings_service as ss
    from services import scheduler_service
    from services.conversation_service import create_conversation
    from services.database import ScheduledEventModel, async_session
    from services.mind_service import thread_lock
    from services.scheduled_events_service import _utcnow

    class _S:
        model = "gpt-6-astra"
        temperature = 0.4
        system_prompt = "be edward"
        provider = "openai"

    held_at_write = []

    class _Graph:
        async def aupdate_state(self, config, values, as_node=None):
            held_at_write.append(thread_lock(config["configurable"]["thread_id"]).locked())

    async def failing_chat(**kw):
        raise RuntimeError("codex down")

    async def fake_graph():
        return _Graph()

    async def fake_settings():
        return _S()

    monkeypatch.setattr(graph_pkg, "chat_with_memory", failing_chat)
    monkeypatch.setattr(graph_pkg, "get_graph", fake_graph)
    monkeypatch.setattr(ss, "get_settings", fake_settings)
    await create_conversation("chat-note", title="Dinner", source="user")
    async with async_session() as s:
        s.add(ScheduledEventModel(id="ev-lock", description="Text me at 5", scheduled_at=_utcnow(),
                                  next_fire_at=_utcnow(), status="processing", conversation_id="chat-note"))
        await s.commit()
    async with async_session() as s:
        ev = await s.get(ScheduledEventModel, "ev-lock")
    await scheduler_service._execute_event(ev)
    assert held_at_write == [True], "the error note waits for the thread like a turn does"
    assert not thread_lock("chat-note").locked()


def _legacy_event(event_id, conversation_id=None):
    from types import SimpleNamespace

    return SimpleNamespace(id=event_id, event_metadata=None, description="Remind Ellis about the vet",
                           delivery_channel=None, conversation_id=conversation_id, recurrence_pattern=None)


def _scheduler_fakes(monkeypatch):
    """The legacy event path around the real ``chat_with_memory`` (whose body
    ``_install`` fakes): settings, the graph, the mind's thread and the
    event store's writes."""
    import services.graph as graph_pkg
    import services.mind_service as ms
    import services.settings_service as settings_service
    from services import scheduler_service

    class _S:
        model = "gpt-6-astra"
        temperature = 0.4
        system_prompt = "be edward"
        provider = "openai"

    async def fake_settings():
        return _S()

    async def fake_graph():
        return object()

    async def nothing():
        return None

    done = {"completed": [], "failed": []}

    async def mark_completed(event_id, result=None):
        done["completed"].append(event_id)

    async def mark_failed(event_id, error):
        done["failed"].append((event_id, error))

    monkeypatch.setattr(settings_service, "get_settings", fake_settings)
    monkeypatch.setattr(graph_pkg, "get_graph", fake_graph)
    monkeypatch.setattr(ms, "_ensure_conversation", nothing)
    monkeypatch.setattr(scheduler_service, "mark_event_completed", mark_completed)
    monkeypatch.setattr(scheduler_service, "mark_event_failed", mark_failed)
    monkeypatch.setattr(scheduler_service, "_record_event_outcome", lambda *a, **k: None)
    return scheduler_service, done


async def test_an_event_behind_a_long_wake_runs_once_the_thread_is_free(monkeypatch):
    """The wait for the thread is not the event's run time (review of
    batch B, 2026-09-27). ``_run_event`` bounds an ordinary event at 600 s;
    a reminder falling back to ``edward-mind`` while the Sunday reflection
    held it (900 s) waited inside that bound, was cancelled, and was written
    "timed out after 600s" — lost, and labelled as having run past."""
    from services.mind_service import thread_lock

    thread = _Thread()
    _install(monkeypatch, thread)
    ss, done = _scheduler_fakes(monkeypatch)
    monkeypatch.setattr(ss, "EVENT_TIMEOUT_SECONDS", 0.3)

    async def long_wake():
        async with thread_lock("edward-mind"):
            thread.log.append(("start", "reflect"))
            await asyncio.sleep(0.6)  # longer than the event's bound
            thread.log.append(("end", "reflect"))

    wake = asyncio.create_task(long_wake())
    await asyncio.sleep(0.01)
    await asyncio.wait_for(asyncio.gather(wake, ss._run_event(_legacy_event("ev-behind"))), timeout=5)

    assert done["failed"] == [], "waiting for the thread is not running past the bound"
    assert done["completed"] == ["ev-behind"]
    assert _serial(thread.log) and thread.log[0] == ("start", "reflect"), thread.log
    assert not thread_lock("edward-mind").locked()


async def test_an_event_that_overruns_its_bound_frees_the_thread(monkeypatch):
    from services.mind_service import thread_lock

    thread = _Thread()
    ss, done = _scheduler_fakes(monkeypatch)
    monkeypatch.setattr(ss, "EVENT_TIMEOUT_SECONDS", 0.1)

    async def hung_turn(*, message, conversation_id, **kw):
        await asyncio.sleep(3600)

    from services.graph import streaming

    monkeypatch.setattr(streaming, "_chat_with_memory_impl", hung_turn)
    await asyncio.wait_for(ss._run_event(_legacy_event("ev-hung")), timeout=5)
    assert [eid for eid, _ in done["failed"]] == ["ev-hung"]
    assert "timed out after 0.1s" in done["failed"][0][1]
    assert not thread_lock("edward-mind").locked()
    assert thread.log == []


async def test_an_event_whose_thread_cannot_be_read_is_failed_not_stuck(monkeypatch):
    from services import conversation_service
    from services.mind_service import thread_lock

    ss, done = _scheduler_fakes(monkeypatch)

    async def db_down(conversation_id):
        raise ConnectionRefusedError("postgres is restarting")

    monkeypatch.setattr(conversation_service, "get_conversation", db_down)
    await asyncio.wait_for(ss._run_event(_legacy_event("ev-nodb", conversation_id="chat-x")), timeout=5)
    assert [eid for eid, _ in done["failed"]] == ["ev-nodb"]
    assert "postgres is restarting" in done["failed"][0][1]
    assert not thread_lock("edward-mind").locked() and not thread_lock("chat-x").locked()


# ------------------------------------------------ a stream Ellis stops --------
#
# Review of batch B, 2026-09-27. A chat stream holds its thread across every
# yield until ``done`` or until it is closed. When Ellis presses Stop (the
# PWA aborts the fetch), iOS kills a hidden tab or the network drops,
# Starlette cancels the response, but ``routers/chat.py``'s
# ``_yield_with_heartbeats`` runs each step in a shielded task and nothing
# closes the generator: the step finishes, the turn sits at a ``yield`` with
# the thread held, and the chain is kept by a reference cycle (through the
# BaseHTTPMiddleware that AuthMiddleware is) until the cyclic GC happens to
# run — with little allocation after the disconnect, that was over a minute
# in the review's runs. Ellis's next message in the thread waited all that
# time showing "thinking". The router fix (close the stream on disconnect)
# lives in routers/chat.py; a turn waiting on a thread now also nudges the
# young GC generations, so an abandoned stream frees its thread within
# about a second of its last step.

def _sse_app(conversation_id, step_seconds):
    """The production SSE plumbing around a turn: a BaseHTTPMiddleware (as
    AuthMiddleware is), a StreamingResponse, and the router's own
    ``_wrap_chat_stream`` and ``_yield_with_heartbeats``."""
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
                message="first", conversation_id=conversation_id, system_prompt="p", model="gpt-6-astra",
                temperature=0.4, graph=object(), attachments=None, provider="openai",
            )
            async for chunk in chat_router._yield_with_heartbeats(inner, heartbeat_interval=10.0):
                yield chunk
        return StreamingResponse(generate(), media_type="text/event-stream")

    return app


async def _post_then_disconnect(app, after_s):
    """POST /api/chat, then the client goes away ``after_s`` in."""
    body = [{"type": "http.request", "body": b"{}", "more_body": False}]
    gone = asyncio.Event()

    async def receive():
        if body:
            return body.pop(0)
        await gone.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        return None

    scope = {"type": "http", "method": "POST", "path": "/api/chat", "raw_path": b"/api/chat", "root_path": "",
             "query_string": b"", "headers": [(b"content-type", b"application/json")], "scheme": "http",
             "server": ("test", 80), "client": ("owner", 1), "http_version": "1.1", "asgi": {"version": "3.0"}}
    request = asyncio.create_task(app(scope, receive, send))
    await asyncio.sleep(after_s)
    gone.set()
    await request


async def test_a_stream_owner_stops_mid_turn_does_not_hold_the_thread(monkeypatch):
    import gc

    from routers import chat as chat_router
    from services import thread_turns
    from services.graph import streaming
    from services.mind_service import thread_lock

    cid = "chat-stopped"
    step = 0.3

    async def slow_turn(*, message, conversation_id, **kw):
        yield {"type": "progress", "step": "memory_search", "status": "started", "conversation_id": conversation_id}
        if message == "first":
            await asyncio.sleep(step)  # an LLM call in flight when Ellis presses Stop
        yield {"type": "content", "content": f"re: {message}", "conversation_id": conversation_id}
        yield {"type": "done", "conversation_id": conversation_id}

    async def no_explainer(**kw):
        return "explained"  # never a real LLM call from a test

    monkeypatch.setattr(streaming, "_stream_with_memory_events_impl", slow_turn)
    monkeypatch.setattr(chat_router, "_format_chat_error_explanation", no_explainer)
    monkeypatch.setattr(thread_turns, "GC_NUDGE_SECONDS", 0.05)

    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()  # no automatic collection may free the thread by luck
    try:
        await _post_then_disconnect(_sse_app(cid, step), after_s=0.1)
        started = asyncio.get_running_loop().time()
        events = await asyncio.wait_for(_stream("second", conversation_id=cid), timeout=2)
        waited = asyncio.get_running_loop().time() - started
    finally:
        if was_enabled:
            gc.enable()
    assert events[-1]["type"] == "done"
    assert waited < step + 1.0, f"the next turn waited {waited:.2f}s"
    assert not thread_lock(cid).locked()


async def test_a_stream_abandoned_in_a_reference_cycle_frees_its_thread(monkeypatch):
    """Whoever leaks it: a stream left suspended at a yield, kept only by a
    reference cycle, frees its thread at the next nudge while a turn waits."""
    import gc

    from services import thread_turns
    from services.graph import streaming
    from services.mind_service import thread_lock

    thread = _Thread()
    _install(monkeypatch, thread)
    monkeypatch.setattr(thread_turns, "GC_NUDGE_SECONDS", 0.05, raising=False)
    cid = "chat-leaked"

    class _Frame:
        pass

    was_enabled = gc.isenabled()
    gc.collect()
    gc.disable()
    try:
        gen = streaming.stream_with_memory_events("first", cid, "p", "gpt-6-astra", 0.4, object())
        await gen.__anext__()  # the turn has started and holds the thread
        leaked = _Frame()
        leaked.stream, leaked.self = gen, leaked  # a cycle, as the SSE plumbing leaves one
        del gen, leaked
        assert thread_lock(cid).locked()
        events = await asyncio.wait_for(_stream("second", conversation_id=cid), timeout=2)
    finally:
        if was_enabled:
            gc.enable()
    assert events[-1]["type"] == "done"


# ------------------------------------------------------- the backstop -------

def test_the_backstop_outlasts_every_holders_own_bound():
    """A turn never waits forever on its thread, but it never gives up on a
    holder that is still inside its own deadline either."""
    from services import scheduler_service, thread_turns
    from services.graph import tool_deadlines

    longest_tick = max(scheduler_service.event_timeout_for({"type": "wakeup_tick", "kind": kind})
                       for kind in ("morning", "evening", "reflect", "reflect_lite"))
    streamed_turn = tool_deadlines.STREAM_TURN_MAX_SECONDS + tool_deadlines.LLM_CALL_TIMEOUT_SECONDS
    assert thread_turns.ACQUIRE_BACKSTOP_SECONDS > max(longest_tick, streamed_turn,
                                                        scheduler_service.EVENT_TIMEOUT_SECONDS)


async def test_a_turn_behind_a_thread_nobody_frees_runs_after_the_backstop(monkeypatch, capsys):
    from services import thread_turns
    from services.mind_service import thread_lock

    thread = _Thread()
    _install(monkeypatch, thread)
    monkeypatch.setattr(thread_turns, "ACQUIRE_BACKSTOP_SECONDS", 0.2)
    stranded = thread_lock(CID)
    await stranded.acquire()  # a slot nothing will ever release in time
    try:
        assert await asyncio.wait_for(_chat("still there?"), timeout=2) == "re: still there?"
        assert "running without the thread's lock" in capsys.readouterr().out
    finally:
        stranded.release()
    assert not stranded.locked()
    assert await asyncio.wait_for(_chat("and after"), timeout=2) == "re: and after"
