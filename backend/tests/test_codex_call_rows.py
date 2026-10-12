"""Every Codex HTTP request is one ``codex_calls`` row, and a response's
``usage_metadata`` is LangChain-standard (muse-and-beyond plan Phase 3 and
contract 1 of the agent brief, 2026-09-24).

``ChatCodexResponses`` writes the row for success and failure alike —
``ok``, ``error`` (with the stream's error code), ``rate_limited`` (429 or
the usage cap), ``timeout`` (the total deadline) — with tokens (cached and
reasoning apart), latency, time to the first answer delta, the model and
effort, the rate-gate priority and purpose, and the turn that caused it
(``turn_ledger.current_turn``). The table itself, the meter samples and
``codex exec`` rows are in ``tests/test_codex_call_ledger.py``.
"""

from __future__ import annotations

import json
from typing import List

import pytest
from langchain_core.messages import HumanMessage

_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN = f"hdr.{_JWT_PAYLOAD}.sig"


@pytest.fixture
def captured(monkeypatch):
    """Capture ledger rows ChatCodexResponses schedules (no DB)."""
    from services import codex_call_ledger

    rows = []
    monkeypatch.setattr(codex_call_ledger, "schedule_call", lambda **fields: rows.append(fields))
    return rows


USAGE = {
    "input_tokens": 5000,
    "input_tokens_details": {"cached_tokens": 4096},
    "output_tokens": 300,
    "output_tokens_details": {"reasoning_tokens": 200},
    "total_tokens": 5300,
}


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


def _ok_stream(usage=USAGE) -> List[str]:
    return [
        _sse({"type": "response.reasoning_summary_text.delta", "delta": "hmm"}),
        _sse({"type": "response.output_text.delta", "delta": "Hello"}),
        _sse({"type": "response.completed", "response": {"model": "gpt-6-astra", "status": "completed",
                                                         "output": [], "usage": usage}}),
    ]


class _FakeStreamResponse:
    def __init__(self, status_code: int = 200, sse_lines: List = (), error_body: str = "", headers=None):
        self.status_code = status_code
        self._sse_lines = list(sse_lines)
        self._error_body = error_body
        self.headers = headers or {}

    async def aiter_bytes(self):
        if self._error_body:
            yield self._error_body.encode("utf-8")

    async def aiter_text(self):
        for line in self._sse_lines:
            yield line


class _FakeStreamCM:
    def __init__(self, response):
        self._response = response

    async def __aenter__(self):
        return self._response

    async def __aexit__(self, *exc):
        return None


class _FakeAsyncClient:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls = []

    def __call__(self, *args, **kwargs):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    def stream(self, method, url, *, json, headers):
        self.calls.append({"json": json, "headers": headers})
        return _FakeStreamCM(self._responses.pop(0))


@pytest.fixture
def ccm(monkeypatch):
    from services import codex_chat_model

    monkeypatch.setattr(codex_chat_model, "RETRY_BACKOFF_MIN_S", 0.0)
    monkeypatch.setattr(codex_chat_model, "RETRY_BACKOFF_MAX_S", 0.0)
    return codex_chat_model


def _client(ccm, monkeypatch, responses):
    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(ccm.httpx, "AsyncClient", client)
    return client


def test_usage_metadata_from_codex_usage_is_langchain_standard():
    from services.codex_chat_model import usage_metadata_from

    assert usage_metadata_from(USAGE) == {
        "input_tokens": 5000,
        "output_tokens": 300,
        "total_tokens": 5300,
        "input_token_details": {"cache_read": 4096},
        "output_token_details": {"reasoning": 200},
    }
    assert usage_metadata_from({}) is None
    assert usage_metadata_from(None) is None
    assert usage_metadata_from({"input_tokens": 3, "output_tokens": 1})["total_tokens"] == 4


@pytest.mark.asyncio
async def test_agenerate_and_astream_populate_usage_metadata(ccm, monkeypatch, captured):
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=_ok_stream()), _FakeStreamResponse(sse_lines=_ok_stream())])
    llm = ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)

    msg = (await llm._agenerate([HumanMessage(content="hi")])).generations[0].message
    assert msg.usage_metadata["input_token_details"] == {"cache_read": 4096}
    assert msg.usage_metadata["output_token_details"] == {"reasoning": 200}
    assert msg.response_metadata["usage"] == USAGE, "the raw usage stays for existing readers"

    chunks = [c async for c in llm._astream([HumanMessage(content="hi")])]
    with_usage = [c.message.usage_metadata for c in chunks if c.message.usage_metadata]
    assert len(with_usage) == 1 and with_usage[0]["input_tokens"] == 5000


