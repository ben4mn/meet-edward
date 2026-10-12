# ADAPT: the Codex fallback reaches ChatGPT's private backend, not a public API.
#   How it works here: chatgpt.com/backend-api/codex with the Codex CLI's OAuth
#   login (~/.codex/auth.json) and its client id — unofficial, may break at any
#   time, and may conflict with OpenAI's terms. Used only when
#   EDWARD_LLM_PROVIDER=codex; the default provider is the Claude API.
#   To adapt: leave EDWARD_LLM_PROVIDER unset (or anthropic) and set
#   ANTHROPIC_API_KEY; or set OPENAI_API_KEY to use the public OpenAI API instead.
"""LangChain-compatible chat model that uses the Codex Responses API.

This wraps the ChatGPT subscription's Codex endpoint at
chatgpt.com/backend-api/codex/responses, which requires:
- OAuth bearer token from ~/.codex/auth.json
- ChatGPT-Account-Id header (from JWT claims)
- stream: true, store: false
- Responses API format (instructions + input messages)

2026-09-24 (reasoning replay, typed stream errors, tool schemas and the spend ledger):
- Reasoning items (with their ``encrypted_content``) are captured on the
  AIMessage and replayed before that message's function calls within the
  current turn, so each tool iteration knows *why* it called what it did.
- ``response.failed`` / ``response.incomplete`` / ``error`` and a stream
  that closes early are typed errors (``CodexStreamError``), retried once
  when transient — never an empty answer. Bad argument JSON is marked, not
  emptied. A total per-request deadline sits on top of httpx's per-read one.
- Tool schemas keep nested enums, defaults (in the description) and unions;
  ``parallel_tool_calls`` and ``prompt_cache_key`` are sent.
- ``usage_metadata`` is LangChain-standard (cached and reasoning tokens) and
  every HTTP request writes a ``codex_calls`` row (services/codex_call_ledger).
"""

import asyncio
import base64
import contextlib
import hashlib
import json
import logging
import os
import random
import re
import sys
import time
from typing import Any, Dict, List, Optional, Iterator, AsyncIterator, Union

import httpx
from langchain_core.callbacks import CallbackManagerForLLMRun, AsyncCallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatResult, ChatGeneration, ChatGenerationChunk
from langchain_core.messages import AIMessageChunk

from services import codex_call_ledger
from services.codex_auth_service import get_codex_auth_manager

logger = logging.getLogger(__name__)

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
# A 429 whose reset is further out than this is the account's weekly cap,
# not a rolling-window cooldown (the weekly one answered with ~5 days on
# 2026-09-09; the longest rolling window on any plan is 5h).
WEEKLY_CAP_THRESHOLD_SECONDS = 6 * 3600

# httpx's timeout is per read: a stream that trickles a byte a minute never
# trips it. The total deadline (CODEX_REQUEST_TIMEOUT_SECONDS) bounds the
# whole request, the one retry included (2026-09-24).
PER_READ_TIMEOUT_SECONDS = 120
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0
# One retry of a transient stream failure, after a short jittered pause.
RETRY_BACKOFF_MIN_S = 1.0
RETRY_BACKOFF_MAX_S = 3.0
# Contract 2 of the 2026-09-24 agent brief: unparseable function-call
# arguments reach the tool loop as {"__invalid_json__": raw[:2000]}.
INVALID_JSON_KEY = "__invalid_json__"
INVALID_JSON_MAX_CHARS = 2000
PROMPT_CACHE_KEY_MAX_CHARS = 64


