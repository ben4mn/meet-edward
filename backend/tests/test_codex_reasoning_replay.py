"""D3 (muse-and-beyond plan, 2026-09-24): reasoning is carried between
tool iterations, not thrown away.

The request asks Codex for ``include: ["reasoning.encrypted_content"]``
with ``store: false``. Until now the SSE parser kept only ``function_call``
output items, so the ``reasoning`` items (and their encrypted content) were
dropped, and ``_messages_to_input`` never sent them back. Each tool
iteration knew *what* it had called but not *why* — more latency per step
and more re-verifying calls, worst on Astra at high effort. It also dropped
the assistant text that came with a tool call ("Let me look.").

Pinned here, with SSE in the shape Codex actually sends:

* call N's reasoning items are captured on the AIMessage
  (``additional_kwargs["reasoning_items"]``) and appear, in order, before
  that message's ``function_call`` items in call N+1's ``input``;
* nothing is replayed from an older turn (before the last HumanMessage);
* a reasoning item is never sent without its ``encrypted_content``;
* preamble text is kept as an assistant message before the calls;
* ``strip_reasoning_items`` removes them from older turns (for checkpoints).
"""

from __future__ import annotations

import json
from typing import List

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

pytestmark = pytest.mark.unit

_JWT_PAYLOAD = (
    "eyJodHRwczovL2FwaS5vcGVuYWkuY29tL2F1dGgiOnsiY2hhdGdwdF9hY2NvdW50X2lkIjoiYWNjdF90ZXN0In19"
)
FAKE_TOKEN = f"hdr.{_JWT_PAYLOAD}.sig"


def _sse(event: dict) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event)}\n\n"


REASONING_ITEM = {
    "id": "rs_68d1a",
    "type": "reasoning",
    "encrypted_content": "gAAAAB-call-N-reasoning",
    "summary": [{"type": "summary_text", "text": "Ellis asked about today; check the calendar first."}],
}
FUNCTION_CALL_ITEM = {
    "id": "fc_68d1b",
    "type": "function_call",
    "status": "completed",
    "call_id": "call_Qx1",
    "name": "calendar",
    "arguments": "{\"op\":\"list\"}",
}
USAGE_N = {
    "input_tokens": 1200,
    "input_tokens_details": {"cached_tokens": 1024},
    "output_tokens": 80,
    "output_tokens_details": {"reasoning_tokens": 50},
    "total_tokens": 1280,
}


def call_n_stream() -> List[str]:
    """Reasoning → preamble text → a function call, as Codex streams it."""
    message_item = {
        "id": "msg_68d1c", "type": "message", "status": "completed", "role": "assistant",
        "content": [{"type": "output_text", "text": "Let me look.", "annotations": []}],
    }
    return [
        _sse({"type": "response.created", "sequence_number": 0,
              "response": {"id": "resp_1", "status": "in_progress", "output": []}}),
        _sse({"type": "response.output_item.added", "output_index": 0,
              "item": {"id": "rs_68d1a", "type": "reasoning", "summary": []}}),
        _sse({"type": "response.reasoning_summary_text.delta", "item_id": "rs_68d1a", "output_index": 0,
              "summary_index": 0, "delta": "Ellis asked about today; check the calendar first."}),
        _sse({"type": "response.output_item.done", "output_index": 0, "item": REASONING_ITEM}),
        _sse({"type": "response.output_item.added", "output_index": 1,
              "item": {"id": "msg_68d1c", "type": "message", "status": "in_progress", "role": "assistant", "content": []}}),
        _sse({"type": "response.output_text.delta", "item_id": "msg_68d1c", "output_index": 1,
              "content_index": 0, "delta": "Let me look."}),
        _sse({"type": "response.output_item.done", "output_index": 1, "item": message_item}),
        _sse({"type": "response.output_item.added", "output_index": 2,
              "item": {**FUNCTION_CALL_ITEM, "status": "in_progress", "arguments": ""}}),
        _sse({"type": "response.function_call_arguments.delta", "item_id": "fc_68d1b", "output_index": 2,
              "delta": "{\"op\":\"list\"}"}),
        _sse({"type": "response.output_item.done", "output_index": 2, "item": FUNCTION_CALL_ITEM}),
        _sse({"type": "response.completed", "response": {
            "id": "resp_1", "status": "completed", "model": "gpt-6-astra",
            "output": [REASONING_ITEM, message_item, FUNCTION_CALL_ITEM], "usage": USAGE_N,
        }}),
    ]