@pytest.mark.asyncio
async def test_a_successful_call_is_one_row_with_tokens_timing_and_the_turn(ccm, monkeypatch, captured):
    from services import turn_ledger

    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=_ok_stream())])
    rec = turn_ledger.TurnRecord.start(conversation_id="edward-mind", source="mind", model="gpt-6-astra", provider="openai")
    token = turn_ledger._current.set(rec)
    try:
        llm = ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN, reasoning_effort="high",
                                     priority="interactive")
        await llm._agenerate([HumanMessage(content="hi")])
    finally:
        turn_ledger._current.reset(token)

    assert len(captured) == 1
    row = captured[0]
    assert row["status"] == "ok" and row["http_status"] == 200
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"], row["reasoning_tokens"]) == (5000, 4096, 300, 200)
    assert row["model"] == "gpt-6-astra"
    assert row["priority"] == "interactive"
    assert row["turn_source"] == "mind" and row["turn_id"] == rec.id and row["conversation_id"] == "edward-mind"
    assert isinstance(row["latency_ms"], int) and row["latency_ms"] >= 0
    assert isinstance(row["ttft_ms"], int) and row["ttft_ms"] <= row["latency_ms"]
    assert row["purpose"], "an interactive call is labelled by its caller"
    assert row["error_class"] is None


@pytest.mark.asyncio
async def test_a_background_call_carries_its_purpose(ccm, monkeypatch, captured):
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=_ok_stream())])
    llm = ccm.ChatCodexResponses(model="gpt-5.6-terra", api_key=FAKE_TOKEN, priority="triage",
                                 purpose="search_tag_service.generate_tags")
    await llm._agenerate([HumanMessage(content="hi")])
    assert captured[0]["purpose"] == "search_tag_service.generate_tags"
    assert captured[0]["priority"] == "triage"
    assert captured[0]["turn_source"] is None


@pytest.mark.asyncio
async def test_a_429_is_a_rate_limited_row(ccm, monkeypatch, captured):
    from services import llm_rate_gate

    monkeypatch.setattr(llm_rate_gate, "_gate", llm_rate_gate.LLMRateGate())
    monkeypatch.setattr(ccm, "_report_limit", lambda *a, **k: None)
    _client(ccm, monkeypatch, [_FakeStreamResponse(429, error_body=json.dumps(
        {"error": {"type": "usage_limit_reached", "resets_in_seconds": 30}}))])
    with pytest.raises(ValueError):
        await ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)._agenerate([HumanMessage(content="hi")])
    assert [r["status"] for r in captured] == ["rate_limited"]
    assert captured[0]["http_status"] == 429


@pytest.mark.asyncio
async def test_a_401_then_success_is_two_rows(ccm, monkeypatch, captured):
    from unittest.mock import AsyncMock, MagicMock

    mgr = MagicMock()
    mgr.force_refresh = AsyncMock(return_value=True)
    mgr.get_api_key = MagicMock(return_value=FAKE_TOKEN + "-fresh")
    monkeypatch.setattr(ccm, "get_codex_auth_manager", lambda: mgr)
    _client(ccm, monkeypatch, [
        _FakeStreamResponse(401, error_body='{"error": {"code": "token_expired"}}'),
        _FakeStreamResponse(sse_lines=_ok_stream()),
    ])
    await ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)._agenerate([HumanMessage(content="hi")])
    assert [(r["status"], r["http_status"]) for r in captured] == [("error", 401), ("ok", 200)]
    assert captured[0]["error_class"] == "auth_expired"


@pytest.mark.asyncio
async def test_a_failed_stream_is_an_error_row_per_attempt(ccm, monkeypatch, captured):
    failed = _sse({"type": "response.failed", "response": {"error": {"code": "server_error", "message": "x"}}})
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=[failed]), _FakeStreamResponse(sse_lines=[failed])])
    with pytest.raises(ccm.CodexStreamError):
        await ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)._agenerate([HumanMessage(content="hi")])
    assert [(r["status"], r["error_class"]) for r in captured] == [("error", "server_error"), ("error", "server_error")]


