"""
DreamContext — shared query-generation cache for reflection and deep retrieval.

Both subsystems used to call Haiku independently to generate search queries
from the same conversation context. That's one wasted LLM call per turn.
This helper is the single entry point; results are cached for 60 seconds
keyed on ``(conversation_id, sha(last_human_message))`` so back-to-back
callers for the same turn share one call.

GPT-only: routes through ``build_background_llm()``. Never ``ChatAnthropic``.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from typing import List

from langchain_core.messages import HumanMessage

from services.json_parsing import extract_json_payload

logger = logging.getLogger(__name__)

# In-process cache — keyed on (conversation_id, last_human_hash).
# The cached list is the largest ``n`` ever requested for that key;
# callers take ``result[:n]`` to honor a smaller request.
_cache: dict[tuple[str, str], tuple[List[str], float]] = {}
_CACHE_TTL_SECONDS = 60.0

# How long a caller waits for the LLM. Before the turn (deep retrieval) the
# owner is waiting on first-token latency, so 5 s; after the turn (reflection)
# nobody is. A call that outlives its caller's patience is *not* cancelled:
# it keeps running (``_inflight``) and its answer is cached, so the next
# caller for the same key — reflection, seconds later — gets it free.
# 2026-09-21: 1,615 timeouts in a day, each a Codex call spent for nothing.
DEEP_RETRIEVAL_TIMEOUT_SECONDS = 5.0
REFLECTION_TIMEOUT_SECONDS = 20.0
_LLM_TIMEOUT_SECONDS = DEEP_RETRIEVAL_TIMEOUT_SECONDS
_inflight: dict[tuple[str, str], "asyncio.Task[List[str]]"] = {}

# Request this many queries from the LLM so callers with smaller n still
# benefit from the cache; the largest consumer is deep retrieval with 5.
_DEFAULT_LLM_N = 5


def _last_human_hash(messages: list) -> str:
    """SHA-256 of the most recent user turn's content."""
    for msg in reversed(messages):
        if isinstance(msg, dict) and str(msg.get("role")).lower() == "user":
            content = str(msg.get("content", ""))
            return hashlib.sha256(content.encode("utf-8")).hexdigest()
    return "empty"


def _format_conversation(messages: list, window: int) -> str:
    return "\n".join(
        f"{str(msg.get('role', 'unknown')).upper()}: {msg.get('content', '')}"
        for msg in messages[-window:]
        if isinstance(msg, dict)
    )


async def generate_search_queries(
    conversation_id: str,
    messages: list,
    n: int = 5,
    window: int = 10,
    timeout: float | None = None,
) -> List[str]:
    """Return up to ``n`` search queries derived from the recent conversation.

    Cached per ``(conversation_id, last_human_hash)`` for 60 s so reflection
    and deep retrieval for the same turn don't duplicate LLM work. A caller
    waits at most ``timeout`` seconds (default: the pre-turn budget); a call
    that takes longer keeps running and lands in the cache for the next
    caller instead of being cancelled and paid for twice.
    """
    if n <= 0:
        return []

    key = (conversation_id, _last_human_hash(messages))
    now = time.monotonic()
    entry = _cache.get(key)
    if entry is not None:
        queries, ts = entry
        if now - ts < _CACHE_TTL_SECONDS:
            return queries[:n]

    conversation_text = _format_conversation(messages, window)
    if not conversation_text.strip():
        return []

    llm_n = max(n, _DEFAULT_LLM_N)
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(_generate(key, conversation_text, window, llm_n))
        _inflight[key] = task

    budget = _LLM_TIMEOUT_SECONDS if timeout is None else timeout
    try:
        queries = await asyncio.wait_for(asyncio.shield(task), timeout=budget)
    except asyncio.TimeoutError:
        logger.warning(
            "dream query generation still running after %.0fs; the answer will be cached when it lands",
            budget,
        )
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning("dream query generation failed: %s", e)
        return []
    return queries[:n]


async def _generate(key: tuple[str, str], conversation_text: str, window: int, llm_n: int) -> List[str]:
    """One LLM call → parsed queries, cached under ``key``. Runs to completion
    even when every caller has stopped waiting."""
    # Import at call time so tests can monkeypatch services.llm_utils.build_background_llm.
    from services.llm_utils import build_background_llm, make_background_system_message

    try:
        llm = build_background_llm(max_tokens=512)
        response = await llm.ainvoke(
            [
                make_background_system_message(
                    "Generate search queries to find relevant long-term memories for a conversation. Return ONLY a JSON array of query strings."
                ),
                HumanMessage(
                    content=(
                        f"Last {window} messages:\n{conversation_text}\n\n"
                        f"Generate exactly {llm_n} diverse queries "
                        "(direct topic, related context, named entities, synonyms, broader themes). "
                        "Return ONLY a JSON array of query strings."
                    )
                ),
            ]
        )
    finally:
        _inflight.pop(key, None)

    response_text = response.content
    if isinstance(response_text, list):
        response_text = " ".join(
            block.get("text", "") if isinstance(block, dict) else str(block)
            for block in response_text
        )
    response_text = (response_text or "").strip()

    queries = extract_json_payload(
        response_text, expected="array", subsystem="dream_context"
    )
    if not isinstance(queries, list):
        return []

    queries = [q for q in queries if isinstance(q, str) and q.strip()][:llm_n]
    _cache[key] = (queries, time.monotonic())
    return queries