def text_stream(text: str = "You have three things today.") -> List[str]:
    return [
        _sse({"type": "response.output_text.delta", "item_id": "msg_2", "output_index": 0,
              "content_index": 0, "delta": text}),
        _sse({"type": "response.completed", "response": {
            "id": "resp_2", "status": "completed", "model": "gpt-6-astra", "output": [],
            "usage": {"input_tokens": 1400, "output_tokens": 12, "total_tokens": 1412},
        }}),
    ]


class _FakeStreamResponse:
    def __init__(self, status_code: int, sse_lines: List[str], error_body: str = ""):
        self.status_code = status_code
        self._sse_lines = sse_lines
        self._error_body = error_body
        self.headers = {}

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


CALENDAR_TOOL = {
    "type": "function",
    "name": "calendar",
    "description": "Read Ellis's calendar",
    "parameters": {"type": "object", "properties": {"op": {"type": "string"}}, "required": ["op"]},
}


def _llm(monkeypatch, responses):
    from services import codex_chat_model

    client = _FakeAsyncClient(responses)
    monkeypatch.setattr(codex_chat_model.httpx, "AsyncClient", client)
    llm = codex_chat_model.ChatCodexResponses(model="gpt-6-astra", api_key=FAKE_TOKEN).bind_tools([CALENDAR_TOOL])
    return llm, client


def _types(items):
    return [i.get("type") or f"role:{i.get('role')}" for i in items]


# ------------------------------------------------------------ capture --

@pytest.mark.asyncio
async def test_agenerate_captures_reasoning_items_in_output_order(monkeypatch):
    llm, _ = _llm(monkeypatch, [_FakeStreamResponse(200, call_n_stream())])
    result = await llm._agenerate([HumanMessage(content="what's on today?")])
    msg = result.generations[0].message

    items = msg.additional_kwargs.get("reasoning_items")
    assert items == [{
        "type": "reasoning",
        "id": "rs_68d1a",
        "encrypted_content": "gAAAAB-call-N-reasoning",
        "summary": [{"type": "summary_text", "text": "Ellis asked about today; check the calendar first."}],
    }]
    assert msg.content == "Let me look."
    assert [tc["name"] for tc in msg.tool_calls] == ["calendar"]


@pytest.mark.asyncio
async def test_astream_final_chunk_carries_the_reasoning_items(monkeypatch):
    llm, _ = _llm(monkeypatch, [_FakeStreamResponse(200, call_n_stream())])
    chunks = [c async for c in llm._astream([HumanMessage(content="what's on today?")])]
    carried = [c.message.additional_kwargs["reasoning_items"] for c in chunks
               if c.message.additional_kwargs.get("reasoning_items")]
    assert len(carried) == 1, "exactly one chunk carries the turn's reasoning items"
    assert carried[0][0]["encrypted_content"] == "gAAAAB-call-N-reasoning"


@pytest.mark.asyncio
async def test_reasoning_items_fall_back_to_response_completed_output(monkeypatch):
    """A stream that never sent output_item.done for the reasoning item
    still yields it from response.completed's output array."""
    stream = [line for line in call_n_stream()
              if not ('"response.output_item.done"' in line and '"reasoning"' in line)]
    llm, _ = _llm(monkeypatch, [_FakeStreamResponse(200, stream)])
    result = await llm._agenerate([HumanMessage(content="what's on today?")])
    items = result.generations[0].message.additional_kwargs.get("reasoning_items") or []
    assert [i["id"] for i in items] == ["rs_68d1a"]


# ------------------------------------------------------------- replay --