@pytest.mark.asyncio
async def test_a_deadline_is_a_timeout_row(ccm, monkeypatch, captured):
    import asyncio

    monkeypatch.setenv("CODEX_REQUEST_TIMEOUT_SECONDS", "0.05")

    class _Slow(_FakeStreamResponse):
        async def aiter_text(self):
            await asyncio.sleep(1)
            yield ""

    _client(ccm, monkeypatch, [_Slow()])
    with pytest.raises(ccm.CodexStreamError):
        await ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)._agenerate([HumanMessage(content="hi")])
    assert captured[0]["status"] == "timeout"


@pytest.mark.integration
async def test_scheduled_rows_land_when_writes_are_enabled(clean_db, ccm, monkeypatch):
    from sqlalchemy import select
    from services import codex_call_ledger
    from services.database import async_session, CodexCallModel

    monkeypatch.setattr(codex_call_ledger, "WRITES_ENABLED", True)
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=_ok_stream())])
    await ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN)._agenerate([HumanMessage(content="hi")])
    await codex_call_ledger.drain()
    async with async_session() as s:
        rows = (await s.execute(select(CodexCallModel))).scalars().all()
    assert [(r.status, r.input_tokens, r.reasoning_tokens) for r in rows] == [("ok", 5000, 200)]


@pytest.mark.asyncio
async def test_a_wake_and_the_pulse_label_their_codex_calls(ccm, monkeypatch, captured):
    """Purpose labels (2026-09-25): mind and pulse calls were ``unknown`` at
    interactive priority in ``codex_calls``, so the spend report could not
    tell Edward's own turns from Ellis's chat. A wake's calls are
    ``mind:<reason>``, the pulse's ``pulse``, and a wake inside an outer
    scope keeps that scope's label."""
    import services.mind_service as ms
    from services.call_purpose import purpose_scope
    from tests.test_mind_service import _stub

    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=_ok_stream()) for _ in range(4)])

    async def chat(**kw):
        llm = ccm.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN, priority="interactive")
        await llm._agenerate([HumanMessage(content="hi")])
        return "Quiet."

    _stub(monkeypatch, ms, chat)
    try:
        await ms.wake("self")
        await ms.wake("mention", required_tools=["send_imessage"])
        await ms.wake("surfaces", message="pulse", conversation_id="edward-surfaces", tools=("update_widget",),
                      system_prompt="S", turn_source="pulse", skip_extraction=True, record=False)
        with purpose_scope("mind:notice"):
            await ms.wake("speak", message="[SPEAK] x", tools=ms.legacy_speak_tools("note"))
    finally:
        ms.reset_state()
    assert [r["purpose"] for r in captured] == ["mind:self", "mind:mention", "pulse", "mind:notice"]


# ------------------------------------------------ turn labels (L5) -------
#
# Spend labels (followups L5, 2026-09-27): a scheduled event's and a
# heartbeat turn's calls reached codex_calls as ``unknown`` (4 calls, 263K
# tokens since 9/26) — no job scope around them, and the stack walk names
# only LLM plumbing. A turn with no scope of its own labels its own chat
# model ``turn:<turn_source>`` (``streaming._build_llm``); an outer job
# scope (``mind:*``, ``surplus:*``, ``pulse``) always wins.
#
# The label is the turn's model's, never a scope around the turn: the
# first version opened ``purpose_scope("turn:<source>")``, and since a
# scope beats a model's own label (``_call_context``: ``scoped or
# self.purpose``) every enrichment call inside the turn — extraction,
# entity extraction, query generation, search tags, each stamped with its
# caller by ``build_background_llm`` — read ``turn:heartbeat`` too, and the
# spend report could no longer tell extraction from the turn (review of
# batch B, 2026-09-27).


