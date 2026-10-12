"""A fake ``anthropic.AsyncAnthropic`` for ChatClaude tests.

ChatClaude streams every request through
``client.messages.with_streaming_response.create(stream=True, **kwargs)``
and parses the raw SSE stream itself, so the fake only has to record the
kwargs it was called with and hand back a scripted response. The response
is a real ``httpx.Response`` over the stream's UTF-8 bytes (JSON with raw
non-ASCII, as the API sends it), cut into small chunks that split lines
and multi-byte characters, and its ``iter_bytes`` / ``iter_lines`` delegate
to httpx exactly as the SDK's ``AsyncAPIResponse`` does. Nothing here opens
a socket.

``message(...)`` builds the event list of one response from high-level
blocks, the way the Messages API streams them::

    message([thinking("Planning.", "sig-1"), tool_use("toolu_1", "web_search", {"q": "x"})],
            stop_reason="tool_use", usage={"input_tokens": 10, "output_tokens": 5})
"""

from __future__ import annotations

import copy
import json
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------- blocks --

def text(value: str, *, chunks: int = 2) -> Dict[str, Any]:
    return {"kind": "text", "text": value, "chunks": chunks}


def thinking(summary: str, signature: str = "sig-abc") -> Dict[str, Any]:
    return {"kind": "thinking", "thinking": summary, "signature": signature}


def redacted(data: str = "opaque") -> Dict[str, Any]:
    return {"kind": "raw", "block": {"type": "redacted_thinking", "data": data}}


def tool_use(tool_id: str, name: str, args: Any, *, raw_json: Optional[str] = None,
             server: bool = False) -> Dict[str, Any]:
    return {"kind": "tool_use", "id": tool_id, "name": name, "args": args, "raw_json": raw_json,
            "server": server}


def raw_block(block: Dict[str, Any]) -> Dict[str, Any]:
    """A block that arrives whole in ``content_block_start`` (server tool
    results, fallback markers, anything new)."""
    return {"kind": "raw", "block": block}