@pytest.mark.asyncio
async def test_call_n_reasoning_is_replayed_before_its_function_call_in_call_n_plus_1(monkeypatch):
    llm, client = _llm(monkeypatch, [
        _FakeStreamResponse(200, call_n_stream()),
        _FakeStreamResponse(200, text_stream()),
    ])
    older_ai = AIMessage(
        content="Morning!",
        additional_kwargs={"reasoning_items": [{
            "type": "reasoning", "id": "rs_old", "encrypted_content": "gAAAAB-OLDER-TURN", "summary": [],
        }]},
    )
    history = [
        SystemMessage(content="You are Edward."),
        HumanMessage(content="hi"),
        older_ai,
        HumanMessage(content="what's on today?"),
    ]
    first = (await llm._agenerate(history)).generations[0].message
    call_id = first.tool_calls[0]["id"]
    second_history = history + [first, ToolMessage(content="3 events", tool_call_id=call_id)]
    await llm._agenerate(second_history)

    sent = client.calls[1]["json"]["input"]
    assert _types(sent) == [
        "role:user", "role:assistant",          # the older turn: text only
        "role:user",                            # this turn's task
        "reasoning", "role:assistant",          # call N: its reasoning, then its preamble
        "function_call", "function_call_output",
    ], sent
    reasoning = sent[3]
    assert reasoning["encrypted_content"] == "gAAAAB-call-N-reasoning"
    assert reasoning["summary"] == REASONING_ITEM["summary"]
    assert sent[4]["content"] == "Let me look.", "preamble text that came with the call is kept"
    assert "gAAAAB-OLDER-TURN" not in json.dumps(sent), "nothing is replayed from an older turn"


def test_a_reasoning_item_is_never_sent_without_its_encrypted_content():
    from services.codex_chat_model import _messages_to_input

    ai = AIMessage(
        content="",
        tool_calls=[{"name": "calendar", "args": {"op": "list"}, "id": "call_1", "type": "tool_call"}],
        additional_kwargs={"reasoning_items": [
            {"type": "reasoning", "id": "rs_no_blob", "encrypted_content": None, "summary": []},
            {"type": "reasoning", "id": "rs_empty", "encrypted_content": "", "summary": []},
            {"type": "reasoning", "id": "rs_ok", "encrypted_content": "gAAAAB-ok", "summary": []},
        ]},
    )
    _, items = _messages_to_input([HumanMessage(content="go"), ai])
    reasoning = [i for i in items if i.get("type") == "reasoning"]
    assert [r["encrypted_content"] for r in reasoning] == ["gAAAAB-ok"]
    # store=false: an id alone references nothing server-side, so the id is
    # not sent at all (Codex CLI does the same); the blob carries the item.
    assert all("id" not in r for r in reasoning)
    assert "rs_no_blob" not in json.dumps(items) and "rs_empty" not in json.dumps(items)


def test_preamble_text_is_kept_before_the_function_calls():
    from services.codex_chat_model import _messages_to_input

    ai = AIMessage(
        content="Checking your calendar now.",
        tool_calls=[{"name": "calendar", "args": {"op": "list"}, "id": "call_1", "type": "tool_call"}],
    )
    _, items = _messages_to_input([HumanMessage(content="go"), ai, ToolMessage(content="ok", tool_call_id="call_1")])
    assert _types(items) == ["role:user", "role:assistant", "function_call", "function_call_output"]
    assert items[1]["content"] == "Checking your calendar now."


def test_tool_calls_without_text_add_no_empty_assistant_message():
    from services.codex_chat_model import _messages_to_input

    ai = AIMessage(content="", tool_calls=[{"name": "calendar", "args": {}, "id": "call_1", "type": "tool_call"}])
    _, items = _messages_to_input([HumanMessage(content="go"), ai])
    assert _types(items) == ["role:user", "function_call"]


# -------------------------------------------------------------- strip --

def test_strip_reasoning_items_keeps_only_the_current_turn():
    from services.codex_chat_model import strip_reasoning_items

    def ai(tag):
        return AIMessage(content=tag, additional_kwargs={
            "reasoning_items": [{"type": "reasoning", "id": tag, "encrypted_content": f"blob-{tag}", "summary": []}],
            "other": "kept",
        })

    old, current = ai("old"), ai("current")
    messages = [HumanMessage(content="a"), old, HumanMessage(content="b"), current]
    out = strip_reasoning_items(messages)

    assert out is not messages
    assert "reasoning_items" not in out[1].additional_kwargs
    assert out[1].additional_kwargs["other"] == "kept"
    assert out[3].additional_kwargs["reasoning_items"][0]["id"] == "current"
    # copy, don't mutate
    assert old.additional_kwargs["reasoning_items"][0]["id"] == "old"
    assert messages[1] is old


def test_strip_reasoning_items_without_any_human_message_keeps_everything():
    from services.codex_chat_model import strip_reasoning_items

    ai = AIMessage(content="x", additional_kwargs={"reasoning_items": [{"type": "reasoning", "encrypted_content": "b"}]})
    out = strip_reasoning_items([SystemMessage(content="s"), ai])
    assert out[1].additional_kwargs["reasoning_items"]
