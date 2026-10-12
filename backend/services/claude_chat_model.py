"""ChatClaude — a LangChain chat model over the Anthropic Messages API.

Built 2026-10-10; Edward's default provider (``EDWARD_LLM_PROVIDER``,
``services/claude_routing.py``). It speaks the same LangChain contract the
Codex adapter (``codex_chat_model.ChatCodexResponses``) set and the tool
loops in ``services/graph/streaming.py`` read, so a turn does not know which
provider it is on:

* **Streaming.** Text deltas are ``AIMessageChunk.content``; thinking
  summaries are ``additional_kwargs["reasoning_delta"]`` (the work rail's
  ``reasoning`` event); a client tool's partial input JSON is
  ``additional_kwargs["tool_args_delta"] = {id, name, delta, index}`` (the
  ``tool_args_delta`` event). Complete tool calls arrive once, on the final
  chunk, as ``tool_call_chunks`` carrying the whole input and the block's
  index (LangChain derives ``tool_calls`` from them). The loop collects
  ``chunk.tool_calls`` from every chunk, so a partial call on a delta chunk
  would be executed — and holding the calls to the end means a refusal or a
  ``max_tokens`` cut can drop them before anything runs.
* **Replay.** The raw content blocks of a response (thinking with its
  signature, text, ``tool_use``, server tool blocks) ride
  ``additional_kwargs["claude_content"]`` and are sent back verbatim while
  the turn that produced them is running (every AIMessage after the last
  HumanMessage). Older turns are rebuilt from text + tool calls with their
  thinking dropped — a leading run of thinking blocks, which preserved
  thinking allows — and ``strip_claude_thinking`` keeps checkpoints from
  carrying signatures past their turn (the twin of
  ``strip_reasoning_items``). Requests send ``thinking.block_binding.
  prefix_mismatch_behavior="drop_block"`` (beta
  ``thinking-binding-controls-2026-08-01``), so an edited earlier turn (the
  window eliding an old tool result) degrades instead of failing with a 400.
* **Reasoning.** Thinking summaries also ride
  ``additional_kwargs["reasoning_items"]`` in the Codex shape
  (``summary: [{type: summary_text, text}]``, no ``encrypted_content``), so
  the turn trace, the thought journal and the screen read them unchanged.
* **The prompt cache.** A SystemMessage may carry ``STATIC_DYNAMIC_SPLIT``:
  the text before it is one top-level system block with ``cache_control``
  (``cache_ttl``, 1 h by default); the per-turn text after it is sent as a
  mid-conversation ``{"role": "system"}`` message directly after the last
  human message, so the frozen prefix — tools, the static prompt, the
  history up to the owner's last message — is reused across turns. That human
  message carries a breakpoint and top-level automatic caching covers the
  growing tail (``tail_cache_ttl``, 5 min; a longer TTL never follows a
  shorter one).
* **Stops.** ``refusal`` is an honest short answer (``REFUSAL_NOTE``),
  never an exception and never a tool call; ``max_tokens`` returns the
  partial answer marked ``incomplete_reason`` without tool calls;
  ``pause_turn`` (server tools) is resumed with the partial turn appended,
  at most ``MAX_PAUSE_CONTINUATIONS`` times.
* **Errors.** Everything is one ``LLMProviderError`` (``services/llm_errors``)
  whose ``retryable`` ``mind_service.is_transient_error`` reads. A transient
  failure is retried once, after a short pause, while nothing has reached
  the consumer.
* **Spend** (review 2026-10-11). Claude is paid per token, so before
  **every** HTTP request — the first, each ``pause_turn`` continuation and
  the one retry — the daily spend cap decides (``_admit``: the rate gate's
  ``acquire_spend`` for the first, which keeps background's stampede
  spacing, ``llm_spend.check_spend`` for the rest), outside the code that
  records the request, so a refused call sends and records nothing. Each
  request is then priced on its own ``usage`` (``llm_pricing``, the one
  price table) and written to ``llm_calls`` (``llm_spend.record_claude_call``,
  failures too, never ``codex_calls``) with one ``[claude-cache]`` log line.
  When a server-side fallback served it, the row's model is the one that
  served it (``served_model``), the counts are the sum of
  ``usage.iterations`` (``billed_usage``) and each attempt is priced at the
  model that ran it. The turn's ``estimated_cost_usd`` is the sum of its
  requests' prices — never the price of ``_sum_usage``'s total, which adds
  the requests' prompts together and would put two 60K-token Haiku prompts
  on the >100K card.
* **The stream** is split into lines on raw bytes (``_byte_lines``): a
  U+2028 inside a JSON string is not a line end.

Request rules for the 5.5 models: adaptive thinking always (Opus 5.5 cannot
turn it off), ``output_config.effort``, no ``temperature`` / ``top_p``, no
assistant prefill, no forced ``tool_choice`` on Opus / Sonnet 5.5, and
``fallbacks: "default"`` (beta ``server-side-fallback-2026-07-01``) only on
Opus and Sonnet 5.5 — Haiku 5.5 has no server-side fallback.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import hashlib
import json
import logging
import os
import random
import re
import time
import weakref
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Dict, Iterable, List, Optional, Sequence, Tuple

from langchain_core.callbacks import AsyncCallbackManagerForLLMRun, CallbackManagerForLLMRun
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    AIMessageChunk,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.outputs import ChatGeneration, ChatGenerationChunk, ChatResult
from pydantic import Field

from services import llm_pricing
from services.codex_chat_model import INVALID_JSON_KEY, _parse_arguments, _turn_context
from services.llm_errors import LLMProviderError

logger = logging.getLogger(__name__)

PROVIDER = "anthropic"

# The seam ``compose_system_prompt`` puts between the frozen prompt and the
# per-turn context when the turn runs on Claude.
STATIC_DYNAMIC_SPLIT = "\n<<<EDWARD_DYNAMIC_CONTEXT>>>\n"
CLAUDE_CONTENT_KEY = "claude_content"
EARLIER_CONVERSATION_PLACEHOLDER = "(Earlier conversation omitted.)"
NO_MESSAGE_PLACEHOLDER = "(Begin.)"
EMPTY_MESSAGE_PLACEHOLDER = "(empty message)"
NO_OUTPUT_PLACEHOLDER = "(no output)"
NO_TOOL_RESULT_TEXT = "Tool error: no result was recorded for this call."
REFUSAL_NOTE = "I can't help with that one: my model declined the request on a safety check."

BETA_THINKING_BINDING = "thinking-binding-controls-2026-08-01"
BETA_SERVER_FALLBACK = "server-side-fallback-2026-07-01"

EFFORTS = ("low", "medium", "high", "xhigh", "max")
THINKING_DISPLAYS = ("summarized", "omitted")
CACHE_TTLS = ("5m", "1h")
DEFAULT_MAX_TOKENS = 32000
MAX_PAUSE_CONTINUATIONS = 3
DEFAULT_REQUEST_TIMEOUT_SECONDS = 300.0
PER_READ_TIMEOUT_SECONDS = 120.0
CONNECT_TIMEOUT_SECONDS = 10.0
RETRY_BACKOFF_S = (1.0, 3.0)

TOOL_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,128}$")
SUPPORTED_IMAGE_TYPES = frozenset({"image/jpeg", "image/png", "image/gif", "image/webp"})
_DATE_SUFFIX = re.compile(r"-\d{8}$")

# Server-side refusal fallback ("default" form) — Opus 5.5 and Sonnet 5.5 only.
FALLBACK_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5"})
# A forced tool_choice (any / tool) is a 400 on these.
NO_FORCED_TOOL_CHOICE_MODELS = frozenset({"claude-opus-5-5", "claude-sonnet-5-5", "claude-fable-5-1",
                                          "claude-mythos-5-1"})
# Mid-conversation {"role": "system"} messages, no beta header.
MID_CONVERSATION_SYSTEM_MODELS = frozenset({
    "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-sonnet-5-5", "claude-haiku-5-5",
    "claude-fable-5", "claude-fable-5-1", "claude-mythos-5", "claude-mythos-5-1",
})
# Models that answered "role 'system' is not supported" in this process.
_LEARNED_NO_MID_SYSTEM: set = set()
_LOGGED_ONCE: set = set()

# Prices live in one place: ``services/llm_pricing.py`` (the spend ledger's).


def model_family(model: Optional[str]) -> str:
    """``claude-opus-5-5-20261001`` → ``claude-opus-5-5``. Pure."""
    return _DATE_SUFFIX.sub("", (model or "").strip().lower())


def _log_once(key: str, text: str, *args: Any) -> None:
    if key in _LOGGED_ONCE:
        return
    _LOGGED_ONCE.add(key)
    logger.info(text, *args)


def _n(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# ================================================================== usage ==

def usage_metadata_from(usage: Any) -> Optional[Dict[str, Any]]:
    """Anthropic ``usage`` → LangChain ``usage_metadata``: input is every
    prompt token (uncached + cache reads + cache writes), with the reads and
    writes under ``input_token_details``. None without token counts. Pure."""
    if not isinstance(usage, dict) or not ("input_tokens" in usage or "output_tokens" in usage):
        return None
    uncached = _n(usage.get("input_tokens"))
    read = _n(usage.get("cache_read_input_tokens"))
    write = _n(usage.get("cache_creation_input_tokens"))
    out = _n(usage.get("output_tokens"))
    total_in = uncached + read + write
    meta: Dict[str, Any] = {
        "input_tokens": total_in,
        "output_tokens": out,
        "total_tokens": total_in + out,
        "input_token_details": {"cache_read": read, "cache_creation": write},
    }
    details = usage.get("output_tokens_details") if isinstance(usage.get("output_tokens_details"), dict) else {}
    thinking_tokens = _n(details.get("thinking_tokens") or details.get("reasoning_tokens") or usage.get("thinking_tokens"))
    if thinking_tokens:
        meta["output_token_details"] = {"reasoning": thinking_tokens}
    return meta


def _sum_usage(parts: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Usage of several requests (a resumed ``pause_turn``) added up; their
    ``iterations`` are kept end to end."""
    if len(parts) == 1:
        return dict(parts[0] or {})
    total: Dict[str, Any] = {}
    for part in parts:
        for key, value in (part or {}).items():
            if isinstance(value, bool):
                continue
            if isinstance(value, int):
                total[key] = _n(total.get(key)) + value
            elif isinstance(value, list):
                kept = total.get(key)
                total[key] = (list(kept) if isinstance(kept, list) else []) + list(value)
            elif isinstance(value, dict):
                inner = total.setdefault(key, {})
                if isinstance(inner, dict):
                    for k2, v2 in value.items():
                        if isinstance(v2, int) and not isinstance(v2, bool):
                            inner[k2] = _n(inner.get(k2)) + v2
            elif key not in total:
                total[key] = value
    return total


