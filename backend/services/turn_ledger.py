"""The turn ledger — one row per LLM turn, from every entry point.

Why: a health check could judge Edward's *attention* (triage decisions,
the push ledger, the owner's verdicts) but nothing about his *turns*. Which
tools fail, how often the loop runs out of iterations, how slow voice
is, whether retrieval feeds anything — all of that lived only in the
SSE stream and a log file that resets on restart.

Shape:

* ``TurnRecord`` — the pure accumulator. Both LLM loops feed it.
* ``observe_events(record, stream)`` — wraps the streaming turn: passes
  every event through untouched, fills the record from what it sees
  (first token, tool start/end, memory count, the exhausted marker,
  done) and settles the outcome, including *stopped* when the consumer
  goes away (stop pressed, phone hung up) and *error* when the turn
  raises.
* A task-local context variable (``begin_turn`` / ``current_turn`` /
  ``end_turn``) so code deep inside a turn (the used-memory credit, the
  non-streaming tool loop, post-turn extraction) can annotate it
  without threading a parameter through 800 lines.
* Thin persistence: insert at begin (so a turn whose process dies stays
  ``running`` — visible), update at end. Fire-and-forget; never raises
  into the turn.
* Retention: ``prune()`` keeps ``RETENTION_DAYS`` of ``turn_log``,
  ``self_observation_hits``, ``user_signals``, ``ledger_events`` and the
  spend ledger (``codex_calls``, ``llm_meter_samples``), and decided
  ``pending_approvals``. ``maybe_prune`` runs it
  at most once every 6 h per process, after a write — no new loop.

Tool failures also become ``self_observations`` (``kind=tool_failure``,
signature ``tool_failure.<tool>.<error_class>``) so the pattern shows up
where Edward's other self-knowledge lives, with a hit per occurrence.

Taint (2026-09-24, ``taint_service``): the record carries ``tainted`` and
``taint_reason`` — set at the start when the turn carries third-party text
by construction (a heartbeat trigger, a mind or pulse pack), and by the
tool executor after a read of untrusted content. A send held for the
owner's approval is recorded ``{"ok": true, "held": true}``: it did not fail, and
it did not deliver.

The thought journal (2026-09-25, ``thought_journal``): the record also
keeps what the turn *did* — ``actions``, one ``{name, args, result, ok[,
held]}`` per call in the journal's action set, args JSON-safe and bounded —
and the reasoning summaries its model responses carried (``reasoning``).
A wake that acts becomes a short conversation built from these, not from
the model's own account. Held in memory only; ``turn_log`` is unchanged.
"""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, AsyncGenerator, AsyncIterator, Dict, List, Optional

from services.thought_journal import HELD_PREFIX

logger = logging.getLogger(__name__)

RETENTION_DAYS = 30
PRUNE_EVERY_S = 6 * 3600
ABANDON_WINDOW_S = 60
TOOLS_JSON_MAX_ENTRIES = 120
TOOL_ERR_MAX_CHARS = 80
ERROR_CLASS_MAX_CHARS = 160
MIN_REPEAT_CHARS = 8

TURN_OUTCOMES = ("running", "completed", "stopped", "error", "exhausted", "empty")

# The tool result a gated send answers with while it waits for the owner
# (``approvals_service.waiting_message``). Not a failure, not a delivery.
# The one shared constant (``thought_journal.HELD_PREFIX``, which
# ``approvals_service.WAITING_PREFIX`` is too), so the three always match.
HELD_FOR_APPROVAL_PREFIX = HELD_PREFIX

# What the thought journal keeps of a turn (2026-09-25). Bounded so a wake
# that saves a long document or loops on a tool cannot grow the record.
ACTION_ARG_MAX_CHARS = 300
ACTION_RESULT_MAX_CHARS = 300
MAX_ACTIONS_PER_TURN = 40
REASONING_MAX_CHARS = 2000

# The turns whose thinking shows live on the host Mac's screen
# (``screen_thoughts``, the stage, 2026-09-28): wakes and the pulse only.
SCREEN_THOUGHT_SOURCES = frozenset({"mind", "pulse"})

