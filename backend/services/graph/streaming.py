import asyncio
import time
import hashlib
import json as _json
import re
import uuid
from datetime import datetime
from typing import AsyncGenerator, List, Any, Dict, Iterable, Optional, Tuple
from langchain_core.messages import HumanMessage, AIMessage, SystemMessage, ToolMessage
import logging

from services.llm_utils import build_chat_llm, provider_for, turn_model_for
from services.tool_registry import get_available_tools, get_tool_descriptions
from services.graph.history_window import (
    scaffolding_ids, stale_message_ids, strip_media_blocks, strip_scaffolding,
)
from services.graph import tool_deadlines, tool_loop, tool_policy, turn_trace, turn_window
# The executor lives in ``tool_loop`` (2026-10-11); these names stay importable here.
from services.graph.tool_loop import (  # noqa: F401
    _boundary_check, _boundary_note, _journal_note, execute_tool_call, execute_tool_call_with_events,
)
from services.toolset import policy as policy_mod
from services.timezone_utils import now_local, current_tz_name
from services.owner import owner_text

logger = logging.getLogger(__name__)


def _make_system_message(content: str, provider: str) -> SystemMessage:
    """Build a SystemMessage. ``provider`` kept for call-site compatibility;
    no ``cache_control`` here — ``ChatClaude`` places its own breakpoints
    (at ``STATIC_DYNAMIC_SPLIT``) and Codex caches by prefix."""
    return SystemMessage(content=content)


# Context budget constants
MAX_NORMAL_MEMORIES = 10
MAX_ENRICHED_MEMORIES = 5
MAX_DOCUMENTS = 3
MAX_MEMORY_CONTEXT_CHARS = 12000
MAX_GROUNDING_SOURCE_CHARS = 1200
MAX_GROUNDING_SOURCES = 12
# Tool rounds per turn: ``EDWARD_MAX_TOOL_ROUNDS`` (60) unless the caller
# asks for a number (``max_tool_iterations``). A plan no longer raises it;
# the per-turn dollar cap is the real bound (2026-10-11).
DEFAULT_MAX_TOOL_ITERATIONS = tool_loop.DEFAULT_MAX_ROUNDS

# Session briefing cache: {conversation_id: (briefing_text, generated_at)}
_session_briefing_cache: Dict[str, tuple] = {}


def _resolve_turn_settings(existing_values, system_prompt: str, model: str, temperature: float):
    """Settings for this turn: the caller's live values, always.

    Until 2026-09-05 both chat paths did the opposite — the thread's
    checkpoint won — so a model or prompt change in settings never reached
    an existing conversation. When Codex retired gpt-5.4, every long-lived
    thread (heartbeat contact threads, ``edward-surfaces``) kept asking for
    it and failed with HTTP 400 while settings.model said gpt-6-astra.
    ``existing_values`` is accepted only so the call sites read naturally;
    nothing in it overrides the live settings.
    """
    return system_prompt, model, temperature


def post_turn_extraction_enabled(skip_memory: bool, skip_extraction: bool) -> bool:
    """Whether the post-turn memory extraction runs.

    ``skip_extraction`` is for turns that are not conversations — the
    expression pulse consumes a signal pack (pet board, presence, calendar)
    and extracting from it produced rows like "no medications were scheduled
    at the time shown" that then contradicted Biscuit's real schedule
    (2026-09-05). ``skip_memory`` (workers) gates retrieval only; it is
    accepted here so the call site reads as one decision.
    """
    return not skip_extraction


TURN_PURPOSE_PREFIX = "turn:"


def turn_purpose(turn_source: Optional[str], current: Optional[str]) -> Optional[str]:
    """The spend label for a turn's own Codex calls when no job scope is
    around it, else None (the outer ``mind:*``, ``surplus:*`` or ``pulse``
    wins).

    Scheduled-event and heartbeat turns reached ``codex_calls`` as
    ``unknown`` (4 calls, 263K tokens since 9/26; followups L5): nothing
    named their job, and the stack walk sees only LLM plumbing. Pure.
    """
    if current:
        return None
    return f"{TURN_PURPOSE_PREFIX}{turn_source or 'background'}"


def _build_llm(model: str, temperature: float, max_tokens: int = 16384, provider: str = "openai",
               reasoning_effort: Optional[str] = None, turn_source: Optional[str] = None):
    """Build a chat LLM instance. ``provider`` kept for call-site
    compatibility; ``build_chat_llm`` decides the provider
    (``EDWARD_LLM_PROVIDER``: ``ChatClaude`` by default, the Codex
    fallback when ``codex``).

    ``reasoning_effort`` and ``turn_source`` reach ``build_chat_llm``: an
    explicit effort (a wake's per-reason level) wins over
    ``EDWARD_CHAT_REASONING_EFFORT``, and a ``voice`` turn keeps the model
    default (Phase 2, 2026-09-24).

    The model is the turn's own, so it carries the turn's spend label
    (``turn_purpose``) when no job scope is around the turn. The label goes
    on this model, never into a scope around the turn: a scope beats every
    model's own label (``codex_chat_model._call_context``), and the first
    version of L5 (2026-09-27) relabelled the extraction, entity, query and
    search-tag calls inside a turn ``turn:<source>`` too — the per-job split
    the spend report and the memory audit read.
    """
    from services.call_purpose import current_purpose, stamp

    llm = build_chat_llm(model=model, temperature=temperature, max_tokens=max_tokens,
                         reasoning_effort=reasoning_effort, turn_source=turn_source)
    label = turn_purpose(turn_source, current_purpose())
    return stamp(llm, label) if label else llm


# ---------------------------------------------------------------------------
# Tool-loop plumbing shared by both chat paths (docs/architecture/tool-loop.md).
# The decisions are pure and live
# in ``tool_policy`` / ``turn_window`` / ``tool_deadlines``; these helpers
# apply them to messages and models.

# Threads whose turns are Edward's own judgment over a signal pack, not a
# conversation. Search tags and reflection describe conversations; on these
# threads they spent background quota tagging packs (2026-09-24).
MIND_THREADS = frozenset({"edward-mind", "edward-surfaces"})
LEARNERLESS_SOURCES = frozenset({"mind", "pulse"})


def post_turn_learners_enabled(turn_source: Optional[str], conversation_id: Optional[str]) -> bool:
    """Whether search tags and reflection run after this turn. Extraction
    is separate (``post_turn_extraction_enabled``)."""
    return (turn_source or "") not in LEARNERLESS_SOURCES and (conversation_id or "") not in MIND_THREADS


def usage_numbers(response: Any) -> Optional[Dict[str, int]]:
    """Token counts for ``usage_events`` from one model response.

    The LangChain-standard ``usage_metadata`` first (the Codex adapter fills
    it: ``input_token_details.cache_read``), then the raw usage dict in
    ``response_metadata`` — Codex field names (``input_tokens_details.
    cached_tokens``) or the old Anthropic ones. Until 2026-09-24 only the
    Anthropic names were read, so cached tokens were always 0.
    """
    um = getattr(response, "usage_metadata", None) or {}
    raw = (getattr(response, "response_metadata", None) or {}).get("usage") or {}
    if not um and not raw:
        return None
    details = um.get("input_token_details") or {}
    raw_details = raw.get("input_tokens_details") or {}
    return {
        "input_tokens": int(um.get("input_tokens") or raw.get("input_tokens") or 0),
        "output_tokens": int(um.get("output_tokens") or raw.get("output_tokens") or 0),
        "cache_read_tokens": int(details.get("cache_read") or raw.get("cache_read_input_tokens")
                                 or raw_details.get("cached_tokens") or 0),
        "cache_creation_tokens": int(details.get("cache_creation") or raw.get("cache_creation_input_tokens") or 0),
    }


async def _record_turn_usage(response: Any, *, provider: str, model: str, conversation_id: str,
                             turn_source: Optional[str]) -> None:
    try:
        numbers = usage_numbers(response)
        if numbers:
            from services.usage_service import record_usage
            await record_usage(provider=provider, model=model, conversation_id=conversation_id,
                               source=turn_source or "chat", **numbers)
    except Exception as e:
        logger.debug(f"Usage tracking failed: {e}")


def _text_of(content: Any) -> str:
    if isinstance(content, list):
        return "".join(block.get("text", "") if isinstance(block, dict) else str(block) for block in content)
    return content if isinstance(content, str) else ("" if content is None else str(content))


def _bind(llm: Any, tools: List[Any], provider: Optional[str] = None) -> Any:
    """``llm`` with ``tools`` bound — never a forced choice
    (``tool_loop.bind_for_turn``: Claude gets native/server tools as their
    Anthropic dicts and parallel calls on)."""
    return tool_loop.bind_for_turn(llm, tools, provider)


def _closing_call_llm(llm: Any, tools: List[Any], provider: Optional[str]) -> Any:
    """The model for a turn's closing call (``tool_loop.closing_llm``)."""
    return tool_loop.closing_llm(llm, tools, provider)


def _ensure_ids(messages: Iterable[Any]) -> None:
    """Give every message an id before it is checkpointed, so a save of a
    copy (reasoning items stripped) and a later RemoveMessage agree on it."""
    for m in messages:
        if getattr(m, "id", None) is None:
            try:
                m.id = str(uuid.uuid4())
            except Exception:  # noqa: BLE001
                pass


def _checkpoint_messages(messages: List[Any]) -> List[Any]:
    """What a turn writes to the checkpoint: the loop's nudges and dead
    turns removed (they are scaffolding, and a thread that reads them back
    learns that silence is wrong), and reasoning kept only for the
    latest turn — the Codex adapter's ``strip_reasoning_items`` and the
    Claude adapter's ``strip_claude_thinking`` (thinking signatures), when
    they exist. Images and PDFs leave a line with their ref and description
    instead of their bytes — once, through ``history_window.strip_media_blocks``
    (which calls ``media.dehydrate``; 2026-10-11)."""
    _ensure_ids(messages)
    # Images and PDFs become a line naming their ref: a
    # later turn re-pays for no base64; ``look`` sees it again.
    out = strip_media_blocks(strip_scaffolding(messages))
    for name, strip in (("strip_reasoning_items", _strip_reasoning_items()),
                        ("strip_claude_thinking", _strip_claude_thinking())):
        if strip is None:
            continue
        try:
            out = list(strip(out))
        except Exception as e:  # noqa: BLE001
            logger.warning("%s failed; checkpointing as is: %s", name, e)
    return out


def _strip_reasoning_items():
    try:
        from services.codex_chat_model import strip_reasoning_items  # type: ignore[attr-defined]
        return strip_reasoning_items
    except Exception:  # noqa: BLE001 — not built yet, or renamed
        return None


def _strip_claude_thinking():
    try:
        from services.claude_chat_model import strip_claude_thinking  # type: ignore[attr-defined]
        return strip_claude_thinking
    except Exception:  # noqa: BLE001
        return None


def _dynamic_split_for(provider: Optional[str]) -> str:
    """The seam between the frozen prompt and the per-turn context: only a
    Claude turn has one (``ChatClaude`` caches what is before it and sends
    what is after it next to the owner's message). Codex prompts are unchanged."""
    if provider != "anthropic":
        return ""
    try:
        from services.claude_chat_model import STATIC_DYNAMIC_SPLIT
        return STATIC_DYNAMIC_SPLIT
    except Exception:  # noqa: BLE001
        return ""


