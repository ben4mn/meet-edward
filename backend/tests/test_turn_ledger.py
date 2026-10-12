"""The turn ledger: one row per LLM turn, from every entry point.

A health check needs to see how Edward *thinks* — which tools fail, how
often the loop runs out of iterations, how slow voice is, whether
retrieval feeds anything — and none of that survived a restart before
(only the SSE stream and a log file that resets). ``TurnRecord`` is the
pure accumulator; ``observe_events`` feeds it from the streaming event
stream; the DB layer is thin and retention-pruned.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from services import turn_ledger as tl


# ---------------------------------------------------------------- pure --

def test_classify_tool_result_ok_and_failures():
    assert tl.classify_tool_result("Sent to +1555…") is None
    assert tl.classify_tool_result("") is None
    assert tl.classify_tool_result(None) is None
    assert tl.classify_tool_result("BLOCKED: send_message already failed") == "blocked"
    assert tl.classify_tool_result("Unknown tool: frobnicate") == "unknown_tool"
    cls = tl.classify_tool_result("Tool error: Missing required parameters: to, message. You must provide these parameters.")
    assert cls.startswith("missing_required_parameters")


def test_classify_tool_result_strips_volatile_bits():
    a = tl.classify_tool_result("Tool error: HTTP 500 for https://x.test/a/12345 (request 9f8e7d6c-1234-4abc-9def-000000000001)")
    b = tl.classify_tool_result("Tool error: HTTP 500 for https://x.test/b/99999 (request 00000000-0000-4000-8000-000000000002)")
    assert a == b
    assert len(a) <= 80


def test_is_repeat_normalizes_case_space_and_punctuation():
    assert tl.is_repeat("What's on my calendar today?", "whats on my calendar today")
    assert not tl.is_repeat("What's on my calendar today?", "What's on my calendar tomorrow?")
    assert not tl.is_repeat("ok", "ok")  # too short to mean anything
    assert not tl.is_repeat(None, "hello there friend")


def test_turn_record_accumulates_tools_and_outcome():
    rec = tl.TurnRecord.start(conversation_id="c1", source="chat", model="gpt-5.6-astra", provider="openai", message="hello there world")
    assert rec.outcome == "running"
    assert rec.message_chars == len("hello there world")
    rec.note_iteration()
    rec.note_tool("web_search", "results…", ms=120)
    rec.note_tool("send_message", "Tool error: Missing required parameters: to", ms=5)
    rec.note_iteration()
    rec.note_first_token()
    rec.note_content("Here you go.")
    rec.note_memories(retrieved=7, used=3)
    rec.finish("completed")

    assert rec.iterations == 2
    assert rec.tool_calls == 2
    assert rec.tool_failures == 1
    assert rec.tools[1]["err"].startswith("missing_required_parameters")
    assert rec.ttft_ms is not None and rec.ttft_ms >= 0
    assert rec.response_chars == len("Here you go.")
    assert rec.memories_retrieved == 7 and rec.memories_used == 3
    assert rec.outcome == "completed" and rec.total_ms is not None

    row = rec.to_row()
    assert row["conversation_id"] == "c1"
    assert row["tool_failures"] == 1
    assert '"web_search"' in row["tools_json"]


def test_turn_record_marks_repeat_of_previous():
    rec = tl.TurnRecord.start(conversation_id="c1", source="chat", model="m", provider="openai", message="What's the plan for dinner?")
    rec.note_previous_user_text("whats the plan for dinner")
    assert rec.repeat_of_previous is True


def test_turn_record_empty_and_error_outcomes():
    rec = tl.TurnRecord.start(conversation_id="c", source="chat", model="m", provider="openai", message="x")
    rec.finish("completed")
    assert rec.outcome == "empty"  # finished with no content at all

    rec2 = tl.TurnRecord.start(conversation_id="c", source="chat", model="m", provider="openai", message="x")
    rec2.finish("error", error=RuntimeError("boom 12345"))
    assert rec2.outcome == "error"
    assert rec2.error_class.startswith("RuntimeError")
    assert "12345" not in rec2.error_class


def test_tools_json_is_bounded():
    rec = tl.TurnRecord.start(conversation_id="c", source="chat", model="m", provider="openai", message="x")
    for i in range(500):
        rec.note_tool("execute_code", "Tool error: " + ("x" * 5000), ms=1)
    row = rec.to_row()
    assert len(row["tools_json"]) < 20_000
    assert row["tool_calls"] == 500


# ------------------------------------------------------- event wrapper --

async def _events(seq):
    for e in seq:
        if isinstance(e, BaseException):
            raise e
        yield e


def _ev(type_, **kw):
    return {"type": type_, "conversation_id": "c1", **kw}


@pytest.mark.asyncio
async def test_observe_events_fills_record_from_stream(monkeypatch):
    emitted = []
    monkeypatch.setattr(tl, "_emit_tool_failure", lambda *a, **k: emitted.append(a))
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append((rec, outcome, error)) or rec.finish(outcome, error))

    rec = tl.TurnRecord.start(conversation_id="c1", source="chat", model="m", provider="openai", message="hi there edward")
    stream = _events([
        _ev("progress", step="memory_search", status="completed", message="Found 4 memories", count=4),
        _ev("tool_start", tool_name="web_search"),
        _ev("tool_end", tool_name="web_search", result="ok"),
        _ev("thinking", content="Thinking..."),
        _ev("tool_start", tool_name="send_message"),
        _ev("tool_end", tool_name="send_message", result="Tool error: Missing required parameters: to"),
        _ev("content", content="Hel"),
        _ev("content", content="lo"),
        _ev("progress", step="generating", status="completed", message="Response complete"),
        _ev("done"),
    ])
    out = [e async for e in tl.observe_events(rec, stream)]
    assert [e["type"] for e in out][-1] == "done"
    assert len(out) == 10  # every event passed through untouched
    assert rec.memories_retrieved == 4
    assert rec.tool_calls == 2 and rec.tool_failures == 1
    assert rec.iterations == 2  # first pass + one "thinking" round
    assert rec.ttft_ms is not None
    assert rec.response_chars == 5
    assert ended and ended[0][1] == "completed"
    assert emitted and emitted[0][0] == "send_message"


@pytest.mark.asyncio
async def test_observe_events_exhausted_when_loop_ran_out(monkeypatch):
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append(outcome))
    rec = tl.TurnRecord.start(conversation_id="c1", source="chat", model="m", provider="openai", message="hi there")
    stream = _events([
        _ev("progress", step="tool_loop", status="exhausted", message="Ran out of tool iterations"),
        _ev("content", content="Summary."),
        _ev("done"),
    ])
    [e async for e in tl.observe_events(rec, stream)]
    assert ended == ["exhausted"]


@pytest.mark.asyncio
async def test_observe_events_error_is_recorded_and_reraised(monkeypatch):
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append((outcome, error)))
    rec = tl.TurnRecord.start(conversation_id="c1", source="chat", model="m", provider="openai", message="hi there")
    stream = _events([_ev("content", content="a"), RuntimeError("kaboom")])
    with pytest.raises(RuntimeError):
        [e async for e in tl.observe_events(rec, stream)]
    assert ended[0][0] == "error" and isinstance(ended[0][1], RuntimeError)


@pytest.mark.asyncio
async def test_observe_events_stopped_when_consumer_goes_away(monkeypatch):
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append(outcome))
    rec = tl.TurnRecord.start(conversation_id="c1", source="chat", model="m", provider="openai", message="hi there")
    stream = _events([_ev("content", content="a"), _ev("content", content="b"), _ev("done")])
    gen = tl.observe_events(rec, stream)
    await gen.__anext__()
    await gen.aclose()  # the phone hung up / stop pressed
    assert ended == ["stopped"]


@pytest.mark.asyncio
async def test_observe_events_break_right_after_done_is_completed(monkeypatch):
    # The voice socket breaks out of its loop on "done" (routers/voice_ws.py),
    # which closes this generator with GeneratorExit at the yield of "done".
    # Every voice turn in turn_log read "stopped" (8 of 8, 9/16–9/21).
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append(outcome))
    rec = tl.TurnRecord.start(conversation_id="c1", source="voice", model="m", provider="openai", message="hi there")
    stream = _events([_ev("content", content="Sure."), _ev("done")])
    gen = tl.observe_events(rec, stream)
    async for event in gen:
        if event["type"] == "done":
            break
    await gen.aclose()  # what the loop's async-generator finalizer does after the break
    assert ended == ["completed"]


@pytest.mark.asyncio
async def test_observe_events_break_after_done_keeps_exhausted(monkeypatch):
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append(outcome))
    rec = tl.TurnRecord.start(conversation_id="c1", source="voice", model="m", provider="openai", message="hi there")
    stream = _events([
        _ev("progress", step="tool_loop", status="exhausted", message="Ran out of tool iterations"),
        _ev("content", content="Summary."),
        _ev("done"),
    ])
    gen = tl.observe_events(rec, stream)
    async for event in gen:
        if event["type"] == "done":
            break
    await gen.aclose()
    assert ended == ["exhausted"]


@pytest.mark.asyncio
async def test_observe_events_break_before_done_is_still_stopped(monkeypatch):
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append(outcome))
    rec = tl.TurnRecord.start(conversation_id="c1", source="voice", model="m", provider="openai", message="hi there")
    stream = _events([_ev("content", content="a"), _ev("content", content="b"), _ev("done")])
    gen = tl.observe_events(rec, stream)
    async for event in gen:
        if event["type"] == "content":
            break  # barge-in: Ellis talked over him
    await gen.aclose()
    assert ended == ["stopped"]


@pytest.mark.asyncio
async def test_observe_events_cancelled_counts_as_stopped(monkeypatch):
    ended = []
    monkeypatch.setattr(tl, "end_turn", lambda rec, outcome, error=None: ended.append(outcome))
    rec = tl.TurnRecord.start(conversation_id="c1", source="chat", model="m", provider="openai", message="hi there")
    stream = _events([_ev("content", content="a"), asyncio.CancelledError()])
    with pytest.raises(asyncio.CancelledError):
        [e async for e in tl.observe_events(rec, stream)]
    assert ended == ["stopped"]


# ------------------------------------------------------ context variable --

@pytest.mark.asyncio
async def test_current_turn_is_task_local(monkeypatch):
    monkeypatch.setattr(tl, "_schedule", lambda coro: coro.close())
    rec = tl.begin_turn(conversation_id="c1", source="chat", model="m", provider="openai", message="hello world")
    assert tl.current_turn() is rec

    async def other():
        return tl.current_turn()

    # A fresh task copies the context, so it sees the same turn …
    assert await asyncio.create_task(other()) is rec
    tl.note_tool_result("web_search", "ok", ms=3)
    assert rec.tool_calls == 1
    tl.end_turn(rec, "completed")
    assert tl.current_turn() is None


# ---------------------------------------------------------- integration --

@pytest.mark.integration
async def test_turn_persists_and_updates(clean_db):
    from sqlalchemy import select
    from services.database import async_session, TurnLogModel

    rec = tl.begin_turn(conversation_id="conv-1", source="voice", model="gpt-5.6-luna", provider="openai", message="hello there", fast_context=True)
    await rec.wait_persisted()
    rec.note_first_token()
    rec.note_content("hi")
    rec.note_tool("web_search", "ok", ms=10)
    tl.end_turn(rec, "completed")
    await rec.wait_persisted()

    async with async_session() as s:
        row = (await s.execute(select(TurnLogModel).where(TurnLogModel.id == rec.id))).scalar_one()
    assert row.source == "voice" and row.fast_context is True
    assert row.outcome == "completed" and row.tool_calls == 1 and row.ttft_ms is not None

    await tl.note_extraction(rec.id, 3)
    async with async_session() as s:
        row = (await s.execute(select(TurnLogModel).where(TurnLogModel.id == rec.id))).scalar_one()
    assert row.memories_extracted == 3


async def _extracted(turn_id):
    from sqlalchemy import select
    from services.database import async_session, TurnLogModel

    async with async_session() as s:
        return (await s.execute(
            select(TurnLogModel.memories_extracted).where(TurnLogModel.id == turn_id))).scalar_one()


@pytest.mark.integration
@pytest.mark.parametrize("where", ["inside the turn", "outside its context"])
async def test_the_end_of_turn_update_keeps_the_extraction_count(clean_db, where):
    """Extraction reported before the turn ends must survive ``end_turn``'s
    write. The non-streaming loop extracts inside the turn, so its update
    wrote ``memories_extracted=None`` over every count: 0 of 1,573 pulse,
    mind, scheduled-event and heartbeat rows had one (7 days to 2026-09-27).
    A post-turn task that no longer sees the turn as its own (the streaming
    path's race) must not lose its count either."""
    import contextvars

    rec = tl.begin_turn(conversation_id="conv-x", source="mind", model="m", provider="openai", message="[WAKE: morning]")
    await rec.wait_persisted()
    rec.note_content("done")
    if where == "inside the turn":
        await tl.note_extraction(rec.id, 4)
    else:
        await asyncio.create_task(tl.note_extraction(rec.id, 4), context=contextvars.Context())
    tl.end_turn(rec, "completed")
    await rec.wait_persisted()

    assert await _extracted(rec.id) == 4


@pytest.mark.integration
async def test_a_turn_whose_insert_never_landed_keeps_its_count(clean_db, monkeypatch):
    """The start-of-turn insert failed, so the count had no row to go to;
    the end-of-turn update writes the row and must carry the count."""
    async def _lost(rec):
        return None

    monkeypatch.setattr(tl, "_insert", _lost)
    rec = tl.begin_turn(conversation_id="conv-x", source="heartbeat", model="m", provider="openai", message="new text")
    await rec.wait_persisted()
    await tl.note_extraction(rec.id, 2)
    rec.note_content("replied")
    tl.end_turn(rec, "completed")
    await rec.wait_persisted()

    assert await _extracted(rec.id) == 2


@pytest.mark.integration
async def test_a_turn_that_extracts_nothing_records_zero_not_null(clean_db):
    rec = tl.begin_turn(conversation_id="conv-x", source="pulse", model="m", provider="openai", message="pack")
    await rec.wait_persisted()
    await tl.note_extraction(rec.id, 0)
    rec.note_content("left alone")
    tl.end_turn(rec, "completed")
    await rec.wait_persisted()

    assert await _extracted(rec.id) == 0


@pytest.mark.integration
async def test_a_non_streaming_turn_ends_with_its_extraction_count(clean_db, monkeypatch):
    """End to end through ``chat_with_memory`` with the ledger's writes on."""
    from types import SimpleNamespace

    import services.memory_service as ms
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeGraph, ai, base_kwargs, patch_loop_io

    real_schedule = tl._schedule
    patch_loop_io(monkeypatch, tools=[], script=[ai("Reminder sent.")])
    monkeypatch.setattr(tl, "_schedule", real_schedule)

    async def _two(**kw):
        return [SimpleNamespace(id="m1"), SimpleNamespace(id="m2")]

    monkeypatch.setattr(ms, "extract_and_store_memories", _two)

    await streaming.chat_with_memory(message="[SCHEDULED EVENT] remind Ellis about the vet", turn_source="scheduled_event",
                                     extraction_profile="scheduled_event", **base_kwargs(FakeGraph(), conversation_id="t-1"))
    rec = tl.last_finished_turn()
    assert rec is not None and rec.source == "scheduled_event"
    await rec.wait_persisted()

    assert await _extracted(rec.id) == 2


@pytest.mark.integration
async def test_first_chat_turn_right_after_an_answer_marks_abandonment(clean_db):
    from sqlalchemy import select
    from services.database import async_session, TurnLogModel

    a = tl.begin_turn(conversation_id="conv-a", source="chat", model="m", provider="openai", message="first question")
    a.note_content("answer")
    tl.end_turn(a, "completed")
    await a.wait_persisted()

    b = tl.begin_turn(conversation_id="conv-b", source="chat", model="m", provider="openai", message="second question", first_turn=True)
    b.note_content("answer")
    tl.end_turn(b, "completed")
    await b.wait_persisted()

    async with async_session() as s:
        row = (await s.execute(select(TurnLogModel).where(TurnLogModel.id == b.id))).scalar_one()
    assert row.abandoned_previous is True


@pytest.mark.integration
async def test_prune_keeps_only_retention_window(clean_db):
    from sqlalchemy import select, func
    from services.database import async_session, TurnLogModel, SelfObservationHitModel, UserSignalModel

    old = datetime.utcnow() - timedelta(days=tl.RETENTION_DAYS + 1)
    async with async_session() as s:
        s.add(TurnLogModel(id="old", conversation_id="c", source="chat", model="m", provider="openai", started_at=old, outcome="completed"))
        s.add(TurnLogModel(id="new", conversation_id="c", source="chat", model="m", provider="openai", started_at=datetime.utcnow(), outcome="completed"))
        s.add(SelfObservationHitModel(observation_id="o", seen_at=old))
        s.add(UserSignalModel(kind="today_action", created_at=old))
        await s.commit()

    deleted = await tl.prune()
    assert deleted["turn_log"] == 1 and deleted["self_observation_hits"] == 1 and deleted["user_signals"] == 1
    async with async_session() as s:
        n = (await s.execute(select(func.count()).select_from(TurnLogModel))).scalar_one()
    assert n == 1


# ------------------------------------------------------------- wiring --

def test_both_llm_loops_take_a_turn_source():
    """Every entry point labels its bucket; the wrappers exist."""
    import inspect
    from services.graph import streaming

    assert "turn_source" in inspect.signature(streaming.stream_with_memory_events).parameters
    assert "turn_source" in inspect.signature(streaming.chat_with_memory).parameters
    assert inspect.isasyncgenfunction(streaming._stream_with_memory_events_impl)
    assert inspect.iscoroutinefunction(streaming._chat_with_memory_impl)


def test_callers_label_their_source():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent
    expected = {
        "services/scheduler_service.py": "scheduled_event",
        "services/expression_service.py": "pulse",
        "services/orchestrator_service.py": "worker",
    }
    for rel, source in expected.items():
        text = (root / rel).read_text()
        assert re.search(rf'turn_source="{source}"', text), f"{rel} should label its turns {source!r}"
    # The heartbeat runs no turns of its own any more (its legacy triage
    # layers are gone): a batch is handed to the mind as a wake request,
    # and the wake's turn is labelled by the mind.
    heartbeat = root / "services" / "heartbeat"
    for path in heartbeat.glob("*.py"):
        assert "chat_with_memory" not in path.read_text(), f"{path.name} should not run a turn of its own"
    assert "mind_service.request_wake(" in (heartbeat / "mind_path.py").read_text()
    # Live voice labels each session's turns by its surface (2026-10-11):
    # the app's are Ellis's ``voice``, the household hub's ``hub_voice``.
    voice = (root / "routers/voice_ws.py").read_text()
    assert re.search(r'"app":\s*"voice"', voice) and re.search(r'"hub":\s*"hub_voice"', voice)
    assert "turn_source=self.turn_source" in voice


@pytest.mark.asyncio
async def test_last_finished_turn_is_available_to_the_caller(monkeypatch):
    """A caller (the pulse, the scheduler) that awaited a turn can read
    what the turn did — its tools — after the wrapper returns."""
    monkeypatch.setattr(tl, "_schedule", lambda coro: coro.close())
    rec = tl.begin_turn(conversation_id="c1", source="pulse", model="m", provider="openai", message="signal pack here")
    tl.note_tool_result("update_widget", "ok", ms=3)
    tl.end_turn(rec, "completed")
    assert tl.current_turn() is None
    assert tl.last_finished_turn() is rec
    assert tl.last_finished_turn().tools[0]["n"] == "update_widget"


def test_non_streaming_turn_has_no_first_token_time():
    """chat_with_memory sees the whole answer at once; a ttft equal to the
    total would only skew the p50s. Only streaming turns carry ttft."""
    rec = tl.TurnRecord.start(conversation_id="c", source="pulse", model="m", provider="openai", message="signal pack")
    rec.note_content("the answer", streaming=False)
    rec.finish("completed")
    assert rec.ttft_ms is None and rec.response_chars == len("the answer")