_JSON_SAFE_MAX_DEPTH = 4
_JSON_SAFE_MAX_ITEMS = 20

# ------------------------------------------------------------------ pure --

_UUID_RE = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_URL_RE = re.compile(r"https?://\S+")
_NUM_RE = re.compile(r"\d+")
_NONWORD_RE = re.compile(r"[^a-z0-9]+")


def _normalize_error(text: str, limit: int) -> str:
    text = _URL_RE.sub("url", text)
    text = _UUID_RE.sub("id", text)
    text = _NUM_RE.sub("n", text)
    text = _NONWORD_RE.sub("_", text.lower()).strip("_")
    return text[:limit].rstrip("_") or "unknown"


def classify_tool_result(result: Any) -> Optional[str]:
    """``None`` when the tool call succeeded, else a stable error class.

    Stable means volatile bits (ids, numbers, URLs) are stripped so the
    same failure collapses to one class across occurrences.
    """
    if result is None:
        return None
    text = str(result)
    if not text:
        return None
    if text.startswith("BLOCKED:"):
        return "blocked"
    if text.startswith("Unknown tool:"):
        return "unknown_tool"
    if text.startswith("Tool error:"):
        return _normalize_error(text[len("Tool error:"):].strip(), TOOL_ERR_MAX_CHARS)
    return None


def error_class(exc: BaseException) -> str:
    """``TypeName: normalized head of the message`` — stable across ids."""
    head = _normalize_error(str(exc)[:200], ERROR_CLASS_MAX_CHARS - len(type(exc).__name__) - 2)
    return f"{type(exc).__name__}:{head}" if head and head != "unknown" else type(exc).__name__


def normalize_text(text: Optional[str]) -> str:
    if not text:
        return ""
    return _NONWORD_RE.sub(" ", text.lower().replace("'", "").replace("’", "")).strip()


def is_repeat(previous: Optional[str], new: Optional[str]) -> bool:
    """Same question asked again (case, spacing and punctuation aside)."""
    a, b = normalize_text(previous), normalize_text(new)
    if len(b) < MIN_REPEAT_CHARS:
        return False
    return a == b


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if isinstance(message, list):
        return " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part) for part in message
        )
    return str(message or "")