def _split(s: str, n: int) -> List[str]:
    if n <= 1 or len(s) < n:
        return [s]
    step = max(1, len(s) // n)
    parts = [s[i:i + step] for i in range(0, len(s), step)]
    return parts


def message(blocks: List[Dict[str, Any]], *, stop_reason: str = "end_turn", model: str = "claude-opus-5-5",
            usage: Optional[Dict[str, Any]] = None, msg_id: str = "msg_1", stop_details: Any = None,
            end: bool = True, error: Optional[Dict[str, Any]] = None,
            truncate_after_block: Optional[int] = None) -> List[Dict[str, Any]]:
    """The SSE events of one streamed response."""
    usage = dict(usage or {"input_tokens": 100, "output_tokens": 20,
                           "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0})
    # ``iterations`` (one entry per attempt: a server-side fallback, a
    # compaction) arrives with the final usage on message_delta.
    start_usage = {k: v for k, v in usage.items() if k not in ("output_tokens", "iterations")}
    start_usage["output_tokens"] = 1
    events: List[Dict[str, Any]] = [{
        "type": "message_start",
        "message": {"id": msg_id, "type": "message", "role": "assistant", "model": model, "content": [],
                    "stop_reason": None, "stop_sequence": None, "usage": start_usage},
    }]
    for index, b in enumerate(blocks):
        kind = b["kind"]
        if kind == "text":
            events.append({"type": "content_block_start", "index": index,
                           "content_block": {"type": "text", "text": ""}})
            for part in _split(b["text"], b.get("chunks", 2)):
                events.append({"type": "content_block_delta", "index": index,
                               "delta": {"type": "text_delta", "text": part}})
        elif kind == "thinking":
            events.append({"type": "content_block_start", "index": index,
                           "content_block": {"type": "thinking", "thinking": "", "signature": ""}})
            for part in _split(b["thinking"], 2):
                events.append({"type": "content_block_delta", "index": index,
                               "delta": {"type": "thinking_delta", "thinking": part}})
            events.append({"type": "content_block_delta", "index": index,
                           "delta": {"type": "signature_delta", "signature": b["signature"]}})
        elif kind == "tool_use":
            btype = "server_tool_use" if b.get("server") else "tool_use"
            events.append({"type": "content_block_start", "index": index,
                           "content_block": {"type": btype, "id": b["id"], "name": b["name"], "input": {}}})
            raw = b["raw_json"] if b.get("raw_json") is not None else json.dumps(b["args"], ensure_ascii=False)
            for part in _split(raw, 3):
                events.append({"type": "content_block_delta", "index": index,
                               "delta": {"type": "input_json_delta", "partial_json": part}})
        elif kind == "raw":
            events.append({"type": "content_block_start", "index": index, "content_block": b["block"]})
        events.append({"type": "content_block_stop", "index": index})
        if truncate_after_block is not None and index >= truncate_after_block:
            return events
    if error is not None:
        events.append({"type": "error", "error": error})
        return events
    delta: Dict[str, Any] = {"stop_reason": stop_reason, "stop_sequence": None}
    if stop_details is not None:
        delta["stop_details"] = stop_details
    delta_usage: Dict[str, Any] = {"output_tokens": usage.get("output_tokens", 0)}
    if "iterations" in usage:
        delta_usage["iterations"] = usage["iterations"]
    events.append({"type": "message_delta", "delta": delta, "usage": delta_usage})
    if end:
        events.append({"type": "message_stop"})
    return events


# ---------------------------------------------------------------- client --

class FakeStreamResponse:
    """One streamed response. ``chunk_size`` is how the bytes arrive (small
    by default, so lines and UTF-8 sequences are cut); ``newline`` is the
    line ending the stream uses (SSE allows \\n, \\r\\n and \\r)."""

    status_code = 200

    def __init__(self, events: List[Dict[str, Any]], request_id: str = "req_test", *,
                 chunk_size: int = 7, newline: str = "\n"):
        self._events = events
        self._chunk_size = max(1, int(chunk_size))
        self._newline = newline
        self.headers = {"request-id": request_id, "content-type": "text/event-stream"}

    def payload(self) -> bytes:
        nl = self._newline
        parts = []
        for ev in self._events:
            parts.append(f"event: {ev.get('type')}{nl}")
            parts.append("data: " + json.dumps(ev, ensure_ascii=False) + nl + nl)
        return "".join(parts).encode("utf-8")

    def _http_response(self):
        import httpx

        data = self.payload()
        size = self._chunk_size

        async def chunks():
            for i in range(0, len(data), size):
                yield data[i:i + size]

        return httpx.Response(200, content=chunks(), headers=self.headers,
                              request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))

    async def iter_bytes(self, chunk_size: Optional[int] = None):
        async for chunk in self._http_response().aiter_bytes(chunk_size):
            yield chunk

    async def iter_lines(self):
        async for line in self._http_response().aiter_lines():
            yield line


class _StreamContext:
    def __init__(self, item: Any):
        self._item = item

    async def __aenter__(self):
        if isinstance(self._item, BaseException):
            raise self._item
        if isinstance(self._item, FakeStreamResponse):
            return self._item
        return FakeStreamResponse(self._item)

    async def __aexit__(self, *exc):
        return False


class FakeMessages:
    def __init__(self, scripts: List[Any]):
        self.scripts = scripts
        self.calls: List[Dict[str, Any]] = []

    @property
    def with_streaming_response(self) -> "FakeMessages":
        return self

    def create(self, **kwargs):
        self.calls.append(copy.deepcopy(kwargs))
        item = self.scripts.pop(0) if self.scripts else message([text("(script exhausted)")])
        return _StreamContext(item)


class FakeAnthropic:
    """``FakeAnthropic([message(...), message(...), SomeError(...)])``."""

    def __init__(self, scripts: List[Any]):
        self.messages = FakeMessages(list(scripts))

    @property
    def calls(self) -> List[Dict[str, Any]]:
        return self.messages.calls


def status_error(status: int, err_type: str, message_text: str = "boom"):
    """A real ``anthropic.APIStatusError`` subclass for ``status``."""
    import anthropic
    import httpx

    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    body = {"type": "error", "error": {"type": err_type, "message": message_text}}
    response = httpx.Response(status, request=request, json=body, headers={"request-id": "req_err"})
    classes = {400: anthropic.BadRequestError, 401: anthropic.AuthenticationError,
               403: anthropic.PermissionDeniedError, 404: anthropic.NotFoundError,
               429: anthropic.RateLimitError, 500: anthropic.InternalServerError}
    cls = classes.get(status, anthropic.APIStatusError)
    return cls(f"Error code: {status} - {body}", response=response, body=body)


def connection_error():
    import anthropic
    import httpx

    return anthropic.APIConnectionError(request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"))