def request_timeout_seconds() -> float:
    """The total deadline for one Codex request. Read per call (no restart)."""
    raw = os.getenv("CODEX_REQUEST_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw else DEFAULT_REQUEST_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_REQUEST_TIMEOUT_SECONDS


class CodexStreamError(ValueError):
    """A Codex response that failed after the HTTP 200: ``response.failed``,
    an ``error`` event, a non-token ``response.incomplete``, a stream that
    closed before ``response.completed``, a connection reset mid-stream, or
    the total deadline.

    Before 2026-09-24 the parser ignored all of these, so a server error
    looked like an empty completion and the tool loop ended on its canned
    "I completed the requested actions…". A ``ValueError`` like every other
    Codex API error, so existing handlers still see it.
    """

    def __init__(self, code: str, message: str = "", retryable: Optional[bool] = None,
                 http_status: Optional[int] = None):
        self.code = str(code or "unknown")[:80]
        self.message = str(message or "")
        self.retryable = is_retryable_stream_code(self.code) if retryable is None else bool(retryable)
        self.http_status = http_status
        super().__init__(f"Codex stream failed ({self.code}): {self.message[:300]}")


_RETRYABLE_CODES = {
    "server_error", "internal_error", "internal_server_error", "overloaded", "server_overloaded",
    "server_is_overloaded", "service_unavailable", "bad_gateway", "gateway_timeout",
    "stream_truncated", "connection_error",
}
# HTTP statuses that mean "the service, not the request": raised as a
# retryable ``CodexStreamError(f"http_{status}")``.
RETRYABLE_HTTP_STATUSES = frozenset({500, 502, 503, 504})
# Never retried here: the rate gate owns rate limits and quota, and the
# rest fail the same way twice.
_NEVER_RETRY_MARKERS = (
    "rate_limit", "quota", "usage_limit", "usage_not_included", "context_length", "context_window",
    "invalid", "incomplete", "request_timeout", "auth", "content_filter", "not_supported",
)


def is_retryable_stream_code(code: Any) -> bool:
    """Transient server-side failures: ``server_error``, overloaded and
    5xx-like codes, a truncated stream, a connection reset. Pure."""
    c = str(code or "").strip().lower()
    if not c or any(marker in c for marker in _NEVER_RETRY_MARKERS):
        return False
    if c in _RETRYABLE_CODES or "overload" in c or "unavailable" in c or "server_error" in c:
        return True
    return re.fullmatch(r"5\d\d", c) is not None


def _stream_error_from_event(event: Dict[str, Any]) -> CodexStreamError:
    """``response.failed`` carries ``response.error``; an ``error`` event
    carries ``code`` / ``message`` at the top or under ``error``."""
    if event.get("type") == "response.failed":
        err = (event.get("response") or {}).get("error") or {}
        default_code = "server_error"  # a failed response with no code is server-side
    else:
        err = event.get("error") if isinstance(event.get("error"), dict) else event
        default_code = "stream_error"
    if not isinstance(err, dict):
        err = {"message": str(err)}
    code = err.get("code") or err.get("type") or default_code
    return CodexStreamError(str(code), str(err.get("message") or ""))


def _parse_arguments(raw: Any) -> Dict[str, Any]:
    """Function-call arguments → a dict. Empty means no arguments; anything
    that is not a JSON object is marked (contract 2), never silently ``{}``
    — a tool must not run with arguments the model did not give it."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    text = str(raw)
    if not text.strip():
        return {}
    try:
        parsed = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return {INVALID_JSON_KEY: text[:INVALID_JSON_MAX_CHARS]}
    if not isinstance(parsed, dict):
        return {INVALID_JSON_KEY: text[:INVALID_JSON_MAX_CHARS]}
    return parsed


def _reasoning_item(item: Dict[str, Any]) -> Dict[str, Any]:
    """The stored shape (contract 3): what replay needs, nothing else."""
    return {
        "type": "reasoning",
        "id": item.get("id"),
        "encrypted_content": item.get("encrypted_content"),
        "summary": list(item.get("summary") or []),
    }


def usage_metadata_from(usage: Any) -> Optional[Dict[str, Any]]:
    """Codex ``usage`` → LangChain ``usage_metadata`` (contract 1): input,
    output and total tokens, cached input as ``cache_read`` and reasoning
    as ``reasoning``. ``None`` when the response reported no token counts."""
    if not isinstance(usage, dict) or not ("input_tokens" in usage or "output_tokens" in usage):
        return None

    def _n(value: Any) -> int:
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    inp = _n(usage.get("input_tokens"))
    out = _n(usage.get("output_tokens"))
    return {
        "input_tokens": inp,
        "output_tokens": out,
        "total_tokens": _n(usage.get("total_tokens")) or inp + out,
        "input_token_details": {"cache_read": _n((usage.get("input_tokens_details") or {}).get("cached_tokens"))},
        "output_token_details": {"reasoning": _n((usage.get("output_tokens_details") or {}).get("reasoning_tokens"))},
    }


def _fmt_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds >= 86400:
        return f"{seconds // 86400}d {(seconds % 86400) // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {(seconds % 3600) // 60}m"
    return f"{max(1, seconds // 60)}m"


def _report_limit(kind: str, model: str, cooldown_seconds):
    """Fire-and-forget: log a CODEX_LIMIT warning and (throttled) push the owner.
    Never raises and never blocks the failing request path."""
    try:
        import asyncio
        from services.llm_limit_alert import handle_limit_event
        return asyncio.get_running_loop().create_task(
            handle_limit_event(kind=kind, model=model, cooldown_seconds=cooldown_seconds)
        )
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("CODEX_LIMIT alert could not be scheduled: %s", e)
        return None


class _CodexAuthExpired(Exception):
    """Raised internally when Codex returns ANY HTTP 401.

    Historically only matched ``code: token_expired``, but production
    also sees ``code: token_invalidated`` (server-side OAuth revocation)
    and bare 401s with no recognizable code. Server error-code shape is
    not a stable contract — treat any 401 as "auth needs a refresh."
    """

    def __init__(self, body: str):
        super().__init__(f"Codex auth rejected (401): {body[:200]}")
        self.body = body


# The body Codex sent for perfectly good tokens during the 2026-09-25
# outage: a service-side auth fault, not an expired sign-in.
_SERVICE_SIDE_AUTH_MARKERS = ("incorrect api key provided",)


def is_service_side_auth_fault(body: str) -> bool:
    """A 401 body that blames the key when the key is fine. Pure."""
    b = (body or "").lower()
    return any(marker in b for marker in _SERVICE_SIDE_AUTH_MARKERS)


class CodexAuthError(ValueError):
    """A 401 that survived ``_attempt_with_auth``'s one refresh and retry.

    ``refresh_attempted`` tells the tool loop not to refresh again (it
    used to, stacking up to four requests and three refreshes per call).
    ``transient`` marks the service-side "Incorrect API key provided"
    shape, whose message must not send the owner to ``codex login``.
    """

    refresh_attempted = True

    def __init__(self, message: str, *, transient: bool = False, body: str = ""):
        super().__init__(message)
        self.transient = bool(transient)
        self.body = body or ""


def _auth_error_after_refresh(body: str) -> CodexAuthError:
    """The error for a token rejected after the refresh. Pure."""
    if is_service_side_auth_fault(body):
        return CodexAuthError(
            "Codex API error (HTTP 401): the service rejected a freshly refreshed sign-in "
            "(\"Incorrect API key provided\"). This is a service-side auth fault on OpenAI's end, "
            "usually brief — the saved credentials are fine; try again in a few minutes.",
            transient=True, body=body,
        )
    return CodexAuthError(
        f"Codex API error (HTTP 401): refreshed token also rejected. "
        f"Run `codex login` to mint a new refresh token. Body: {body[:300]}",
        body=body,
    )


async def _iter_codex_sse_events(text_chunks):
    """Parse Codex Responses SSE chunks into typed events.

    Takes an async iterable of arbitrarily-sized text chunks (as
    yielded by ``httpx.Response.aiter_text``), yields dicts:

        {"type": "text_delta", "text": "..."}
        {"type": "reasoning_delta", "text": "..."}
        {"type": "tool_args_delta", "id": "...", "name": "...", "delta": "..."}
        {"type": "tool_call", "name": "...", "args": {...}, "id": "..."}
        {"type": "reasoning_item", "item": {type, id, encrypted_content, summary}}
        {"type": "response_completed", "response": {...}}
        {"type": "response_incomplete", "response": {...}, "reason": "..."}

    Tool calls are reassembled from streaming arguments — Codex emits
    them as ``output_item.added`` (metadata) → multiple
    ``function_call_arguments.delta`` (json fragments) →
    ``output_item.done`` (final). We yield ONE ``tool_call`` event per
    completed function call, deduplicated by item id. Arguments that are
    not a JSON object arrive as ``{"__invalid_json__": raw[:2000]}``.

    Reasoning output items (2026-09-24) are yielded once each, in
    output order, from ``output_item.done`` or, failing that, from the
    final response's ``output``.

    Raises ``CodexStreamError`` on ``response.failed``, on an ``error``
    event, and when the stream ends with no terminal event
    (``response.completed`` / ``response.incomplete`` / ``[DONE]``) —
    before 2026-09-24 each of those looked like an empty answer.

    Robust to:
      - chunk boundaries inside a data: line
      - malformed json data lines (skipped)
      - missing or duplicate output_item.done events (fallback to
        accumulated arguments if response.completed never resolves it)
    """
    fc_args: Dict[str, str] = {}
    fc_meta: Dict[str, Dict[str, str]] = {}
    seen_ids: set = set()
    seen_reasoning: set = set()
    buffer = ""
    done = False
    terminal = False

    def _function_call_event(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        item_id = item.get("id", "")
        if item_id in seen_ids:
            return None
        seen_ids.add(item_id)
        return {
            "type": "tool_call",
            "name": item.get("name", ""),
            "args": _parse_arguments(item.get("arguments", "")),
            "id": item.get("call_id", item_id),
        }

    def _reasoning_event(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        key = item.get("id") or item.get("encrypted_content") or json.dumps(item, sort_keys=True)
        if key in seen_reasoning:
            return None
        seen_reasoning.add(key)
        return {"type": "reasoning_item", "item": _reasoning_item(item)}

    def _unseen_output(resp_data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Items Codex put in the final ``output`` but never surfaced via
        ``output_item.done``."""
        events = []
        for item in resp_data.get("output") or []:
            if not isinstance(item, dict):
                continue
            kind = item.get("type")
            ev = None
            if kind == "function_call":
                ev = _function_call_event(item)
            elif kind == "reasoning":
                ev = _reasoning_event(item)
            if ev is not None:
                events.append(ev)
        return events

    async for chunk in text_chunks:
        buffer += chunk
        while "\n" in buffer:
            line, buffer = buffer.split("\n", 1)
            line = line.rstrip("\r")

            if not line:
                continue
            if line.startswith("event:"):
                continue
            if not line.startswith("data:"):
                continue

            data_str = line[5:].strip()
            if data_str == "[DONE]":
                done = True
                terminal = True
                break

            try:
                event = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            if not isinstance(event, dict):
                continue

            etype = event.get("type", "")

            if etype == "response.output_text.delta":
                yield {"type": "text_delta", "text": event.get("delta", "")}

            elif etype in (
                "response.reasoning_summary_text.delta",
                "response.reasoning_summary.delta",
            ):
                # N4: Codex emits reasoning deltas when reasoning.summary='auto'
                # is set on the request. Surface to consumers so the UI can
                # show "Edward is reasoning..." with content instead of a
                # blank wait. Tolerate both event names — the OpenAI spec
                # has used both shapes.
                delta = event.get("delta", "") or event.get("text", "")
                if delta:
                    yield {"type": "reasoning_delta", "text": delta}

            elif etype == "response.output_item.added":
                item = event.get("item", {})
                if item.get("type") == "function_call":
                    item_id = item.get("id", "")
                    fc_meta[item_id] = {
                        "name": item.get("name", ""),
                        "call_id": item.get("call_id", item_id),
                    }

            elif etype == "response.function_call_arguments.delta":
                item_id = event.get("item_id", "")
                delta = event.get("delta", "")
                fc_args[item_id] = fc_args.get(item_id, "") + delta
                # Surface the partial JSON so the UI can render
                # "calling <fn> with args: <partial>" while the model
                # is still typing — without this the user stares at a
                # silent screen until output_item.done arrives.
                meta = fc_meta.get(item_id) or {}
                yield {
                    "type": "tool_args_delta",
                    "id": meta.get("call_id", ""),
                    "name": meta.get("name", ""),
                    "delta": delta,
                }

            elif etype == "response.output_item.done":
                item = event.get("item", {})
                kind = item.get("type")
                ev = None
                if kind == "function_call":
                    ev = _function_call_event(item)
                elif kind == "reasoning":
                    ev = _reasoning_event(item)
                if ev is not None:
                    yield ev

            elif etype == "response.completed":
                resp_data = event.get("response", {}) or {}
                for ev in _unseen_output(resp_data):
                    yield ev
                terminal = True
                yield {"type": "response_completed", "response": resp_data}

            elif etype == "response.incomplete":
                # Partial output: the consumer decides (max_output_tokens
                # returns what was produced; anything else is an error).
                resp_data = event.get("response", {}) or {}
                for ev in _unseen_output(resp_data):
                    yield ev
                terminal = True
                reason = (resp_data.get("incomplete_details") or {}).get("reason") or "unknown"
                yield {"type": "response_incomplete", "response": resp_data, "reason": str(reason)}

            elif etype in ("response.failed", "error"):
                raise _stream_error_from_event(event)
        if done:
            break

    if not terminal:
        # The connection closed before Codex said it was finished: what we
        # have may be half an answer. Codex CLI retries these; so do we.
        raise CodexStreamError("stream_truncated", "the stream closed before response.completed")

    # Tail fallback: a tool call whose arguments came through the
    # streaming deltas but whose output_item.done was never seen
    # (and which response.completed didn't include) — still surface it.
    for item_id, args_str in fc_args.items():
        if item_id in seen_ids or item_id not in fc_meta:
            continue
        meta = fc_meta[item_id]
        yield {
            "type": "tool_call",
            "name": meta["name"],
            "args": _parse_arguments(args_str),
            "id": meta["call_id"],
        }


def _extract_account_id(token: str) -> Optional[str]:
    """Extract chatgpt_account_id from JWT claims."""
    try:
        parts = token.split(".")
        payload = parts[1] + "=" * (4 - len(parts[1]) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims.get("https://api.openai.com/auth", {}).get("chatgpt_account_id")
    except Exception as e:
        logger.warning("Failed to extract account_id from JWT: %s", e)
        return None


def _convert_content_blocks(blocks: list) -> list:
    """Convert Claude-style multimodal content blocks to Responses API format."""
    converted = []
    for block in blocks:
        if isinstance(block, str):
            converted.append({"type": "input_text", "text": block})
        elif isinstance(block, dict):
            btype = block.get("type", "")
            if btype == "text":
                converted.append({"type": "input_text", "text": block.get("text", "")})
            elif btype == "image" or btype == "image_url":
                # Claude format: {"type": "image", "source": {"type": "base64", "media_type": ..., "data": ...}}
                source = block.get("source", {})
                if source.get("type") == "base64":
                    media_type = source.get("media_type", "image/png")
                    data = source.get("data", "")
                    converted.append({
                        "type": "input_image",
                        "image_url": f"data:{media_type};base64,{data}",
                    })
                elif "url" in block.get("image_url", {}):
                    converted.append({
                        "type": "input_image",
                        "image_url": block["image_url"]["url"],
                    })
                else:
                    converted.append({"type": "input_text", "text": "[image content not convertible]"})
            elif btype == "document":
                # PDF documents — extract text note since Responses API doesn't support PDF blocks
                converted.append({
                    "type": "input_text",
                    "text": f"[PDF document attached: {block.get('source', {}).get('filename', 'document.pdf')}]",
                })
            else:
                # Pass through unknown block types as text
                converted.append({"type": "input_text", "text": str(block.get("text", block))})
        else:
            converted.append({"type": "input_text", "text": str(block)})
    return converted


def _last_human_index(messages: List[BaseMessage]) -> int:
    """Where the current turn starts: the last HumanMessage (-1 if none)."""
    for i in range(len(messages) - 1, -1, -1):
        if isinstance(messages[i], HumanMessage):
            return i
    return -1


def _ai_text(msg: AIMessage) -> str:
    content = msg.content
    if isinstance(content, list):
        return " ".join(
            b.get("text", "") if isinstance(b, dict) else str(b)
            for b in content
        )
    return content if isinstance(content, str) else str(content or "")


def _replayable_reasoning(msg: AIMessage) -> List[Dict[str, Any]]:
    """The message's reasoning items as Responses input items.

    Only items that carry their ``encrypted_content``: with ``store: false``
    nothing is kept server-side, so an id alone references nothing (a 404
    "Items are not persisted when store is false"). The id is not sent at
    all — the blob carries the item, as Codex CLI does.
    """
    items = (getattr(msg, "additional_kwargs", None) or {}).get("reasoning_items") or []
    out = []
    for item in items:
        if not isinstance(item, dict) or item.get("type", "reasoning") != "reasoning":
            continue
        encrypted = item.get("encrypted_content")
        if not encrypted:
            continue
        out.append({
            "type": "reasoning",
            "summary": list(item.get("summary") or []),
            "encrypted_content": encrypted,
        })
    return out


def strip_reasoning_items(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Drop ``reasoning_items`` from every AIMessage before the last
    HumanMessage (contract 3). Pure: returns a new list, copies the
    messages it changes, mutates nothing.

    Reasoning is replayed only within the turn that produced it, so older
    turns' encrypted blobs (several KB each) are dead weight in a
    checkpoint; the coordinator calls this before checkpointing.
    """
    last_human = _last_human_index(messages)
    out: List[BaseMessage] = []
    for i, msg in enumerate(messages):
        if i < last_human and isinstance(msg, AIMessage):
            ak = getattr(msg, "additional_kwargs", None) or {}
            if "reasoning_items" in ak:
                kept = {k: v for k, v in ak.items() if k != "reasoning_items"}
                msg = msg.model_copy(update={"additional_kwargs": kept})
        out.append(msg)
    return out


def _messages_to_input(messages: List[BaseMessage]) -> tuple[str, list]:
    """Convert LangChain messages to Responses API format (instructions + input).

    Returns (instructions, input_messages).
    The first SystemMessage becomes `instructions`; everything else goes into `input`.

    An AIMessage in the current turn (after the last HumanMessage) replays
    its reasoning items first (2026-09-24), then any text it spoke
    alongside its tool calls ("Let me look."), then its function calls —
    the order Codex produced them in. Older turns send text and calls only.
    """
    instructions = "You are a helpful assistant."
    input_msgs = []
    last_human = _last_human_index(messages)
    # Codex Responses API requires IDs starting with "fc_" for function calls.
    # LangChain uses "call_*" format — remap consistently.
    _id_map: Dict[str, str] = {}
    _fc_counter = 0

    def _remap_call_id(original_id: str) -> str:
        nonlocal _fc_counter
        if original_id.startswith("fc_"):
            return original_id
        if original_id not in _id_map:
            _id_map[original_id] = f"fc_{_fc_counter:08x}"
            _fc_counter += 1
        return _id_map[original_id]

    for index, msg in enumerate(messages):
        if isinstance(msg, SystemMessage):
            instructions = msg.content if isinstance(msg.content, str) else str(msg.content)
        elif isinstance(msg, HumanMessage):
            # Handle multimodal content (list of blocks)
            if isinstance(msg.content, list):
                converted_blocks = _convert_content_blocks(msg.content)
                input_msgs.append({"role": "user", "content": converted_blocks})
            else:
                input_msgs.append({"role": "user", "content": msg.content})
        elif isinstance(msg, AIMessage):
            if index > last_human:
                input_msgs.extend(_replayable_reasoning(msg))
            content = _ai_text(msg)
            # If the AI message had tool calls, represent them properly
            if msg.tool_calls:
                # The text that came with the calls used to be dropped here.
                if content.strip():
                    input_msgs.append({"role": "assistant", "content": content})
                for tc in msg.tool_calls:
                    remapped_id = _remap_call_id(tc.get("id", ""))
                    input_msgs.append({
                        "type": "function_call",
                        "id": remapped_id,
                        "call_id": remapped_id,
                        "name": tc["name"],
                        "arguments": json.dumps(tc.get("args", {})),
                        "status": "completed",
                    })
            else:
                input_msgs.append({"role": "assistant", "content": content})
        elif isinstance(msg, ToolMessage):
            input_msgs.append({
                "type": "function_call_output",
                "call_id": _remap_call_id(msg.tool_call_id),
                "output": msg.content if isinstance(msg.content, str) else json.dumps(msg.content),
            })

    return instructions, input_msgs


def _mark_nullable(schema: Any) -> Any:
    """Add "null" to a sanitized schema's type so the value may be omitted.

    Strict function-calling schemas have no notion of an optional property —
    every declared property must be supplied. The documented way to say "this
    may be absent" is a nullable type array, e.g. ["integer", "null"]. An
    enum gains ``None`` too, or null would still be outside it.
    """
    if not isinstance(schema, dict):
        return schema
    declared = schema.get("type")
    out = schema
    if isinstance(declared, str):
        if declared != "null":
            out = {**schema, "type": [declared, "null"]}
    elif isinstance(declared, list):
        if "null" not in declared:
            out = {**schema, "type": [*declared, "null"]}
    elif isinstance(schema.get("anyOf"), list):
        if not any(isinstance(b, dict) and b.get("type") == "null" for b in schema["anyOf"]):
            out = {**schema, "anyOf": [*schema["anyOf"], {"type": "null"}]}
        return out
    else:
        return schema
    if isinstance(out.get("enum"), list) and None not in out["enum"]:
        out = {**out, "enum": [*out["enum"], None]}
    return out


# Keys the Codex endpoint rejects or that only confuse the model.
_DROPPED_SCHEMA_KEYS = ("title", "$defs", "definitions", "examples", "not")
# Rejected at the root of a parameters schema, ordinary JSON Schema below it.
_ROOT_ONLY_DROPPED = ("enum", "const")
_UNION_KEYS = ("anyOf", "oneOf")
_SIMPLE_BRANCH_KEYS = {"type", "description", "format"}
_CONSTRAINT_ONLY_KEYS = {"required", "description", "title", "not"}
_MISSING = object()


def _sanitize_type(value: Any) -> Any:
    """``null``/None → "string" (as before); a type list keeps every member
    and its null (collapsing ["string", "integer"] to "string" hid a type)."""
    if value is None or value in ("None", "null"):
        return "string"
    if isinstance(value, list):
        has_null = any(t in (None, "null", "None") for t in value)
        types = list(dict.fromkeys(t for t in value if t and t not in ("null", "None")))
        if not types:
            types = ["string"]
        if has_null:
            types.append("null")
        return types[0] if len(types) == 1 else types
    return value


def _infer_type(result: Dict[str, Any]) -> str:
    """A schema without ``type``: say what its enum / items imply, else the
    old default of object."""
    values = result.get("enum") if isinstance(result.get("enum"), list) else (
        [result["const"]] if "const" in result else None)
    if values:
        vals = [v for v in values if v is not None]
        if vals and all(isinstance(v, bool) for v in vals):
            return "boolean"
        if vals and all(isinstance(v, int) and not isinstance(v, bool) for v in vals):
            return "integer"
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            return "number"
        if vals and all(isinstance(v, str) for v in vals):
            return "string"
    if "items" in result:
        return "array"
    return "object"


def _fold_default(result: Dict[str, Any], default: Any) -> None:
    """``default`` is not sent (the old validator rejected it); say it in
    the description instead, so the model knows what omitting means."""
    if default is None:
        return
    try:
        rendered = json.dumps(default, ensure_ascii=False)
    except (TypeError, ValueError):
        rendered = str(default)
    description = str(result.get("description") or "").strip()
    if "Default:" in description:
        return
    note = f"Default: {rendered}."
    result["description"] = f"{description} {note}" if description else note


def _sanitize_keys(schema: Dict[str, Any], nested: bool) -> Dict[str, Any]:
    """Sanitize a schema's own keys (unions are the caller's). No type is
    invented here."""
    result: Dict[str, Any] = {}
    default: Any = _MISSING
    for k, v in schema.items():
        if k in _DROPPED_SCHEMA_KEYS or k in _UNION_KEYS or k == "allOf":
            continue
        if k == "default":
            default = v
            continue
        if k in _ROOT_ONLY_DROPPED and not nested:
            # Codex rejects them on the parameters object itself.
            continue
        if k == "type":
            result["type"] = _sanitize_type(v)
        elif k == "properties" and isinstance(v, dict):
            result["properties"] = {pk: _sanitize_schema(pv, _nested=True) for pk, pv in v.items()}
        elif k == "items" and isinstance(v, dict):
            result["items"] = _sanitize_schema(v, _nested=True)
        else:
            result[k] = v
    if default is not _MISSING:
        _fold_default(result, default)
    return result


def _overlay(base: Dict[str, Any], siblings: Dict[str, Any], nested: bool) -> Dict[str, Any]:
    """Lay a union's sibling keys (description, default, …) over the
    union's result. Pydantic writes ``{"anyOf": [...], "description": ...}``
    with anyOf first; returning early from anyOf used to drop the
    description of every Optional parameter."""
    if not siblings:
        return base
    extra = _sanitize_keys(siblings, nested)
    out = dict(base)
    for k, v in extra.items():
        if k == "type" and ("type" in out or "anyOf" in out):
            continue
        if k == "properties" and isinstance(out.get("properties"), dict) and isinstance(v, dict):
            out["properties"] = {**out["properties"], **v}
        elif k == "required" and isinstance(out.get("required"), list) and isinstance(v, list):
            out["required"] = list(dict.fromkeys([*out["required"], *v]))
        else:
            out[k] = v
    return out


def _is_null_branch(sub: Any) -> bool:
    return isinstance(sub, dict) and (sub.get("type") == "null" or sub.get("type") == ["null"])


def _is_simple_branch(sub: Dict[str, Any]) -> bool:
    t = sub.get("type")
    typed = isinstance(t, str) or (isinstance(t, list) and all(isinstance(x, str) for x in t))
    return typed and set(sub) <= _SIMPLE_BRANCH_KEYS


def _sanitize_union(branches: List[Any], nested: bool) -> Dict[str, Any]:
    """``anyOf`` / ``oneOf`` → one schema, keeping what the branches say.

    The null member is kept as nullability: Optional[int] arrives as
    ``{"anyOf": [{"type": "integer"}, {"type": "null"}]}`` and dropping the
    null made the parameter non-nullable under strict function calling, so
    the model had to supply *something* — it picked 0, then -1. That
    silently broke read_imessage_thread (ROWID < 0) and search_inbox
    (category=0). See tests/test_codex_schema_preserves_null.py.

    One non-null branch → that branch (nullable if a null was there).
    Nested, several simple branches → a type list; several complex ones →
    ``anyOf`` of them (``oneOf`` is not accepted, ``anyOf`` is). At the root
    the parameters must be one object, so the first branch wins there, as
    before.
    """
    has_null = any(_is_null_branch(b) for b in branches)
    non_null = [_sanitize_schema(b, _nested=nested) for b in branches if not _is_null_branch(b)]
    if not non_null:
        return {"type": "null"}
    if len(non_null) == 1 or not nested:
        return _mark_nullable(non_null[0]) if has_null else non_null[0]
    if all(_is_simple_branch(b) for b in non_null):
        types: List[str] = []
        for b in non_null:
            t = b["type"]
            for x in (t if isinstance(t, list) else [t]):
                if x == "null":
                    has_null = True
                elif x not in types:
                    types.append(x)
        if has_null:
            types.append("null")
        return {"type": types[0] if len(types) == 1 else types}
    union = list(non_null)
    if has_null:
        union.append({"type": "null"})
    return {"anyOf": union}


def _has_ref(node: Any) -> bool:
    if isinstance(node, dict):
        return "$ref" in node or any(_has_ref(v) for v in node.values())
    if isinstance(node, list):
        return any(_has_ref(v) for v in node)
    return False


def _inline_refs(schema: Any) -> Any:
    """Resolve local ``$ref``s (``#/$defs/X``, ``#/definitions/X``) in place
    of the reference, before ``$defs`` is dropped. Pydantic writes a nested
    model (``plan(items: List[PlanItem])``) as ``$defs`` + ``$ref``; dropping
    the defs left ``{"$ref": "#/$defs/PlanItem"}`` dangling beside an empty
    object (review 2026-10-11). A recursive or unresolvable reference
    becomes a plain object; the reference's own siblings (description …)
    are kept. Pure."""
    if not isinstance(schema, dict) or not _has_ref(schema):
        return schema
    defs: Dict[str, Any] = {}
    for key in ("$defs", "definitions"):
        table = schema.get(key)
        if isinstance(table, dict):
            for name, sub in table.items():
                defs.setdefault(f"#/{key}/{name}", sub)

    def walk(node: Any, seen: frozenset) -> Any:
        if isinstance(node, list):
            return [walk(v, seen) for v in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str):
            siblings = {k: walk(v, seen) for k, v in node.items() if k != "$ref"}
            target = defs.get(ref)
            resolved = walk(target, seen | {ref}) if target is not None and ref not in seen else {"type": "object"}
            if not isinstance(resolved, dict):
                return resolved
            return {**resolved, **siblings}
        return {k: (v if k in ("$defs", "definitions") else walk(v, seen)) for k, v in node.items()}

    return walk(schema, frozenset())


def _sanitize_schema(schema: Any, _nested: bool = False) -> Any:
    """Recursively sanitize a JSON schema for the Codex API.

    - Local ``$ref``s are inlined first (``_inline_refs``), so dropping
      ``$defs`` never leaves a reference dangling
    - type: null or type: None → type: "string"
    - Missing 'type' → inferred from enum/const/items, else "object"
    - Drops 'title', '$defs', 'definitions', 'examples', 'not'; folds
      'default' into the description
    - At the root only (the parameters object): drops 'enum' / 'const' and
      collapses a union to its first branch — Codex rejects them there.
      Nested, enums, consts and unions survive (2026-09-24: the model
      guessed values, and each wrong guess was a new argument set, so the
      tool loop's breaker never fired).
    """
    if schema is None:
        return {"type": "object", "properties": {}}
    if not isinstance(schema, dict):
        return schema
    if not _nested:
        schema = _inline_refs(schema)

    all_of = schema.get("allOf")
    if isinstance(all_of, list) and all_of:
        siblings = {k: v for k, v in schema.items() if k != "allOf"}
        if len(all_of) == 1:
            # Flatten single-element allOf (common pydantic pattern)
            base = _sanitize_schema(all_of[0], _nested=_nested)
        else:
            # Multi-element allOf: merge all sub-schemas into one object
            base = {"type": "object", "properties": {}, "required": []}
            for sub in all_of:
                s = _sanitize_schema(sub, _nested=_nested)
                base["properties"].update(s.get("properties", {}))
                base["required"].extend(s.get("required", []))
            if not base["required"]:
                del base["required"]
        return _overlay(base, siblings, _nested)

    for union_key in _UNION_KEYS:
        branches = schema.get(union_key)
        if not (isinstance(branches, list) and branches):
            continue
        siblings = {k: v for k, v in schema.items() if k not in _UNION_KEYS}
        if all(isinstance(b, dict) and set(b) <= _CONSTRAINT_ONLY_KEYS for b in branches):
            # "One of these is required" (MCP servers write this at the
            # root): a constraint on the schema beside it, not a type.
            # Flattening to the first branch threw away every property.
            return _sanitize_schema(siblings, _nested=_nested)
        return _overlay(_sanitize_union(branches, _nested), siblings, _nested)

    result = _sanitize_keys(schema, _nested)
    # Ensure schemas have a type
    if "type" not in result:
        result["type"] = _infer_type(result)
    if result.get("type") == "object" and "properties" not in result:
        result["properties"] = {}

    return result


def _ensure_object_schema(schema: dict) -> dict:
    """Ensure a function parameters schema is type: object (Codex requirement).

    If sanitization produced a non-object type (e.g. from flattening anyOf),
    wrap it in an object with a single 'input' property.
    """
    if schema.get("type") != "object":
        return {
            "type": "object",
            "properties": {"input": schema},
            "required": [],
        }
    if "properties" not in schema:
        schema["properties"] = {}
    return schema


def _convert_tools(tools: list) -> list:
    """Convert LangChain tool definitions to Responses API format."""
    converted = []
    for tool in tools:
        if isinstance(tool, dict):
            # Already in dict form (from bind_tools)
            if tool.get("type") == "function":
                # Sanitize the parameters schema
                params = tool.get("parameters", {})
                tool_copy = dict(tool)
                tool_copy["parameters"] = _ensure_object_schema(_sanitize_schema(params))
                converted.append(tool_copy)
            elif "function" in tool:
                # OpenAI Chat Completions format → Responses format
                fn = tool["function"]
                converted.append({
                    "type": "function",
                    "name": fn["name"],
                    "description": fn.get("description", ""),
                    "parameters": _ensure_object_schema(_sanitize_schema(fn.get("parameters", {}))),
                })
            else:
                converted.append(tool)
        else:
            # LangChain BaseTool. Prefer args_schema if it's already a dict —
            # langchain-mcp-adapters wraps MCP tools that way and the dict
            # carries the real inputSchema. Falling through to
            # get_input_schema().model_json_schema() yields a generic
            # anyOf [string, object, ToolCall] fallback that strips every
            # property name, which is why apple-mcp "calendar" calls used
            # to fail with "Invalid arguments for calendar tool".
            args_schema = getattr(tool, "args_schema", None)
            if isinstance(args_schema, dict) and args_schema.get("properties"):
                schema = args_schema
            else:
                schema = tool.get_input_schema().model_json_schema()
            converted.append({
                "type": "function",
                "name": tool.name,
                "description": tool.description or "",
                "parameters": _ensure_object_schema(_sanitize_schema(schema)),
            })
    return converted


def _tool_context_conversation_id() -> Optional[str]:
    """The tool layer's task-local conversation id — read only when that
    module is already loaded (importing it here would pull in every tool)."""
    mod = sys.modules.get("services.graph.tools")
    getter = getattr(mod, "get_current_conversation_id", None) if mod is not None else None
    try:
        cid = getter() if getter is not None else None
    except Exception:  # noqa: BLE001
        cid = None
    return str(cid) if cid else None


def _turn_context() -> Dict[str, Optional[str]]:
    """The turn this call belongs to (``turn_ledger.current_turn``): its
    source, id and conversation. A fire-and-forget job spawned inside a
    turn (extraction, tags) inherits it — its spend is that turn's."""
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001
        rec = None
    if rec is None:
        return {"turn_source": None, "turn_id": None, "conversation_id": _tool_context_conversation_id()}
    return {
        "turn_source": rec.source,
        "turn_id": rec.id,
        "conversation_id": rec.conversation_id or _tool_context_conversation_id(),
    }


_TOOL_CHOICE_WORDS = ("auto", "none", "required")


def _normalize_tool_choice(choice: Any, tool_names: set) -> Optional[Union[str, Dict[str, Any]]]:
    """LangChain spellings → the Responses API's.

    ``"any"`` is ``"required"``. A tool name, ``{"type": "function", "name":
    …}`` or the Chat Completions ``{"function": {"name": …}}`` forces that
    one function — only when it is bound (forcing an unbound function is a
    400 for the whole turn), else ``"auto"``.
    """
    if choice is None or choice is False:
        return None
    if choice is True:
        return "required"
    if isinstance(choice, str):
        if choice == "any":
            return "required"
        if choice in _TOOL_CHOICE_WORDS:
            return choice
        name = choice
    elif isinstance(choice, dict):
        name = choice.get("name") or (choice.get("function") or {}).get("name")
        if not name:
            kind = choice.get("type")
            return kind if kind in _TOOL_CHOICE_WORDS else None
    else:
        return None
    if name in tool_names:
        return {"type": "function", "name": name}
    logger.warning("tool_choice names %r, which is not bound; sending auto", name)
    return "auto"


def _deadline_error() -> CodexStreamError:
    return CodexStreamError(
        "request_timeout",
        f"no complete answer within {request_timeout_seconds():.0f}s (CODEX_REQUEST_TIMEOUT_SECONDS)",
        retryable=False,
    )


async def _until_deadline(chunks, deadline: float):
    """Pass chunks through until the monotonic ``deadline``; then raise.

    Each read is bounded by what is left of the whole request, which
    httpx's per-read timeout is not.
    """
    it = chunks.__aiter__()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _deadline_error()
        try:
            chunk = await asyncio.wait_for(it.__anext__(), timeout=remaining)
        except StopAsyncIteration:
            return
        except asyncio.TimeoutError:
            raise _deadline_error() from None
        yield chunk


# ---- prompt-cache diagnostics (2026-09-27, followups L6) -------------------------
#
# 4.7–5.7 % of input tokens were cached although the prompt is ordered
# static-first (``compose_system_prompt``); the mind got 3 % on ~12.5K
# tokens a call, the pulse 14 %. Something early in the prefix differs
# between calls — the tools' order, the cache key, per-wake text in
# ``base`` — and nothing recorded enough to say which. One ``[codex-cache]``
# line per request carries short hashes of the first 1K / 4K / 16K
# characters of the instructions and of the tools as sent, the
# ``prompt_cache_key`` and the cached share the response reported: two
# consecutive lines with the same key show where the prompts first differ.
# ``codex_calls`` has no free-form column, so the line is the record.
CACHE_PREFIX_CHARS = {"1k": 1_000, "4k": 4_000, "16k": 16_000}


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]


def prompt_fingerprint(body: Dict[str, Any]) -> Dict[str, Any]:
    """Short hashes of the prefix a Codex request's cache can reuse. Pure.

    The tools are hashed as serialized in the order they are sent — the
    order is part of the prefix."""
    instructions = str(body.get("instructions") or "")
    tools_list = body.get("tools") or []
    tools = json.dumps(tools_list, ensure_ascii=False, separators=(",", ":"), default=str)
    return {
        "key": body.get("prompt_cache_key"),
        "instructions": {label: _short_hash(instructions[:n]) for label, n in CACHE_PREFIX_CHARS.items()},
        "instructions_chars": len(instructions),
        "tools": {label: _short_hash(tools[:n]) for label, n in CACHE_PREFIX_CHARS.items()},
        "tools_chars": len(tools),
        "n_tools": len(tools_list) if isinstance(tools_list, list) else 0,
    }


def fingerprint_line(fp: Dict[str, Any], row: Dict[str, Any]) -> str:
    """The ``[codex-cache]`` log line for one request. Pure."""
    def hashes(d: Dict[str, str]) -> str:
        return "/".join(d.get(label, "-") for label in CACHE_PREFIX_CHARS)

    return (f"[codex-cache] key={fp.get('key') or '-'} purpose={row.get('purpose') or '-'} "
            f"model={row.get('model') or '-'} status={row.get('status') or '-'} "
            f"in={row.get('input_tokens') if row.get('input_tokens') is not None else '-'} "
            f"cached={row.get('cached_tokens') if row.get('cached_tokens') is not None else '-'} "
            f"instr={fp.get('instructions_chars')}:{hashes(fp.get('instructions') or {})} "
            f"tools={fp.get('n_tools')}/{fp.get('tools_chars')}:{hashes(fp.get('tools') or {})}")


def _log_fingerprint(body: Dict[str, Any], row: Dict[str, Any]) -> None:
    """Never lets a diagnostic touch the request."""
    try:
        logger.info(fingerprint_line(prompt_fingerprint(body), row))
    except Exception as exc:  # noqa: BLE001
        logger.debug("prompt fingerprint failed: %s", exc)


def _note_usage(row: Dict[str, Any], usage: Any) -> None:
    meta = usage_metadata_from(usage)
    if meta is None:
        return
    row["input_tokens"] = meta["input_tokens"]
    row["cached_tokens"] = meta["input_token_details"]["cache_read"]
    row["output_tokens"] = meta["output_tokens"]
    row["reasoning_tokens"] = meta["output_token_details"]["reasoning"]


def _event_chunk(event: Dict[str, Any]) -> Optional[ChatGenerationChunk]:
    """One parser event → the chunk ``_astream`` yields for it."""
    etype = event["type"]
    if etype == "text_delta":
        return ChatGenerationChunk(message=AIMessageChunk(content=event["text"]))
    if etype == "reasoning_delta":
        # N4: pass reasoning text through additional_kwargs so consumers
        # can render "thinking..." UI without mixing it into the content
        # stream.
        return ChatGenerationChunk(
            message=AIMessageChunk(content="", additional_kwargs={"reasoning_delta": event["text"]})
        )
    if etype == "tool_args_delta":
        # Surface partial tool-call argument JSON so the UI can show
        # "calling <fn> with: <partial>" while Codex is still typing the
        # args. Carried via additional_kwargs so existing tool_calls
        # accumulators don't see it as a real tool call.
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                additional_kwargs={
                    "tool_args_delta": {"id": event["id"], "name": event["name"], "delta": event["delta"]}
                },
            )
        )
    if etype == "tool_call":
        return ChatGenerationChunk(
            message=AIMessageChunk(
                content="",
                tool_calls=[{"name": event["name"], "args": event["args"], "id": event["id"], "type": "tool_call"}],
            )
        )
    return None


def _commits(chunk: ChatGenerationChunk) -> bool:
    """Has the consumer now got answer text or a tool call? After that a
    retry would replay it, so ``_astream`` no longer retries."""
    msg = chunk.message
    return bool(msg.content) or bool(getattr(msg, "tool_calls", None))


class _Collected:
    """``_agenerate``'s accumulator: one attempt's chunks → one AIMessage."""

    def __init__(self) -> None:
        self.text: List[str] = []
        self.tool_calls: list = []
        self.response_metadata: Dict[str, Any] = {}
        self.usage_metadata: Optional[Dict[str, Any]] = None
        self.additional_kwargs: Dict[str, Any] = {}

    def add(self, chunk: ChatGenerationChunk) -> None:
        msg = chunk.message
        content = msg.content
        if isinstance(content, str) and content:
            self.text.append(content)
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block:
                    self.text.append(block["text"])
                elif isinstance(block, str):
                    self.text.append(block)
        for tc in (getattr(msg, "tool_calls", None) or []):
            self.tool_calls.append(tc)
        meta = getattr(msg, "response_metadata", None) or {}
        if meta.get("model") or meta.get("usage"):
            self.response_metadata = meta
        if getattr(msg, "usage_metadata", None):
            self.usage_metadata = msg.usage_metadata
        ak = getattr(msg, "additional_kwargs", None) or {}
        if ak.get("reasoning_items"):
            self.additional_kwargs["reasoning_items"] = list(ak["reasoning_items"])
        if ak.get("incomplete_reason"):
            self.additional_kwargs["incomplete_reason"] = ak["incomplete_reason"]

    def message(self, model: str) -> AIMessage:
        kwargs: Dict[str, Any] = {
            "content": "".join(self.text),
            "tool_calls": self.tool_calls if self.tool_calls else [],
            "response_metadata": self.response_metadata or {"model": model, "usage": {}},
            "additional_kwargs": self.additional_kwargs,
        }
        if self.usage_metadata:
            kwargs["usage_metadata"] = self.usage_metadata
        return AIMessage(**kwargs)


class ChatCodexResponses(BaseChatModel):
    """LangChain chat model using the Codex Responses API."""

    model: str = "gpt-6-astra"
    temperature: float = 0.7
    max_tokens: int = 16384
    api_key: str = ""
    account_id: Optional[str] = None
    bound_tools: List[Dict[str, Any]] = []
    # "required", "auto", "none", "any", a bound tool's name, or
    # {"type": "function", "name": …} (normalised in _build_request).
    tool_choice: Optional[Union[str, Dict[str, Any]]] = None
    # Rate-gate priority class: "interactive" waits through 429 cooldowns,
    # "background" defers (see services/llm_rate_gate.py).
    priority: str = "interactive"
    # Who built this model (``build_background_llm`` stamps its caller) —
    # the rate gate's ledger row names the job it deferred.
    purpose: str = ""
    # Reasoning effort to request. None = the model's catalog default
    # (services/model_catalog). A value the model does not support is
    # replaced by that default rather than sent (it would be a 400).
    reasoning_effort: Optional[str] = None
    # 2026-09-24: one function call per response unless a caller opts
    # in. Browser actions depend on order (open, then read, then click),
    # and a batch of calls cannot see each other's results. None = let the
    # endpoint decide (the field is not sent).
    parallel_tool_calls: Optional[bool] = False

    class Config:
        arbitrary_types_allowed = True

    @property
    def _llm_type(self) -> str:
        return "codex-responses"

    @property
    def _identifying_params(self) -> dict:
        return {"model": self.model, "temperature": self.temperature}

    def _resolve_account_id(self) -> str:
        if self.account_id:
            return self.account_id
        aid = _extract_account_id(self.api_key)
        if aid:
            self.account_id = aid
            return aid
        raise ValueError("Could not extract ChatGPT-Account-Id from token")

    def bind_tools(self, tools: list, **kwargs) -> "ChatCodexResponses":
        """Return a new instance with tools bound."""
        converted = _convert_tools(tools)
        tool_choice = kwargs.get("tool_choice", self.tool_choice)
        parallel_tool_calls = kwargs.get("parallel_tool_calls", self.parallel_tool_calls)
        return ChatCodexResponses(
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            api_key=self.api_key,
            account_id=self.account_id,
            bound_tools=converted,
            tool_choice=tool_choice,
            priority=self.priority,
            purpose=self.purpose,
            reasoning_effort=self.reasoning_effort,
            parallel_tool_calls=parallel_tool_calls,
        )

    def _generate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[CallbackManagerForLLMRun] = None,
        **kwargs,
    ) -> ChatResult:
        """Sync generation — runs async version in event loop."""
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                result = pool.submit(
                    asyncio.run,
                    self._agenerate(messages, stop, run_manager=None, **kwargs)
                ).result()
                return result
        return asyncio.run(self._agenerate(messages, stop, run_manager=None, **kwargs))

    def _reasoning_effort(self) -> str:
        """The effort to send: the requested one when the model supports
        it, else the model's catalog default, else "medium"."""
        from services import model_catalog

        return model_catalog.effort_for(self.model, self.reasoning_effort) or "medium"

    def _prompt_cache_key(self) -> Optional[str]:
        """Route requests that share a prefix to the same cache.

        A turn's calls share the conversation's prefix, so they are keyed by
        the conversation (Codex CLI keys by its session the same way). A
        background job's prompts share its system prompt across every
        conversation, so they are keyed by the job (``purpose``).
        """
        if self.priority != "interactive" and self.purpose:
            key: Optional[str] = self.purpose
        else:
            key = _turn_context()["conversation_id"]
        return str(key)[:PROMPT_CACHE_KEY_MAX_CHARS] if key else None

    def _build_request(self, messages: List[BaseMessage]) -> tuple[Dict[str, str], Dict[str, Any]]:
        """Build the headers + body for a Codex Responses API call. Pulled
        out so _astream and _agenerate share the same request shape."""
        account_id = self._resolve_account_id()
        instructions, input_msgs = _messages_to_input(messages)

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "ChatGPT-Account-Id": account_id,
            "Content-Type": "application/json",
            "originator": "edward",
            "OpenAI-Beta": "responses=experimental",
        }
        body: Dict[str, Any] = {
            "model": self.model,
            "instructions": instructions,
            "store": False,
            "stream": True,
            "input": input_msgs,
            "include": ["reasoning.encrypted_content"],
            "reasoning": {"effort": self._reasoning_effort(), "summary": "auto"},
        }
        if self.bound_tools:
            body["tools"] = self.bound_tools
            names = {t.get("name") for t in self.bound_tools if isinstance(t, dict)}
            tool_choice = _normalize_tool_choice(self.tool_choice, names)
            if tool_choice:
                body["tool_choice"] = tool_choice
            if self.parallel_tool_calls is not None:
                body["parallel_tool_calls"] = bool(self.parallel_tool_calls)
        cache_key = self._prompt_cache_key()
        if cache_key:
            body["prompt_cache_key"] = cache_key
        return headers, body

    def _call_context(self, body: Dict[str, Any]) -> Dict[str, Any]:
        """What the ledger row knows before the request goes out."""
        # A surplus job's scope (call_purpose, 2026-09-24) names the job
        # behind every call it causes, ahead of the model's own label.
        try:
            from services.call_purpose import current_purpose
            scoped = current_purpose()
        except Exception:  # noqa: BLE001
            scoped = None
        ctx: Dict[str, Any] = {
            "purpose": scoped or self.purpose or None,
            "priority": self.priority,
            "model": self.model,
            "effort": (body.get("reasoning") or {}).get("effort"),
        }
        ctx.update(_turn_context())
        if not ctx["purpose"]:
            try:
                from services.event_ledger import caller_label

                ctx["purpose"] = caller_label()
            except Exception:  # noqa: BLE001
                pass
        return ctx

    def _final_chunk(self, resp_data: Dict[str, Any], reasoning_items: List[Dict[str, Any]],
                     incomplete_reason: Optional[str] = None) -> ChatGenerationChunk:
        """The last chunk of a response: model, raw usage, LangChain
        ``usage_metadata`` (contract 1) and the reasoning items to replay
        (contract 3)."""
        usage = resp_data.get("usage", {}) or {}
        additional_kwargs: Dict[str, Any] = {}
        if reasoning_items:
            additional_kwargs["reasoning_items"] = list(reasoning_items)
        if incomplete_reason:
            additional_kwargs["incomplete_reason"] = incomplete_reason
        kwargs: Dict[str, Any] = {
            "content": "",
            "additional_kwargs": additional_kwargs,
            "response_metadata": {"model": resp_data.get("model", self.model), "usage": usage},
        }
        usage_metadata = usage_metadata_from(usage)
        if usage_metadata:
            kwargs["usage_metadata"] = usage_metadata
        return ChatGenerationChunk(message=AIMessageChunk(**kwargs))

    async def _stream_attempt(self, headers: Dict[str, str], body: Dict[str, Any], token: str,
                              deadline: float, ctx: Dict[str, Any]):
        """One HTTP request to Codex → chunks. Writes exactly one
        ``codex_calls`` row, whatever happens (services/codex_call_ledger)."""
        attempt_headers = dict(headers)
        attempt_headers["Authorization"] = f"Bearer {token}"
        started = time.monotonic()
        row: Dict[str, Any] = dict(ctx, status="error", error_class=None, http_status=None, ttft_ms=None)
        reasoning_items: List[Dict[str, Any]] = []
        final_sent = False
        try:
            async with httpx.AsyncClient(timeout=PER_READ_TIMEOUT_SECONDS) as client:
                async with contextlib.AsyncExitStack() as stack:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise _deadline_error()
                    try:
                        resp = await asyncio.wait_for(
                            stack.enter_async_context(client.stream(
                                "POST", CODEX_RESPONSES_URL, json=body, headers=attempt_headers
                            )),
                            timeout=remaining,
                        )
                    except asyncio.TimeoutError:
                        raise _deadline_error() from None
                    row["http_status"] = getattr(resp, "status_code", None)
                    # Every response — 200 or 429 — carries the account's
                    # usage meter in x-codex-* headers. The gate paces
                    # background work against it.
                    try:
                        from services.llm_rate_gate import get_llm_rate_gate as _gate_for_meter
                        _gate_for_meter().note_usage_headers(resp.headers)
                    except Exception as _meter_exc:  # noqa: BLE001
                        logger.debug("usage headers not recorded: %s", _meter_exc)
                    if resp.status_code != 200:
                        error_body = ""
                        async for chunk in resp.aiter_bytes():
                            error_body += chunk.decode("utf-8", errors="replace")
                        row["error_class"] = f"http_{resp.status_code}"
                        if resp.status_code == 401:
                            row["error_class"] = "auth_expired"
                            raise _CodexAuthExpired(error_body)
                        if resp.status_code == 429:
                            row["status"] = "rate_limited"
                            from services.llm_rate_gate import get_llm_rate_gate
                            gate = get_llm_rate_gate()
                            reset_after = gate._parse_resets_in_seconds(error_body)
                            cooldown = gate.note_429(error_body)
                            if reset_after is not None and reset_after > WEEKLY_CAP_THRESHOLD_SECONDS:
                                # The account's weekly cap. Every GPT model on
                                # the account shares it (astra, terra and luna
                                # all answered this on 2026-09-09) — a model
                                # switch cannot help, only the reset can.
                                row["error_class"] = "usage_limit_reached"
                                _report_limit("usage_limit_reached", self.model, None)
                                raise ValueError(
                                    f"Codex weekly usage cap reached for this ChatGPT account "
                                    f"(resets in {_fmt_duration(reset_after)}); it is shared by every GPT model, "
                                    f"so switching models will not help; background "
                                    f"work pauses until the reset. Body: {error_body[:200]}"
                                )
                            _report_limit("429", self.model, cooldown)
                            raise ValueError(
                                f"Codex API error (HTTP 429): rate gate cooldown "
                                f"{cooldown:.0f}s. Body: {error_body[:300]}"
                            )
                        if resp.status_code == 404 and "usage_limit_reached" in error_body:
                            row["status"] = "rate_limited"
                            row["error_class"] = "usage_limit_reached"
                            _report_limit("usage_limit_reached", self.model, None)
                            raise ValueError(
                                "Codex usage cap reached for this ChatGPT account; every GPT "
                                "model shares it. Wait for the reset."
                            )
                        if resp.status_code == 400 and "not supported" in error_body and "model" in error_body:
                            row["error_class"] = "model_not_supported"
                            from services import model_catalog
                            raise ValueError(
                                f"Model '{self.model}' is not available on this ChatGPT account "
                                f"(Codex: {error_body[:160]}). Pick another model in Settings → "
                                f"Model; the catalog recommends {model_catalog.recommended_model()}."
                            )
                        if resp.status_code in RETRYABLE_HTTP_STATUSES:
                            # A gateway or server fault: as transient as a
                            # ``response.failed`` server_error, and retried
                            # once the same way (2026-09-25 audit — these
                            # were plain ValueErrors and failed the turn).
                            raise CodexStreamError(
                                f"http_{resp.status_code}",
                                error_body[:500] or f"HTTP {resp.status_code}",
                                retryable=True, http_status=resp.status_code,
                            )
                        raise ValueError(
                            f"Codex API error (HTTP {resp.status_code}): {error_body[:500]}"
                        )

                    # A 200 is the only evidence the gate ever gets that a
                    # limit has lifted — the API exposes no quota meter. The
                    # weekly 429 of 2026-09-09 reported a ~5-day reset, and
                    # without this the cooldown outlived the quota by a day,
                    # taxing every interactive call and deferring every
                    # background loop while calls were plainly succeeding.
                    from services.llm_rate_gate import get_llm_rate_gate as _gate
                    _gate().note_success()

                    async for event in _iter_codex_sse_events(_until_deadline(resp.aiter_text(), deadline)):
                        etype = event["type"]
                        if row["ttft_ms"] is None and etype in ("text_delta", "tool_args_delta", "tool_call"):
                            row["ttft_ms"] = int((time.monotonic() - started) * 1000)
                        if etype == "reasoning_item":
                            reasoning_items.append(event["item"])
                        elif etype == "response_completed":
                            _note_usage(row, event["response"].get("usage"))
                            final_sent = True
                            yield self._final_chunk(event["response"], reasoning_items)
                        elif etype == "response_incomplete":
                            _note_usage(row, event["response"].get("usage"))
                            if event["reason"] != "max_output_tokens":
                                raise CodexStreamError(
                                    f"incomplete:{event['reason']}"[:80],
                                    "Codex stopped before finishing the answer",
                                    retryable=False,
                                )
                            # Out of output tokens: what was produced is kept,
                            # and marked, so the caller can tell.
                            final_sent = True
                            yield self._final_chunk(event["response"], reasoning_items,
                                                    incomplete_reason=event["reason"])
                        else:
                            chunk = _event_chunk(event)
                            if chunk is not None:
                                yield chunk
                    if not final_sent and reasoning_items:
                        yield ChatGenerationChunk(message=AIMessageChunk(
                            content="", additional_kwargs={"reasoning_items": list(reasoning_items)},
                        ))
            row["status"] = "ok"
        except _CodexAuthExpired:
            raise
        except CodexStreamError as exc:
            row["status"] = "timeout" if exc.code == "request_timeout" else "error"
            row["error_class"] = exc.code
            raise
        except httpx.TimeoutException as exc:
            row["status"] = "timeout"
            row["error_class"] = type(exc).__name__
            raise
        except httpx.TransportError as exc:
            # A reset mid-stream (or a connect that never happened): the
            # answer is incomplete; transient, so retried once.
            row["error_class"] = "connection_error"
            raise CodexStreamError("connection_error", f"{type(exc).__name__}: {exc}", retryable=True) from exc
        except (GeneratorExit, asyncio.CancelledError):
            # The consumer went away (stop pressed, a caller's timeout).
            row["status"] = "cancelled"
            row["error_class"] = "cancelled"
            raise
        except Exception as exc:
            if row["error_class"] is None:
                row["error_class"] = type(exc).__name__
            raise
        finally:
            row["latency_ms"] = int((time.monotonic() - started) * 1000)
            codex_call_ledger.schedule_call(**row)
            _log_fingerprint(body, row)

    async def _attempt_with_auth(self, headers: Dict[str, str], body: Dict[str, Any], deadline: float,
                                 ctx: Dict[str, Any]):
        """One attempt, with the 401 refresh-and-retry-once.

        On HTTP 401 token_expired: force-refresh once and retry. The
        retry happens BEFORE any chunks have been yielded (status check
        is done before the SSE parse), so the consumer never sees
        replayed text.
        """
        rejected_token = self.api_key
        try:
            async for chunk in self._stream_attempt(headers, body, rejected_token, deadline, ctx):
                yield chunk
            return
        except _CodexAuthExpired as exc:
            first_body = exc.body
            logger.warning("Codex 401 — refreshing auth and retrying once")

        # Single-flight: concurrent 401s share one refresh, and one that
        # finished under a minute ago is reused (codex_auth_service).
        mgr = get_codex_auth_manager()
        refreshed = await mgr.force_refresh()
        if not refreshed:
            raise CodexAuthError(
                "Codex API error (HTTP 401): auth rejected and refresh failed. "
                "Run `codex login` (or set OPENAI_API_KEY) to seed fresh credentials.",
                body=first_body,
            )
        new_token = mgr.get_api_key()
        if not new_token:
            raise CodexAuthError("Codex token refreshed but no token available from auth manager",
                                 body=first_body)
        if new_token == rejected_token:
            # The shared refresh handed back the very token just refused:
            # a retry would send it again.
            raise _auth_error_after_refresh(first_body)
        self.api_key = new_token
        # Single retry only — if the refreshed token also 401s, surface a
        # clean error instead of looping; the tool loop sees
        # ``refresh_attempted`` and does not refresh again.
        try:
            async for chunk in self._stream_attempt(headers, body, new_token, deadline, ctx):
                yield chunk
        except _CodexAuthExpired as exc:
            raise _auth_error_after_refresh(exc.body) from None

    async def _pause_before_retry(self, exc: CodexStreamError, deadline: float) -> bool:
        """Sleep a jittered 1–3 s before the one retry; False when the
        deadline leaves no room for it."""
        delay = random.uniform(RETRY_BACKOFF_MIN_S, RETRY_BACKOFF_MAX_S)
        if deadline - time.monotonic() <= delay:
            return False
        logger.warning("Codex stream failed (%s) — retrying once in %.1fs: %s",
                       exc.code, delay, exc.message[:200])
        await asyncio.sleep(delay)
        return True

    async def _prepare(self, messages: List[BaseMessage]):
        from services.llm_rate_gate import get_llm_rate_gate
        # Background calls raise LLMRateLimitDeferred during a 429
        # cooldown — the fire-and-forget loops absorb it and retry on
        # their next scheduled run instead of burning the rate window.
        await get_llm_rate_gate().acquire(self.priority, purpose=self.purpose)
        headers, body = self._build_request(messages)
        ctx = self._call_context(body)
        deadline = time.monotonic() + request_timeout_seconds()
        return headers, body, ctx, deadline

    async def _astream(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs,
    ):
        """Async streaming generation via the Codex Responses API.

        Yields one ChatGenerationChunk per text delta as it arrives —
        this is what makes the user-facing SSE pipeline feel like real
        streaming. Tool calls surface as chunks with both
        ``tool_call_chunks`` (for LangChain accumulation) and ``tool_calls``
        (for direct consumption by Edward's tool-loop). The final chunk
        carries response_metadata (model, usage), ``usage_metadata`` and the
        turn's ``reasoning_items`` when the SSE stream emits
        ``response.completed``.

        A transient stream failure is retried once, but only while nothing
        has reached the consumer (answer text or a tool call) — after that
        a retry would replay it, so the error is raised instead.
        """
        headers, body, ctx, deadline = await self._prepare(messages)
        committed = False
        for attempt in (1, 2):
            try:
                async for chunk in self._attempt_with_auth(headers, body, deadline, ctx):
                    if not committed and _commits(chunk):
                        committed = True
                    yield chunk
                return
            except CodexStreamError as exc:
                if attempt == 1 and exc.retryable and not committed and await self._pause_before_retry(exc, deadline):
                    continue
                raise

    async def _agenerate(
        self,
        messages: List[BaseMessage],
        stop: Optional[List[str]] = None,
        run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
        **kwargs,
    ) -> ChatResult:
        """Non-streaming async generation — the same attempts as
        ``_astream`` (one parse, one auth retry), collected into one
        AIMessage. Nothing reaches a consumer until the end, so a transient
        failure is retried once from scratch even mid-answer.
        """
        headers, body, ctx, deadline = await self._prepare(messages)
        collected = _Collected()
        for attempt in (1, 2):
            collected = _Collected()
            try:
                async for chunk in self._attempt_with_auth(headers, body, deadline, ctx):
                    collected.add(chunk)
                break
            except CodexStreamError as exc:
                if attempt == 1 and exc.retryable and await self._pause_before_retry(exc, deadline):
                    continue
                raise
        return ChatResult(generations=[ChatGeneration(message=collected.message(self.model))])