def json_safe(value: Any, max_str: int = ACTION_ARG_MAX_CHARS, _depth: int = 0) -> Any:
    """A JSON-safe, bounded copy of a tool's arguments: every string at most
    ``max_str`` characters, lists and dicts at most twenty entries, four
    levels deep; anything else becomes its ``repr``. Pure."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_str]
    if _depth >= _JSON_SAFE_MAX_DEPTH:
        return repr(value)[:max_str]
    if isinstance(value, dict):
        return {str(k)[:60]: json_safe(v, max_str, _depth + 1)
                for k, v in list(value.items())[:_JSON_SAFE_MAX_ITEMS]}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v, max_str, _depth + 1) for v in list(value)[:_JSON_SAFE_MAX_ITEMS]]
    try:
        return repr(value)[:max_str]
    except Exception:  # noqa: BLE001
        return type(value).__name__


def reasoning_texts(message: Any) -> List[str]:
    """The reasoning summary texts a model response carried: the Codex
    adapter's ``additional_kwargs["reasoning_items"][*]["summary"]``, items
    of type ``summary_text``. Pure; odd shapes are skipped."""
    ak = getattr(message, "additional_kwargs", None) or {}
    items = ak.get("reasoning_items") if isinstance(ak, dict) else None
    out: List[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        for part in item.get("summary") or []:
            if isinstance(part, dict) and part.get("type") == "summary_text":
                text = str(part.get("text") or "").strip()
                if text:
                    out.append(text)
    return out


@dataclass
class TurnRecord:
    id: str
    conversation_id: Optional[str]
    source: str
    model: Optional[str]
    provider: Optional[str]
    started_at: datetime
    fast_context: bool = False
    is_worker: bool = False
    first_turn: bool = False
    message_chars: int = 0
    ttft_ms: Optional[int] = None
    total_ms: Optional[int] = None
    ended_at: Optional[datetime] = None
    iterations: int = 0
    tools: List[Dict[str, Any]] = field(default_factory=list)
    memories_retrieved: int = 0
    memories_used: int = 0
    memories_extracted: Optional[int] = None
    response_chars: int = 0
    repeat_of_previous: bool = False
    outcome: str = "running"
    error_class: Optional[str] = None
    tainted: bool = False
    taint_reason: Optional[str] = None
    # The thought journal's view of the turn (2026-09-25): what it did and
    # what it noticed. In memory only; never written to turn_log.
    actions: List[Dict[str, Any]] = field(default_factory=list)
    reasoning: List[str] = field(default_factory=list)
    _started_mono: float = field(default_factory=time.monotonic, repr=False)
    _open_tools: Dict[str, float] = field(default_factory=dict, repr=False)
    _overflow_calls: int = field(default=0, repr=False)
    _overflow_failures: int = field(default=0, repr=False)
    _persist_task: Optional["asyncio.Task"] = field(default=None, repr=False)
    _new_text: str = field(default="", repr=False)

    @classmethod
    def start(
        cls,
        *,
        conversation_id: Optional[str],
        source: str,
        model: Optional[str],
        provider: Optional[str],
        message: Any = "",
        fast_context: bool = False,
        is_worker: bool = False,
        first_turn: bool = False,
    ) -> "TurnRecord":
        rec = cls(
            id=str(uuid.uuid4()),
            conversation_id=conversation_id,
            source=(source or "unknown")[:40],
            model=model,
            provider=provider,
            started_at=datetime.utcnow(),
            fast_context=fast_context,
            is_worker=is_worker,
            first_turn=first_turn,
            message_chars=len(_message_text(message)),
            _new_text=_message_text(message),
        )
        try:
            from services.taint_service import construction_taint

            rec.note_taint(construction_taint(rec.source, conversation_id))
        except Exception:  # noqa: BLE001 — bookkeeping never breaks a turn
            pass
        return rec

    # -- annotations -------------------------------------------------------

    def _elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started_mono) * 1000)

    def note_iteration(self) -> None:
        self.iterations += 1

    def note_first_token(self) -> None:
        if self.ttft_ms is None:
            self.ttft_ms = self._elapsed_ms()

    def note_content(self, text: Optional[str], streaming: bool = True) -> None:
        """Count answer text; only a streamed chunk marks the first token
        (a non-streaming turn's ttft would just equal its total)."""
        if text:
            if streaming:
                self.note_first_token()
            self.response_chars += len(text)

    def note_tool_start(self, name: str) -> None:
        self._open_tools[name] = time.monotonic()

    def note_tool_end(self, name: str, result: Any) -> Optional[str]:
        started = self._open_tools.pop(name, None)
        ms = int((time.monotonic() - started) * 1000) if started is not None else None
        return self.note_tool(name, result, ms=ms)

    def note_taint(self, reason: Optional[str]) -> None:
        """The turn read third-party text (``taint_service``); each cause once."""
        if not reason:
            return
        from services.taint_service import merge_reasons

        self.tainted = True
        self.taint_reason = merge_reasons(self.taint_reason, reason)

    def note_tool(self, name: str, result: Any, ms: Optional[int] = None) -> Optional[str]:
        """Record one tool call; returns its error class (``None`` = ok)."""
        err = classify_tool_result(result)
        if len(self.tools) < TOOLS_JSON_MAX_ENTRIES:
            entry: Dict[str, Any] = {"n": name, "ok": err is None, "ms": ms}
            if err:
                entry["err"] = err
            elif str(result or "").startswith(HELD_FOR_APPROVAL_PREFIX):
                entry["held"] = True
            self.tools.append(entry)
        else:
            self._overflow_calls += 1
            if err:
                self._overflow_failures += 1
        return err

    def note_action(self, name: str, args: Any, result: Any) -> None:
        """Keep one call in the journal's action set (``thought_journal``):
        its bounded args, the head of its result, whether it worked and
        whether it is held for the owner. Everything else the turn looked at is
        not an action. Bookkeeping: never raises."""
        try:
            from services import thought_journal

            if len(self.actions) >= MAX_ACTIONS_PER_TURN or not thought_journal.is_journal_tool(name, args):
                return
            ok, held = thought_journal.action_ok(name, result)
            entry: Dict[str, Any] = {
                "name": str(name),
                "args": json_safe(args if isinstance(args, dict) else {}),
                "result": str(result if result is not None else "")[:ACTION_RESULT_MAX_CHARS],
                "ok": ok,
            }
            if held:
                entry["held"] = True
            entry.update(thought_journal.action_facts(name, result))
            self.actions.append(entry)
        except Exception as exc:  # noqa: BLE001
            logger.debug("turn ledger action note failed: %s", exc)

    def note_reasoning(self, message: Any) -> None:
        """Keep the reasoning summaries of one model response, once each,
        within ``REASONING_MAX_CHARS`` for the turn. Never raises."""
        try:
            used = sum(len(t) for t in self.reasoning)
            for text in reasoning_texts(message):
                if text in self.reasoning:
                    continue
                room = REASONING_MAX_CHARS - used
                if room <= 0:
                    return
                text = text[:room]
                self.reasoning.append(text)
                used += len(text)
        except Exception as exc:  # noqa: BLE001
            logger.debug("turn ledger reasoning note failed: %s", exc)

    def note_memories(self, retrieved: Optional[int] = None, used: Optional[int] = None) -> None:
        if retrieved is not None:
            self.memories_retrieved = int(retrieved)
        if used is not None:
            self.memories_used = int(used)

    def note_previous_user_text(self, text: Optional[str], new_text: Optional[str] = None) -> None:
        """Flag the turn when it repeats the previous user message."""
        if new_text is None:
            new_text = self._new_text
        self.repeat_of_previous = is_repeat(text, new_text)

    def finish(self, outcome: str, error: Optional[BaseException] = None) -> None:
        if outcome not in TURN_OUTCOMES:
            outcome = "error"
        if outcome == "completed" and self.response_chars == 0:
            outcome = "empty"
        self.outcome = outcome
        self.error_class = error_class(error)[:ERROR_CLASS_MAX_CHARS] if error is not None else None
        self.ended_at = datetime.utcnow()
        self.total_ms = self._elapsed_ms()

    # -- views -------------------------------------------------------------

    @property
    def tool_calls(self) -> int:
        return len(self.tools) + self._overflow_calls

    @property
    def tool_failures(self) -> int:
        return sum(1 for t in self.tools if not t.get("ok")) + self._overflow_failures

    def to_row(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "conversation_id": self.conversation_id,
            "source": self.source,
            "model": self.model,
            "provider": self.provider,
            "fast_context": bool(self.fast_context),
            "is_worker": bool(self.is_worker),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "ttft_ms": self.ttft_ms,
            "total_ms": self.total_ms,
            "iterations": self.iterations,
            "tool_calls": self.tool_calls,
            "tool_failures": self.tool_failures,
            "tools_json": json.dumps(self.tools, separators=(",", ":")) if self.tools else None,
            "memories_retrieved": self.memories_retrieved,
            "memories_used": self.memories_used,
            "memories_extracted": self.memories_extracted,
            "message_chars": self.message_chars,
            "response_chars": self.response_chars,
            "repeat_of_previous": bool(self.repeat_of_previous),
            "outcome": self.outcome,
            "error_class": self.error_class,
            "tainted": bool(self.tainted),
            "taint_reason": self.taint_reason,
        }

    async def wait_persisted(self) -> None:
        """Tests and shutdown: wait for the pending write, if any."""
        task = self._persist_task
        if task is not None:
            try:
                await task
            except Exception:
                pass


# ------------------------------------------------------- task-local turn --

_current: contextvars.ContextVar[Optional[TurnRecord]] = contextvars.ContextVar("edward_turn", default=None)
_last_finished: contextvars.ContextVar[Optional[TurnRecord]] = contextvars.ContextVar("edward_last_turn", default=None)


def current_turn() -> Optional[TurnRecord]:
    return _current.get()


def last_finished_turn() -> Optional[TurnRecord]:
    """The turn this task most recently finished — so a caller that awaited
    ``chat_with_memory`` (the pulse, the scheduler) can see what it did."""
    return _last_finished.get()


def _schedule(coro) -> Optional["asyncio.Task"]:
    """Fire-and-forget on the running loop; drop the coroutine if none."""
    try:
        return asyncio.get_running_loop().create_task(coro)
    except RuntimeError:
        coro.close()
        return None


def begin_turn(
    *,
    conversation_id: Optional[str],
    source: str,
    model: Optional[str],
    provider: Optional[str],
    message: Any = "",
    fast_context: bool = False,
    is_worker: bool = False,
    first_turn: bool = False,
) -> TurnRecord:
    rec = TurnRecord.start(
        conversation_id=conversation_id, source=source, model=model, provider=provider,
        message=message, fast_context=fast_context, is_worker=is_worker, first_turn=first_turn,
    )
    _current.set(rec)
    rec._persist_task = _schedule(_insert(rec))
    _screen("begin", rec)
    return rec


def _screen(hook: str, rec: Optional[TurnRecord], *args: Any) -> None:
    """Feed the thinking stream on the host Mac's screen (``screen_thoughts``).
    Any turn but a wake or the pulse returns here; never raises."""
    try:
        if rec is None or rec.source not in SCREEN_THOUGHT_SOURCES:
            return
        from services import screen_thoughts

        getattr(screen_thoughts, hook)(rec, *args)
    except Exception as exc:  # noqa: BLE001 — the screen never breaks a turn
        logger.debug("screen thoughts %s hook failed: %s", hook, exc)


def end_turn(rec: TurnRecord, outcome: str, error: Optional[BaseException] = None) -> None:
    """Settle the record and write it. Idempotent; never raises."""
    try:
        if rec.outcome != "running":
            return
        rec.finish(outcome, error)
        if _current.get() is rec:
            _current.set(None)
        _last_finished.set(rec)
        _screen("end", rec)
        rec._persist_task = _schedule(_update(rec, after=rec._persist_task))
        # A turn that is not a wake and ran code on the host Mac on its own
        # (Codex, an MCP server) leaves a journal entry (review 2026-09-27).
        from services import thought_journal

        thought_journal.schedule_unattended_entry(rec)
    except Exception as exc:  # pragma: no cover - belt and braces
        logger.debug("end_turn failed: %s", exc)


def note_tool_result(name: str, result: Any, ms: Optional[int] = None) -> None:
    """Non-streaming loop hook: record one tool call on the current turn
    and turn a failure into a self-observation."""
    rec = current_turn()
    err = rec.note_tool(name, result, ms=ms) if rec is not None else classify_tool_result(result)
    _screen("tool_finished", rec, name, err is None)
    if err and err != "blocked":
        _emit_tool_failure(name, err, result)


def note_tool_started(name: str) -> None:
    """Non-streaming loop hook, just before a tool runs: the screen shows
    it as running. Records nothing on the turn — the loop times the call
    itself and ``note_tool_result`` records it once (the streamed loop's
    ``note_tool_start`` only starts that clock). Never raises."""
    try:
        _screen("tool_started", current_turn(), name)
    except Exception as exc:  # noqa: BLE001
        logger.debug("turn ledger tool start hook failed: %s", exc)


def note_action_result(name: str, args: Any, result: Any) -> None:
    """Tool executor hook (both loops): keep a journal action on the
    current turn. A no-op outside a turn; never raises."""
    try:
        rec = current_turn()
        if rec is not None:
            rec.note_action(name, args, result)
    except Exception as exc:  # noqa: BLE001
        logger.debug("turn ledger action hook failed: %s", exc)


def note_response_reasoning(message: Any) -> None:
    """Loop hook: keep a model response's reasoning summaries on the
    current turn. A no-op outside a turn; never raises."""
    try:
        rec = current_turn()
        if rec is not None:
            before = list(rec.reasoning) if rec.source in SCREEN_THOUGHT_SOURCES else None
            rec.note_reasoning(message)
            if before is not None:
                _screen("note_reasoning", rec, [t for t in reasoning_texts(message) if t not in before])
    except Exception as exc:  # noqa: BLE001
        logger.debug("turn ledger reasoning hook failed: %s", exc)


def _emit_tool_failure(name: str, err: str, result: Any) -> None:
    async def _go():
        from services.observe import record_observation

        await record_observation(
            kind="tool_failure",
            source="tool_loop",
            signature=f"tool_failure.{name}.{err}",
            description=f"{name} failed: {err}",
            severity=0.3,
            confidence=0.9,
            evidence={"tool": name, "result": str(result)[:300]},
        )

    _schedule(_go())


async def observe_events(rec: TurnRecord, stream: AsyncIterator[Dict[str, Any]]) -> AsyncGenerator[Dict[str, Any], None]:
    """Pass a streaming turn's events through, filling ``rec`` from them.

    Settles the outcome: ``completed`` on ``done`` (``exhausted`` if the
    loop reported running out of iterations first), ``stopped`` when the
    consumer closes the generator or the task is cancelled before ``done``,
    ``error`` when the turn raises (re-raised).

    A consumer that stops reading once it has ``done`` is not a stop: the
    voice socket breaks out of its loop on ``done``, which closes this
    generator with ``GeneratorExit`` at that yield, and every voice turn in
    ``turn_log`` read ``stopped`` (8 of 8, 9/16–9/21; followups M1).
    ``done`` is the turn's last event — post-turn work runs before it — so
    once it has passed, the turn finished.
    """
    outcome = "stopped"
    error: Optional[BaseException] = None
    exhausted = False
    saw_done = False
    if rec.iterations == 0:
        rec.note_iteration()  # the first pass; each "thinking" event is one more
    try:
        async for event in stream:
            try:
                et = event.get("type") if isinstance(event, dict) else None
                if et == "content":
                    rec.note_content(event.get("content"))
                elif et == "tool_start":
                    rec.note_tool_start(event.get("tool_name") or "?")
                    _screen("tool_started", rec, event.get("tool_name") or "?")
                elif et == "tool_end":
                    name = event.get("tool_name") or "?"
                    err = rec.note_tool_end(name, event.get("result"))
                    _screen("tool_finished", rec, name, err is None)
                    if err and err != "blocked":
                        _emit_tool_failure(name, err, event.get("result"))
                elif et == "thinking":
                    rec.note_iteration()
                elif et == "progress":
                    step, status = event.get("step"), event.get("status")
                    if step == "memory_search" and status == "completed":
                        rec.note_memories(retrieved=event.get("count") or 0)
                    elif step == "tool_loop" and status == "exhausted":
                        exhausted = True
                elif et == "done":
                    saw_done = True
            except Exception as note_exc:  # never let bookkeeping break a turn
                logger.debug("turn ledger note failed: %s", note_exc)
            yield event
        outcome = "exhausted" if exhausted else "completed"
    except (GeneratorExit, asyncio.CancelledError):
        if saw_done:
            outcome = "exhausted" if exhausted else "completed"
        else:
            outcome = "stopped"
        raise
    except BaseException as exc:
        outcome, error = "error", exc
        raise
    finally:
        end_turn(rec, outcome, error)


# -------------------------------------------------------------- persist --

_last_prune_mono: Optional[float] = None


async def _insert(rec: TurnRecord) -> None:
    try:
        from services.database import async_session, TurnLogModel

        row = rec.to_row()
        async with async_session() as session:
            session.add(TurnLogModel(**row))
            await session.commit()
    except Exception as exc:
        logger.debug("turn ledger insert failed: %s", exc)


async def _update(rec: TurnRecord, after: Optional["asyncio.Task"] = None) -> None:
    if after is not None:
        try:
            await after
        except Exception:
            pass
    try:
        from sqlalchemy import select, update
        from services.database import async_session, TurnLogModel

        row = rec.to_row()
        row.pop("id")
        row.pop("started_at")
        # ``note_extraction`` may already have written the count; a record
        # that never learned it must not write NULL over it (every
        # non-streaming turn lost its count until 2026-09-27).
        if rec.memories_extracted is None:
            row.pop("memories_extracted")
        async with async_session() as session:
            abandoned = False
            if rec.first_turn and rec.source == "chat":
                since = rec.started_at - timedelta(seconds=ABANDON_WINDOW_S)
                prev = await session.execute(
                    select(TurnLogModel.id)
                    .where(TurnLogModel.source == "chat")
                    .where(TurnLogModel.conversation_id != rec.conversation_id)
                    .where(TurnLogModel.ended_at >= since)
                    .where(TurnLogModel.ended_at <= rec.started_at)
                    .limit(1)
                )
                abandoned = prev.first() is not None
            row["abandoned_previous"] = abandoned
            result = await session.execute(update(TurnLogModel).where(TurnLogModel.id == rec.id).values(**row))
            if result.rowcount == 0:
                session.add(TurnLogModel(id=rec.id, started_at=rec.started_at, **row))
            await session.commit()
    except Exception as exc:
        logger.debug("turn ledger update failed: %s", exc)
    maybe_prune()


async def note_extraction(turn_id: Optional[str], count: int) -> None:
    """Post-turn extraction reports how many memories the turn produced.

    Written to the row, and to the record when it is still this task's
    turn (the non-streaming loop extracts before ``end_turn``), so the
    end-of-turn update writes the count too."""
    if not turn_id:
        return
    rec = current_turn()
    if rec is not None and rec.id == turn_id:
        rec.memories_extracted = int(count)
    try:
        from sqlalchemy import update
        from services.database import async_session, TurnLogModel

        async with async_session() as session:
            await session.execute(
                update(TurnLogModel).where(TurnLogModel.id == turn_id).values(memories_extracted=int(count))
            )
            await session.commit()
    except Exception as exc:
        logger.debug("turn ledger extraction note failed: %s", exc)


async def prune(days: int = RETENTION_DAYS) -> Dict[str, int]:
    """Delete ledger rows older than ``days``. Returns counts per table."""
    from sqlalchemy import delete
    from services.database import (
        async_session, TurnLogModel, SelfObservationHitModel, UserSignalModel, LedgerEventModel,
        CodexCallModel, LLMMeterSampleModel, PendingApprovalModel,
    )

    cutoff = datetime.utcnow() - timedelta(days=days)
    deleted: Dict[str, int] = {}
    async with async_session() as session:
        for name, model, col in (
            ("turn_log", TurnLogModel, TurnLogModel.started_at),
            ("self_observation_hits", SelfObservationHitModel, SelfObservationHitModel.seen_at),
            ("user_signals", UserSignalModel, UserSignalModel.created_at),
            ("ledger_events", LedgerEventModel, LedgerEventModel.created_at),
            # The spend ledger (codex_call_ledger, 2026-09-24).
            ("codex_calls", CodexCallModel, CodexCallModel.created_at),
            ("llm_meter_samples", LLMMeterSampleModel, LLMMeterSampleModel.sampled_at),
            # Approvals (taint and approvals, 2026-09-24): a pending one
            # expires after 24 h, so a month-old row is long decided.
            ("pending_approvals", PendingApprovalModel, PendingApprovalModel.created_at),
        ):
            result = await session.execute(delete(model).where(col < cutoff))
            deleted[name] = int(result.rowcount or 0)
        # The pay-per-token ledger (llm_spend, 2026-10-10) is kept far longer:
        # the credits remaining are a sum since the purchase date.
        from services import llm_spend
        from services.database import LLMCallModel

        long_cutoff = datetime.utcnow() - timedelta(days=max(days, llm_spend.RETENTION_DAYS))
        result = await session.execute(delete(LLMCallModel).where(LLMCallModel.created_at < long_cutoff))
        deleted["llm_calls"] = int(result.rowcount or 0)
        await session.commit()
    if any(deleted.values()):
        logger.info("turn ledger prune: %s", deleted)
    return deleted


def maybe_prune() -> None:
    """Run ``prune`` at most once every ``PRUNE_EVERY_S`` per process."""
    global _last_prune_mono
    now = time.monotonic()
    if _last_prune_mono is not None and now - _last_prune_mono < PRUNE_EVERY_S:
        return
    _last_prune_mono = now

    async def _go():
        try:
            await prune()
        except Exception as exc:
            logger.debug("turn ledger prune failed: %s", exc)

    _schedule(_go())