_USAGE_COUNTS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens", "output_tokens")


def _iterations(usage: Any) -> List[Dict[str, Any]]:
    items = usage.get("iterations") if isinstance(usage, dict) else None
    return [it for it in items if isinstance(it, dict)] if isinstance(items, list) else []


def billed_usage(usage: Any) -> Dict[str, Any]:
    """The tokens a response billed. Its top-level ``usage`` covers only the
    attempt that produced the message; when the request ran more than one
    (a server-side refusal fallback — a mid-stream decline bills at normal
    rates — or a compaction), ``usage.iterations`` itemizes every attempt
    and the counts are their sum. Without iterations it is the top level.
    Token counts only — the price is ``llm_pricing.price``'s. Idempotent.
    Pure."""
    if not isinstance(usage, dict):
        return {}
    items = _iterations(usage)
    if not items:
        return dict(usage)
    out = {k: v for k, v in usage.items() if k not in _USAGE_COUNTS and k != "cache_creation"}
    for key in _USAGE_COUNTS:
        out[key] = sum(_n(it.get(key)) for it in items)
    splits = [it.get("cache_creation") for it in items if isinstance(it.get("cache_creation"), dict)]
    if splits:
        out["cache_creation"] = {
            "ephemeral_5m_input_tokens": sum(_n(c.get("ephemeral_5m_input_tokens")) for c in splits),
            "ephemeral_1h_input_tokens": sum(_n(c.get("ephemeral_1h_input_tokens")) for c in splits),
        }
    return out


def estimate_cost_usd(model: Optional[str], usage: Any, *, requested_model: Optional[str] = None) -> Optional[float]:
    """What one request cost at list prices (``llm_pricing.price``: cache
    writes by TTL, Haiku's >100K-token card, web search at $10 per 1,000,
    ``usage.iterations`` each at the model that ran it), or None for a
    model ``llm_pricing`` does not list. ``model`` is the model that
    produced the message (a server-side fallback may have served it);
    ``requested_model`` the one asked for (default: ``model``). Pure."""
    if not isinstance(usage, dict):
        return None
    priced = llm_pricing.price(requested_model or model, usage, served_model=model)
    return priced.total if priced.known else None


# ============================================================= conversion ==

def _text_of(content: Any) -> str:
    """The text of a message body: a string, or the text parts of a list."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, str):
                out.append(part)
            elif isinstance(part, dict) and part.get("type") in (None, "text", "input_text", "output_text"):
                value = part.get("text")
                if isinstance(value, str):
                    out.append(value)
        return "".join(out)
    return str(content)


def _text_block(text: str) -> Dict[str, Any]:
    return {"type": "text", "text": text}


def _note(text: str) -> Dict[str, Any]:
    return _text_block(text)


def _image_from_base64(media_type: Optional[str], data: Any) -> Dict[str, Any]:
    media = (media_type or "image/png").strip().lower()
    if media == "image/jpg":
        media = "image/jpeg"
    if media not in SUPPORTED_IMAGE_TYPES:
        return _note(f"[An image of type {media} was attached; it cannot be shown to the model.]")
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": str(data or "")}}


def _image_from_url(url: Any) -> Dict[str, Any]:
    if isinstance(url, dict):
        url = url.get("url")
    url = str(url or "")
    if url.startswith("data:"):
        header, _, data = url.partition(",")
        media = header[5:].split(";")[0] or "image/png"
        if ";base64" not in header:
            return _note("[An image was attached in a form the model cannot read.]")
        return _image_from_base64(media, data)
    if url.startswith(("https://", "http://")):
        return {"type": "image", "source": {"type": "url", "url": url}}
    return _note("[An image was attached in a form the model cannot read.]")


def _document_block(part: Dict[str, Any]) -> Dict[str, Any]:
    """A document block with only the fields the API accepts (Edward's PDF
    blocks used to carry a ``filename`` inside ``source`` for Codex)."""
    src = part.get("source") if isinstance(part.get("source"), dict) else {}
    kind = src.get("type")
    if kind == "base64":
        source = {"type": "base64", "media_type": src.get("media_type") or "application/pdf",
                  "data": str(src.get("data") or "")}
    elif kind == "url":
        source = {"type": "url", "url": str(src.get("url") or "")}
    elif kind == "text":
        source = {"type": "text", "media_type": "text/plain", "data": str(src.get("data") or "")}
    elif kind == "file":
        source = {"type": "file", "file_id": src.get("file_id")}
    else:
        return _note("[A document was attached in a form the model cannot read.]")
    out: Dict[str, Any] = {"type": "document", "source": source}
    for key in ("title", "context"):
        if isinstance(part.get(key), str) and part[key]:
            out[key] = part[key]
    return out


def _standard_file_block(part: Dict[str, Any]) -> Dict[str, Any]:
    """LangChain's standard ``{"type": "file"|"image", "source_type": …}``."""
    mime = str(part.get("mime_type") or "").lower()
    source_type = part.get("source_type") or ("base64" if part.get("base64") or part.get("data") else
                                               "url" if part.get("url") else None)
    data = part.get("data") or part.get("base64")
    if mime.startswith("image/") or part.get("type") == "image":
        if source_type == "base64":
            return _image_from_base64(mime or "image/png", data)
        if source_type == "url":
            return _image_from_url(part.get("url"))
    if mime == "application/pdf":
        if source_type == "base64":
            return _document_block({"source": {"type": "base64", "media_type": mime, "data": data}})
        if source_type == "url":
            return _document_block({"source": {"type": "url", "url": part.get("url")}})
    if mime.startswith("text/") and source_type == "base64":
        return _document_block({"source": {"type": "text", "data": data}})
    return _note(f"[A file ({mime or 'unknown type'}) was attached; it cannot be shown to the model.]")


def _convert_part(part: Any) -> Optional[Dict[str, Any]]:
    """One part of a user or tool-result body → an Anthropic block, or None
    for a blank one. ``cache_control`` from the caller is never kept: the
    adapter places the breakpoints (four at most per request)."""
    if part is None:
        return None
    if isinstance(part, str):
        return _text_block(part) if part.strip() else None
    if not isinstance(part, dict):
        text = str(part)
        return _text_block(text) if text.strip() else None
    kind = part.get("type")
    if kind in ("text", "input_text", "output_text") or (kind is None and "text" in part):
        text = part.get("text")
        if not isinstance(text, str) or not text.strip():
            return None
        return _text_block(text)
    if kind == "image":
        if isinstance(part.get("source"), dict):
            src = part["source"]
            if src.get("type") == "base64":
                return _image_from_base64(src.get("media_type"), src.get("data"))
            if src.get("type") == "url":
                return _image_from_url(src.get("url"))
            if src.get("type") == "file" and src.get("file_id"):
                return {"type": "image", "source": {"type": "file", "file_id": src["file_id"]}}
            return _note("[An image was attached in a form the model cannot read.]")
        return _standard_file_block(part)
    if kind == "image_url":
        return _image_from_url(part.get("image_url"))
    if kind == "input_image":
        return _image_from_url(part.get("image_url") or part.get("url"))
    if kind == "document":
        return _document_block(part)
    if kind == "file":
        return _standard_file_block(part)
    if kind == "tool_result":
        # A tool result inside a user body (old checkpoints): its tool_use
        # is gone, so it can only travel as text.
        return _text_block(f"[tool result] {_text_of(part.get('content'))}"[:8000])
    if isinstance(part.get("text"), str) and part["text"].strip():
        return _text_block(part["text"])
    try:
        rendered = json.dumps(part, ensure_ascii=False, default=str)[:4000]
    except (TypeError, ValueError):
        rendered = str(part)[:4000]
    return _text_block(rendered)


def _user_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, list):
        blocks = [b for b in (_convert_part(p) for p in content) if b is not None]
    else:
        block = _convert_part(content if isinstance(content, str) else _text_of(content))
        blocks = [block] if block else []
    return blocks or [_text_block(EMPTY_MESSAGE_PLACEHOLDER)]