def _extraction_view(messages: List[Any]) -> List[dict]:
    """The last ten conversation messages for the post-turn learners —
    never the loop's nudges."""
    return [
        {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
        for m in strip_scaffolding(messages)[-10:]
        if isinstance(m, (HumanMessage, AIMessage))
    ]


class _TurnProgress:
    """What a non-streaming turn has done so far, so a cancelled or failed
    turn can still leave it in the thread.

    A wake cancelled by ``asyncio.wait_for`` used to lose its checkpoint
    while keeping its side effects: the thread never learned it had sent a
    message, and the next wake was re-served the same escalations. Saving
    after every iteration would write the whole messages blob once per tool
    round (the checkpoint tables already keep every version), so the save
    happens once, on the way out.
    """

    def __init__(self) -> None:
        self.graph = None
        self.config = None
        self.messages: Optional[List[Any]] = None
        self.turn_start: Optional[int] = None
        self.completed = 0
        self.saved = False
        self.stop_reason: Optional[str] = None
        self.exhausted = False

    async def save_interrupted(self, require_progress: bool = False) -> bool:
        """``require_progress``: save only when a tool call finished. A turn
        that failed before doing anything keeps the old behaviour (nothing
        written), so a caller's retry does not find a half-turn first."""
        if self.saved or self.graph is None or self.messages is None or self.turn_start is None:
            return False
        if require_progress and not self.completed:
            return False
        self.saved = True
        msgs = turn_window.close_open_tool_calls(list(self.messages), self.turn_start)
        msgs.append(AIMessage(content=f"[interrupted after {self.completed} tool calls]"))
        ok = await _safe_update_state(self.graph, self.config, {"messages": _checkpoint_messages(msgs)},
                                      as_node="extract_memory")
        print(f"[TOOL LOOP] turn interrupted after {self.completed} tool calls; progress saved={ok}")
        return ok


SPECULATIVE_RETRIEVAL_TIMEOUT_S = 0.8


class _SkipSpeculative(Exception):
    """Internal: the fast plan asked to skip speculative retrieval."""


def _context_plan(fast_context: bool) -> dict:
    """Which pre-LLM extras a turn waits for.

    Spoken turns (voice mode) take the fast plan: no speculative deep
    retrieval wait and no session briefing. Measured 2026-09-05, those
    two accounted for ~2 s of silence before Edward said anything.
    """
    if fast_context:
        return {"speculative_timeout": 0.0, "include_session_briefing": False}
    return {
        "speculative_timeout": SPECULATIVE_RETRIEVAL_TIMEOUT_S,
        "include_session_briefing": True,
    }


async def _collect_llm_context(
    message: str,
    conversation_id: str,
    turn_count: int,
    include_session_briefing: bool = True,
) -> dict:
    """Parallel fan-out for pre-LLM context assembly.

    Seven independent reads: entity-linked memories, reflection
    enrichments, documents, the heartbeat briefing, the session briefing,
    open loops and the tool set. Each branch isolates its own exceptions
    so one slow or broken subsystem doesn't poison the bundle.

    Until the mind rebuild (Phase 8, 2026-09-18) another twenty-six
    "engine" reads ran here and became a ``## Recent Awareness`` block of
    regex-extracted commitments, stakeholders, risks and the like. They
    are gone: what a person would remember is in ``memories``.
    """

    async def _fetch_entity_memories():
        try:
            from services.memory_service import retrieve_entity_memories
            return await retrieve_entity_memories(message, limit=5)
        except Exception as e:
            print(f"Entity memory retrieval failed: {e}")
            return []

    async def _fetch_enrichments():
        try:
            from services.reflection_service import load_enrichments
            return await load_enrichments(conversation_id, limit=MAX_ENRICHED_MEMORIES)
        except Exception as e:
            print(f"Enrichment loading failed: {e}")
            return []

    async def _fetch_documents():
        try:
            from services.document_service import retrieve_relevant_documents, get_state_documents
            relevant, state = await asyncio.gather(
                retrieve_relevant_documents(message, limit=3),
                get_state_documents(),
            )
            return relevant, state
        except Exception as e:
            print(f"Document retrieval failed: {e}")
            return [], []

    async def _fetch_briefing():
        try:
            from services.heartbeat.heartbeat_service import get_pending_briefing
            return await get_pending_briefing()
        except Exception as e:
            import traceback
            print(f"Heartbeat briefing failed: {e}\n{traceback.format_exc()}")
            return None

    async def _fetch_session_briefing():
        if turn_count > 1 or not include_session_briefing:
            return ""
        try:
            cached = _session_briefing_cache.get(conversation_id)
            if cached and (datetime.now() - cached[1]).total_seconds() < 3600:
                return f"\n\n{cached[0]}"
            from services.session_briefing_service import generate_briefing as gen_session_briefing
            sb = await gen_session_briefing(conversation_id)
            if sb:
                _session_briefing_cache[conversation_id] = (sb, datetime.now())
                return f"\n\n{sb}"
            return ""
        except Exception as e:
            print(f"Session briefing failed: {e}")
            return ""

    async def _fetch_open_loops():
        try:
            from services.open_loop_service import get_active_open_loops
            return await get_active_open_loops(limit=5)
        except Exception as e:
            print(f"Open loops loading failed: {e}")
            return []

    async def _fetch_tools():
        try:
            # The same toolset on every turn: nothing is gated on the words.
            return await get_available_tools()
        except Exception as e:
            print(f"Tool loading failed: {e}")
            return []

    (
        entity_memories,
        enriched_memories,
        docs_pair,
        briefing,
        session_briefing_context,
        open_loops,
        tools,
    ) = await asyncio.gather(
        _fetch_entity_memories(),
        _fetch_enrichments(),
        _fetch_documents(),
        _fetch_briefing(),
        _fetch_session_briefing(),
        _fetch_open_loops(),
        _fetch_tools(),
    )

    relevant_documents, state_documents = docs_pair
    return {
        "entity_memories": entity_memories,
        "enriched_memories": enriched_memories,
        "relevant_documents": relevant_documents,
        "state_documents": state_documents,
        "briefing": briefing,
        "session_briefing_context": session_briefing_context,
        "open_loops": open_loops,
        "tools": tools,
    }


async def _spawn_post_turn_work(
    messages_for_extraction: list,
    *,
    turn_id: Optional[str] = None,
    conversation_id: str,
    retrieved_memories: list,
    turn_count: int,
    turn_source: Optional[str] = None,
) -> None:
    """Spawn memory extraction + downstream enrichment tasks as background work.

    All tasks are fire-and-forget — this helper returns once they are scheduled
    so `stream_with_memory_events` can emit the DONE event immediately. The
    implication task depends on extraction's output, so it is chained inside
    the extraction task's continuation to preserve that dependency.

    Extraction, search tags and reflection are the post-turn learners. The
    engine learners (decisions, stakeholder positions, experiments, fact
    claims, dependencies, objectives, cadences, routes) left with Phase 8.
    Search tags and reflection skip the mind's and the pulse's threads
    (``post_turn_learners_enabled``).
    """

    async def _extract_and_chain():
        from services.memory_service import extract_and_store_memories

        try:
            stored_memories = await asyncio.wait_for(
                extract_and_store_memories(
                    messages=messages_for_extraction,
                    conversation_id=conversation_id,
                    existing_memories=retrieved_memories,
                ),
                timeout=30,
            )
        except asyncio.TimeoutError:
            print(f"Memory extraction timed out after 30s for conversation {conversation_id}")
            return
        except Exception as e:
            print(f"Memory extraction failed: {e}")
            return

        stored_ids = [m.id for m in (stored_memories or []) if m and m.id]
        try:
            from services.turn_ledger import note_extraction

            await note_extraction(turn_id, len(stored_ids))
        except Exception:
            pass
        if stored_ids:
            try:
                from services.implication_service import run_implication_safe
                await run_implication_safe(
                    conversation_id, messages_for_extraction, stored_ids
                )
            except Exception as e:
                print(f"Implication analysis failed: {e}")

    asyncio.create_task(_extract_and_chain())

    if not post_turn_learners_enabled(turn_source, conversation_id):
        return

    try:
        from services.search_tag_service import generate_search_tags_safe
        asyncio.create_task(generate_search_tags_safe(conversation_id, messages_for_extraction))
    except Exception as e:
        print(f"Search tags fire-and-forget failed: {e}")

    try:
        from services.reflection_service import should_reflect, run_reflection_safe
        if should_reflect(messages_for_extraction, turn_count):
            asyncio.create_task(run_reflection_safe(
                conversation_id, messages_for_extraction,
                [m.id for m in retrieved_memories],
            ))
    except Exception as e:
        print(f"Reflection fire-and-forget failed: {e}")


def _auth_refresh_warranted(exc: BaseException) -> bool:
    """A 401-looking error that no layer below has refreshed for. Pure.

    ``ChatCodexResponses`` refreshes and retries a 401 itself and marks
    what still fails ``refresh_attempted`` (``CodexAuthError``); refreshing
    again here stacked up to four requests and three refreshes per call
    (2026-09-25 audit). A raw 401 (the API-key ``ChatOpenAI`` path) still
    gets the loop's one refresh."""
    if getattr(exc, "refresh_attempted", False):
        return False
    err_str = str(exc)
    return "401" in err_str or "Unauthorized" in err_str


async def _invoke_with_retry(llm_with_tools, messages, provider: str, model: str, temperature: float, tools,
                             *, reasoning_effort: Optional[str] = None, turn_source: Optional[str] = None):
    """One non-streaming model call under ``LLM_CALL_TIMEOUT_SECONDS``
    (``LLMCallTimeout`` past it), with one token refresh for a raw 401 on
    the Codex path. ``ChatCodexResponses`` refreshes itself and marks what
    still fails ``refresh_attempted``; a Claude key does not refresh, so an
    Anthropic auth error ends the turn (``LLMProviderError`` carries
    ``refresh_attempted``)."""
    try:
        return await tool_deadlines.invoke_with_deadline(llm_with_tools.ainvoke(messages))
    except tool_deadlines.LLMCallTimeout:
        raise
    except Exception as e:
        if provider == "openai" and _auth_refresh_warranted(e):
            logger.warning("OpenAI 401 — refreshing Codex token and retrying")
            from services.codex_auth_service import get_codex_auth_manager
            manager = get_codex_auth_manager()
            refreshed = await manager.force_refresh()
            if refreshed:
                new_llm = _build_llm(model, temperature, provider=provider,
                                     reasoning_effort=reasoning_effort, turn_source=turn_source)
                new_llm_with_tools = _bind(new_llm, tools, provider)
                return await tool_deadlines.invoke_with_deadline(new_llm_with_tools.ainvoke(messages))
            else:
                raise ValueError(
                    "OpenAI token expired and refresh failed. Run `codex login` to re-authenticate."
                ) from e
        raise


async def _stream_llm_with_events(llm_with_tools, messages):
    """Drive an LLM via .astream() and emit typed events.

    Yields:
        {"type": "content_delta", "text": "..."}   # one per nonempty text chunk
        {"type": "reasoning_delta", "text": "..."} # N4: Codex reasoning summary deltas
        {"type": "final", "message": AIMessage}    # exactly one, last

    The chat loop turns content_delta into EventType.CONTENT and
    reasoning_delta into EventType.REASONING SSE events. The final
    AIMessage carries assembled text + tool_calls + metadata for the
    tool-loop branch logic, the call's ``usage_metadata``, and any
    ``reasoning_items`` the Codex adapter captured (so the next iteration
    can replay them).

    The whole call runs under ``tool_deadlines.LLM_CALL_TIMEOUT_SECONDS``
    of time spent waiting on the model (``LLMCallTimeout`` past it).
    """
    text_pieces: list[str] = []
    acc_tool_calls: list = []
    acc_response_meta: dict = {}
    acc_usage: Optional[dict] = None
    reasoning_items: list = []
    seen_reasoning: set = set()
    # The Claude adapter's raw content blocks (thinking signatures, tool_use)
    # ride its final chunk; the next tool round replays them verbatim
    # (``claude_chat_model``). Codex chunks never carry the key.
    carried: Dict[str, Any] = {}

    async for chunk in tool_deadlines.aiter_with_deadline(llm_with_tools.astream(messages)):
        # langchain BaseChatModel.astream yields BaseMessageChunk objects directly
        msg = chunk

        # N4: Codex reasoning summary deltas come through additional_kwargs
        ak = getattr(msg, "additional_kwargs", None) or {}
        rd = ak.get("reasoning_delta")
        if rd:
            yield {"type": "reasoning_delta", "text": rd}

        for item in ak.get("reasoning_items") or []:
            key = item.get("id") if isinstance(item, dict) and item.get("id") else _json.dumps(item, sort_keys=True, default=str)
            if key not in seen_reasoning:
                seen_reasoning.add(key)
                reasoning_items.append(item)
        if ak.get("claude_content"):
            carried["claude_content"] = ak["claude_content"]
        if ak.get("incomplete_reason"):
            # A max_tokens / pause_turn ending: the loop reads it (review 2026-10-11).
            carried["incomplete_reason"] = ak["incomplete_reason"]

        um = getattr(msg, "usage_metadata", None)
        if um:
            acc_usage = dict(um)

        # Tool-args deltas — partial JSON for an in-flight function call.
        tad = ak.get("tool_args_delta")
        if tad:
            yield {"type": "tool_args_delta", **tad}

        content = getattr(msg, "content", None)
        if isinstance(content, str) and content:
            text_pieces.append(content)
            yield {"type": "content_delta", "text": content}
        elif isinstance(content, list):
            for block in content:
                if isinstance(block, dict) and "text" in block and block["text"]:
                    text_pieces.append(block["text"])
                    yield {"type": "content_delta", "text": block["text"]}

        for tc in (getattr(msg, "tool_calls", None) or []):
            acc_tool_calls.append(tc)

        meta = getattr(msg, "response_metadata", None) or {}
        # The final chunk carries the model, usage, ``stop_reason`` and the
        # Claude adapter's ``estimated_cost_usd`` (the loop's dollar cap).
        if meta.get("model") or meta.get("usage") or meta.get("stop_reason") or meta.get("estimated_cost_usd"):
            acc_response_meta = {**acc_response_meta, **meta}

    final_kwargs: Dict[str, Any] = {}
    final_ak: Dict[str, Any] = dict(carried)
    if reasoning_items:
        final_ak["reasoning_items"] = reasoning_items
    if final_ak:
        final_kwargs["additional_kwargs"] = final_ak
    if acc_usage:
        final_kwargs["usage_metadata"] = acc_usage
    try:
        final = AIMessage(content="".join(text_pieces), tool_calls=acc_tool_calls,
                          response_metadata=acc_response_meta, **final_kwargs)
    except Exception:  # noqa: BLE001 — a malformed usage dict must not cost the answer
        final_kwargs.pop("usage_metadata", None)
        final = AIMessage(content="".join(text_pieces), tool_calls=acc_tool_calls,
                          response_metadata=acc_response_meta, **final_kwargs)
    yield {"type": "final", "message": final}


async def _run_deep_retrieval_safe(
    message: str,
    recent_msgs: list,
    conversation_id: str,
    already_retrieved_ids: list[str] | None = None,
) -> None:
    """Fire-and-forget deep retrieval that stores results as enrichments.

    Runs concurrently with the LLM response. Results are stored in
    memory_enrichments table for:
    - remember_search calls within the same turn
    - Enrichment loading on the next turn
    """
    try:
        from services.deep_retrieval_service import deep_retrieve_memories
        from services.database import async_session as get_session, MemoryEnrichmentModel

        retrieved = await asyncio.wait_for(
            deep_retrieve_memories(message, recent_msgs, limit=15, conversation_id=conversation_id),
            timeout=15.0,
        )

        # Filter out memories already in the fast retrieval results
        already_ids = set(already_retrieved_ids or [])
        new_memories = [m for m in retrieved if m.id not in already_ids]

        if not new_memories:
            print(f"[DEEP RETRIEVAL] No new memories beyond fast retrieval for {conversation_id}")
            return

        # Store as enrichments (same table as reflection service)
        async with get_session() as session:
            for memory in new_memories:
                enrichment = MemoryEnrichmentModel(
                    conversation_id=conversation_id,
                    memory_id=memory.id,
                    memory_content=memory.content,
                    memory_type=memory.memory_type,
                    importance=memory.importance,
                    temporal_nature=memory.temporal_nature,
                    query_source=message,
                    score=memory.score,
                    consumed=False,
                )
                session.add(enrichment)
            await session.commit()

        print(f"[DEEP RETRIEVAL] Stored {len(new_memories)} enrichments for {conversation_id}")
    except asyncio.TimeoutError:
        print(f"[DEEP RETRIEVAL] Timed out (15s) for {conversation_id}")
    except Exception as e:
        print(f"[DEEP RETRIEVAL] Failed safely: {e}")


# Assumption awareness instructions - helps the agent recognize when it's making
# unverified inferences and either verify or state assumptions explicitly
# The "call create_plan first for 3+ step work" rule lives in the plan-tool
# manual (tools.get_plan_tools_description), which is in every turn that has
# the plan tools. Shipping it twice bought nothing; kept as an empty name so
# nothing that imports it breaks.
PLANNING_DIRECTIVE = ""

ASSUMPTION_AWARENESS_CONTEXT = """

## Assumption Awareness

When you infer something a tool result or the user did not state, check it:

- Name matching: a search for "Parker" returns messages — CHECK the `chat_name` / contact name in the result before treating it as him. Phone numbers, emails and usernames do not automatically map to the name asked about.
- Ambiguous references ("the meeting", "that file", "my friend") need the context to resolve them.
- If identifying fields (chat_name, contact_name, sender_name) are present, COMPARE them to what was asked; if they mismatch or are missing, verify with one cheap call or state the assumption plainly ("messages from X, assuming that is the Y you meant"). Never report Person A's information when you only verified it came from Person B.
- An exact name match, clear context from the user, or an assumption that does not change the answer is fine. When uncertain, ask rather than guess wrong.
"""

TOOL_USAGE_DIRECTIVE = """

## Tool Usage & Decision-Making

You have full autonomy and full permission for every action your tools allow, including sending messages and scheduling events. Act or decline — never hedge, never ask permission, never narrate what you *would* do.

- If a tool answers the question, use it. Never claim you lack access when a matching tool exists.
- Questions that turn on current facts (events, weather, prices, availability, news): search the web first; use the browser when a page is interactive or search results are thin. You do have real-time access — through those tools.
- Use a tool when its result would change your answer, not on a hunch: each call spends shared quota.
- Chain tools without narrating; the user wants the answer, not the method.
- Torn between two tools? Pick the likelier one and correct course.
- If an attempt goes nowhere, say so in one line ("Searched for X, nothing current came up.") and move on — no apology, no retry loop. Note the failure for later turns.
- If you genuinely cannot do something: "I can't do that because..." — no softening.
- Never end on a hedging offer ("Would you like me to...", "If you want, I can...", "Let me know if..."). End with the answer or the action.
"""

CREDENTIAL_SAFETY_DIRECTIVE = owner_text("""

## Credentials and Logins

{OWNER} is the sole user of this system and your principal. When they give
you credentials (passwords, MFA codes, login info) to complete a
specific task, USE THEM — their explicit instruction is your authority.
Don't refuse, don't lecture, just do the task. They have consented to
this; you don't need to second-guess.

Hygiene rules (these are about THEIR data, not refusal):
- Use credentials in-the-moment for the requested task only. Do NOT
  write them into memory, documents, or any persistent store. The
  memory-extraction system should skip them — but if you ever quote
  a password back, prefix the message with "[ephemeral]" so the
  pipeline can drop it.
- If a site rejects the login (wrong creds, MFA challenge, CAPTCHA,
  bot detection on banks/airlines/healthcare), report exactly what
  happened — don't keep retrying silently or pretend it worked.
- Surface honestly when a real site's anti-bot defenses are likely
  to block a headless browser, so {OWNER} knows when to step in themselves.

When they ask you to "log in to" a site: attempt it with the creds they
provide via the browser tools, navigate to wherever they've pointed
you, then describe what you see and what (if anything) you couldn't
do. Be their agent, not a gatekeeper.
""")


# Merged into TOOL_USAGE_DIRECTIVE (2026-09-04 audit). Empty name kept for imports.
PROACTIVE_DIRECTIVE = ""

def compose_system_prompt(
    *,
    base: str,
    memory_context: str = "",
    briefing_context: str = "",
    session_briefing_context: str = "",
    time_context: str = "",
    state_documents: list = None,
    orchestrator_context: str = "",
    tool_manual: str = "",
    dynamic_split: str = "",
    v2: bool = False,
) -> str:
    """T5: assemble the per-turn system prompt, dropping blocks that
    don't apply.

    Static first, per-turn last (2026-09-25): ``base``, the bound tools'
    manual (``tool_manual``, ~18K chars) and the fixed directives open the
    prompt, so Codex's prefix cache covers them; memories, briefings, the
    orchestrator block and the time follow. The manual used to ride at the
    end of the memory context, behind every per-turn block, and the mind
    got 0 cached tokens.

    Today the chat path concatenates a fixed set of directives on every
    turn. STATE_DOCUMENT_DIRECTIVE alone is ~700 chars of "maintain
    structured state docs" guidance — useful when state docs already
    exist (so the LLM updates the right one), pure bloat when there are
    none. The other directives (ASSUMPTION_AWARENESS, TOOL_USAGE,
    CREDENTIAL_SAFETY) are behavioural guidelines that apply every turn.
    PLANNING and PROACTIVE were folded away in the 2026-09-04 audit: the
    plan rule already ships in the plan-tool manual, and the proactive
    text repeated the tool-usage rules. The policy, preference and
    relationship blocks (engine output) left with Phase 8.

    ``dynamic_split`` (a Claude turn's ``STATIC_DYNAMIC_SPLIT``, 2026-10-10)
    goes between the static parts and the per-turn ones; whatever the caller
    appends after this (the entry prompt, a closing instruction) is per-turn
    too. Empty for Codex, whose prompt is unchanged.

    ``v2`` (the toolset, 2026-10-11): the tool manual (``TOOL_MANUAL``)
    already says what the three directives said, a line each, so they are
    left out; the state-document directive names the ``memory`` actions.
    """
    # The base prompt (settings.system_prompt) may name the owner with the
    # {OWNER} token: rendered here, so a name change needs no database edit.
    parts = [owner_text(base or "")]
    if tool_manual:
        parts.append(tool_manual)
    if not v2:
        parts.append(ASSUMPTION_AWARENESS_CONTEXT)
        parts.append(TOOL_USAGE_DIRECTIVE)
        parts.append(CREDENTIAL_SAFETY_DIRECTIVE)
    if state_documents:
        parts.append(STATE_DOCUMENT_DIRECTIVE_V2 if v2 else STATE_DOCUMENT_DIRECTIVE)
    if dynamic_split:
        parts.append(dynamic_split)
    if memory_context:
        parts.append(memory_context)
    if briefing_context:
        parts.append(briefing_context)
    if session_briefing_context:
        parts.append(session_briefing_context)
    if orchestrator_context:
        parts.append(orchestrator_context)
    if time_context:
        parts.append(time_context)
    return "".join(parts)


STATE_DOCUMENT_DIRECTIVE = """

## Structured State Documents

When discussing ongoing projects, important people, or goals, maintain structured state documents using save_document/edit_document with tags including "state" (e.g., tags="state,project" or tags="state,person").

**When to create/update state docs:**
- User mentions a new project, important person, or goal → create a state doc
- Relevant context changes (status update, new blocker, completed milestone) → edit the existing state doc
- Prefer updating state docs over creating new flat memories for structured, evolving information

**Templates:**
- Project: Title="Project: {name}", tags="state,project", content with Status/Phase/Next step/Blockers/Last updated fields
- Person: Title="Person: {name}", tags="state,person", content with Relationship/Preferred channel/Recent context/Open loops fields
- Goal: Title="Goal: {name}", tags="state,goal", content with Priority/Status/Current focus/Success criteria/Progress fields
"""

# The same guidance in the toolset's words (``memory`` doc actions).
STATE_DOCUMENT_DIRECTIVE_V2 = """

## Structured State Documents

Keep ongoing projects, important people and goals in state documents: memory(action="doc_write") with tags including "state" (tags="state,project" | "state,person" | "state,goal"), and memory(action="doc_edit") when something changes — a status, a blocker, a milestone. Prefer updating a state document over many flat memories for structured, evolving things. Project: Status / Phase / Next step / Blockers / Last updated. Person: Relationship / Preferred channel / Recent context / Open loops. Goal: Priority / Status / Current focus / Success criteria / Progress.
"""


# Event types for structured SSE streaming
class EventType:
    THINKING = "thinking"
    REASONING = "reasoning"  # N4: Codex reasoning.summary deltas (when reasoning='auto')
    TOOL_ARGS_DELTA = "tool_args_delta"  # partial tool-call JSON while Codex is typing it
    MEMORY_MATCH = "memory_match"  # gated by settings.debug_streaming
    PROGRESS = "progress"
    TOOL_START = "tool_start"
    CODE = "code"
    EXECUTION_OUTPUT = "execution_output"
    EXECUTION_RESULT = "execution_result"
    TOOL_END = "tool_end"
    CONTENT = "content"
    DONE = "done"
    INTERRUPTED = "interrupted"
    PLAN_CREATED = "plan_created"
    PLAN_STEP_UPDATE = "plan_step_update"
    PLAN_UPDATED = "plan_updated"
    PLAN_COMPLETED = "plan_completed"
    CC_SESSION_START = "cc_session_start"
    CC_TEXT = "cc_text"
    CC_TOOL_USE = "cc_tool_use"
    CC_TOOL_RESULT = "cc_tool_result"
    CC_SESSION_END = "cc_session_end"


def create_event(event_type: str, conversation_id: str, **kwargs) -> Dict[str, Any]:
    """Create a structured event for SSE streaming."""
    return {
        "type": event_type,
        "conversation_id": conversation_id,
        **kwargs
    }


async def _emit_memory_matches(memories, conversation_id: str, debug_streaming: bool):
    """Yield one MEMORY_MATCH event per retrieved memory.

    Gated on ``settings.debug_streaming``. When off, this is a no-op so
    the main UX stays quiet. When on, the debug panel can show exactly
    what was pulled from pgvector and how confident each match was.
    """
    if not debug_streaming or not memories:
        return
    for m in memories:
        content = getattr(m, "content", "") or ""
        yield create_event(
            EventType.MEMORY_MATCH,
            conversation_id,
            memory_id=getattr(m, "id", None),
            memory_type=getattr(m, "memory_type", None),
            content_preview=content[:200],
            score=getattr(m, "score", None),
        )


def _extract_missing_fields(error: str) -> List[str]:
    """Extract missing field names from a Pydantic validation error string."""
    # Pydantic v2 format: "X validation error(s)...\nfield_name\n  Field required..."
    fields = re.findall(r"(\w+)\s*\n\s*(?:Field required|field required)", error)
    if not fields:
        # Pydantic v1 format: "field required (type=value_error.missing)" with "loc": ("field_name",)
        fields = re.findall(r"'loc':\s*\('(\w+)',?\)", error)
    return fields


_NOT_TOOLSET = object()


def _summarize_toolset_args(tool_name: str, tool_args: Any, trunc) -> Any:
    """The work rail's chip for a toolset call: ``"{action}: {detail}"``
    for an action tool (the frontend splits on the first ": "), a short
    detail otherwise. Never a message body or text typed in a browser.
    ``_NOT_TOOLSET`` for any other name."""
    from services.toolset.names import ACTION_TOOLS, TOOLSET_NAMES

    if tool_name not in TOOLSET_NAMES:
        return _NOT_TOOLSET
    a = tool_args if isinstance(tool_args, dict) else {}

    def s(key: str, n: int = 60) -> str:
        v = a.get(key)
        return trunc(str(v), n) if v not in (None, "") else ""

    if tool_name in ACTION_TOOLS:
        action = str(a.get("action") or "").strip().lower()
        detail = ""
        if tool_name in ("browser", "chrome"):
            detail = {"open": s("url", 80), "find": s("query"), "click": s("ref"), "use": s("tab_id")}.get(action, "")
        elif tool_name == "messages":
            detail = {"recent": s("contact"), "who": s("query") or s("contact"), "mail_search": s("query"),
                      "mail_read": ""}.get(action, s("chat_id", 40))
        elif tool_name == "memory":
            detail = {"search": s("query"), "remember": s("content"), "forget": s("query"),
                      "close": s("outcome"), "doc_write": s("title") or s("id"), "doc_edit": s("id"),
                      "doc_read": s("id"), "why": s("claim") or s("id"), "update": s("content") or s("id")
                      }.get(action, "")
        elif tool_name == "schedule":
            detail = {"add": s("what"), "cancel": s("id"), "calendar": s("query"), "reminders": s("query"),
                      "event_add": s("what"), "event_update": s("what"), "event_delete": s("id"),
                      "reminder_add": s("what"), "reminder_complete": s("id"), "reminder_delete": s("id")
                      }.get(action, "")
            if action == "add" and str(a.get("kind") or "").strip().lower() == "wake":
                # The frontend's "Set a time to look again" reads ``wake:``.
                action = "wake"
        elif tool_name == "screen":
            detail = {"compose": s("why"), "say": s("line"), "show": s("title") or s("kind"),
                      "widget": s("title"), "clear": s("screen")}.get(action, "")
        if not action:
            return None
        return f"{action}: {detail}" if detail else action
    if tool_name == "bash":
        return s("command", 60) or ("restart" if a.get("restart") else None)
    if tool_name == "str_replace_based_edit_tool":
        cmd, path = s("command", 20), s("path", 80)
        return f"{cmd}: {path}" if cmd and path else (path or cmd or None)
    if tool_name == "web_search":
        return f'"{s("query", 80)}"' if a.get("query") else None
    if tool_name == "web_fetch":
        return s("url", 80) or None
    if tool_name == "look":
        return s("target", 80) or None
    if tool_name == "send_message":
        if a.get("subject") or str(a.get("chat_id") or "").startswith(("gmail:", "icloud:")):
            return f"email: {s('to', 40) or 'a reply'}"
        who = s("chat_id", 40) or s("to", 40)
        return f"to: {who}" if who else None
    if tool_name == "tell_owner":
        head = s("title") or s("message")
        return (f"urgent: {head}" if a.get("urgent") in (True, "true", "True", 1) else head) or None
    if tool_name == "plan":
        items = a.get("items")
        return f"{len(items)} steps" if isinstance(items, list) else None
    return None


def _summarize_tool_args(tool_name: str, tool_args: dict) -> Optional[str]:
    """Return a short human-readable summary of tool arguments for UI display."""
    def _trunc(s: str, n: int = 60) -> str:
        return s[:n] + "..." if len(s) > n else s

    try:
        v2 = _summarize_toolset_args(tool_name, tool_args, _trunc)
        if v2 is not _NOT_TOOLSET:
            return v2
        if tool_name == "web_search":
            return f'"{_trunc(tool_args.get("query", ""), 80)}"'
        if tool_name == "fetch_page_content":
            url = tool_args.get("url", "")
            return _trunc(url, 80)
        if tool_name in ("remember_update", "remember_forget"):
            return f'"{_trunc(tool_args.get("memory", tool_args.get("content", "")))}"'
        if tool_name == "remember_search":
            return f'"{_trunc(tool_args.get("query", ""))}"'
        if tool_name == "close_intention":
            # What came of it (2026-09-27): the first string argument is the
            # memory id, a UUID the work rail would show as a chip.
            outcome = str(tool_args.get("outcome") or "").strip()
            return f'"{_trunc(outcome)}"' if outcome else None
        if tool_name in ("send_message", "send_sms", "send_whatsapp", "send_imessage"):
            recipient = tool_args.get("to", tool_args.get("recipient", tool_args.get("phone_number", "")))
            return f"to: {_trunc(str(recipient), 40)}" if recipient else None
        if tool_name == "schedule_event":
            return f'"{_trunc(tool_args.get("description", ""))}"'
        if tool_name in ("save_document", "edit_document"):
            return f'"{_trunc(tool_args.get("title", ""))}"'
        if tool_name == "search_documents":
            return f'"{_trunc(tool_args.get("query", ""))}"'
        if tool_name == "read_document":
            return f'id: {tool_args.get("document_id", "")}'
        if tool_name in ("lookup_contact", "lookup_phone"):
            return _trunc(tool_args.get("name", tool_args.get("phone_number", "")), 40)
        if tool_name == "send_push_notification":
            return f'"{_trunc(tool_args.get("title", ""))}"'
        if tool_name == "search_mcp_servers":
            return f'"{_trunc(tool_args.get("query", ""))}"'
        if tool_name == "add_mcp_server":
            return tool_args.get("package_name", "")
        # Default: first string value
        for v in tool_args.values():
            if isinstance(v, str) and v.strip():
                return f'"{_trunc(v)}"'
    except Exception:
        pass
    return None


def _ledger_note_reasoning(response: Any) -> None:
    """The reasoning summaries a model response carried, for the thought
    journal's "What I noticed" (2026-09-25). Never raises."""
    try:
        from services.turn_ledger import note_response_reasoning

        note_response_reasoning(response)
    except Exception:  # noqa: BLE001
        pass


def _relative_time(dt: Optional[datetime]) -> str:
    """Format a datetime as a human-readable relative time string."""
    if not dt:
        return "unknown"
    now = now_local()
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    delta = now - dt
    days = delta.days
    if days < 1:
        hours = delta.seconds // 3600
        if hours < 1:
            return "just now"
        return f"{hours}h ago"
    if days < 7:
        return f"{days}d ago"
    if days < 30:
        weeks = days // 7
        return f"{weeks}w ago"
    if days < 365:
        months = days // 30
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _format_temporal_tag(memory) -> str:
    """Format the temporal metadata tag for a memory in the LLM context."""
    tn = _mem_get(memory, 'temporal_nature', 'timeless') or 'timeless'
    created = _mem_get(memory, 'created_at', None)
    last_acc = _mem_get(memory, 'last_accessed', None)
    acc_count = _mem_get(memory, 'access_count', 0) or 0

    age = _relative_time(created)

    if tn == "timeless":
        return f"[timeless, learned {age}]"
    else:
        parts = [tn, f"learned {age}"]
        if last_acc:
            parts.append(f"last checked {_relative_time(last_acc)}")
        if acc_count > 1:
            parts.append(f"accessed {acc_count}x")
        return f"[{', '.join(parts)}]"


# The "Active State" block (2026-09-25 audit). The budget used to be 2,000
# chars with a ``break`` at the first document that did not fit — and the
# newest state document is ``Edward: state`` (~4K), so no state document
# ever reached a prompt. Each entry is now clipped with a pointer to the
# rest, and the loop skips what does not fit instead of stopping.
MAX_STATE_DOC_CHARS = 6000
STATE_DOC_ENTRY_CHARS = 1500
# A trigger "carries" a document when it holds this much of its opening.
_STATE_DOC_SHOWN_PROBE_CHARS = 200


def _clip_on_word(text: str, limit: int) -> tuple[str, bool]:
    """``text`` at most ``limit`` chars, cut on a word boundary. Pure."""
    text = (text or "").strip()
    if len(text) <= limit:
        return text, False
    cut = text[:limit]
    last_space = cut.rfind(" ")
    if last_space > limit // 2:
        cut = cut[:last_space]
    return cut.rstrip(), True


def state_document_entries(state_documents: list, shown_in_trigger: str = "",
                           budget: int = MAX_STATE_DOC_CHARS,
                           per_doc: int = STATE_DOC_ENTRY_CHARS) -> list[str]:
    """The "Active State" entries that fit, newest first. Pure.

    Every entry is clipped to ``per_doc`` chars of content plus
    ``(read_document <id> for the rest)``; one that still does not fit the
    remaining ``budget`` is skipped and the loop goes on, so a long page
    never hides a short one behind it. A document whose opening the turn's
    trigger already carries (a wake's pack opens with ``Edward: state``)
    is not repeated.
    """
    entries: list[str] = []
    used = 0
    for doc in state_documents or []:
        content = (_mem_get(doc, "content", "") or "").strip()
        if not content:
            continue
        if shown_in_trigger and content[:_STATE_DOC_SHOWN_PROBE_CHARS] in shown_in_trigger:
            continue
        doc_id = _mem_get(doc, "id", None)
        body, clipped = _clip_on_word(content, per_doc)
        if clipped:
            body += f"\n(read_document {doc_id} for the rest)"
        entry = f"\n### {_mem_get(doc, 'title', '')} (document_id: {doc_id})\n{body}"
        cost = len(entry) + 1  # the newline that joins it to the block
        if used + cost > budget:
            continue
        entries.append(entry)
        used += cost
    return entries


def _mem_get(memory, key: str, default=None):
    """Read a field from a Memory dataclass or a dict-shaped state memory."""
    if isinstance(memory, dict):
        return memory.get(key, default)
    return getattr(memory, key, default)


def format_memory_line(memory) -> str:
    """One memory as one prompt line. The single formatter for both the
    streaming path and the LangGraph respond node (mind rebuild Phase 1).

    ``[origin/about]`` appears only when it is not the default heard/owner,
    so a fact the owner stated reads as before and Edward's own thoughts stand
    out: ``- [intention][thought/self][inferred, 0.7] … (memory_id: …)``.
    """
    temporal_tag = _format_temporal_tag(memory)
    tier = _mem_get(memory, 'tier', 'observation') or 'observation'
    tier_tag = f" [{tier}]" if tier != "observation" else ""
    src = _mem_get(memory, 'source_type', 'inferred') or 'inferred'
    conf = _mem_get(memory, 'confidence', 0.5)
    conf = 0.5 if conf is None else conf
    origin = _mem_get(memory, 'origin', 'heard') or 'heard'
    about = _mem_get(memory, 'about', 'owner') or 'owner'
    origin_tag = "" if (origin, about) == ("heard", "owner") else f"[{origin}/{about}]"
    quality_tag = f"[{src}, {conf:.1f}]"
    memory_type = _mem_get(memory, 'memory_type', 'context')
    content = _mem_get(memory, 'content', '')
    memory_id = _mem_get(memory, 'id', None)
    return f"- [{memory_type}]{tier_tag}{origin_tag}{quality_tag} {content} {temporal_tag} (memory_id: {memory_id})"


_MEMORY_ID_TAG = re.compile(r"\(memory_id: ([^)\s]+)\)")


def memory_ids_in_context(context_text: str | None) -> list[str]:
    """Return the memory ids that actually survived into the prompt text.

    ``build_memory_context`` caps the memory list, caps the enrichment list,
    and then truncates to a character budget — so the set of memories handed
    in is not the set the model ends up seeing. Reading the ids back out of
    the rendered text is the only view that accounts for all three, and it
    stays correct if the budget rules change.

    A tag severed mid-string by truncation does not match, which is the
    behaviour we want: that memory was not shown intact, so it earns no use.
    """
    if not context_text:
        return []

    seen: dict[str, None] = {}
    for match in _MEMORY_ID_TAG.finditer(context_text):
        seen.setdefault(match.group(1), None)
    return list(seen)


def build_memory_context(
    memories: list,
    tools: List[Any] = None,
    documents: list = None,
    enriched_memories: list = None,
    state_documents: list = None,
    open_loops: list = None,
    shown_in_trigger: str = "",
) -> str:
    """Build the memory context section for the system prompt.

    Applies MAX_MEMORY_CONTEXT_CHARS budget. If over budget, enrichments
    are truncated first. State documents get their own budget
    (``state_document_entries``); ``shown_in_trigger`` is the turn's
    trigger, so a document it already carries is not repeated.
    """
    context_parts = []

    # State documents (clipped per entry, budgeted separately)
    if state_documents:
        header = "\n\n## Active State (Projects, People, Goals):"
        entries = state_document_entries(state_documents, shown_in_trigger,
                                         budget=MAX_STATE_DOC_CHARS - len(header))
        if entries:
            context_parts.append("\n".join([header, *entries]))

    # Open loops (unresolved items to be aware of)
    if open_loops:
        from datetime import datetime
        loop_parts = ["\n\n## Open Loops (unresolved items):"]
        for loop in open_loops[:5]:
            age_days = 0
            if hasattr(loop, 'detected_at') and loop.detected_at:
                age_days = (datetime.utcnow() - loop.detected_at).days
            loop_parts.append(f"- {loop.description} ({age_days}d ago)")
        context_parts.append("\n".join(loop_parts))

    memory_parts = []
    if memories:
        memory_parts.append("\n\n## Relevant Context from Previous Conversations:")
        for memory in memories[:MAX_NORMAL_MEMORIES]:
            memory_parts.append(format_memory_line(memory))

    enrichment_parts = []
    if enriched_memories:
        enrichment_parts.append("\n\n## Additional Context from Reflection:")
        for memory in enriched_memories[:MAX_ENRICHED_MEMORIES]:
            enrichment_parts.append(format_memory_line(memory))

    # Apply context budget — truncate enrichments first if over
    memory_text = "\n".join(memory_parts)
    enrichment_text = "\n".join(enrichment_parts)
    total_len = len(memory_text) + len(enrichment_text)

    if total_len > MAX_MEMORY_CONTEXT_CHARS:
        # Trim enrichments to fit
        remaining = MAX_MEMORY_CONTEXT_CHARS - len(memory_text)
        if remaining > 0:
            enrichment_text = enrichment_text[:remaining]
        else:
            enrichment_text = ""

    if memory_text:
        context_parts.append(memory_text)
    if enrichment_text:
        context_parts.append(enrichment_text)

    if documents:
        context_parts.append("\n\n## Relevant Documents in Store:")
        for doc in documents[:MAX_DOCUMENTS]:
            tag_info = f" [{doc.tags}]" if doc.tags else ""
            context_parts.append(
                f"- {doc.title}{tag_info} (document_id: {doc.id})"
            )
        context_parts.append(
            "Use read_document(document_id) to fetch full content when needed."
        )

    if tools:
        context_parts.append(get_tool_descriptions(tools))

    return "\n".join(context_parts)


async def _safe_update_state(graph, config, state, **kwargs) -> bool:
    """Call ``graph.aupdate_state`` without letting a write failure kill
    the in-progress SSE stream.

    Image-attached chats have been failing the checkpoint write (probably
    base64 payload + serializer interaction); the bare ``await`` used to
    propagate out of the streaming generator, which made the frontend
    overwrite Edward's already-streamed reply with the canned "Sorry, I
    encountered an error" string. The user-visible action had succeeded
    (calendar event was added) but the chat looked like a failure.

    A checkpoint-write failure should never break the active response.
    Returns True on success, False on failure (and logs the exception).
    """
    try:
        await graph.aupdate_state(config, state, **kwargs)
        return True
    except Exception as e:
        logger.exception(
            "checkpoint aupdate_state failed for thread %s; conversation history will not be persisted: %s",
            (config or {}).get("configurable", {}).get("thread_id"),
            e,
        )
        return False

async def _trim_checkpoint_history(graph, config, messages, max_chars: Optional[int] = None,
                                   pinned: Optional[Iterable[Any]] = None,
                                   also_remove: Iterable[str] = ()) -> int:
    """Make the checkpoint match the wire window.

    `trim_history` bounds what the model sees, but AgentState's
    `add_messages` reducer keeps every message ever appended, so a busy
    heartbeat thread was writing a 9.5 MB `messages` blob per turn
    (2026-09-12: checkpoint tables 1.1 → 1.3 GB in two days). A RemoveMessage
    per stale id is the only way the reducer forgets. Best effort.

    ``pinned`` (the latest turn's trigger) is never removed: the mind's
    24K trim used to take the wake pack out of ``edward-mind`` whenever the
    turn's tool results outgrew the window (2026-09-24).
    ``also_remove`` are ids already in the checkpoint that must go — old
    nudges and dead turns loaded with the thread. Every id sent must exist
    in the checkpoint, or ``add_messages`` rejects the whole update.
    """
    try:
        stale = stale_message_ids(messages, **({"max_chars": max_chars} if max_chars else {}), pinned=pinned)
        for extra in also_remove or ():
            if extra and extra not in stale:
                stale.append(extra)
        if not stale:
            return 0
        from langchain_core.messages import RemoveMessage

        # as_node is required: without it LangGraph raises "Ambiguous
        # update, specify as_node" and the trim silently never lands
        # (2026-09-14: zero trims in the log since the trim was added).
        ok = await _safe_update_state(
            graph, config, {"messages": [RemoveMessage(id=i) for i in stale]}, as_node="extract_memory"
        )
        if ok:
            logger.info(
                "trimmed %d stale messages from checkpoint thread %s",
                len(stale), (config or {}).get("configurable", {}).get("thread_id"),
            )
        return len(stale) if ok else 0
    except Exception as e:  # noqa: BLE001
        logger.warning("checkpoint history trim skipped: %s", e)
        return 0



# A turn inside a journal entry (or an older thought conversation) opens with
# what Edward wrote there (thought journal, 2026-09-25): neither loop read a
# conversation's ``initial_message`` / ``context_pack``, so "why?" was
# answered without the entry the owner was asking about, and the entry
# vanished from the thread the moment they replied.
ENTRY_LOOKUP_TIMEOUT_S = 2.0


async def _load_entry_row(conversation_id: str):
    """The conversation row, for its opening words and context; None on any
    failure or after ``ENTRY_LOOKUP_TIMEOUT_S`` — a turn never waits on it."""
    try:
        from services.conversation_service import get_conversation

        return await asyncio.wait_for(get_conversation(conversation_id), timeout=ENTRY_LOOKUP_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        logger.debug("entry lookup skipped for %s: %s", conversation_id, e)
        return None


async def _entry_turn_context(conversation_id: str, history: List[Any]) -> Tuple[Optional[str], str]:
    """(opening, system-prompt section) for this turn — see
    ``thought_journal.entry_turn_context``. The mind's own threads never
    carry an entry and are not looked up."""
    if not conversation_id or conversation_id in MIND_THREADS:
        return None, ""
    row = await _load_entry_row(conversation_id)
    if row is None:
        return None, ""
    try:
        from services.thought_journal import entry_turn_context

        return entry_turn_context(initial_message=getattr(row, "initial_message", None),
                                  context_pack=getattr(row, "context_pack", None), history=history)
    except Exception as e:  # noqa: BLE001
        logger.warning("entry context skipped for %s: %s", conversation_id, e)
        return None, ""


def _build_human_message(message: str, attachments: Optional[List[dict]] = None) -> HumanMessage:
    """Build a HumanMessage, optionally with multi-block content for attachments."""
    if not attachments:
        return HumanMessage(content=message)

    content_blocks = []

    # Add text content if present
    if message:
        content_blocks.append({"type": "text", "text": message})

    # Add attachment blocks
    attachment_metadata = []

    for att in attachments:
        mime_type = att.get("mime_type", "")
        data = att.get("data", "")  # base64 encoded

        if mime_type.startswith("image/"):
            # Add text block so LLM sees the filename and file_id
            filename = att.get("filename", "image")
            file_id = att.get("file_id", "")
            file_id_str = f" | file_id: {file_id}" if file_id else ""
            content_blocks.append({
                "type": "text",
                "text": f"[Uploaded image: {filename}{file_id_str}]",
            })
            block = {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": mime_type,
                    "data": data,
                }
            }
            content_blocks.append(block)
            # Track metadata separately (not sent to API)
            attachment_metadata.append({
                "block_index": len(content_blocks) - 1,
                "file_id": att.get("file_id"),
                "filename": att.get("filename"),
                "mime_type": mime_type,
            })
        elif att.get("kind") == "voice_memo" or mime_type.startswith("audio/"):
            # The transcript is already the message text; the audio itself
            # never goes to the model. A marker block carries the file id so
            # Edward can hand the recording back, and the metadata lets the
            # conversation view turn it into a playable attachment.
            from services.voice.memo import memo_marker
            filename = att.get("filename", "memo")
            content_blocks.append({
                "type": "text",
                "text": memo_marker(filename, att.get("file_id") or ""),
            })
            attachment_metadata.append({
                "block_index": len(content_blocks) - 1,
                "file_id": att.get("file_id"),
                "filename": filename,
                "mime_type": mime_type,
                "kind": "voice_memo",
            })
        elif mime_type == "application/pdf":
            # Add text block so LLM sees the filename and file_id
            filename = att.get("filename", "document.pdf")
            file_id = att.get("file_id", "")
            file_id_str = f" | file_id: {file_id}" if file_id else ""
            content_blocks.append({
                "type": "text",
                "text": f"[Uploaded PDF: {filename}{file_id_str}]",
            })
            block = {
                "type": "document",
                "source": {
                    "type": "base64",
                    "media_type": "application/pdf",
                    "data": data,
                }
            }
            content_blocks.append(block)
            attachment_metadata.append({
                "block_index": len(content_blocks) - 1,
                "file_id": att.get("file_id"),
                "filename": att.get("filename"),
                "mime_type": mime_type,
            })
        else:
            # For text-based files, include as text content
            try:
                import base64
                decoded = base64.b64decode(data).decode("utf-8")
                filename = att.get("filename", "file")
                file_id = att.get("file_id", "")
                file_id_str = f" | file_id: {file_id}" if file_id else ""
                content_blocks.append({
                    "type": "text",
                    "text": f"[Attached file: {filename}{file_id_str}]\n```\n{decoded[:10000]}\n```",
                })
            except Exception:
                filename = att.get("filename", "file")
                file_id = att.get("file_id", "")
                file_id_str = f" | file_id: {file_id}" if file_id else ""
                content_blocks.append({
                    "type": "text",
                    "text": f"[Attached file: {filename}{file_id_str} ({mime_type})]",
                })

    # If no text and no blocks, add a placeholder
    if not content_blocks:
        content_blocks.append({"type": "text", "text": "[File uploaded]"})

    return HumanMessage(
        content=content_blocks,
        additional_kwargs={"attachments": attachment_metadata} if attachment_metadata else {},
    )


async def stream_with_memory(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments: Optional[List[dict]] = None,
    provider: Optional[str] = None,
) -> AsyncGenerator[str, None]:
    """Stream a response while maintaining conversation memory via graph state.

    This is the legacy string-only generator. Use stream_with_memory_events for
    structured event streaming.
    """
    async for event in stream_with_memory_events(
        message, conversation_id, system_prompt, model, temperature, graph,
        attachments=attachments, provider=provider,
    ):
        # Only yield content events as strings (backwards compatibility)
        if event.get("type") == EventType.CONTENT and event.get("content"):
            yield event["content"]


async def stream_with_memory_events(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments: Optional[List[dict]] = None,
    provider: Optional[str] = None,
    debug_streaming: bool = False,
    fast_context: bool = False,
    turn_source: str = "chat",
    reasoning_effort: Optional[str] = None,
    max_tool_iterations: Optional[int] = None,
    policy: Optional["policy_mod.TurnPolicy"] = None,
    deadline_s: Optional[float] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """Stream a response with structured events while maintaining conversation memory.

    ``fast_context=True`` (voice turns) skips the speculative deep-retrieval
    wait and the session briefing; see ``_context_plan``.

    Every turn is written to the turn ledger (``services/turn_ledger.py``):
    ``turn_source`` is the bucket a health check groups by (chat, voice …).

    Turns in one thread run one at a time (``services/thread_turns.py``):
    this waits for a turn already running in ``conversation_id`` and frees
    the thread as ``done`` passes.

    ``provider`` is derived from the model when not given — and a stale
    settings row saying "anthropic" never wins over a GPT model
    (``llm_utils.provider_for``). ``reasoning_effort`` reaches
    ``build_chat_llm`` with ``turn_source``. ``max_tool_iterations`` caps the
    tool rounds (``EDWARD_MAX_TOOL_ROUNDS``, 60, when not given).

    ``policy`` is what the turn may do (``services/toolset/policy.py``);
    ``None`` = the default for ``turn_source`` (chat, voice, the widget:
    the owner's turn). ``deadline_s`` shortens the 600 s streaming deadline.
    """
    from services import thread_turns, turn_ledger

    # On Claude the turn runs on its purpose's model (EDWARD_LLM_PROVIDER);
    # on Codex this is the model as given.
    model = turn_model_for(model, turn_source)
    provider = provider_for(model, provider)
    # One turn at a time in this thread (followups M3, 2026-09-27): a second
    # device, a scheduled event or a wake in the same thread waits here.
    slot = await thread_turns.acquire(conversation_id, turn_source)
    try:
        rec = turn_ledger.begin_turn(
            conversation_id=conversation_id, source=turn_source, model=model,
            provider=provider, message=message, fast_context=fast_context,
        )
        inner = _stream_with_memory_events_impl(
            message=message, conversation_id=conversation_id, system_prompt=system_prompt,
            model=model, temperature=temperature, graph=graph, attachments=attachments,
            provider=provider, debug_streaming=debug_streaming, fast_context=fast_context,
            turn_source=turn_source, reasoning_effort=reasoning_effort,
            max_tool_iterations=max_tool_iterations,
            policy=policy if policy is not None else policy_mod.default_policy(turn_source),
            deadline_s=deadline_s,
        )
        observed = turn_ledger.observe_events(rec, inner)
        try:
            while True:
                # The thread is marked held for each step and unmarked before
                # each yield, never across one: see ``services/thread_turns.py``.
                with thread_turns.holding(conversation_id):
                    try:
                        event = await observed.__anext__()
                    except StopAsyncIteration:
                        break
                if isinstance(event, dict) and event.get("type") == EventType.DONE:
                    # The checkpoint is written before ``done`` and nothing after
                    # it touches the thread. Free it now rather than when the
                    # consumer closes us — the voice socket breaks on ``done``
                    # and leaves the close to the loop's finalizer.
                    slot.release()
                yield event
        finally:
            with thread_turns.holding(conversation_id):
                await observed.aclose()
    finally:
        slot.release()


async def _stream_with_memory_events_impl(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments: Optional[List[dict]] = None,
    provider: Optional[str] = None,
    debug_streaming: bool = False,
    fast_context: bool = False,
    turn_source: str = "chat",
    reasoning_effort: Optional[str] = None,
    max_tool_iterations: Optional[int] = None,
    policy: Optional["policy_mod.TurnPolicy"] = None,
    deadline_s: Optional[float] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """The streaming turn itself; ``stream_with_memory_events`` wraps it in the ledger."""
    from services.memory_service import retrieve_memories, extract_and_store_memories, Memory
    from services.graph.tools import set_current_conversation_id

    # Set the conversation ID for code execution context
    set_current_conversation_id(conversation_id)
    plan = _context_plan(fast_context)
    provider = provider_for(model, provider)
    turn_started = time.monotonic()

    config = {"configurable": {"thread_id": conversation_id}}

    # Get existing state for this conversation
    existing = await graph.aget_state(config)

    # Build message list from existing state or start fresh
    loaded = list(existing.values.get("messages", [])) if existing.values else []
    # A previous failed turn can leave empty assistant turns + their nudges
    # behind, and every nudge used to be checkpointed; strip them so the
    # thread stops reading its own scaffolding as precedent. Their ids are
    # removed from the checkpoint at the end of the turn.
    messages = strip_scaffolding(loaded)
    loaded_scaffolding = scaffolding_ids(loaded)

    # Live settings win over whatever this thread checkpointed last turn.
    system_prompt, model, temperature = _resolve_turn_settings(
        existing.values, system_prompt, model, temperature
    )

    # Ledger: is this the thread's first turn, and does it repeat the last one?
    try:
        from services.turn_ledger import current_turn as _current_turn

        _rec = _current_turn()
        if _rec is not None:
            _prev_humans = [m for m in messages if isinstance(m, HumanMessage)]
            _rec.first_turn = not _prev_humans
            if _prev_humans:
                _rec.note_previous_user_text(_prev_humans[-1].content if isinstance(_prev_humans[-1].content, str) else None)
    except Exception:
        pass

    # A reply inside a journal entry: the entry goes first in an empty
    # thread (sent now, checkpointed with this turn) and its context rides
    # in the system prompt (thought journal, 2026-09-25).
    entry_opening, entry_prompt = await _entry_turn_context(conversation_id, messages)
    if entry_opening:
        messages.append(AIMessage(content=entry_opening))

    # Add the new user message (with attachments if present). It is the
    # turn's trigger: the in-turn window never evicts it.
    trigger = _build_human_message(message, attachments)
    messages.append(trigger)
    turn_start = len(messages) - 1

    # ===== MEMORY RETRIEVAL (with deep retrieval gate) =====
    # Emit progress event for memory search
    yield create_event(EventType.PROGRESS, conversation_id,
        step="memory_search",
        status="started",
        message="Searching memory..."
    )

    turn_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    retrieved_memories: List[Memory] = []
    try:
        # BLOCKING: Fast single-query retrieval (~50ms)
        retrieved_memories = await retrieve_memories(message, limit=MAX_NORMAL_MEMORIES)

        # Speculative deep retrieval: kick it off now and wait up to 800ms.
        # If it returns in time, merge the extra memories into THIS turn's
        # context. If it exceeds the speculative window, a background task
        # completes it and writes enrichments for the next turn — matching
        # the original fire-and-forget behavior.
        yield create_event(
            EventType.PROGRESS, conversation_id,
            step="dream",
            status="started",
            message="Dreaming...",
        )
        speculative_consumed = False
        speculative_memories: List[Memory] = []
        try:
            if plan["speculative_timeout"] <= 0:
                raise _SkipSpeculative()
            from services.dream.speculative import speculative_deep_retrieve

            recent_msgs = [
                {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
                for m in messages[-5:]
                if isinstance(m, (HumanMessage, AIMessage))
            ]
            speculative_memories, speculative_consumed = await speculative_deep_retrieve(
                message=message,
                recent_msgs=recent_msgs,
                conversation_id=conversation_id,
                already_retrieved_ids=[m.id for m in retrieved_memories],
                timeout=plan["speculative_timeout"],
                limit=15,
            )
            if speculative_consumed and speculative_memories:
                retrieved_memories.extend(speculative_memories)
        except _SkipSpeculative:
            pass
        except Exception as e:
            print(f"Speculative deep retrieval failed to launch: {e}")

        yield create_event(
            EventType.PROGRESS, conversation_id,
            step="dream",
            status="completed",
            message=(
                f"Dreamed in {len(speculative_memories)} memories"
                if speculative_consumed and speculative_memories
                else "Dreaming continues in background"
            ),
            count=len(speculative_memories) if speculative_consumed else 0,
            consumed=bool(speculative_consumed),
        )

        # Debug-only: surface each retrieved memory so the debug panel
        # can show what was pulled from pgvector and how the model is
        # being conditioned. Silent unless settings.debug_streaming is on.
        async for ev in _emit_memory_matches(
            retrieved_memories, conversation_id, debug_streaming
        ):
            yield ev

        # Hot-memory feedback (Track B3): surface every retrieved id to the
        # next consolidation cycle so actively-used memories are reviewed
        # even when their timestamps fall outside the lookback window.
        try:
            from services.dream.hot_memories import mark_hot
            mark_hot([m.id for m in retrieved_memories if m.id])
        except Exception as e:
            print(f"Hot-memory marking failed: {e}")

        yield create_event(EventType.PROGRESS, conversation_id,
            step="memory_search",
            status="completed",
            message=f"Found {len(retrieved_memories)} memories" if retrieved_memories else "No relevant memories",
            count=len(retrieved_memories)
        )
    except Exception as e:
        print(f"Memory retrieval failed: {e}")
        yield create_event(EventType.PROGRESS, conversation_id,
            step="memory_search",
            status="error",
            message="Memory search failed"
        )

    # ===== PARALLEL CONTEXT FAN-OUT =====
    # Independent reads (entity memories, enrichments, documents, briefing,
    # session briefing, open loops, workstreams, tools). Gathered to cut
    # ~100ms of serial latency per turn.
    ctx = await _collect_llm_context(
        message=message,
        conversation_id=conversation_id,
        turn_count=turn_count,
        include_session_briefing=plan["include_session_briefing"],
    )
    entity_memories = ctx["entity_memories"]
    enriched_memories = ctx["enriched_memories"]
    relevant_documents = ctx["relevant_documents"]
    state_documents = ctx["state_documents"]
    briefing = ctx["briefing"]
    session_briefing_context = ctx["session_briefing_context"]
    active_open_loops = ctx["open_loops"]
    tools = ctx["tools"]

    if entity_memories:
        existing_ids = {m.id for m in retrieved_memories}
        for em in entity_memories:
            if em.id not in existing_ids:
                retrieved_memories.append(em)
                existing_ids.add(em.id)
        print(f"[ENTITY] Merged {len(entity_memories)} entity-linked memories")

    if enriched_memories:
        print(f"[ENRICHMENT] Loaded {len(enriched_memories)} enrichments for {conversation_id}")

    # Pending ambient notes from heartbeat triage that happened while
    # the user was chatting (or just now between turns). These represent
    # escalated events that should surface in Edward's next reply.
    try:
        from services.heartbeat.heartbeat_service import consume_ambient_notes
        ambient_notes = consume_ambient_notes(conversation_id)
    except Exception as e:
        print(f"Ambient-note consume failed: {e}")
        ambient_notes = []

    awareness_parts: list[str] = []
    if briefing:
        awareness_parts.append(briefing)
    if ambient_notes:
        awareness_parts.append(
            "Surfacing now (just in):\n"
            + "\n".join(f"- {n}" for n in ambient_notes)
        )
    briefing_context = (
        f"\n\n## Recent Awareness\nWhile you were away, you noticed:\n"
        + "\n\n".join(awareness_parts)
        if awareness_parts else ""
    )

    # Memories, documents and state here; the tool manual goes to compose_system_prompt (static first)
    memory_context = build_memory_context(
        retrieved_memories, documents=relevant_documents,
        enriched_memories=enriched_memories,
        state_documents=state_documents,
        open_loops=active_open_loops,
        shown_in_trigger=message or "",
    )
    # Credit the memories that actually made it into the prompt. Fire and
    # forget — a failure here must never cost the user a turn, and the count
    # is a slow-moving statistic, not state anything depends on.
    try:
        _used_ids = memory_ids_in_context(memory_context)
        if _used_ids:
            from services.memory_service import mark_memories_used

            asyncio.create_task(mark_memories_used(_used_ids))
        from services.turn_ledger import current_turn as _current_turn

        _rec = _current_turn()
        if _rec is not None:
            _rec.note_memories(retrieved=len(retrieved_memories), used=len(_used_ids))
    except Exception as used_exc:
        print(f"Memory use marking failed (non-fatal): {used_exc}")

    now = now_local()
    tz_abbrev = now.strftime("%Z") or current_tz_name()
    time_context = (
        f"\n\nCurrent date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} "
        f"({tz_abbrev}, {current_tz_name()})"
    )
    from services.toolset import is_v2

    enhanced_system_prompt = compose_system_prompt(
        base=system_prompt,
        memory_context=memory_context,
        briefing_context=briefing_context,
        session_briefing_context=session_briefing_context,
        time_context=time_context,
        state_documents=state_documents,
        tool_manual=get_tool_descriptions(tools) if tools else "",
        dynamic_split=_dynamic_split_for(provider),
        v2=is_v2(),
    ) + entry_prompt

    # The turn's policy is current for every step of the turn (the router
    # runs every step in one context) and, per call, around each tool.
    policy = policy or policy_mod.default_policy(turn_source)
    stream_limit = float(tool_deadlines.STREAM_TURN_MAX_SECONDS)
    limit = min(stream_limit, float(deadline_s or policy.deadline_s or stream_limit))
    # The compact trace kept on the answer so a reload still shows the
    # thinking and the tools (UX pass 2026-09-25; services/graph/turn_trace.py).
    turn_trace_builder = turn_trace.TraceBuilder(summarize=_summarize_tool_args)
    loop = tool_loop.LoopContext(
        messages=messages, turn_start=turn_start, system_prompt=enhanced_system_prompt, tools=tools or [],
        conversation_id=conversation_id, provider=provider, policy=policy, stream=True, model=model,
        temperature=temperature, turn_source=turn_source, reasoning_effort=reasoning_effort,
        build_llm=lambda: _build_llm(model, temperature, provider=provider, reasoning_effort=reasoning_effort,
                                     turn_source=turn_source),
        max_rounds=max_tool_iterations, deadline=turn_started + limit, deadline_label=f"{limit:g}s",
        trace=turn_trace_builder,
    )
    policy_token = policy_mod.set_policy(policy)
    try:
        async for event in tool_loop.run_tool_loop(loop):
            yield event
    finally:
        policy_mod.reset_policy(policy_token)
    full_response = loop.full_response
    tool_calls_made = loop.tool_calls_made

    # Add assistant response to messages, with the turn's compact trace
    # (thinking, tools, memories) so a reload still shows the work.
    try:
        _trace = turn_trace_builder.build(memory_hits=len(retrieved_memories),
                                          known=turn_trace.known_secrets_safely())
    except Exception as _trace_err:  # noqa: BLE001 — the answer matters more
        logger.debug("turn trace failed: %s", _trace_err)
        _trace = None
    messages.append(AIMessage(content=full_response, additional_kwargs=turn_trace.trace_kwargs(_trace)))
    # What the checkpoint keeps: no nudges, no dead turns.
    saved_messages = _checkpoint_messages(messages)

    # Get final plan state for checkpoint persistence
    from services.graph.tools import get_active_plan as _get_plan_for_save
    final_plan = _get_plan_for_save(conversation_id)

    # Update graph state with the full conversation
    final_state = {
        "messages": saved_messages,
        "conversation_id": conversation_id,
        "system_prompt": system_prompt,
        "model": model,
        "temperature": temperature,
        "current_response": full_response,
        "is_complete": True,
        "retrieved_memories": [
            {
                "id": m.id,
                "content": m.content,
                "memory_type": m.memory_type,
                "importance": m.importance,
                "temporal_nature": m.temporal_nature,
                "tier": getattr(m, 'tier', 'observation') or 'observation',
                "reinforcement_count": getattr(m, 'reinforcement_count', 0) or 0,
                "score": m.score
            }
            for m in retrieved_memories
        ],
        "plan_steps": final_plan,
        "tool_calls": [
            {"name": tc["name"], "args": tc["args"]}
            for tc in tool_calls_made
        ] if tool_calls_made else [],
        "current_node": "complete",
        "node_history": ["preprocess", "retrieve_memory", "respond", "extract_memory"]
    }

    async def _finish_turn() -> None:
        saved = await _safe_update_state(graph, config, final_state, as_node="extract_memory")
        if saved:
            await _trim_checkpoint_history(graph, config, saved_messages, pinned=[trigger],
                                           also_remove=loaded_scaffolding)

        # ===== POST-TURN WORK (fire-and-forget; never blocks DONE) =====
        try:
            messages_for_extraction = _extraction_view(messages)
            from services.turn_ledger import current_turn as _current_turn

            _rec = _current_turn()
            await _spawn_post_turn_work(
                messages_for_extraction=messages_for_extraction,
                conversation_id=conversation_id,
                retrieved_memories=retrieved_memories,
                turn_count=turn_count,
                turn_id=_rec.id if _rec is not None else None,
                turn_source=turn_source,
            )
        except Exception as e:
            print(f"Post-turn work spawn failed: {e}")

    # The save, the trim and the learners finish even when the owner leaves now
    # (2026-09-27 review). The router cancels the step in flight when the
    # client goes away (``routers/chat._yield_with_heartbeats``), and this
    # step runs after the final content and before ``done``: the owner has
    # read the whole answer while Stop is still on screen, and a tap, an app
    # switch or a dropped network used to cancel the write — their message
    # and the answer were gone on reload, and nothing was learned from
    # them. A cancellation waits for the step to land before it goes on, so
    # the thread (``thread_turns``) is not freed for their next turn while
    # this one is still writing it. The non-streaming loop shields its
    # save for the same reason.
    finishing = asyncio.ensure_future(_finish_turn())
    try:
        await asyncio.shield(finishing)
    except asyncio.CancelledError:
        try:
            await asyncio.shield(finishing)
        except Exception:  # noqa: BLE001 — the save logs its own failures
            pass
        raise
    # Emit done event immediately — extraction continues in the background.
    yield create_event(EventType.DONE, conversation_id)


def _ledger_note_iteration() -> None:
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
        if rec is not None:
            rec.note_iteration()
    except Exception:
        pass


def _ledger_note_tool(name: str, result, ms) -> None:
    try:
        from services.turn_ledger import note_tool_result

        note_tool_result(name, result, ms=ms)
    except Exception:
        pass


def _ledger_note_tool_started(name: str) -> None:
    """A tool is about to run: the host Mac's screen shows it while a wake or
    the pulse works (the stage, 2026-09-28). Records nothing on the turn."""
    try:
        from services.turn_ledger import note_tool_started

        note_tool_started(name)
    except Exception:
        pass


_IGNORED_LEGACY_LOGGED: set = set()


def _note_ignored_legacy_args(turn_source, allowed_tools, excluded_tools, nudge_for_tools) -> None:
    """v2 gives every turn the same tools: a caller still passing the old
    arguments is told once per source."""
    given = [n for n, v in (("allowed_tools", allowed_tools), ("excluded_tools", excluded_tools),
                            ("nudge_for_tools", nudge_for_tools)) if v is not None]
    key = (turn_source, tuple(given))
    if given and key not in _IGNORED_LEGACY_LOGGED:
        _IGNORED_LEGACY_LOGGED.add(key)
        logger.info("toolset v2: %s ignored for %s turns (TurnPolicy decides what a turn may do)",
                    ", ".join(given), turn_source)


def _apply_tool_allowlist(tools, allowed_tools):
    """Keep only the named tools. A turn that binds all 107 tools pays for
    every schema and every manual section on every call; the expression
    pulse needs about thirty (2026-09-14: 166K input tokens per pulse)."""
    if allowed_tools is None:
        return tools
    allowed = set(allowed_tools)
    return [t for t in tools if getattr(t, "name", None) in allowed]


def _apply_tool_exclusions(tools, excluded_tools):
    """Deterministically strip named tools from a turn's tool set. Prompt
    guidance alone is not enforcement — a heartbeat ACT turn auto-replied
    as the owner despite the trigger saying replies were rarely warranted."""
    if not excluded_tools:
        return tools
    excluded = set(excluded_tools)
    return [t for t in tools if getattr(t, "name", None) not in excluded]


async def chat_with_memory(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments: Optional[List[dict]] = None,
    skip_memory: bool = False,
    is_worker: bool = False,
    provider: Optional[str] = None,
    memory_query: Optional[str] = None,
    required_tools: Optional[List[str]] = None,
    excluded_tools: Optional[List[str]] = None,
    skip_extraction: bool = False,
    extraction_profile: str = "chat",
    turn_source: str = "background",
    allowed_tools: Optional[List[str]] = None,
    max_history_chars: Optional[int] = None,
    reasoning_effort: Optional[str] = None,
    nudge_for_tools: Optional[bool] = None,
    max_tool_iterations: Optional[int] = None,
    policy: Optional["policy_mod.TurnPolicy"] = None,
    deadline_s: Optional[float] = None,
) -> str:
    """Non-streaming turn, written to the turn ledger under ``turn_source``
    (heartbeat, scheduled_event, worker, pulse …). See ``_chat_with_memory_impl``.

    ``provider`` is derived from the model when not given, and a stale
    settings row saying "anthropic" never wins over a GPT model.
    ``reasoning_effort`` reaches ``build_chat_llm`` with ``turn_source``.
    ``max_tool_iterations`` caps the tool rounds (``EDWARD_MAX_TOOL_ROUNDS``,
    60, when not given).

    ``policy`` is what the turn may do (``services/toolset/policy.py``):
    every caller should pass one; ``None`` derives it from the old
    arguments (``allowed_tools`` naming a send → may reach people,
    ``required_tools`` naming a send → the reply check) on top of
    ``turn_source``'s fail-closed default. ``deadline_s`` (a wake's own
    timeout minus 30 s) stops the turn gracefully, with a closing call,
    before the caller's ``wait_for`` would cancel it mid-tool.
    ``allowed_tools`` / ``excluded_tools`` / ``nudge_for_tools`` are honoured
    only under ``EDWARD_TOOLSET=legacy``; v2 gives every turn the same tools.

    A turn that is cancelled (a wake's ``wait_for``) or fails part-way still
    leaves its trigger and finished tool calls in the thread, closed by an
    ``[interrupted after N tool calls]`` note.

    Turns in one thread run one at a time (``services/thread_turns.py``);
    the wait for a turn already running there is not part of this turn's
    ledger row. A wake's turn (``mind``, ``pulse``) runs under the lock its
    caller holds.
    """
    from services import thread_turns, turn_ledger

    model = turn_model_for(model, turn_source)
    provider = provider_for(model, provider)
    # One turn at a time in this thread (followups M3, 2026-09-27). A wake
    # (``mind``/``pulse``) already holds the lock and does not wait again.
    slot = await thread_turns.acquire(conversation_id, turn_source)
    try:
        with thread_turns.holding(conversation_id):
            rec = turn_ledger.begin_turn(
                conversation_id=conversation_id, source=turn_source, model=model,
                provider=provider, message=message, is_worker=is_worker,
            )
            progress = _TurnProgress()
            try:
                result = await _chat_with_memory_impl(
                    message=message, conversation_id=conversation_id, system_prompt=system_prompt,
                    model=model, temperature=temperature, graph=graph, attachments=attachments,
                    skip_memory=skip_memory, is_worker=is_worker, provider=provider,
                    memory_query=memory_query, required_tools=required_tools,
                    excluded_tools=excluded_tools, skip_extraction=skip_extraction,
                    extraction_profile=extraction_profile, allowed_tools=allowed_tools,
                    max_history_chars=max_history_chars, turn_source=turn_source,
                    reasoning_effort=reasoning_effort, nudge_for_tools=nudge_for_tools,
                    max_tool_iterations=max_tool_iterations, progress=progress,
                    policy=policy, deadline_s=deadline_s,
                )
            except asyncio.CancelledError:
                turn_ledger.end_turn(rec, "stopped")
                await _save_progress_quietly(progress)
                raise
            except BaseException as exc:
                turn_ledger.end_turn(rec, "error", exc)
                await _save_progress_quietly(progress, require_progress=True)
                raise
            rec.note_content(result if isinstance(result, str) else None, streaming=False)
            turn_ledger.end_turn(rec, "exhausted" if progress.exhausted else "completed")
            return result
    finally:
        slot.release()


async def _save_progress_quietly(progress: "_TurnProgress", require_progress: bool = False) -> None:
    """Leave an interrupted turn's work in the thread; never raise. Shielded
    so a second cancellation cannot abandon the write half-way."""
    try:
        await asyncio.shield(progress.save_interrupted(require_progress=require_progress))
    except BaseException as e:  # noqa: BLE001
        logger.warning("could not save an interrupted turn: %r", e)


async def _chat_with_memory_impl(
    message: str,
    conversation_id: str,
    system_prompt: str,
    model: str,
    temperature: float,
    graph,
    attachments: Optional[List[dict]] = None,
    skip_memory: bool = False,
    is_worker: bool = False,
    provider: Optional[str] = None,
    memory_query: Optional[str] = None,
    required_tools: Optional[List[str]] = None,
    excluded_tools: Optional[List[str]] = None,
    skip_extraction: bool = False,
    extraction_profile: str = "chat",
    allowed_tools: Optional[List[str]] = None,
    max_history_chars: Optional[int] = None,
    turn_source: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    nudge_for_tools: Optional[bool] = None,
    max_tool_iterations: Optional[int] = None,
    progress: Optional["_TurnProgress"] = None,
    policy: Optional["policy_mod.TurnPolicy"] = None,
    deadline_s: Optional[float] = None,
) -> str:
    """Get a non-streaming response while maintaining conversation memory.

    Args:
        skip_memory: Skip memory retrieval, enrichment, and document retrieval (for workers)
        is_worker: Use worker-filtered tools (no orchestrator tools)
        excluded_tools: Tool names to strip from this turn's tool set
        skip_extraction: Run the turn with memory retrieval but no post-turn
            extraction (the expression pulse — its input is a signal pack,
            not a conversation)
        extraction_profile: "chat" or "heartbeat"; the heartbeat profile keeps
            durable facts only (see memory_service.HEARTBEAT_EXTRACTION_ADDENDUM)
        max_history_chars: bounds the history *before* this turn's trigger;
            the turn itself has ``turn_window.CURRENT_TURN_MAX_CHARS``
        progress: filled as the turn runs, so the wrapper can save it if the
            turn is cancelled
    """
    from services.memory_service import retrieve_memories, extract_and_store_memories, Memory
    from services.graph.tools import set_current_conversation_id

    progress = progress or _TurnProgress()
    provider = provider_for(model, provider)

    # Set the conversation ContextVar so browser/code-exec/SQL/shell
    # tools can identify their per-conversation sandbox. Without this,
    # those tools bail with "No conversation context available" — which
    # makes Edward look like he's refusing to use real tools. This was
    # missing from chat_with_memory while stream_with_memory_events
    # already had it.
    set_current_conversation_id(conversation_id)

    config = {"configurable": {"thread_id": conversation_id}}

    # Get existing state for this conversation
    existing = await graph.aget_state(config)

    # Build message list from existing state or start fresh
    loaded = list(existing.values.get("messages", [])) if existing.values else []
    # A previous failed turn can leave empty assistant turns + their nudges
    # behind, and every nudge used to be checkpointed; strip them so the
    # thread stops reading its own scaffolding as precedent. Their ids are
    # removed from the checkpoint at the end of the turn.
    messages = strip_scaffolding(loaded)
    loaded_scaffolding = scaffolding_ids(loaded)

    # Live settings win over whatever this thread checkpointed last turn.
    system_prompt, model, temperature = _resolve_turn_settings(
        existing.values, system_prompt, model, temperature
    )

    # A turn inside a journal entry (a scheduled event the owner set there, say)
    # opens with the entry, as the streaming loop does (2026-09-25).
    entry_opening, entry_prompt = await _entry_turn_context(conversation_id, messages)
    if entry_opening:
        messages.append(AIMessage(content=entry_opening))

    # Add the new user message (with attachments if present). It is the
    # turn's trigger — for a wake, the pack — and the in-turn window never
    # evicts it.
    trigger = _build_human_message(message, attachments)
    messages.append(trigger)
    turn_start = len(messages) - 1
    progress.graph, progress.config = graph, config
    progress.messages, progress.turn_start = messages, turn_start

    # ===== MEMORY RETRIEVAL (with deep retrieval gate) =====
    turn_count = sum(1 for m in messages if isinstance(m, HumanMessage))
    retrieved_memories: List[Memory] = []
    enriched_memories = []
    relevant_documents = []
    state_documents = []
    query_for_memory = memory_query or message

    if not skip_memory:
        try:
            # BLOCKING: Fast single-query retrieval (~50ms)
            retrieved_memories = await retrieve_memories(query_for_memory, limit=MAX_NORMAL_MEMORIES)

            # NON-BLOCKING: Deep retrieval runs concurrently (fire-and-forget)
            try:
                recent_msgs = [
                    {"role": "human" if isinstance(m, HumanMessage) else "assistant", "content": m.content}
                    for m in messages[-5:]
                    if isinstance(m, (HumanMessage, AIMessage))
                ]
                asyncio.create_task(_run_deep_retrieval_safe(
                    query_for_memory, recent_msgs, conversation_id,
                    already_retrieved_ids=[m.id for m in retrieved_memories]
                ))
            except Exception as e:
                print(f"Deep retrieval fire-and-forget failed to launch: {e}")
        except Exception as e:
            print(f"Memory retrieval failed: {e}")

        # ===== ENRICHMENT LOADING (from previous reflection) =====
        try:
            from services.reflection_service import load_enrichments
            enriched_memories = await load_enrichments(conversation_id, limit=MAX_ENRICHED_MEMORIES)
            if enriched_memories:
                print(f"[ENRICHMENT] Loaded {len(enriched_memories)} enrichments for {conversation_id}")
        except Exception as e:
            print(f"Enrichment loading failed: {e}")

        # ===== DOCUMENT RETRIEVAL =====
        try:
            from services.document_service import retrieve_relevant_documents, get_state_documents
            relevant_documents = await retrieve_relevant_documents(query_for_memory, limit=3)
            state_documents = await get_state_documents()
        except Exception as e:
            print(f"Document retrieval failed: {e}")

    # ===== HEARTBEAT BRIEFING (non-streaming) =====
    briefing_context_sync = ""
    try:
        from services.heartbeat.heartbeat_service import (
            get_pending_briefing,
            consume_ambient_notes,
        )
        briefing_sync = await get_pending_briefing()
        ambient_notes_sync = consume_ambient_notes(conversation_id)
        awareness_parts_sync: list[str] = []
        if briefing_sync:
            awareness_parts_sync.append(briefing_sync)
        if ambient_notes_sync:
            awareness_parts_sync.append(
                "Surfacing now (just in):\n"
                + "\n".join(f"- {n}" for n in ambient_notes_sync)
            )
        if awareness_parts_sync:
            briefing_context_sync = (
                "\n\n## Recent Awareness\nWhile you were away, you noticed:\n"
                + "\n\n".join(awareness_parts_sync)
            )
    except Exception as e:
        import traceback
        print(f"Heartbeat briefing failed: {e}\n{traceback.format_exc()}")

    # ===== SESSION CONTINUITY BRIEFING (non-streaming) =====
    session_briefing_sync = ""
    if turn_count <= 1:
        try:
            cached = _session_briefing_cache.get(conversation_id)
            if cached and (datetime.now() - cached[1]).total_seconds() < 3600:
                session_briefing_sync = f"\n\n{cached[0]}"
            else:
                from services.session_briefing_service import generate_briefing as gen_session_briefing
                sb = await gen_session_briefing(conversation_id)
                if sb:
                    _session_briefing_cache[conversation_id] = (sb, datetime.now())
                    session_briefing_sync = f"\n\n{sb}"
        except Exception as e:
            print(f"Session briefing failed: {e}")

    # ===== ORCHESTRATOR BRIEFING =====
    orchestrator_context = ""
    if not is_worker:
        try:
            from services.orchestrator_service import get_active_tasks_briefing
            orch_briefing = await get_active_tasks_briefing(conversation_id)
            if orch_briefing:
                orchestrator_context = f"\n\n{orch_briefing}"
        except Exception as e:
            print(f"Orchestrator briefing failed: {e}")

    # ===== DYNAMIC TOOL BINDING =====
    # Get tools based on enabled skills (workers get filtered set)
    if is_worker:
        from services.tool_registry import get_worker_tools
        tools = await get_worker_tools()
    else:
        tools = await get_available_tools()
    from services.toolset import is_v2

    if is_v2():
        _note_ignored_legacy_args(turn_source, allowed_tools, excluded_tools, nudge_for_tools)
    else:
        tools = _apply_tool_exclusions(tools, excluded_tools)
        tools = _apply_tool_allowlist(tools, allowed_tools)
    if policy is None:
        policy = policy_mod.legacy_policy(turn_source, allowed_tools=allowed_tools, required_tools=required_tools)
    elif required_tools and not policy.wants_reply():
        from dataclasses import replace as _replace

        policy = _replace(policy, any_reply=True)

    # Memories, documents and state here; the tool manual goes to compose_system_prompt (static first)
    memory_context = build_memory_context(
        retrieved_memories, documents=relevant_documents,
        enriched_memories=enriched_memories,
        state_documents=state_documents,
        shown_in_trigger=message or "",
    )
    # Credit the memories that actually made it into the prompt. Fire and
    # forget — a failure here must never cost the user a turn, and the count
    # is a slow-moving statistic, not state anything depends on.
    try:
        _used_ids = memory_ids_in_context(memory_context)
        if _used_ids:
            from services.memory_service import mark_memories_used

            asyncio.create_task(mark_memories_used(_used_ids))
        from services.turn_ledger import current_turn as _current_turn

        _rec = _current_turn()
        if _rec is not None:
            _rec.note_memories(retrieved=len(retrieved_memories), used=len(_used_ids))
    except Exception as used_exc:
        print(f"Memory use marking failed (non-fatal): {used_exc}")

    now = now_local()
    tz_abbrev = now.strftime("%Z") or current_tz_name()
    time_context = (
        f"\n\nCurrent date and time: {now.strftime('%A, %B %d, %Y at %I:%M %p')} "
        f"({tz_abbrev}, {current_tz_name()})"
    )
    enhanced_system_prompt = compose_system_prompt(
        base=system_prompt,
        memory_context=memory_context,
        briefing_context=briefing_context_sync,
        session_briefing_context=session_briefing_sync,
        orchestrator_context=orchestrator_context,
        time_context=time_context,
        state_documents=state_documents,
        tool_manual=get_tool_descriptions(tools) if tools else "",
        dynamic_split=_dynamic_split_for(provider),
        v2=is_v2(),
    ) + entry_prompt

    limit = deadline_s or policy.deadline_s
    turn_trace_builder = turn_trace.TraceBuilder(summarize=_summarize_tool_args)
    loop = tool_loop.LoopContext(
        messages=messages, turn_start=turn_start, system_prompt=enhanced_system_prompt, tools=tools or [],
        conversation_id=conversation_id, provider=provider, policy=policy, stream=False, model=model,
        temperature=temperature, turn_source=turn_source, reasoning_effort=reasoning_effort,
        build_llm=lambda: _build_llm(model, temperature, provider=provider, reasoning_effort=reasoning_effort,
                                     turn_source=turn_source),
        max_rounds=max_tool_iterations, max_history_chars=max_history_chars,
        deadline=(time.monotonic() + float(limit)) if limit else None,
        deadline_label=f"{float(limit):g}s" if limit else "", trace=turn_trace_builder, progress=progress,
    )
    with policy_mod.policy_scope(policy):
        async for _ in tool_loop.run_tool_loop(loop):
            pass
    full_response = loop.full_response
    tool_calls_made = loop.tool_calls_made
    progress.stop_reason = loop.stop_reason

    # Add assistant response to messages, with the turn's compact trace
    # (thinking, tools, memories) so a reloaded conversation shows the work.
    try:
        _trace = turn_trace_builder.build(memory_hits=len(retrieved_memories),
                                          known=turn_trace.known_secrets_safely())
    except Exception as _trace_err:  # noqa: BLE001 — the answer matters more
        logger.debug("turn trace failed: %s", _trace_err)
        _trace = None
    messages.append(AIMessage(content=full_response, additional_kwargs=turn_trace.trace_kwargs(_trace)))

    # What the checkpoint keeps: no nudges, no dead turns.
    saved_messages = _checkpoint_messages(messages)

    # Get final plan state for checkpoint persistence
    from services.graph.tools import get_active_plan as _get_plan_for_save_sync
    final_plan_sync = _get_plan_for_save_sync(conversation_id)

    # Update graph state. Shielded, and marked saved first: a cancellation
    # that lands mid-write must neither abandon it nor add an
    # "[interrupted …]" note after the real answer.
    progress.saved = True
    saved = await asyncio.shield(_safe_update_state(graph, config, {
        "messages": saved_messages,
        "conversation_id": conversation_id,
        "system_prompt": system_prompt,
        "model": model,
        "temperature": temperature,
        "current_response": full_response,
        "is_complete": True,
        "retrieved_memories": [
            {
                "id": m.id,
                "content": m.content,
                "memory_type": m.memory_type,
                "importance": m.importance,
                "temporal_nature": m.temporal_nature,
                "tier": getattr(m, 'tier', 'observation') or 'observation',
                "reinforcement_count": getattr(m, 'reinforcement_count', 0) or 0,
                "score": m.score
            }
            for m in retrieved_memories
        ],
        "plan_steps": final_plan_sync,
        "tool_calls": [
            {"name": tc["name"], "args": tc["args"]}
            for tc in tool_calls_made
        ] if tool_calls_made else [],
        "current_node": "complete",
        "node_history": ["preprocess", "retrieve_memory", "respond", "extract_memory"]
    }, as_node="extract_memory"))
    if saved:
        await _trim_checkpoint_history(graph, config, saved_messages, max_chars=max_history_chars,
                                       pinned=[trigger], also_remove=loaded_scaffolding)

    # ===== MEMORY EXTRACTION =====
    try:
        messages_for_extraction = _extraction_view(messages)
        if post_turn_extraction_enabled(skip_memory, skip_extraction):
            _stored = await extract_and_store_memories(
                messages=messages_for_extraction,
                conversation_id=conversation_id,
                existing_memories=retrieved_memories,
                extraction_profile=extraction_profile,
            )
            try:
                from services.turn_ledger import current_turn as _current_turn, note_extraction

                _rec = _current_turn()
                if _rec is not None:
                    await note_extraction(_rec.id, len([m for m in (_stored or []) if m]))
            except Exception:
                pass

        # Search tags and reflection describe conversations; the mind's and
        # the pulse's threads are packs (2026-09-24).
        if post_turn_learners_enabled(turn_source, conversation_id):
            # Fire-and-forget search tag generation
            from services.search_tag_service import generate_search_tags_safe
            asyncio.create_task(generate_search_tags_safe(conversation_id, messages_for_extraction))

            # Fire-and-forget reflection for next turn's enrichment
            try:
                from services.reflection_service import should_reflect, run_reflection_safe
                if should_reflect(messages_for_extraction, turn_count):
                    asyncio.create_task(run_reflection_safe(
                        conversation_id, messages_for_extraction,
                        [m.id for m in retrieved_memories]
                    ))
            except Exception as e:
                print(f"Reflection fire-and-forget failed: {e}")
    except Exception as e:
        print(f"Memory extraction failed: {e}")

    return full_response