def _turn_fakes(ccm, monkeypatch, n):
    import asyncio

    from services import llm_utils, turn_ledger
    from services.graph import streaming

    monkeypatch.setattr(turn_ledger, "_schedule", lambda coro: coro.close())
    _client(ccm, monkeypatch, [_FakeStreamResponse(sse_lines=_ok_stream()) for _ in range(n)])
    # Both builders, real, over a Codex model with a fake token.
    monkeypatch.setattr(llm_utils, "_build_gpt_llm", lambda model, temperature, max_tokens, priority="interactive":
                        ccm.ChatCodexResponses(model=model, api_key=FAKE_TOKEN, priority=priority))
    spawned = []

    async def turn_call(turn_source):
        # The turn's own model, built the way both tool loops build it.
        llm = streaming._build_llm("gpt-6-astra", 0.4, turn_source=turn_source)
        await llm._agenerate([HumanMessage(content="hi")])

    async def extract_and_store_memories():
        # Enrichment: ``build_background_llm`` stamps its caller.
        llm = llm_utils.build_background_llm(priority="triage")
        await llm._agenerate([HumanMessage(content="hi")])

    async def chat_impl(*, turn_source, **kw):
        await turn_call(turn_source)
        # The non-streaming loop awaits extraction inside the turn.
        await extract_and_store_memories()
        return "done"

    async def stream_impl(*, turn_source, **kw):
        yield {"type": "progress", "step": "memory_search", "status": "started"}
        await turn_call(turn_source)
        # A worker the turn starts is a turn of its own.
        await _run_chat(streaming, "worker")
        # Post-turn work is spawned inside the turn.
        spawned.append(asyncio.get_running_loop().create_task(extract_and_store_memories()))
        yield {"type": "content", "content": "ok"}
        yield {"type": "done"}

    monkeypatch.setattr(streaming, "_chat_with_memory_impl", chat_impl)
    monkeypatch.setattr(streaming, "_stream_with_memory_events_impl", stream_impl)
    return streaming, spawned


async def _run_chat(streaming, source, conversation_id=None):
    return await streaming.chat_with_memory(
        message="x", conversation_id=conversation_id or f"c-{source}", system_prompt="p", model="gpt-6-astra",
        temperature=0.4, graph=object(), turn_source=source,
    )


def test_turn_purpose():
    from services.graph.streaming import turn_purpose

    assert turn_purpose("scheduled_event", None) == "turn:scheduled_event"
    assert turn_purpose("heartbeat", None) == "turn:heartbeat"
    assert turn_purpose(None, None) == "turn:background"
    assert turn_purpose("scheduled_event", "surplus:free_time") is None, "an outer scope wins"
    assert turn_purpose("mind", "mind:evening") is None


@pytest.mark.asyncio
async def test_a_turn_with_no_job_scope_labels_its_calls_by_source(ccm, monkeypatch, captured):
    import asyncio

    from services.call_purpose import current_purpose

    streaming, spawned = _turn_fakes(ccm, monkeypatch, 8)
    await _run_chat(streaming, "scheduled_event")
    await _run_chat(streaming, "heartbeat")
    purposes_between_events = []
    async for _ in streaming.stream_with_memory_events("hi", "c-chat", "p", "gpt-6-astra", 0.4, object()):
        purposes_between_events.append(current_purpose())
    await asyncio.gather(*spawned)

    enrichment = "tests.test_codex_call_rows.extract_and_store_memories"
    assert [r["purpose"] for r in captured] == [
        "turn:scheduled_event", enrichment,
        "turn:heartbeat", enrichment,
        "turn:chat", "turn:worker", enrichment, enrichment,
    ], "the turn's own calls carry its source; enrichment inside it keeps its caller's label"
    assert purposes_between_events and set(purposes_between_events) == {None}, \
        "the label is the turn's, never its consumer's"
    assert current_purpose() is None


@pytest.mark.asyncio
async def test_an_outer_job_scope_wins_over_the_turn_label(ccm, monkeypatch, captured):
    from services.call_purpose import purpose_scope

    streaming, _ = _turn_fakes(ccm, monkeypatch, 6)
    with purpose_scope("surplus:free_time"):
        await _run_chat(streaming, "scheduled_event")
    with purpose_scope("mind:evening"):
        await _run_chat(streaming, "mind", conversation_id="edward-mind")
    with purpose_scope("pulse"):
        await _run_chat(streaming, "pulse", conversation_id="edward-surfaces")
    # A job's scope names every call it causes, extraction included: the
    # surplus queue's accounting counts what its job spent.
    assert [r["purpose"] for r in captured] == ["surplus:free_time"] * 2 + ["mind:evening"] * 2 + ["pulse"] * 2