def safe_tool_id(raw: Any) -> str:
    """A tool id the API accepts (``^[a-zA-Z0-9_-]+$``): unchanged when it
    already is one, else a stable hash, so a ``tool_use`` and its result
    still agree. Pure."""
    text = str(raw or "")
    if TOOL_ID_RE.match(text):
        return text
    return "toolu_" + hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:24]


def _is_error_result(msg: ToolMessage) -> bool:
    if getattr(msg, "status", "success") == "error":
        return True
    return _text_of(msg.content).lstrip().startswith("Tool error:")


def _tool_result_block(msg: ToolMessage) -> Dict[str, Any]:
    content = msg.content
    if isinstance(content, list):
        parts = []
        for p in content:
            b = _convert_part(p)
            if b is not None and b.get("type") in ("text", "image", "document"):
                parts.append(b)
        body: Any = parts or NO_OUTPUT_PLACEHOLDER
    else:
        text = content if isinstance(content, str) else _text_of(content)
        body = text if text.strip() else NO_OUTPUT_PLACEHOLDER
    block: Dict[str, Any] = {"type": "tool_result", "tool_use_id": safe_tool_id(msg.tool_call_id), "content": body}
    if _is_error_result(msg):
        block["is_error"] = True
    return block


def _ai_text(msg: AIMessage) -> str:
    return _text_of(msg.content)


def _rebuilt_ai_blocks(msg: AIMessage) -> List[Dict[str, Any]]:
    """Text + tool calls: what an older turn (or a message without raw
    blocks) is sent as. Thinking and server tool blocks are not."""
    blocks: List[Dict[str, Any]] = []
    text = _ai_text(msg)
    if text.strip():
        blocks.append(_text_block(text))
    for tc in getattr(msg, "tool_calls", None) or []:
        args = tc.get("args")
        blocks.append({"type": "tool_use", "id": safe_tool_id(tc.get("id")), "name": tc.get("name") or "",
                       "input": args if isinstance(args, dict) else {}})
    return blocks


_THINKING_TYPES = ("thinking", "redacted_thinking")


