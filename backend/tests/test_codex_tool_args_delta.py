"""
Stream Codex's response.function_call_arguments.delta events out as
tool_args_delta events so the chat UI can show "calling web_search
with query: 'lat...'" while the model is still typing the JSON,
instead of going silent until output_item.done arrives.

Today the parser at codex_chat_model.py:122-142 buffers per-item args
text into ``fc_args[item_id]`` and only yields a single ``tool_call``
event once the call is fully formed. That makes for a long silent
gap between when the model decides to call a tool and when the UI
shows anything about it.

This test pins the new contract:

  {"type": "tool_args_delta", "id": <call_id>, "name": <fn_name>, "delta": "<partial json>"}

is yielded for every ``response.function_call_arguments.delta`` Codex
emits. The final ``tool_call`` event is preserved exactly as today.
"""

from __future__ import annotations

import pytest


async def _async_iter(items):
    for x in items:
        yield x


@pytest.mark.asyncio
async def test_parser_yields_tool_args_delta_per_codex_delta() -> None:
    from services.codex_chat_model import _iter_codex_sse_events

    chunks = [
        # Add the function_call item with name + ids
        'event: response.output_item.added\n'
        'data: {"type":"response.output_item.added","item":{"id":"fc_1","type":"function_call","name":"web_search","call_id":"call_abc"}}\n\n',
        # Three streamed argument fragments
        'event: response.function_call_arguments.delta\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"{\\"que"}\n\n',
        'event: response.function_call_arguments.delta\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"ry\\":\\"lat"}\n\n',
        'event: response.function_call_arguments.delta\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_1","delta":"est ai news\\"}"}\n\n',
        # Done with the call — final tool_call should still be yielded.
        'event: response.output_item.done\n'
        'data: {"type":"response.output_item.done","item":{"id":"fc_1","type":"function_call","name":"web_search","call_id":"call_abc","arguments":"{\\"query\\":\\"latest ai news\\"}"}}\n\n',
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{}}\n'
        'data: [DONE]\n\n',
    ]

    events = [e async for e in _iter_codex_sse_events(_async_iter(chunks))]

    # The three delta events must be present in order, with the call's
    # id and function name carried so the UI can render them.
    deltas = [e for e in events if e["type"] == "tool_args_delta"]
    assert len(deltas) == 3, (
        f"expected 3 tool_args_delta events (one per codex delta), got "
        f"{len(deltas)}: {events}"
    )
    assert [d["delta"] for d in deltas] == [
        '{"que', 'ry":"lat', 'est ai news"}',
    ], f"deltas should pass through verbatim, got: {deltas}"

    for d in deltas:
        assert d["name"] == "web_search", f"each delta must carry the function name, got: {d}"
        assert d["id"] == "call_abc", (
            f"each delta must carry the call_id (not the internal item_id), got: {d}"
        )

    # Final tool_call event preserved exactly as today.
    tool_calls = [e for e in events if e["type"] == "tool_call"]
    assert len(tool_calls) == 1, f"expected exactly one tool_call, got: {events}"
    tc = tool_calls[0]
    assert tc["name"] == "web_search"
    assert tc["args"] == {"query": "latest ai news"}
    assert tc["id"] == "call_abc"

    # Ordering: all deltas must come before the final tool_call.
    delta_idx = [i for i, e in enumerate(events) if e["type"] == "tool_args_delta"]
    tc_idx = next(i for i, e in enumerate(events) if e["type"] == "tool_call")
    assert max(delta_idx) < tc_idx, (
        "tool_args_delta events must precede the final tool_call event"
    )


@pytest.mark.asyncio
async def test_tool_args_delta_works_when_metadata_arrives_late() -> None:
    """Defensive: if Codex emits arguments deltas before output_item.added
    (unusual but spec-allowed), the parser should still yield them with
    whatever metadata it has — never crash, never drop deltas."""
    from services.codex_chat_model import _iter_codex_sse_events

    chunks = [
        'event: response.function_call_arguments.delta\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_2","delta":"{\\"a\\":1"}\n\n',
        'event: response.output_item.added\n'
        'data: {"type":"response.output_item.added","item":{"id":"fc_2","type":"function_call","name":"calc","call_id":"call_xyz"}}\n\n',
        'event: response.function_call_arguments.delta\n'
        'data: {"type":"response.function_call_arguments.delta","item_id":"fc_2","delta":"}"}\n\n',
        'event: response.output_item.done\n'
        'data: {"type":"response.output_item.done","item":{"id":"fc_2","type":"function_call","name":"calc","call_id":"call_xyz","arguments":"{\\"a\\":1}"}}\n\n',
        'event: response.completed\n'
        'data: {"type":"response.completed","response":{}}\n'
        'data: [DONE]\n\n',
    ]

    events = [e async for e in _iter_codex_sse_events(_async_iter(chunks))]
    deltas = [e for e in events if e["type"] == "tool_args_delta"]
    assert len(deltas) == 2
    assert [d["delta"] for d in deltas] == ['{"a":1', "}"]
    # The first delta arrived before metadata — name/id may be empty,
    # but the delta payload is preserved.
    assert deltas[0]["delta"] == '{"a":1'
    assert deltas[1]["name"] == "calc"
    assert deltas[1]["id"] == "call_xyz"