def _after_fallback(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Echo rule after a server-side fallback: before the last ``fallback``
    block keep text and paired server-tool blocks only (thinking, client
    ``tool_use``, an unpaired ``server_tool_use`` and unknown model-internal
    blocks go); the ``fallback`` markers themselves are dropped."""
    last = max((i for i, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    if last < 0:
        return blocks
    before, after = blocks[:last], blocks[last + 1:]
    result_ids = {b.get("tool_use_id") for b in before
                  if str(b.get("type", "")).endswith("_tool_result") and b.get("tool_use_id")}
    kept_servers = {b.get("id") for b in before if b.get("type") == "server_tool_use" and b.get("id") in result_ids}
    kept: List[Dict[str, Any]] = []
    for b in before:
        kind = b.get("type")
        if kind == "text":
            kept.append(b)
        elif kind == "server_tool_use" and b.get("id") in kept_servers:
            kept.append(b)
        elif str(kind).endswith("_tool_result") and b.get("tool_use_id") in kept_servers:
            kept.append(b)
    return kept + [b for b in after if b.get("type") != "fallback"]


def _replay_blocks(msg: AIMessage) -> Optional[List[Dict[str, Any]]]:
    """The raw blocks of a current-turn message, as the API returned them,
    or None when they are missing or no longer match the tool calls the loop
    ran (then the message is rebuilt)."""
    raw = (getattr(msg, "additional_kwargs", None) or {}).get(CLAUDE_CONTENT_KEY)
    if not isinstance(raw, list) or not raw:
        return None
    blocks = [copy.deepcopy(b) for b in raw if isinstance(b, dict) and b.get("type")]
    blocks = _after_fallback(blocks)
    blocks = [b for b in blocks if not (b.get("type") == "text" and not str(b.get("text") or "").strip())]
    use_ids = sorted(str(b.get("id")) for b in blocks if b.get("type") == "tool_use")
    call_ids = sorted(str(tc.get("id")) for tc in (getattr(msg, "tool_calls", None) or []))
    if use_ids != call_ids:
        return None
    if not any(b.get("type") not in _THINKING_TYPES for b in blocks):
        return None
    return blocks


def _split_system(text: str) -> Tuple[str, str]:
    if STATIC_DYNAMIC_SPLIT in text:
        static, dynamic = text.split(STATIC_DYNAMIC_SPLIT, 1)
        return static, dynamic.replace(STATIC_DYNAMIC_SPLIT, "\n")
    return text, ""


def _cache_control(ttl: Optional[str], *, explicit: bool = False) -> Optional[Dict[str, Any]]:
    """A breakpoint for ``ttl`` (None when caching is off). The 5-minute TTL
    is the API default and is written out only where ``explicit``."""
    if ttl not in CACHE_TTLS:
        return None
    if ttl == "5m" and not explicit:
        return {"type": "ephemeral"}
    return {"type": "ephemeral", "ttl": ttl}


def supports_mid_conversation_system(model: Optional[str]) -> bool:
    family = model_family(model)
    return family in MID_CONVERSATION_SYSTEM_MODELS and family not in _LEARNED_NO_MID_SYSTEM


def _is_loop_note(message: BaseMessage) -> bool:
    try:
        from services.graph.history_window import is_scaffolding_note
    except Exception:  # noqa: BLE001
        return False
    return is_scaffolding_note(message)


def to_anthropic_messages(messages: Sequence[BaseMessage], *, model: Optional[str] = None,
                          cache_ttl: Optional[str] = "1h", tail_cache_ttl: Optional[str] = "5m",
                          mid_conversation_system: Optional[bool] = None
                          ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """LangChain messages → (``system`` blocks, ``messages``). Pure.

    * SystemMessages join into the top-level system prompt; the text before
      ``STATIC_DYNAMIC_SPLIT`` is one block with ``cache_control``
      (``cache_ttl``), the text after it a ``{"role": "system"}`` message
      right after the last human message (a second, uncached system block on
      a model without mid-conversation system messages).
    * An AIMessage after the last HumanMessage replays its raw blocks
      (``claude_content``); every other is rebuilt from text + tool calls.
    * ToolMessages become ``tool_result`` blocks in one user message right
      after the assistant turn that asked for them, ``is_error`` when the
      tool failed; a ``tool_use`` with no result gets an error result, a
      result with no ``tool_use`` before it is dropped.
    * The first message is a user message (a placeholder when the window
      starts mid-conversation) and the last is never an assistant prefill.
    * The last human message's last block carries the tail breakpoint.
    """
    system_texts: List[str] = []
    convo: List[BaseMessage] = []
    for m in messages:
        if isinstance(m, SystemMessage):
            text = _text_of(m.content)
            if text:
                system_texts.append(text)
        else:
            convo.append(m)
    static, dynamic = _split_system("\n\n".join(system_texts))
    dynamic = dynamic.strip()

    # The owner's last message — never a note the loop added (``history_window.
    # scaffolding_note``): a note moved this boundary, so every earlier
    # message of the turn lost its thinking and server-tool results and the
    # cached prefix changed (review 2026-10-11). A note rides as text in
    # the tool-result turn before it.
    last_human = max((i for i, m in enumerate(convo) if isinstance(m, HumanMessage) and not _is_loop_note(m)),
                     default=-1)

    # 1. One entry per message.
    entries: List[Dict[str, Any]] = []
    for i, m in enumerate(convo):
        if isinstance(m, HumanMessage):
            entries.append({"role": "user", "content": _user_blocks(m.content), "last_human": i == last_human})
        elif isinstance(m, AIMessage):
            blocks = _replay_blocks(m) if i > last_human else None
            if blocks is None:
                blocks = _rebuilt_ai_blocks(m)
            if blocks:
                entries.append({"role": "assistant", "content": blocks})
        elif isinstance(m, ToolMessage):
            entries.append({"role": "user", "content": [_tool_result_block(m)], "last_human": False})
        else:  # ChatMessage / FunctionMessage and friends: as user text
            text = _text_of(getattr(m, "content", ""))
            if text.strip():
                entries.append({"role": "user", "content": [_text_block(text)], "last_human": False})

    # 2. Pair tool results with the assistant turn before them; merge user turns.
    out: List[Dict[str, Any]] = []
    i = 0
    while i < len(entries):
        entry = entries[i]
        if entry["role"] == "assistant":
            out.append({"role": "assistant", "content": entry["content"]})
            use_ids = [b.get("id") for b in entry["content"] if b.get("type") == "tool_use"]
            i += 1
        else:
            use_ids = []
        results: List[Dict[str, Any]] = []
        others: List[Dict[str, Any]] = []
        is_last_human = False
        while i < len(entries) and entries[i]["role"] == "user":
            for b in entries[i]["content"]:
                (results if b.get("type") == "tool_result" else others).append(b)
            is_last_human = is_last_human or entries[i].get("last_human", False)
            i += 1
        answered: List[Dict[str, Any]] = []
        seen: set = set()
        for r in results:
            rid = r.get("tool_use_id")
            if rid in use_ids and rid not in seen:
                seen.add(rid)
                answered.append(r)
        for uid in use_ids:
            if uid not in seen:
                answered.append({"type": "tool_result", "tool_use_id": uid, "content": NO_TOOL_RESULT_TEXT,
                                 "is_error": True})
        content = answered + others
        if content:
            out.append({"role": "user", "content": content, "last_human": is_last_human})

    # 3. Starts with a user turn; never ends on an assistant prefill.
    if out and out[0]["role"] != "user":
        out.insert(0, {"role": "user", "content": [_text_block(EARLIER_CONVERSATION_PLACEHOLDER)],
                       "last_human": False})
    while out and out[-1]["role"] == "assistant":
        out.pop()
    if not out:
        out.append({"role": "user", "content": [_text_block(NO_MESSAGE_PLACEHOLDER)], "last_human": False})

    # 4. The breakpoint on the owner's last message and the per-turn context after it.
    anchor = max((k for k, e in enumerate(out) if e.get("last_human")), default=-1)
    if anchor < 0:
        anchor = max(k for k, e in enumerate(out) if e["role"] == "user")
    tail = _cache_control(tail_cache_ttl)
    if tail is not None and last_human >= 0 and out[anchor].get("last_human"):
        last_block = out[anchor]["content"][-1]
        out[anchor]["content"][-1] = {**last_block, "cache_control": dict(tail)}

    api_messages: List[Dict[str, Any]] = [{"role": e["role"], "content": e["content"]} for e in out]
    system: List[Dict[str, Any]] = []
    if static.strip():
        block: Dict[str, Any] = _text_block(static)
        cc = _cache_control(cache_ttl, explicit=True)
        if cc is not None:
            block["cache_control"] = cc
        system.append(block)
    if dynamic:
        mid = supports_mid_conversation_system(model) if mid_conversation_system is None else mid_conversation_system
        if mid:
            api_messages.insert(anchor + 1, {"role": "system", "content": dynamic})
        else:
            system.append(_text_block(dynamic))
    return system, api_messages


_TOOL_BLOCK_KINDS = ("tool_use", "server_tool_use", "mcp_tool_use", "tool_result")


def _has_tool_blocks(api_messages: List[Dict[str, Any]]) -> bool:
    for m in api_messages:
        content = m.get("content")
        if isinstance(content, list):
            for b in content:
                kind = str(b.get("type") or "")
                if kind in _TOOL_BLOCK_KINDS or kind.endswith("_tool_result"):
                    return True
    return False


def _render_json(value: Any, limit: int = 2000) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)[:limit]
    except (TypeError, ValueError):
        return str(value)[:limit]


def without_tool_blocks(api_messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """The conversation with every tool block written out as text, and no
    thinking. For a request that binds no tools: the API refuses
    ``tool_use`` / ``tool_result`` blocks without tool definitions. A
    safety net — the tool loops' closing summary call keeps its tools and
    sends ``tool_choice="none"`` instead. Pure."""
    names: Dict[str, str] = {}
    out: List[Dict[str, Any]] = []
    for m in api_messages:
        content = m.get("content")
        if not isinstance(content, list):
            out.append(m)
            continue
        blocks: List[Dict[str, Any]] = []
        for b in content:
            kind = str(b.get("type") or "")
            if kind in _THINKING_TYPES or kind == "fallback":
                continue
            if kind in ("tool_use", "server_tool_use", "mcp_tool_use"):
                names[str(b.get("id"))] = str(b.get("name") or "a tool")
                blocks.append(_text_block(f"[I called {b.get('name')} with {_render_json(b.get('input'))}]"))
            elif kind == "tool_result":
                name = names.get(str(b.get("tool_use_id")), "a tool")
                body = b.get("content")
                images = [p for p in body if isinstance(p, dict) and p.get("type") == "image"] if isinstance(body, list) else []
                text = _text_of(body) if isinstance(body, list) else str(body or "")
                label = "Error from" if b.get("is_error") else "Result of"
                blocks.append(_text_block(f"[{label} {name}] {text or NO_OUTPUT_PLACEHOLDER}"))
                blocks.extend(images)
            elif kind.endswith("_tool_result"):
                name = names.get(str(b.get("tool_use_id")), "a server tool")
                blocks.append(_text_block(f"[{name} returned {_render_json(b.get('content'), 1500)}]"))
            else:
                blocks.append({k: v for k, v in b.items()})
        if blocks:
            out.append({**m, "content": blocks})
    while out and out[-1].get("role") == "assistant":
        out.pop()
    return out


def strip_claude_thinking(messages: List[BaseMessage]) -> List[BaseMessage]:
    """Drop ``claude_content`` (thinking signatures, server tool payloads)
    from every AIMessage before the last HumanMessage. Pure: copies what it
    changes. The twin of ``codex_chat_model.strip_reasoning_items`` — older
    turns are rebuilt from text and tool calls, so their raw blocks are
    dead weight in a checkpoint."""
    last_human = max((i for i, m in enumerate(messages) if isinstance(m, HumanMessage)), default=-1)
    out: List[BaseMessage] = []
    for i, msg in enumerate(messages):
        if i < last_human and isinstance(msg, AIMessage):
            ak = getattr(msg, "additional_kwargs", None) or {}
            if CLAUDE_CONTENT_KEY in ak:
                kept = {k: v for k, v in ak.items() if k != CLAUDE_CONTENT_KEY}
                msg = msg.model_copy(update={"additional_kwargs": kept})
        out.append(msg)
    return out


# ================================================================== tools ==

def _object_schema(schema: Any) -> Dict[str, Any]:
    """An ``input_schema`` the API accepts: an object at the root. A union
    at the root (MCP servers write "one of these is required" there) is
    flattened the way the Codex adapter does it."""
    if not isinstance(schema, dict):
        return {"type": "object", "properties": {}}
    if any(k in schema for k in ("anyOf", "oneOf", "allOf")):
        from services.codex_chat_model import _ensure_object_schema, _sanitize_schema

        return _ensure_object_schema(_sanitize_schema(schema))
    out = dict(schema)
    if "type" not in out:
        out["type"] = "object"
    if out.get("type") != "object":
        return {"type": "object", "properties": {"input": out}, "required": []}
    if not isinstance(out.get("properties"), dict):
        out["properties"] = {}
    return out


def _from_openai_function(fn: Dict[str, Any]) -> Dict[str, Any]:
    return {"name": str(fn.get("name") or ""), "description": str(fn.get("description") or ""),
            "input_schema": _object_schema(fn.get("parameters"))}


def _anthropic_spec(tool: Any, model: Optional[str]) -> Optional[Dict[str, Any]]:
    """The Anthropic-defined tool a LangChain tool stands for, if any
    (2026-10-11): ``metadata["anthropic_tool_resolver"]``
    (``"module:function"``, called with the tool's name and the model —
    flags and per-model versions are read per bind), else a static
    ``metadata["anthropic_tool"]`` dict. None binds the tool's own schema
    (its local implementation runs)."""
    meta = getattr(tool, "metadata", None)
    if not isinstance(meta, dict):
        return None
    resolver = meta.get("anthropic_tool_resolver")
    if isinstance(resolver, str) and ":" in resolver:
        mod_name, fn_name = resolver.split(":", 1)
        try:
            import importlib

            spec = getattr(importlib.import_module(mod_name), fn_name)(tool.name, model)
        except Exception as exc:  # noqa: BLE001 — a broken resolver binds the local tool
            _log_once(f"resolver:{resolver}", "ChatClaude: tool resolver %s failed (%s); binding %s's schema",
                      resolver, exc, getattr(tool, "name", "?"))
            return None
        return dict(spec) if isinstance(spec, dict) else None
    spec = meta.get("anthropic_tool")
    return dict(spec) if isinstance(spec, dict) and spec.get("type") else None


def _convert_tool(tool: Any, model: Optional[str] = None) -> Optional[Dict[str, Any]]:
    if isinstance(tool, dict):
        kind = tool.get("type")
        if "input_schema" in tool and kind in (None, "custom"):
            out = {"name": str(tool.get("name") or ""), "description": str(tool.get("description") or ""),
                   "input_schema": _object_schema(tool.get("input_schema"))}
            for key in ("strict", "eager_input_streaming", "defer_loading", "input_examples"):
                if key in tool:
                    out[key] = tool[key]
            return out
        if kind == "function" and isinstance(tool.get("function"), dict):
            return _from_openai_function(tool["function"])
        if kind == "function" and tool.get("name"):
            return _from_openai_function(tool)
        if kind is None and tool.get("name") and "parameters" in tool:
            return _from_openai_function(tool)
        # An Anthropic-defined tool (web_search_20260209, bash_20250124,
        # text_editor_20250728, …): the API knows its schema.
        return dict(tool)
    from langchain_core.tools import BaseTool
    from langchain_core.utils.function_calling import convert_to_openai_tool

    if isinstance(tool, BaseTool):
        native = _anthropic_spec(tool, model)
        if native is not None:
            return native
        args_schema = getattr(tool, "args_schema", None)
        if isinstance(args_schema, dict) and args_schema.get("properties"):
            # langchain-mcp-adapters keeps the server's inputSchema here.
            schema = args_schema
        else:
            try:
                schema = convert_to_openai_tool(tool)["function"].get("parameters")
            except Exception:  # noqa: BLE001
                schema = tool.get_input_schema().model_json_schema()
        return {"name": tool.name, "description": tool.description or "", "input_schema": _object_schema(schema)}
    if not isinstance(tool, type) and isinstance(getattr(tool, "name", None), str) and not callable(tool):
        # Duck-typed tools (tests' fakes): a name and a description.
        args_schema = getattr(tool, "args_schema", None)
        return {"name": tool.name, "description": str(getattr(tool, "description", "") or ""),
                "input_schema": _object_schema(args_schema if isinstance(args_schema, dict) else None)}
    try:
        return _from_openai_function(convert_to_openai_tool(tool)["function"])
    except Exception as exc:  # noqa: BLE001
        logger.warning("ChatClaude: cannot convert tool %r (%s); not sent", tool, exc)
        return None


def convert_tools(tools: Iterable[Any], model: Optional[str] = None) -> List[Dict[str, Any]]:
    """LangChain tools, pydantic models, OpenAI-style dicts and raw
    Anthropic tool dicts → the ``tools`` array, sorted by name so the
    prompt prefix (tools render first) is byte-identical turn to turn.
    ``model`` picks the version of a tool that stands for an
    Anthropic-defined one (``_anthropic_spec``)."""
    out = [t for t in (_convert_tool(tool, model) for tool in (tools or [])) if t]
    out.sort(key=lambda t: str(t.get("name") or t.get("type") or ""))
    return out


def _server_tools_for_model(tools: List[Dict[str, Any]], model: Optional[str]) -> List[Dict[str, Any]]:
    """Server web tools in a version ``model`` accepts (Haiku has no
    dynamic filtering): ``services.toolset.web.map_server_tools_for_model``."""
    try:
        from services.toolset.web import map_server_tools_for_model
    except Exception:  # noqa: BLE001
        return tools
    return map_server_tools_for_model(tools, model)


def _normalize_tool_choice(choice: Any) -> Tuple[Optional[str], Optional[str]]:
    """LangChain / OpenAI / Anthropic spellings → (kind, name), kind one of
    auto | none | any | tool | None. Pure."""
    if choice is None or choice is False:
        return None, None
    if choice is True:
        return "any", None
    if isinstance(choice, str):
        low = choice.strip().lower()
        if low in ("auto", "none"):
            return low, None
        if low in ("any", "required"):
            return "any", None
        return "tool", choice
    if isinstance(choice, dict):
        name = choice.get("name") or (choice.get("function") or {}).get("name")
        if name:
            return "tool", str(name)
        kind = str(choice.get("type") or "").lower()
        if kind in ("auto", "none"):
            return kind, None
        if kind in ("any", "required"):
            return "any", None
    return None, None


# ================================================================= stream ==

async def _lines_until(lines: AsyncIterator[str], deadline: float) -> AsyncIterator[str]:
    """Pass lines through until the monotonic ``deadline``; then raise. The
    wait runs in the caller's task, so the stream is never resumed from
    another one."""
    it = lines.__aiter__()
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise _deadline_error(deadline)
        try:
            async with asyncio.timeout(remaining):
                line = await it.__anext__()
        except StopAsyncIteration:
            return
        except TimeoutError:
            raise _deadline_error(deadline) from None
        yield line


async def _byte_lines(chunks: AsyncIterator[bytes]) -> AsyncIterator[str]:
    """Raw response bytes → SSE lines, split on ``\\r``, ``\\n`` and
    ``\\r\\n`` only, each decoded whole.

    httpx's ``aiter_lines`` decodes first and splits with
    ``str.splitlines``, which also breaks on U+2028, U+2029 and U+0085 —
    characters JSON carries unescaped inside a string. A ``data:`` line cut
    there no longer parsed and its delta was dropped without a word (review
    2026-10-11). ``bytes.splitlines`` knows only the ASCII line ends, the
    way the SDK's own SSE decoder splits before decoding. A ``\\r\\n`` cut
    between two chunks is one line end, not two."""
    buf = b""
    skip_lf = False
    async for chunk in chunks:
        if not chunk:
            continue
        if skip_lf:
            skip_lf = False
            if chunk[:1] == b"\n":
                chunk = chunk[1:]
                if not chunk:
                    continue
        if b"\n" not in chunk and b"\r" not in chunk:
            buf += chunk
            continue
        parts = (buf + chunk).splitlines(keepends=True)
        buf = b""
        last = len(parts) - 1
        for k, part in enumerate(parts):
            if not part.endswith((b"\n", b"\r")):
                buf = part  # the unfinished tail (always the last part)
                break
            if k == last and part.endswith(b"\r"):
                skip_lf = True  # its LF may open the next chunk
            yield part.rstrip(b"\r\n").decode("utf-8", "replace")
    if buf:
        yield buf.decode("utf-8", "replace")


async def _sse_events(lines: AsyncIterator[str]) -> AsyncIterator[Dict[str, Any]]:
    """Raw SSE lines → event dicts (the ``data:`` JSON). Malformed data is
    skipped; ``event:`` lines are redundant with the payload's ``type``."""
    data: List[str] = []

    def _flush() -> Optional[Dict[str, Any]]:
        if not data:
            return None
        raw = "\n".join(data)
        data.clear()
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return None
        return parsed if isinstance(parsed, dict) else None

    async for line in lines:
        line = line.rstrip("\r\n")
        if not line:
            ev = _flush()
            if ev is not None:
                yield ev
            continue
        if line.startswith(":"):
            continue
        if line.startswith("data:"):
            value = line[5:]
            data.append(value[1:] if value.startswith(" ") else value)
    ev = _flush()
    if ev is not None:
        yield ev


_TOOL_BLOCK_TYPES = ("tool_use", "server_tool_use", "mcp_tool_use")


class _Segment:
    """One HTTP response, folded from its events."""

    def __init__(self, offset: int = 0):
        self.offset = offset
        self.blocks: Dict[int, Dict[str, Any]] = {}
        self.partial: Dict[int, str] = {}
        self.closed: set = set()
        self.usage: Dict[str, Any] = {}
        self.stop_reason: Optional[str] = None
        self.stop_sequence: Optional[str] = None
        self.stop_details: Any = None
        self.model: Optional[str] = None
        self.id: Optional[str] = None
        self.request_id: Optional[str] = None
        self.stopped = False
        self.input_transformations: List[Any] = []
        self.container: Any = None
        # What this request cost (``llm_spend.record_claude_call``).
        self.cost_usd: float = 0.0

    def apply(self, ev: Dict[str, Any]) -> List[Tuple[str, Any]]:
        etype = ev.get("type")
        if etype == "message_start":
            m = ev.get("message") or {}
            self.id = m.get("id")
            self.model = m.get("model")
            self.usage = dict(m.get("usage") or {})
            self._note_transformations(m.get("input_transformations"))
            if m.get("container"):
                self.container = m.get("container")
            return []
        if etype == "content_block_start":
            index = _n(ev.get("index"))
            block = dict(ev.get("content_block") or {})
            self.blocks[index] = block
            if block.get("type") in _TOOL_BLOCK_TYPES:
                self.partial[index] = ""
            # Text that arrives with the block (not as a delta) still streams.
            if block.get("type") == "text" and isinstance(block.get("text"), str) and block["text"]:
                return [("text", block["text"])]
            if block.get("type") == "thinking" and isinstance(block.get("thinking"), str) and block["thinking"]:
                return [("reasoning", block["thinking"])]
            return []
        if etype == "content_block_delta":
            index = _n(ev.get("index"))
            block = self.blocks.get(index)
            delta = ev.get("delta") or {}
            if block is None:
                return []
            dtype = delta.get("type")
            if dtype == "text_delta":
                piece = str(delta.get("text") or "")
                block["text"] = str(block.get("text") or "") + piece
                return [("text", piece)] if piece else []
            if dtype == "thinking_delta":
                piece = str(delta.get("thinking") or "")
                block["thinking"] = str(block.get("thinking") or "") + piece
                return [("reasoning", piece)] if piece else []
            if dtype == "signature_delta":
                block["signature"] = str(block.get("signature") or "") + str(delta.get("signature") or "")
                return []
            if dtype == "input_json_delta":
                piece = str(delta.get("partial_json") or "")
                self.partial[index] = self.partial.get(index, "") + piece
                if piece and block.get("type") == "tool_use":
                    return [("tool_args", {"id": block.get("id") or "", "name": block.get("name") or "",
                                           "delta": piece, "index": self.offset + index})]
                return []
            if dtype == "citations_delta":
                citation = delta.get("citation")
                if citation is not None:
                    if not isinstance(block.get("citations"), list):
                        block["citations"] = []
                    block["citations"].append(citation)
                return []
            return []
        if etype == "content_block_stop":
            index = _n(ev.get("index"))
            self._close(index)
            return []
        if etype == "message_delta":
            delta = ev.get("delta") or {}
            if "stop_reason" in delta:
                self.stop_reason = delta.get("stop_reason")
            if "stop_sequence" in delta:
                self.stop_sequence = delta.get("stop_sequence")
            if delta.get("stop_details") is not None:
                self.stop_details = delta.get("stop_details")
            for key, value in (ev.get("usage") or {}).items():
                if value is not None:
                    self.usage[key] = value
            self._note_transformations(ev.get("input_transformations"))
            return []
        if etype == "message_stop":
            self.stopped = True
            return []
        if etype == "error":
            err = ev.get("error") if isinstance(ev.get("error"), dict) else {}
            raise LLMProviderError(str(err.get("type") or "stream_error"), str(err.get("message") or ""),
                                   provider=PROVIDER, request_id=self.request_id)
        return []  # ping, and anything newer than this adapter

    def _note_transformations(self, items: Any) -> None:
        if isinstance(items, list) and items:
            self.input_transformations = list(items)

    def _close(self, index: int) -> None:
        block = self.blocks.get(index)
        if block is None:
            return
        if index in self.partial:
            raw = self.partial.pop(index)
            if raw.strip():
                block["input"] = _parse_arguments(raw)
            elif not isinstance(block.get("input"), dict):
                block["input"] = {}
        self.closed.add(index)

    def content(self) -> List[Tuple[int, bool, Dict[str, Any]]]:
        """(index in the turn, closed, block) in order, ``None`` values dropped."""
        out = []
        for index in sorted(self.blocks):
            if index in self.partial:  # never closed: a refusal or max_tokens cut it
                raw = self.partial.pop(index)
                parsed = _parse_arguments(raw) if raw.strip() else {}
                self.blocks[index]["input"] = {} if INVALID_JSON_KEY in parsed else parsed
            block = {k: v for k, v in self.blocks[index].items() if v is not None}
            out.append((self.offset + index, index in self.closed, block))
        return out


@dataclass
class _TurnResult:
    blocks: List[Dict[str, Any]] = field(default_factory=list)
    text: str = ""
    tool_calls: List[Tuple[int, Dict[str, Any]]] = field(default_factory=list)
    reasoning: List[str] = field(default_factory=list)
    usage: Dict[str, Any] = field(default_factory=dict)
    # Every attempt's tokens (``billed_usage``): what usage_metadata reports.
    billed: Dict[str, Any] = field(default_factory=dict)
    stop_reason: Optional[str] = None
    stop_sequence: Optional[str] = None
    stop_details: Any = None
    model: Optional[str] = None
    requested_model: Optional[str] = None
    id: Optional[str] = None
    request_id: Optional[str] = None
    input_transformations: List[Any] = field(default_factory=list)
    container: Any = None
    requests: int = 1
    # The sum of each request's own price (never the price of ``usage``).
    cost_usd: float = 0.0

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


def _turn_result(segments: List[_Segment], indexed: List[Tuple[int, bool, Dict[str, Any]]],
                 requested_model: Optional[str] = None) -> _TurnResult:
    last = segments[-1]
    blocks = [b for _, _, b in indexed]
    fallback_at = max((k for k, b in enumerate(blocks) if b.get("type") == "fallback"), default=-1)
    calls: List[Tuple[int, Dict[str, Any]]] = []
    if last.stop_reason not in ("refusal", "max_tokens"):
        for k, (index, closed, block) in enumerate(indexed):
            if k > fallback_at and block.get("type") == "tool_use" and closed:
                args = block.get("input") if isinstance(block.get("input"), dict) else {}
                calls.append((index, {"name": block.get("name") or "", "args": args, "id": block.get("id") or ""}))
    transformations: List[Any] = []
    for seg in segments:
        transformations.extend(seg.input_transformations)
    return _TurnResult(
        blocks=blocks,
        text="".join(str(b.get("text") or "") for b in blocks if b.get("type") == "text"),
        tool_calls=calls,
        reasoning=[str(b.get("thinking")) for b in blocks if b.get("type") == "thinking" and str(b.get("thinking") or "").strip()],
        usage=_sum_usage([s.usage for s in segments]),
        billed=_sum_usage([billed_usage(s.usage) for s in segments]),
        stop_reason=last.stop_reason,
        stop_sequence=last.stop_sequence,
        stop_details=last.stop_details,
        model=last.model,
        requested_model=requested_model,
        id=last.id,
        request_id=last.request_id,
        input_transformations=transformations,
        container=next((s.container for s in reversed(segments) if s.container), None),
        requests=len(segments),
        cost_usd=sum(s.cost_usd for s in segments),
    )


def _deadline_error(deadline: Optional[float] = None) -> LLMProviderError:
    return LLMProviderError("request_timeout", "no complete answer before the request deadline",
                            retryable=False, provider=PROVIDER)


def _from_sdk_error(exc: BaseException) -> Optional[LLMProviderError]:
    """An SDK or transport exception as an ``LLMProviderError`` (None when
    it is neither)."""
    try:
        import anthropic
    except Exception:  # noqa: BLE001
        anthropic = None  # type: ignore[assignment]
    import httpx

    if anthropic is not None and isinstance(exc, anthropic.APIStatusError):
        body = getattr(exc, "body", None)
        err = body.get("error") if isinstance(body, dict) and isinstance(body.get("error"), dict) else {}
        code = str(err.get("type") or f"http_{exc.status_code}")
        message = str(err.get("message") or getattr(exc, "message", "") or exc)
        return LLMProviderError(code, message, http_status=exc.status_code, provider=PROVIDER,
                                request_id=getattr(exc, "request_id", None), body=body)
    if anthropic is not None and isinstance(exc, anthropic.APITimeoutError):
        return LLMProviderError("timeout_error", str(exc) or "request timed out", retryable=True, provider=PROVIDER)
    if anthropic is not None and isinstance(exc, anthropic.APIConnectionError):
        return LLMProviderError("connection_error", str(exc) or "connection failed", retryable=True, provider=PROVIDER)
    if isinstance(exc, httpx.TransportError):
        return LLMProviderError("connection_error", f"{type(exc).__name__}: {exc}", retryable=True, provider=PROVIDER)
    if anthropic is not None and isinstance(exc, anthropic.AnthropicError):
        return LLMProviderError("client_error", str(exc), retryable=False, provider=PROVIDER)
    return None


def _is_mid_system_rejection(exc: LLMProviderError) -> bool:
    text = f"{exc.message} {exc}".lower()
    return exc.http_status == 400 and "system" in text and "not supported" in text and "role" in text


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()[:8]


def cache_line(kwargs: Dict[str, Any], row: Dict[str, Any], usage: Dict[str, Any], stop_reason: Optional[str],
               request_id: Optional[str]) -> str:
    """The ``[claude-cache]`` log line for one request: ``row["model"]`` is
    the model that served it, ``kwargs["model"]`` the one asked for (named
    as ``requested=`` when a fallback served it); the counts and the price
    cover every attempt (``billed_usage``). Pure."""
    system = kwargs.get("system") or []
    static = "".join(b.get("text", "") for b in system[:1] if isinstance(b, dict))
    tools = json.dumps(kwargs.get("tools") or [], sort_keys=True, default=str)
    requested = kwargs.get("model") or row.get("model")
    served = row.get("model") or requested
    billed = billed_usage(usage)
    # The price ``llm_spend.record_claude_call`` wrote to ``llm_calls``.
    cost = row.get("cost_usd")
    if cost is None:
        cost = estimate_cost_usd(served, usage, requested_model=requested)
    cost_text = f"{cost:.4f}" if cost is not None else "-"
    fallback = (f" requested={requested}" if requested and served
                and model_family(requested) != model_family(served) else "")
    return (f"[claude-cache] purpose={row.get('purpose') or '-'} model={served or '-'}{fallback} "
            f"status={row.get('status') or '-'} stop={stop_reason or '-'} "
            f"in={_n(billed.get('input_tokens'))} read={_n(billed.get('cache_read_input_tokens'))} "
            f"write={_n(billed.get('cache_creation_input_tokens'))} out={_n(billed.get('output_tokens'))} "
            f"usd={cost_text} sys={len(static)}:{_short_hash(static)} "
            f"tools={len(kwargs.get('tools') or [])}:{_short_hash(tools)} req={request_id or '-'}")


# =================================================================== model ==

_CLIENTS: Dict[Tuple[int, int, str], Tuple[Any, Any]] = {}


def _anthropic_client(max_retries: int) -> Any:
    """One ``AsyncAnthropic`` per event loop (its connection pool belongs to
    the loop that opened it) and per key."""
    api_key = os.environ.get("ANTHROPIC_API_KEY") or ""
    if not api_key:
        raise LLMProviderError("no_api_key", "ANTHROPIC_API_KEY is not set", retryable=False, provider=PROVIDER)
    loop = asyncio.get_running_loop()
    key = (id(loop), int(max_retries), _short_hash(api_key))
    entry = _CLIENTS.get(key)
    if entry is not None and entry[0]() is loop:
        return entry[1]
    for stale in [k for k, (ref, _) in _CLIENTS.items() if ref() is None]:
        _CLIENTS.pop(stale, None)
    import anthropic
    import httpx

    client = anthropic.AsyncAnthropic(
        api_key=api_key, max_retries=max_retries,
        timeout=httpx.Timeout(PER_READ_TIMEOUT_SECONDS, connect=CONNECT_TIMEOUT_SECONDS),
    )
    _CLIENTS[key] = (weakref.ref(loop), client)
    return client


def request_timeout_seconds() -> float:
    raw = os.getenv("CLAUDE_REQUEST_TIMEOUT_SECONDS")
    try:
        value = float(raw) if raw else DEFAULT_REQUEST_TIMEOUT_SECONDS
    except (TypeError, ValueError):
        return DEFAULT_REQUEST_TIMEOUT_SECONDS
    return value if value > 0 else DEFAULT_REQUEST_TIMEOUT_SECONDS


class ChatClaude(BaseChatModel):
    """LangChain chat model over the Anthropic Messages API (see module doc)."""

    model: str = "claude-opus-5-5"
    # low | medium | high | xhigh | max; None = the model's default.
    effort: Optional[str] = "medium"
    # Room for thinking and the reply: thinking counts against it.
    max_tokens: int = DEFAULT_MAX_TOKENS
    # "summarized" (the work rail shows reasoning) or "omitted".
    thinking_display: str = "summarized"
    # Total deadline for one call, the pause_turn continuations and the one
    # retry included; None = CLAUDE_REQUEST_TIMEOUT_SECONDS (300).
    timeout: Optional[float] = None
    max_retries: int = 2
    # The spend ledger's labels (``call_purpose.stamp`` sets ``purpose``).
    purpose: str = ""
    priority: str = "interactive"
    # TTL of the static system block's breakpoint, and of the tail's.
    cache_ttl: Optional[str] = "1h"
    tail_cache_ttl: Optional[str] = "5m"
    # Accepted for builder compatibility; never sent (5.5 models reject it).
    temperature: Optional[float] = None
    bound_tools: List[Dict[str, Any]] = Field(default_factory=list)
    tool_choice: Any = None
    # One tool call per response unless a caller opts in (as on Codex: the
    # browser's steps depend on order).
    parallel_tool_calls: Optional[bool] = False
    eager_input_streaming: bool = False
    retry_backoff: Tuple[float, float] = RETRY_BACKOFF_S
    # An AsyncAnthropic-like client (tests inject a fake); None = the shared one.
    client: Any = Field(default=None, exclude=True)

    model_config = {"arbitrary_types_allowed": True}

    @property
    def _llm_type(self) -> str:
        return "claude-messages"

    @property
    def _identifying_params(self) -> Dict[str, Any]:
        return {"model": self.model, "effort": self.effort}

    # ------------------------------------------------------------- tools --

    def bind_tools(self, tools: Sequence[Any], *, tool_choice: Any = None, **kwargs: Any) -> "ChatClaude":
        update: Dict[str, Any] = {"bound_tools": convert_tools(tools, self.model)}
        if tool_choice is not None:
            update["tool_choice"] = tool_choice
        if "parallel_tool_calls" in kwargs:
            update["parallel_tool_calls"] = kwargs["parallel_tool_calls"]
        return self.model_copy(update=update)

    def _tool_choice_param(self) -> Optional[Dict[str, Any]]:
        if not self.bound_tools:
            return None
        kind, name = _normalize_tool_choice(self.tool_choice)
        if kind == "none":
            return {"type": "none"}
        family = model_family(self.model)
        if kind in ("any", "tool") and family in NO_FORCED_TOOL_CHOICE_MODELS:
            _log_once(f"force:{family}", "ChatClaude: %s rejects a forced tool_choice; sending auto", family)
            kind, name = "auto", None
        if kind == "tool" and name not in {t.get("name") for t in self.bound_tools}:
            logger.warning("ChatClaude: tool_choice names %r, which is not bound; sending auto", name)
            kind, name = "auto", None
        parallel_off = self.parallel_tool_calls is False
        if kind is None:
            if not parallel_off:
                return None
            kind = "auto"
        out: Dict[str, Any] = {"type": kind}
        if kind == "tool":
            out["name"] = name
        if parallel_off:
            out["disable_parallel_tool_use"] = True
        return out

    # ----------------------------------------------------------- request --

    def _effort(self) -> Optional[str]:
        effort = (self.effort or "").strip().lower()
        if effort == "minimal":
            return "low"
        return effort if effort in EFFORTS else None

    def _tail_ttl(self) -> Optional[str]:
        tail = self.tail_cache_ttl if self.tail_cache_ttl in CACHE_TTLS else None
        if tail == "1h" and self.cache_ttl == "5m":
            return "5m"  # a 1-hour entry may not follow a 5-minute one
        return tail

    def _request_kwargs(self, messages: Sequence[BaseMessage], *,
                        mid_conversation_system: Optional[bool] = None) -> Dict[str, Any]:
        tail = self._tail_ttl()
        system, api_messages = to_anthropic_messages(
            messages, model=self.model, cache_ttl=self.cache_ttl if self.cache_ttl in CACHE_TTLS else None,
            tail_cache_ttl=tail, mid_conversation_system=mid_conversation_system,
        )
        if not self.bound_tools and _has_tool_blocks(api_messages):
            api_messages = without_tool_blocks(api_messages)
        kwargs: Dict[str, Any] = {"model": self.model, "max_tokens": int(self.max_tokens), "messages": api_messages}
        if system:
            kwargs["system"] = system
        if self.bound_tools:
            tools = _server_tools_for_model([dict(t) for t in self.bound_tools], self.model)
            if self.eager_input_streaming:
                for t in tools:
                    if "input_schema" in t:
                        t["eager_input_streaming"] = True
            kwargs["tools"] = tools
        choice = self._tool_choice_param()
        if choice is not None:
            kwargs["tool_choice"] = choice
        display = self.thinking_display if self.thinking_display in THINKING_DISPLAYS else "summarized"
        body: Dict[str, Any] = {
            "thinking": {"type": "adaptive", "display": display,
                         "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
        }
        effort = self._effort()
        if effort:
            body["output_config"] = {"effort": effort}
        tail_cc = _cache_control(tail)
        if tail_cc is not None:
            body["cache_control"] = tail_cc
        betas = [BETA_THINKING_BINDING]
        if model_family(self.model) in FALLBACK_MODELS:
            body["fallbacks"] = "default"
            betas.append(BETA_SERVER_FALLBACK)
        kwargs["extra_body"] = body
        kwargs["extra_headers"] = {"anthropic-beta": ",".join(betas)}
        return kwargs

    def _call_context(self) -> Dict[str, Any]:
        """What the ledger row knows before the request goes out."""
        try:
            from services.call_purpose import current_purpose

            scoped = current_purpose()
        except Exception:  # noqa: BLE001
            scoped = None
        ctx: Dict[str, Any] = {"purpose": scoped or self.purpose or None, "priority": self.priority,
                               "model": self.model, "effort": self._effort()}
        ctx.update(_turn_context())
        if not ctx["purpose"]:
            try:
                from services.event_ledger import caller_label

                ctx["purpose"] = caller_label()
            except Exception:  # noqa: BLE001
                pass
        return ctx

    def _client(self) -> Any:
        return self.client if self.client is not None else _anthropic_client(self.max_retries)

    def _deadline(self) -> float:
        seconds = self.timeout if (self.timeout and self.timeout > 0) else request_timeout_seconds()
        return time.monotonic() + seconds

    # ----------------------------------------------------------- the call --

    async def _admit(self, ctx: Dict[str, Any], first: bool) -> None:
        """Before an HTTP request: may it go out under today's Claude spend
        cap? Raises ``LLMRateLimitDeferred`` (background) or
        ``llm_spend.LLMSpendCapReached`` (the owner's turns, Edward's own) when
        not. The first request of a call goes through the rate gate's
        ``acquire_spend`` (background keeps its stampede spacing); a
        continuation or the retry only checks the cap again."""
        from services import llm_spend

        purpose = ctx.get("purpose") or None
        if first:
            from services.llm_rate_gate import get_llm_rate_gate

            await get_llm_rate_gate().acquire_spend(self.priority, purpose=purpose or "")
        else:
            await llm_spend.check_spend(self.priority, purpose=purpose)

    def _record(self, seg: "_Segment", ctx: Dict[str, Any], row: Dict[str, Any]) -> float:
        """Price one request into ``llm_calls``; never raises."""
        try:
            from services import llm_spend

            ok = row.get("status") == "ok"
            error = None
            if not ok:
                error = ": ".join(str(p) for p in (row.get("error_class") or row.get("status"), row.get("error"))
                                  if p)
            return llm_spend.record_claude_call(
                self.model, seg.usage, purpose=ctx.get("purpose"), priority=self.priority,
                latency_ms=row.get("latency_ms"), ttft_ms=row.get("ttft_ms"), ok=ok, error=error,
                conversation_id=ctx.get("conversation_id"), served_model=seg.model,
            )
        except Exception as exc:  # noqa: BLE001 — the ledger never breaks a call
            logger.debug("claude spend record failed: %s", exc)
            return 0.0

    async def _stream_once(self, kwargs: Dict[str, Any], deadline: float, ctx: Dict[str, Any],
                           offset: int, *, first: bool = False) -> AsyncIterator[Tuple[str, Any]]:
        """One HTTP request → ("text"|"reasoning"|"tool_args", …) items, then
        ("done", segment). The spend cap is asked first (a refusal raises
        before anything is sent or recorded); after that it writes exactly
        one ``llm_calls`` row, whatever happens."""
        await self._admit(ctx, first)
        started = time.monotonic()
        row: Dict[str, Any] = dict(ctx, status="error", error_class=None, http_status=None, ttft_ms=None)
        seg = _Segment(offset)
        try:
            client = self._client()
            async with contextlib.AsyncExitStack() as stack:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _deadline_error()
                try:
                    async with asyncio.timeout(remaining):
                        resp = await stack.enter_async_context(
                            client.messages.with_streaming_response.create(stream=True, **kwargs))
                except TimeoutError:
                    raise _deadline_error() from None
                row["http_status"] = getattr(resp, "status_code", None) or 200
                try:
                    seg.request_id = resp.headers.get("request-id")
                except Exception:  # noqa: BLE001
                    seg.request_id = None
                async for event in _sse_events(_lines_until(_byte_lines(resp.iter_bytes()), deadline)):
                    for item in seg.apply(event):
                        if row["ttft_ms"] is None and item[0] in ("text", "tool_args"):
                            row["ttft_ms"] = int((time.monotonic() - started) * 1000)
                        yield item
                if not seg.stopped:
                    raise LLMProviderError("stream_truncated", "the stream closed before message_stop",
                                           retryable=True, provider=PROVIDER, request_id=seg.request_id)
            row["status"] = "ok"
            yield ("done", seg)
        except LLMProviderError as exc:
            row["status"] = "timeout" if exc.code == "request_timeout" else (
                "rate_limited" if exc.http_status == 429 else "error")
            row["error_class"] = exc.code
            row["error"] = exc.message
            row["http_status"] = exc.http_status or row["http_status"]
            raise
        except (GeneratorExit, asyncio.CancelledError):
            row["status"] = "cancelled"
            row["error_class"] = "cancelled"
            raise
        except Exception as exc:
            converted = _from_sdk_error(exc)
            if converted is None:
                row["error_class"] = type(exc).__name__
                raise
            row["status"] = "rate_limited" if converted.http_status == 429 else "error"
            row["error_class"] = converted.code
            row["error"] = converted.message
            row["http_status"] = converted.http_status
            raise converted from exc
        finally:
            row["latency_ms"] = int((time.monotonic() - started) * 1000)
            if seg.model:
                row["model"] = seg.model  # the model that served it (a fallback may have)
            # Priced on this request's own usage, failures included (whatever
            # usage arrived before the failure is billed) — into ``llm_calls``
            # only, never ``codex_calls``.
            seg.cost_usd = self._record(seg, ctx, row)
            row["cost_usd"] = seg.cost_usd
            try:
                logger.info(cache_line(kwargs, row, seg.usage, seg.stop_reason, seg.request_id))
                if seg.input_transformations:
                    logger.info("[claude-cache] input_transformations=%s",
                                json.dumps(seg.input_transformations, default=str)[:500])
            except Exception as exc:  # noqa: BLE001
                logger.debug("claude cache line failed: %s", exc)

    async def _run_segments(self, kwargs: Dict[str, Any], deadline: float,
                            ctx: Dict[str, Any], *, first: bool = False) -> AsyncIterator[Tuple[str, Any]]:
        """A request and its ``pause_turn`` continuations → items, then
        ("final", _TurnResult). ``first``: the call's first request (the
        gate's admission rather than a re-check of the cap)."""
        segments: List[_Segment] = []
        indexed: List[Tuple[int, bool, Dict[str, Any]]] = []
        request = kwargs
        for n in range(MAX_PAUSE_CONTINUATIONS + 1):
            seg: Optional[_Segment] = None
            async for item in self._stream_once(request, deadline, ctx, offset=len(indexed), first=first and n == 0):
                if item[0] == "done":
                    seg = item[1]
                else:
                    yield item
            assert seg is not None
            segments.append(seg)
            indexed.extend(seg.content())
            if seg.stop_reason == "pause_turn" and n < MAX_PAUSE_CONTINUATIONS:
                request = dict(kwargs)
                request["messages"] = list(kwargs["messages"]) + [
                    {"role": "assistant", "content": copy.deepcopy([b for _, _, b in indexed])}]
                continue
            break
        yield ("final", _turn_result(segments, indexed, requested_model=kwargs.get("model") or self.model))

    async def _pause_before_retry(self, exc: LLMProviderError, deadline: float) -> bool:
        low, high = self.retry_backoff
        delay = random.uniform(low, high) if high > low else max(0.0, low)
        if deadline - time.monotonic() <= delay:
            return False
        logger.warning("Claude request failed (%s) — retrying once in %.1fs: %s", exc.code, delay, exc.message[:200])
        if delay:
            await asyncio.sleep(delay)
        return True

    async def _iter_turn(self, messages: Sequence[BaseMessage], *, streaming: bool) -> AsyncIterator[Tuple[str, Any]]:
        """The whole call: the request, its continuations, one retry of a
        transient failure while nothing has reached the consumer (in
        streaming), and the fallback for a model that rejects
        mid-conversation system messages."""
        deadline = self._deadline()
        ctx = self._call_context()
        kwargs = self._request_kwargs(messages)
        committed = False
        retried = False
        moved_system = False
        first = True
        while True:
            try:
                async for item in self._run_segments(kwargs, deadline, ctx, first=first):
                    if streaming and item[0] == "text":
                        committed = True
                    yield item
                return
            except LLMProviderError as exc:
                first = False
                if (not moved_system and not committed and _is_mid_system_rejection(exc)
                        and any(m.get("role") == "system" for m in kwargs["messages"])):
                    moved_system = True
                    _LEARNED_NO_MID_SYSTEM.add(model_family(self.model))
                    logger.warning("ChatClaude: %s rejects mid-conversation system messages; "
                                   "sending the per-turn context in the system prompt", self.model)
                    kwargs = self._request_kwargs(messages, mid_conversation_system=False)
                    continue
                if (not retried and exc.retryable and not committed
                        and await self._pause_before_retry(exc, deadline)):
                    retried = True
                    continue
                raise

    # -------------------------------------------------------- the results --

    def _metadata(self, result: _TurnResult) -> Dict[str, Any]:
        meta: Dict[str, Any] = {
            "model": result.model or self.model,
            "model_name": result.model or self.model,
            "model_provider": PROVIDER,
            "id": result.id,
            "request_id": result.request_id,
            "stop_reason": result.stop_reason,
            "stop_sequence": result.stop_sequence,
            "usage": result.usage,
        }
        if result.stop_details is not None:
            meta["stop_details"] = result.stop_details
        if result.input_transformations:
            meta["input_transformations"] = result.input_transformations
        if result.requests > 1:
            meta["requests"] = result.requests
        if result.model and model_family(result.model) != model_family(self.model):
            meta["requested_model"] = self.model  # a server-side fallback served it
        # The sum of each request's own price (``_TurnResult.cost_usd``).
        meta["estimated_cost_usd"] = round(result.cost_usd, 6)
        return meta

    def _additional_kwargs(self, result: _TurnResult) -> Dict[str, Any]:
        ak: Dict[str, Any] = {CLAUDE_CONTENT_KEY: result.blocks}
        if result.reasoning:
            ak["reasoning_items"] = [{"type": "reasoning", "summary": [{"type": "summary_text", "text": t}]}
                                     for t in result.reasoning]
        if result.stop_reason == "max_tokens":
            ak["incomplete_reason"] = "max_tokens"
        elif result.stop_reason == "pause_turn":
            ak["incomplete_reason"] = "pause_turn"
        return ak

    def _final_message(self, result: _TurnResult) -> AIMessage:
        kwargs: Dict[str, Any] = {
            "content": REFUSAL_NOTE if result.refused else result.text,
            "tool_calls": [{**call, "type": "tool_call"} for _, call in result.tool_calls],
            "additional_kwargs": self._additional_kwargs(result),
            "response_metadata": self._metadata(result),
        }
        usage = usage_metadata_from(result.billed)
        if usage:
            kwargs["usage_metadata"] = usage
        return AIMessage(**kwargs)

    def _final_chunk(self, result: _TurnResult) -> AIMessageChunk:
        kwargs: Dict[str, Any] = {
            "content": "",
            "additional_kwargs": self._additional_kwargs(result),
            "response_metadata": self._metadata(result),
        }
        if result.tool_calls:
            kwargs["tool_call_chunks"] = [
                {"name": call["name"], "args": json.dumps(call["args"]), "id": call["id"], "index": index,
                 "type": "tool_call_chunk"}
                for index, call in result.tool_calls
            ]
        usage = usage_metadata_from(result.billed)
        if usage:
            kwargs["usage_metadata"] = usage
        return AIMessageChunk(**kwargs)

    async def _astream(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                       run_manager: Optional[AsyncCallbackManagerForLLMRun] = None,
                       **kwargs: Any) -> AsyncIterator[ChatGenerationChunk]:
        streamed_text = False
        async for kind, payload in self._iter_turn(messages, streaming=True):
            if kind == "text":
                streamed_text = True
                yield ChatGenerationChunk(message=AIMessageChunk(content=payload))
            elif kind == "reasoning":
                yield ChatGenerationChunk(message=AIMessageChunk(content="",
                                                                 additional_kwargs={"reasoning_delta": payload}))
            elif kind == "tool_args":
                yield ChatGenerationChunk(message=AIMessageChunk(content="",
                                                                 additional_kwargs={"tool_args_delta": payload}))
            elif kind == "final":
                if payload.refused:
                    # What was streamed cannot be taken back; say why it stops.
                    note = ("\n\n" if streamed_text else "") + REFUSAL_NOTE
                    yield ChatGenerationChunk(message=AIMessageChunk(content=note))
                yield ChatGenerationChunk(message=self._final_chunk(payload))

    async def _agenerate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                         run_manager: Optional[AsyncCallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        result: Optional[_TurnResult] = None
        async for kind, payload in self._iter_turn(messages, streaming=False):
            if kind == "final":
                result = payload
        assert result is not None
        return ChatResult(generations=[ChatGeneration(message=self._final_message(result))])

    def _generate(self, messages: List[BaseMessage], stop: Optional[List[str]] = None,
                  run_manager: Optional[CallbackManagerForLLMRun] = None, **kwargs: Any) -> ChatResult:
        """Sync generation: the async path on a fresh loop (nothing in
        Edward calls ``invoke`` synchronously today)."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self._agenerate(messages, stop, **kwargs))
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            return pool.submit(asyncio.run, self._agenerate(messages, stop, **kwargs)).result()
