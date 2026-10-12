"""One tool loop for both chat paths (2026-10-11; docs/architecture/tool-loop.md).

``streaming.py`` had two copies of the loop — streaming and non-streaming —
each with its own nudges and breakers. Both now run ``run_tool_loop``: the
streaming path forwards its events as SSE, the non-streaming path drains
them, and both get the same accounting, traces and honest endings.

**Removed** (each 0 fires in 8.4 days of logs, or a GPT-era quirk): the
generic "you MUST use your tools" nudge, forced ``tool_choice`` (a 400 on
Opus/Sonnet 5.5), the GPT-empty "summarize" nudge, the plan nudge and the
plan-raised round limit, the repeat guard, the unproductive-turn breaker,
keyword family gating, the send-review critic and the taint→approval hold
on replies (the recipient rule in ``send_message`` replaced it).

**Kept**: the egress secret scan, never-text-the-owner from a wake, one
recipient, stand-downs, the 10-minute dedupe and the shielded send (in
``send_message``), the per-thread lock (``thread_turns``, in the callers),
the trace and work-rail events, the journal hooks, the invalid-JSON
refusal, deadlines and the honest ending.

**New**:
* the uniform failure contract — every failure is ``Tool error: …`` with
  ``ToolMessage.status="error"`` (``services/toolset/result.py``);
* the identical-failure stop (``tool_policy.FailureTracker``);
* a step-back note after 3 failed rounds and a stop after 6;
* the per-turn dollar cap (``TurnPolicy.turn_cap_usd``), summed from each
  response's ``estimated_cost_usd`` (the Claude adapter's);
* a graceful deadline (a wake's ``deadline_s``) that leaves room for one
  closing call, and tool deadlines clamped to it;
* the reply check: asked in a chat and ended without answering there, the
  turn is told once — never forced;
* server tools (Anthropic web search/fetch) shown on the work rail from the
  response's raw blocks, never executed;
* a closing call that keeps the system prompt byte-identical — the
  instruction rides as a trailing message;
* every local tool result redacted (``redact_result``) before the model,
  the work rail, the trace or the checkpoint sees it — not only the shell's
  and the editor's (integrations audit #6).

A model call that fails (overloaded after the adapter's retry, auth, the
daily cap) propagates to the caller as before: the chat router voices it,
a wake's retry logic reads it, the ping runner decides its outcome.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable, Dict, List, Optional, Sequence, Tuple

from langchain_core.messages import AIMessage, ToolMessage

from services.graph import tool_deadlines, tool_policy, turn_trace, turn_window
from services.graph.history_window import scaffolding_note
from services.toolset import names as toolset_names
from services.toolset import policy as policy_mod
from services.toolset.result import ToolResult, normalize
from services.owner import owner_text

logger = logging.getLogger(__name__)

MAX_ROUNDS_ENV = "EDWARD_MAX_TOOL_ROUNDS"
DEFAULT_MAX_ROUNDS = 60
STEP_BACK_AFTER_FAILED_ROUNDS = 3
STOP_AFTER_FAILED_ROUNDS = 6
NATIVE_COMPUTER_FLAG = "EDWARD_NATIVE_COMPUTER_TOOLS"
SERVER_WEB_FLAG = "EDWARD_SERVER_WEB_TOOLS"
_OFF = frozenset({"off", "0", "false", "no", "disabled"})
TOOL_END_RESULT_CHARS = 500

STEP_BACK_NOTE = owner_text("[SYSTEM] Three rounds in a row failed. Step back: what do the errors have in common? "
                            "Try a different route, or ask {OWNER} for the one thing blocking you.")
EMPTY_AFTER_TOOLS_NOTE = ("[SYSTEM] You ran tools but gave no answer. Say plainly what you did and what came of "
                          "it, and answer. Do not call any more tools.")

# Legacy execution tools (``EDWARD_TOOLSET=legacy`` while they exist), and
# the shell: the work rail shows their code and output.
_CODE_TOOLS: Dict[str, Tuple[str, str]] = {
    "bash": ("command", "bash"),
    "execute_shell": ("command", "bash"),
    "execute_code": ("code", "python"),
    "execute_javascript": ("code", "javascript"),
    "execute_sql": ("query", "sql"),
    "query_persistent_db": ("query", "sql"),
}
_SEARCH_TOOLS = frozenset({"web_search", "web_fetch", "fetch_page_content"})
_BASH_TRAILER = re.compile(r"\n?\[exit (-?\d+) · ([\d.]+)s(?: · [^\]]*)?\]\s*$")


def _streaming():
    from services.graph import streaming

    return streaming


def _flag_on(name: str) -> bool:
    return (os.getenv(name) or "on").strip().lower() not in _OFF


def max_rounds_default() -> int:
    raw = (os.getenv(MAX_ROUNDS_ENV) or "").strip()
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_ROUNDS
    return value if value > 0 else DEFAULT_MAX_ROUNDS


# ------------------------------------------------------------- binding --

def bind_specs(tools: Sequence[Any], provider: Optional[str]) -> List[Any]:
    """What to bind for this provider: under Claude a tool with
    ``metadata["anthropic_tool_resolver"]`` goes as itself and the adapter
    picks its Anthropic-defined form per model and flag (the toolset's
    ``bash``, editor and web tools); a static ``metadata["anthropic_tool"]``
    (``bash_20250124``, ``web_search_20260209`` …) is sent as is unless its
    flag is off (``EDWARD_NATIVE_COMPUTER_TOOLS``,
    ``EDWARD_SERVER_WEB_TOOLS``); otherwise the tool itself (a custom
    schema; the Codex adapter always)."""
    if provider != "anthropic":
        return list(tools or [])
    out: List[Any] = []
    for t in tools or []:
        md = getattr(t, "metadata", None)
        if isinstance(md, dict) and md.get("anthropic_tool_resolver"):
            # The adapter resolves it per bind, for its model and the flags
            # (``ChatClaude._anthropic_spec``): the small model's local web_fetch, and
            # the native-tools flag, hold only if the tool goes as itself.
            out.append(t)
            continue
        spec = md.get("anthropic_tool") if isinstance(md, dict) else None
        if isinstance(spec, dict):
            kind = str(spec.get("type") or "")
            flag = SERVER_WEB_FLAG if kind.startswith(("web_search", "web_fetch")) else NATIVE_COMPUTER_FLAG
            if _flag_on(flag):
                out.append(spec)
                continue
        out.append(t)
    return out


def bind_for_turn(llm: Any, tools: Sequence[Any], provider: Optional[str], tool_choice: Any = None) -> Any:
    """``llm`` with the turn's tools. Never a forced choice: ``tool_choice``
    is only ever ``"none"`` (the closing call on Claude). Claude binds with
    parallel calls on (fewer rounds, less money; they run in order)."""
    if not tools:
        return llm
    specs = bind_specs(tools, provider)
    kwargs: Dict[str, Any] = {}
    if provider == "anthropic":
        kwargs["parallel_tool_calls"] = True
    if tool_choice is not None:
        kwargs["tool_choice"] = tool_choice
    try:
        return llm.bind_tools(specs, **kwargs)
    except TypeError:
        kwargs.pop("parallel_tool_calls", None)
        return llm.bind_tools(specs, **kwargs)


def closing_llm(llm: Any, tools: Sequence[Any], provider: Optional[str]) -> Any:
    """The model for the closing call, which must not call tools. Codex gets
    the bare model, as always. Claude keeps the turn's tools with
    ``tool_choice="none"``: the thread holds tool_use and tool_result
    blocks, which the API refuses without tool definitions, and the same
    tools keep the cached prefix."""
    if provider == "anthropic" and tools:
        return bind_for_turn(llm, tools, provider, "none")
    return llm


# ------------------------------------------------------------- the boundary --

async def _boundary_check(tool_name: str, tool_args: Any) -> Optional[str]:
    """Answers a call without running it, or ``None`` to run it.

    * A legacy send that texts the owner from a wake (``graph.tools.
      text_to_owner_refusal``); the toolset's ``send_message`` checks this
      itself, against the turn's policy.
    * On the legacy loop, a household hub turn's sends and the tools that
      would act as the owner (``graph.tools.hub_legacy_refusal``, 2026-10-11): no
      legacy tool reads the policy.
    * The ping scope (``pings.scope.refusal``) while a friend's ping
      is current: a friend's ping goes without the shell, editor, Chrome
      and screens unless ``EDWARD_FRIENDS_FULL_TOOLSET=on``, and never starts a conversation with someone who asked
      Edward nothing, nor leaves a task behind (moved here from
      ``streaming._boundary_check``, 2026-10-11).
    * ``taint_service.check_tool_call``: the egress secret scan, in every
      mode — a credential never leaves the machine — and the taint gate,
      which is off unless ``EDWARD_TAINT_APPROVALS`` says ``shadow``/``on``
      (the recipient rule replaced it on the send path).

    A check that itself breaks never lets an egress call through unscanned.
    """
    try:
        from services.graph.tools import text_to_owner_refusal

        refused = await text_to_owner_refusal(tool_name, tool_args)
        if refused is not None:
            return refused
    except Exception:  # noqa: BLE001
        logger.exception("text-to-owner check failed for %s", tool_name)
    try:
        from services.graph.tools import hub_legacy_refusal

        refused = hub_legacy_refusal(tool_name, tool_args)
        if refused is not None:
            return refused
    except Exception:  # noqa: BLE001
        logger.exception("hub check failed for %s", tool_name)
    try:
        from services.pings import scope as ping_scope

        refused = await ping_scope.refusal(tool_name, tool_args)
        if refused:
            return "Tool error: " + refused
    except Exception:  # noqa: BLE001
        logger.exception("ping scope check failed for %s", tool_name)
    try:
        from services import taint_service

        return await taint_service.check_tool_call(tool_name, tool_args)
    except Exception as e:  # noqa: BLE001
        logger.exception("boundary check failed for %s", tool_name)
        try:
            from services.secret_scan import is_egress_tool

            if is_egress_tool(tool_name, tool_args):
                return f"Tool error: blocked — the boundary check failed ({type(e).__name__}); nothing was sent."
        except Exception:  # noqa: BLE001
            pass
        return None


def _boundary_note(tool_name: str, tool_args: Any, result: Any) -> None:
    """A read of untrusted content taints the turn (``taint_service``) — a
    ledger signal now, never a hold."""
    try:
        from services import taint_service

        taint_service.note_tool_result(tool_name, tool_args, result)
    except Exception:  # noqa: BLE001
        pass


def _journal_note(tool_name: str, tool_args: Any, result: Any) -> None:
    """What the turn did, for the thought journal: a call in the journal's
    action set is kept on the turn with its args and result — a refusal and
    a failure included. Never raises."""
    try:
        from services.turn_ledger import note_action_result

        note_action_result(tool_name, tool_args, result)
    except Exception:  # noqa: BLE001
        pass


DRY_RUN_ENV = "EDWARD_DRY_RUN_OUTWARD"
# Always effectful, whatever the action: the owner's Chrome and the shell.
_ALWAYS_EFFECTFUL = frozenset({"chrome", "bash"})


def dry_run(policy: Optional[policy_mod.TurnPolicy]) -> bool:
    """The judgement check (``scripts/judgement_eval.py`` sets the env) or a
    policy that says so: outward, surface-changing and committing calls are
    recorded, never run."""
    if policy is not None and policy.dry_run_outward:
        return True
    return (os.getenv(DRY_RUN_ENV) or "").strip().lower() in ("1", "on", "true", "yes")


# What a dry run answered, newest last: the judgement check reads it back
# per conversation (``dry_run_calls``) — no transport ran, so nothing else
# saw the push, the note or the send (review 2026-10-11).
DRY_RUN_LOG_MAX = 500
_dry_run_log: "deque[Dict[str, Any]]" = deque(maxlen=DRY_RUN_LOG_MAX)


def record_dry_run(conversation_id: str, name: str, args: Any) -> None:
    _dry_run_log.append({"conversation_id": conversation_id or "", "name": name,
                         "args": dict(args) if isinstance(args, dict) else {}, "at": time.time()})


def dry_run_calls(conversation_id: Optional[str] = None) -> List[Dict[str, Any]]:
    """The calls a dry run recorded instead of running, oldest first — for
    one conversation, or all of them."""
    return [dict(c) for c in _dry_run_log if conversation_id is None or c["conversation_id"] == conversation_id]


def is_effectful(name: str, args: Any) -> bool:
    """A call that reaches a person, a screen, the owner's Chrome or the shell, or
    commits Edward to something later — for the toolset by ``(tool,
    action)``, for older names by the thought journal's tiers."""
    if name in _ALWAYS_EFFECTFUL or name.startswith("browser_"):
        return True
    if name in toolset_names.TOOLSET_NAMES:
        return toolset_names.tier(name, args) in ("outward", "surface", "commitment")
    try:
        from services import thought_journal

        return (thought_journal.action_tier(name, args) in ("outward", "commitment")
                or name in thought_journal.SURFACE_TOOLS)
    except Exception:  # noqa: BLE001 — unknown: treat as effectful
        return True


def _pings_hooks():
    """The ping runner's hooks (``services/pings/hooks.py``) when installed."""
    try:
        from services.pings import hooks  # type: ignore[import-not-found]

        return hooks
    except Exception:  # noqa: BLE001
        return None


def _pings_note_tool(name: str, args: Any, ok: bool) -> None:
    hooks = _pings_hooks()
    fn = getattr(hooks, "note_tool", None) if hooks is not None else None
    if fn is None:
        return
    try:
        fn(name, args, ok)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pings note_tool failed: %s", exc)


def _pings_note_stop(**fields: Any) -> None:
    hooks = _pings_hooks()
    fn = getattr(hooks, "note_stop", None) if hooks is not None else None
    if fn is None:
        return
    try:
        fn(**fields)
    except Exception as exc:  # noqa: BLE001
        logger.debug("pings note_stop failed: %s", exc)


# ------------------------------------------------------------- one call --

def _resolve(raw_name: str, args: Dict[str, Any], by_name: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """The tool to run: the name as called, or — for a retired name a prompt
    still teaches — the new tool it maps to (``names.alias_call``)."""
    tool = by_name.get(raw_name)
    if tool is not None:
        if raw_name == "send_message" and "recipient" in args:
            schema = getattr(tool, "args", None)
            if isinstance(schema, dict) and "to" in schema and "recipient" not in schema:
                alias = toolset_names.alias_call(raw_name, args)
                if alias is not None:
                    return alias
        return raw_name, args
    alias = toolset_names.alias_call(raw_name, args)
    if alias is not None and alias[0] in by_name:
        logger.info("[tool alias] %s → %s", raw_name, alias[0])
        return alias
    return raw_name, args


def redact_result(result: ToolResult) -> ToolResult:
    """Secrets in a tool's output never reach the model, the work rail, the
    trace or the checkpoint (integrations audit #6): ``secret_scan.
    redact_secrets`` over a text result and over the text blocks of a block
    result — an image or a document block is never touched. The shell and
    the editor redact their own output first; a second pass changes nothing.
    Anthropic's server tools (web search/fetch) ran before Edward saw their
    results and never pass here. A check that breaks keeps nothing."""
    from services import secret_scan

    try:
        known = secret_scan.known_secrets()
        content = result.content
        if isinstance(content, str):
            red, kinds = secret_scan.redact_secrets(content, known)
            return ToolResult(red, result.is_error) if kinds else result
        if isinstance(content, list):
            blocks, changed = [], False
            for block in content:
                if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                    red, kinds = secret_scan.redact_secrets(block["text"], known)
                    if kinds:
                        block, changed = {**block, "text": red}, True
                blocks.append(block)
            return ToolResult(blocks, result.is_error) if changed else result
        return result
    except Exception as exc:  # noqa: BLE001
        logger.exception("redacting a tool result failed")
        return ToolResult(f"Tool error: the result could not be checked for secrets ({type(exc).__name__}), "
                          "so it was withheld.", True)


def _validation_text(name: str, exc: BaseException) -> Optional[str]:
    text = str(exc)
    low = text.lower()
    if "validation error" not in low and "field required" not in low:
        return None
    missing = _streaming()._extract_missing_fields(text)
    if missing:
        return f"Tool error: {name} arguments invalid: missing {', '.join(missing)}"
    first = text.strip().splitlines()[0] if text.strip() else type(exc).__name__
    return f"Tool error: {name} arguments invalid: {first}"


def _ev(event_type: str, conversation_id: str, **kw: Any) -> Dict[str, Any]:
    return _streaming().create_event(event_type, conversation_id, **kw)


def _before_events(name: str, args: Dict[str, Any], conversation_id: str) -> List[Dict[str, Any]]:
    S = _streaming()
    out: List[Dict[str, Any]] = []
    if name in _CODE_TOOLS:
        arg, language = _CODE_TOOLS[name]
        if args.get(arg):
            out.append(_ev(S.EventType.CODE, conversation_id, code=args[arg], language=language))
    if name in _SEARCH_TOOLS:
        msg = f"Searching: {args.get('query', '')}" if name == "web_search" else f"Fetching {args.get('url', '')}"
        out.append(_ev(S.EventType.PROGRESS, conversation_id, step="search", status="started", message=msg,
                       tool_name=name))
    return out


def _after_events(name: str, result: ToolResult, conversation_id: str, ms: int) -> List[Dict[str, Any]]:
    S = _streaming()
    out: List[Dict[str, Any]] = []
    text = result.text
    if name in _SEARCH_TOOLS:
        if name == "web_search":
            count = sum(1 for line in text.splitlines() if line.strip()[:2].rstrip(".").isdigit())
            out.append(_ev(S.EventType.PROGRESS, conversation_id, step="search", status="completed",
                           message=f"Found {count} results" if count else "Search complete", count=count or None,
                           tool_name=name))
        else:
            out.append(_ev(S.EventType.PROGRESS, conversation_id, step="search", status="completed",
                           message=f"Got page ({len(text)} chars)", count=len(text), tool_name=name))
    if name in _CODE_TOOLS:
        output, duration, exit_code = text, ms, None
        m = _BASH_TRAILER.search(text)
        if m:
            output = text[:m.start()]
            exit_code = int(m.group(1))
            try:
                duration = int(float(m.group(2)) * 1000)
            except ValueError:
                pass
        output = output.strip()
        if output:
            out.append(_ev(S.EventType.EXECUTION_OUTPUT, conversation_id, output=output, stream="stdout"))
        success = not result.is_error and (exit_code is None or exit_code == 0)
        out.append(_ev(S.EventType.EXECUTION_RESULT, conversation_id, success=success, duration_ms=duration))
    return out


def _plan_events(name: str, conversation_id: str) -> List[Dict[str, Any]]:
    try:
        from services.graph.tools import PLAN_TOOL_NAMES, get_pending_plan_events
    except Exception:  # noqa: BLE001
        return []
    if name != "plan" and name not in PLAN_TOOL_NAMES:
        return []
    out = []
    for plan_event in get_pending_plan_events(conversation_id):
        event = dict(plan_event)
        event_type = event.pop("event_type")
        out.append(_ev(event_type, conversation_id, **event))
    return out


async def execute_call(tool_call: Dict[str, Any], tools: Sequence[Any], conversation_id: str, *,
                       policy: Optional[policy_mod.TurnPolicy] = None,
                       tracker: Optional[tool_policy.FailureTracker] = None,
                       deadline: Optional[float] = None) -> AsyncIterator[Dict[str, Any]]:
    """Execute one tool call, yielding work-rail events and
    finally ``{"_result": ToolResult, "_name", "_args", "_ms"}``.

    1. Unknown tool / invalid JSON → an error, not run.
    2. The identical-failure stop.
    3. The boundary (never-text-the-owner for legacy sends, the egress scan).
    4. Run under the tool's deadline, clamped to the turn's; the turn's
       policy is current while it runs.
    5. Normalize under the failure contract and redact secrets; journal and
       taint notes.
    """
    S = _streaming()
    raw_name = str(tool_call.get("name") or "")
    args = tool_call.get("args")
    args = dict(args) if isinstance(args, dict) else {}
    by_name = {getattr(t, "name", None): t for t in (tools or [])}
    name, args = _resolve(raw_name, args, by_name)
    tool = by_name.get(name)

    start: Dict[str, Any] = {"tool_name": name}
    summary = S._summarize_tool_args(name, args)
    if summary:
        start["args_summary"] = summary
    yield _ev(S.EventType.TOOL_START, conversation_id, **start)

    t0 = time.monotonic()
    result: Optional[ToolResult] = None
    ran = False
    if tool is None:
        result = ToolResult(f"Unknown tool: {raw_name}", True)
    else:
        invalid = tool_policy.invalid_json_error(name, args)
        if invalid:
            print(f"[TOOL ARGS] {name} arguments were not valid JSON; not executed")
            result = ToolResult(invalid, True)
        elif tracker is not None and (refused := tracker.refusal(name, args)):
            print(f"[TOOL LOOP] {name}: identical failing call refused")
            result = ToolResult(refused, True)
        else:
            refused = await S._boundary_check(name, args)
            if refused is None:
                refused = policy_mod.scope_refusal(
                    policy if policy is not None else policy_mod.current_policy(), name, args)
            if refused is not None:
                result = normalize(refused)
            elif dry_run(policy) and is_effectful(name, args):
                record_dry_run(conversation_id, name, args)
                action = toolset_names.action_of(args)
                result = ToolResult(f"[dry run] {name}{' ' + action if action else ''}: recorded, not run.")
    if result is None:
        for e in _before_events(name, args, conversation_id):
            yield e
        remaining = (deadline - time.monotonic()) if deadline is not None else None
        seconds = tool_deadlines.clamp_to_turn(tool_deadlines.tool_timeout_for(tool), remaining)
        token = policy_mod.set_policy(policy) if policy is not None else None
        try:
            raw: Any = await tool_deadlines.run_tool_with_deadline(name, tool.ainvoke(args), seconds=seconds)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — the contract: every failure is a Tool error
            raw = _validation_text(name, exc) or exc
        finally:
            if token is not None:
                policy_mod.reset_policy(token)
        result = normalize(raw)
        ran = True
    result = redact_result(result)
    ms = int((time.monotonic() - t0) * 1000)
    text = result.text

    _boundary_note(name, args, text)
    if tool is not None:
        _journal_note(name, args, text)
    if ran:
        for e in _after_events(name, result, conversation_id, ms):
            yield e
    yield _ev(S.EventType.TOOL_END, conversation_id, tool_name=name, result=text[:TOOL_END_RESULT_CHARS])
    for e in _plan_events(name, conversation_id):
        yield e
    yield {"_result": result, "_name": name, "_args": args, "_ms": ms}


async def execute_tool_call(tool_call: dict, tools: List[Any]) -> Any:
    """One call, no events (the old non-streaming executor's API): the
    result content (a string, or blocks for an image)."""
    result: Optional[ToolResult] = None
    async for event in execute_call(tool_call, tools, "", policy=None):
        if "_result" in event:
            result = event["_result"]
    return result.content if result is not None else ""


async def execute_tool_call_with_events(tool_call: dict, tools: List[Any], conversation_id: str):
    """One call with its work-rail events (the old streaming executor's
    API): the events, then ``{"_result": content}``."""
    async for event in execute_call(tool_call, tools, conversation_id, policy=None):
        if "_result" in event:
            yield {"_result": event["_result"].content}
        else:
            yield event


# --------------------------------------------------------- server tools --

def _server_summary(name: str, result: Optional[Dict[str, Any]]) -> Tuple[str, bool]:
    """(result line, is_error) for a server tool's result block."""
    if result is None:
        return "(no result)", False
    content = result.get("content")
    if isinstance(content, dict) and (content.get("error_code") or str(content.get("type", "")).endswith("_error")):
        return f"Tool error: {content.get('error_code') or 'failed'}", True
    if isinstance(content, list):
        n = sum(1 for c in content if isinstance(c, dict) and str(c.get("type", "")).endswith("_result"))
        return f"{n} result{'s' if n != 1 else ''}", False
    if isinstance(content, dict):
        url = content.get("url") or ""
        return (f"fetched {url}" if url else "fetched"), False
    return "done", False


def server_tool_uses(response: Any) -> List[Tuple[str, Dict[str, Any], str, bool]]:
    """(name, input, result line, is_error) for each server tool the model
    ran this response — from the adapter's raw blocks (``claude_content``);
    they never appear in ``tool_calls`` and are never executed here."""
    ak = getattr(response, "additional_kwargs", None) or {}
    blocks = ak.get("claude_content") if isinstance(ak, dict) else None
    if not isinstance(blocks, list):
        return []
    results = {b.get("tool_use_id"): b for b in blocks
               if isinstance(b, dict) and str(b.get("type", "")).endswith("_tool_result")}
    out = []
    for b in blocks:
        if isinstance(b, dict) and b.get("type") == "server_tool_use":
            name = str(b.get("name") or "server_tool")
            line, is_error = _server_summary(name, results.get(b.get("id")))
            out.append((name, b.get("input") if isinstance(b.get("input"), dict) else {}, line, is_error))
    return out


def _scan_server_input(name: str, args: Dict[str, Any]) -> None:
    """The egress scan after the fact: a server tool ran at Anthropic before
    we saw it, so a hit is recorded (``egress``/``detected``), never blocked
    — prevention rests on secrets being redacted and unreadable."""
    try:
        from services import secret_scan

        texts = [str(v) for v in args.values() if isinstance(v, str)]
        hits = [f for t in texts for f in secret_scan.find_secrets(t, secret_scan.known_secrets())]
        if not hits:
            return
        kinds = secret_scan.kinds_of(hits)
        logger.warning("[egress] server tool %s carried %s", name, ", ".join(kinds))
        from services.event_ledger import record_event

        record_event(kind="egress", source=name, outcome="detected", reason=", ".join(kinds))
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------- the loop --

@dataclass
class LoopContext:
    """Everything one turn's loop needs, and what it leaves behind."""

    messages: List[Any]
    turn_start: int
    system_prompt: str
    tools: List[Any]
    conversation_id: str
    provider: str
    build_llm: Callable[[], Any]
    policy: policy_mod.TurnPolicy
    stream: bool = False
    model: str = ""
    temperature: float = 0.0
    turn_source: Optional[str] = None
    reasoning_effort: Optional[str] = None
    max_rounds: Optional[int] = None
    max_history_chars: Optional[int] = None
    deadline: Optional[float] = None
    deadline_label: str = ""
    trace: Optional[turn_trace.TraceBuilder] = None
    progress: Any = None
    # What the loop leaves behind.
    full_response: str = ""
    stop_reason: Optional[str] = None
    exhausted: bool = False
    llm_timed_out: bool = False
    tool_calls_made: List[dict] = field(default_factory=list)
    tool_log: List[Tuple[str, Any]] = field(default_factory=list)
    cost_usd: float = 0.0
    rounds: int = 0
    incomplete_reason: Optional[str] = None
    outcome: str = ""


def _owner_facing(policy: policy_mod.TurnPolicy) -> bool:
    """Someone is waiting for this turn's answer: the owner, a ping's chat, or
    whoever is at the household hub (``hub_voice`` — not the owner, still there)."""
    return policy.owner_asked or policy.source in policy_mod.PING_SOURCES or policy.source == policy_mod.HUB_VOICE_SOURCE


def _closing_note(kind: str, ctx: LoopContext) -> str:
    stop = "Do not call any more tools."
    if kind == "time":
        return (f"[SYSTEM] Stop here: you are out of time for this turn ({ctx.deadline_label}). Say plainly what you "
                f"did and what is left, and respond. {stop}")
    if kind == "rounds":
        return (f"[SYSTEM] Stop here: you have used all {ctx.rounds} tool rounds. Say what you accomplished and what "
                f"is left, and respond. {stop}")
    if kind == "budget":
        offer = " You can offer to continue." if ctx.policy.owner_asked else ""
        return (f"[SYSTEM] Stop here: this turn has spent its budget (${ctx.policy.turn_cap_usd():.2f}). Say where "
                f"you got to and what is left.{offer} {stop}")
    if kind == "failed":
        return (f"[SYSTEM] Stop here: {STOP_AFTER_FAILED_ROUNDS} tool rounds in a row failed. Say plainly what you "
                f"did, what failed and why. {stop}")
    if kind == "empty":
        return EMPTY_AFTER_TOOLS_NOTE
    return f"[SYSTEM] Stop here ({ctx.stop_reason or 'no answer yet'}). Say plainly what you did, what failed and what is left. {stop}"


def _window(ctx: LoopContext) -> List[Any]:
    return turn_window.window_for_call(ctx.messages, ctx.turn_start, history_max_chars=ctx.max_history_chars,
                                       elide_turn=ctx.provider != "anthropic")


def _cost(response: Any) -> float:
    meta = getattr(response, "response_metadata", None) or {}
    try:
        return float(meta.get("estimated_cost_usd") or meta.get("cost_usd") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _stop_reason_of(response: Any) -> Optional[str]:
    meta = getattr(response, "response_metadata", None) or {}
    return meta.get("stop_reason") if isinstance(meta, dict) else None


def _has_cut_tool_use(response: Any) -> bool:
    ak = getattr(response, "additional_kwargs", None) or {}
    blocks = ak.get("claude_content") if isinstance(ak, dict) else None
    return isinstance(blocks, list) and any(isinstance(b, dict) and b.get("type") == "tool_use" for b in blocks)


def cap_output_tokens(llm: Any, cap: Optional[int]) -> Any:
    """``llm`` with ``max_tokens`` lowered to ``cap`` when it is higher (a
    policy's ``max_output_tokens``); never raised, and unchanged when the
    model has no such field."""
    current = getattr(llm, "max_tokens", None)
    if (not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0 or not isinstance(current, int)
            or current <= cap or not hasattr(llm, "model_copy")):
        return llm
    try:
        return llm.model_copy(update={"max_tokens": cap})
    except Exception:  # noqa: BLE001
        return llm


def _built(ctx: "LoopContext") -> Any:
    """The turn's model for one call, under its policy's output cap."""
    return cap_output_tokens(ctx.build_llm(), getattr(ctx.policy, "max_output_tokens", None))


def _with_more_tokens(llm: Any) -> Any:
    """The same model with ``max_tokens`` doubled, when it has one."""
    current = getattr(llm, "max_tokens", None)
    if isinstance(current, int) and current > 0 and hasattr(llm, "model_copy"):
        try:
            return llm.model_copy(update={"max_tokens": current * 2})
        except Exception:  # noqa: BLE001
            return llm
    return llm


async def _call_model(ctx: LoopContext, llm: Any, call_messages: List[Any]) -> AsyncIterator[Dict[str, Any]]:
    """One model call. Streaming forwards text, reasoning and tool-argument
    deltas as events; both end with ``{"_response": AIMessage, "_streamed":
    bool}``."""
    S = _streaming()
    if not ctx.stream:
        response = await S._invoke_with_retry(llm, call_messages, ctx.provider, ctx.model, ctx.temperature,
                                              ctx.tools, reasoning_effort=ctx.reasoning_effort,
                                              turn_source=ctx.turn_source)
        yield {"_response": response, "_streamed": False}
        return
    from services.speculative_recall import SpeculativeRecaller

    recaller = SpeculativeRecaller(conversation_id=ctx.conversation_id)
    response = None
    streamed = False
    async for evt in S._stream_llm_with_events(llm, call_messages):
        kind = evt["type"]
        if kind == "content_delta":
            streamed = True
            yield _ev(S.EventType.CONTENT, ctx.conversation_id, content=evt["text"])
            try:
                await recaller.observe(evt["text"])
            except Exception as exc:  # noqa: BLE001
                logger.debug("speculative recall observe failed: %s", exc)
        elif kind == "reasoning_delta":
            if ctx.trace is not None:
                ctx.trace.note_reasoning_delta(evt["text"])
            yield _ev(S.EventType.REASONING, ctx.conversation_id, content=evt["text"])
        elif kind == "tool_args_delta":
            yield _ev(S.EventType.TOOL_ARGS_DELTA, ctx.conversation_id, tool_name=evt.get("name", ""),
                      call_id=evt.get("id", ""), delta=evt.get("delta", ""))
        elif kind == "final":
            response = evt["message"]
    yield {"_response": response if response is not None else AIMessage(content=""), "_streamed": streamed}


def _note_response(ctx: LoopContext, response: Any) -> None:
    S = _streaming()
    S._ledger_note_reasoning(response)
    if ctx.trace is not None:
        ctx.trace.note_response(response)
    ctx.cost_usd += _cost(response)
    ak = getattr(response, "additional_kwargs", None) or {}
    if isinstance(ak, dict) and ak.get("incomplete_reason"):
        ctx.incomplete_reason = ak.get("incomplete_reason")


def _server_tool_events(ctx: LoopContext, response: Any) -> List[Dict[str, Any]]:
    S = _streaming()
    out = []
    for name, args, line, is_error in server_tool_uses(response):
        summary = S._summarize_tool_args(name, args)
        start = {"tool_name": name, **({"args_summary": summary} if summary else {})}
        out.append(_ev(S.EventType.TOOL_START, ctx.conversation_id, **start))
        out.append(_ev(S.EventType.TOOL_END, ctx.conversation_id, tool_name=name, result=line[:TOOL_END_RESULT_CHARS]))
        if ctx.trace is not None:
            ctx.trace.note_tool(name, args, line, None)
        ctx.tool_log.append((name, line))
        if not ctx.stream:
            S._ledger_note_tool(name, line, None)
        _pings_note_tool(name, args, not is_error)
        _scan_server_input(name, args)
    return out


def _deadline_reached(ctx: LoopContext) -> bool:
    if ctx.deadline is None:
        return False
    return ctx.deadline - time.monotonic() <= tool_deadlines.CLOSING_RESERVE_SECONDS


async def run_tool_loop(ctx: LoopContext) -> AsyncIterator[Dict[str, Any]]:
    """The turn: model call, tools, repeat — until an answer, or a stop that
    ends with one closing call. Fills ``ctx`` (``full_response``,
    ``stop_reason``, ``exhausted`` …). Yields SSE events (the non-streaming
    caller drains them)."""
    S = _streaming()
    policy = ctx.policy
    max_rounds = ctx.max_rounds or max_rounds_default()
    tracker = tool_policy.FailureTracker()
    done: List[Tuple[str, Any, bool]] = []
    failed_rounds = 0
    stepped_back = False
    reply_nudged = False
    retried_cut = False
    closing_kind: Optional[str] = None
    system = S._make_system_message(ctx.system_prompt, ctx.provider)
    llm = bind_for_turn(_built(ctx), ctx.tools, ctx.provider)
    try:
        while True:
            # ---- stops before a model call ----
            if ctx.rounds >= max_rounds:
                ctx.stop_reason = f"it ran out of tool rounds ({max_rounds})"
                ctx.exhausted, closing_kind = True, "rounds"
                break
            if ctx.rounds > 0 and _deadline_reached(ctx):
                ctx.stop_reason = f"the turn ran past its {ctx.deadline_label or 'time'} limit"
                ctx.exhausted, closing_kind = True, "time"
                print(f"[TOOL LOOP] deadline after {ctx.rounds} rounds")
                break
            if ctx.rounds > 0 and ctx.cost_usd >= policy.turn_cap_usd():
                ctx.stop_reason = f"the turn spent its budget (${ctx.cost_usd:.2f})"
                ctx.exhausted, closing_kind = True, "budget"
                print(f"[TOOL LOOP] dollar cap: ${ctx.cost_usd:.2f} after {ctx.rounds} rounds")
                break
            ctx.rounds += 1
            if ctx.rounds > 1:
                if ctx.stream:
                    yield _ev(S.EventType.THINKING, ctx.conversation_id, content="Thinking...")
                else:
                    S._ledger_note_iteration()

            # ---- the model ----
            call_messages = [system] + _window(ctx)
            response = None
            streamed = False
            try:
                async for evt in _call_model(ctx, llm, call_messages):
                    if "_response" in evt:
                        response, streamed = evt["_response"], evt["_streamed"]
                    else:
                        yield evt
            except tool_deadlines.LLMCallTimeout as exc:
                ctx.stop_reason = f"the model did not answer within {exc.seconds:g}s"
                ctx.llm_timed_out = True
                print(f"[LLM DEADLINE] {ctx.conversation_id}: {ctx.stop_reason} (round {ctx.rounds})")
                break
            await S._record_turn_usage(response, provider=ctx.provider, model=ctx.model,
                                       conversation_id=ctx.conversation_id, turn_source=ctx.turn_source)
            _note_response(ctx, response)
            for e in _server_tool_events(ctx, response):
                yield e

            api_stop = _stop_reason_of(response)
            tool_calls = list(getattr(response, "tool_calls", None) or [])
            if api_stop == "refusal" and tool_calls:
                # Never run a tool from a response the model declined.
                tool_calls = []
                response = response.model_copy(update={"tool_calls": []})
            cut_call = api_stop == "max_tokens" and not tool_calls and _has_cut_tool_use(response)
            if (api_stop == "max_tokens" and not tool_calls and not retried_cut
                    and (cut_call or (not streamed and not S._text_of(response.content).strip()))):
                # A tool call cut off by max_tokens: never run it; ask once more with room —
                # even after a streamed preamble (review 2026-10-11: the preamble became the
                # "answer" and nothing ran).
                retried_cut = True
                if ctx.stream:
                    yield _ev(S.EventType.PROGRESS, ctx.conversation_id, step="generating", status="retrying",
                              message="My answer was cut off before a tool could run; asking again with more room")
                llm = bind_for_turn(_with_more_tokens(_built(ctx)), ctx.tools, ctx.provider)
                print(f"[TOOL LOOP] max_tokens cut a response; asking again with more room")
                continue
            if cut_call:
                # Cut again: the text before it is not an answer — end honestly.
                ctx.stop_reason = "the answer hit the output limit while writing a tool call, so nothing ran"
                ctx.incomplete_reason = ctx.incomplete_reason or "max_tokens"
                print(f"[TOOL LOOP] max_tokens cut a tool call twice; ending honestly")
                break

            if tool_calls:
                ctx.messages.append(response)
                round_errors: List[bool] = []
                for call in tool_calls:
                    ctx.tool_calls_made.append(call)
                    result: Optional[ToolResult] = None
                    executed = (call.get("name"), call.get("args"))
                    ms = 0
                    if ctx.stream:
                        yield _ev(S.EventType.PROGRESS, ctx.conversation_id, step="tool_execution", status="started",
                                  message=f"Running {call.get('name')}...", tool_name=call.get("name"))
                    async for evt in execute_call(call, ctx.tools, ctx.conversation_id, policy=policy,
                                                  tracker=tracker, deadline=ctx.deadline):
                        if "_result" in evt:
                            result, ms = evt["_result"], evt["_ms"]
                            executed = (evt["_name"], evt["_args"])
                            continue
                        if not ctx.stream and evt.get("type") == S.EventType.TOOL_START:
                            S._ledger_note_tool_started(evt.get("tool_name") or "?")
                        if ctx.stream:
                            yield evt
                    if ctx.stream:
                        yield _ev(S.EventType.PROGRESS, ctx.conversation_id, step="tool_execution",
                                  status="completed", message=f"Completed {call.get('name')}",
                                  tool_name=call.get("name"))
                    result = result or ToolResult("Tool error: the call produced no result", True)
                    name, args = executed
                    text = result.text
                    print(f"Tool {name} result: {text[:200]}{'...' if len(text) > 200 else ''}")
                    content = turn_window.cap_tool_result(result.content)
                    ctx.messages.append(ToolMessage(content=content, tool_call_id=call["id"],
                                                    status="error" if result.is_error else "success"))
                    tracker.record(name, args, is_error=result.is_error, text=text)
                    done.append((name, args, not result.is_error))
                    ctx.tool_log.append((name, text))
                    if ctx.trace is not None:
                        ctx.trace.note_tool(name, args, text, ms)
                    if not ctx.stream:
                        S._ledger_note_tool(name, text, ms)
                    _pings_note_tool(name, args, not result.is_error)
                    if ctx.progress is not None:
                        ctx.progress.completed += 1
                    round_errors.append(result.is_error)
                if round_errors and all(round_errors):
                    failed_rounds += 1
                    print(f"[TOOL LOOP] failed rounds in a row: {failed_rounds}")
                else:
                    failed_rounds = 0
                if failed_rounds >= STOP_AFTER_FAILED_ROUNDS:
                    ctx.stop_reason = f"{failed_rounds} tool rounds in a row failed"
                    ctx.exhausted, closing_kind = True, "failed"
                    break
                if failed_rounds >= STEP_BACK_AFTER_FAILED_ROUNDS and not stepped_back:
                    stepped_back = True
                    ctx.messages.append(scaffolding_note(STEP_BACK_NOTE))
                continue

            # ---- an answer ----
            text = S._text_of(response.content)
            if (policy.wants_reply() and not reply_nudged and ctx.rounds < max_rounds
                    and not tool_policy.reply_satisfied(policy, done)
                    and not (policy.final_text_answers() and text.strip())):
                reply_nudged = True
                ctx.messages.append(AIMessage(content=text))
                ctx.messages.append(scaffolding_note(tool_policy.reply_note(policy)))
                print(f"[TOOL LOOP] reply check: asked in {policy.reply_to or 'a chat'}, no answer there yet")
                continue
            if text.strip():
                ctx.full_response = text
                if ctx.stream:
                    yield _ev(S.EventType.PROGRESS, ctx.conversation_id, step="generating", status="started",
                              message="Generating response...")
                    if not streamed:
                        yield _ev(S.EventType.CONTENT, ctx.conversation_id, content=text)
                    yield _ev(S.EventType.PROGRESS, ctx.conversation_id, step="generating", status="completed",
                              message="Response complete")
                ctx.outcome = "answered"
            elif ctx.tool_calls_made and _owner_facing(policy):
                ctx.stop_reason = "the model gave no answer after its tools"
                closing_kind = "empty"
            elif _owner_facing(policy):
                ctx.stop_reason = "the model returned an empty answer"
            else:
                ctx.outcome = "left_alone"  # an empty end_turn is a valid ending for a wake
            break

        # ---- the closing call ----
        if closing_kind is not None and not ctx.llm_timed_out:
            if ctx.exhausted and ctx.stream:
                label = {"time": f"Ran out of time ({ctx.deadline_label})", "budget": "Reached this turn's budget",
                         "failed": f"Stopped: {ctx.stop_reason}"}.get(closing_kind, f"Ran out of tool rounds ({ctx.rounds})")
                yield _ev(S.EventType.PROGRESS, ctx.conversation_id, step="tool_loop", status="exhausted",
                          message=label, count=ctx.rounds)
            async for evt in _closing_call(ctx, closing_kind):
                yield evt

        if not ctx.full_response.strip() and ctx.outcome != "left_alone":
            ctx.full_response = tool_policy.honest_ending(ctx.stop_reason or "the model returned nothing",
                                                          ctx.tool_log)
            ctx.outcome = ctx.outcome or "honest_ending"
            if ctx.stream:
                yield _ev(S.EventType.CONTENT, ctx.conversation_id, content=ctx.full_response)
        if ctx.progress is not None:
            ctx.progress.stop_reason = ctx.stop_reason
            ctx.progress.exhausted = ctx.exhausted
        _pings_note_stop(outcome=ctx.outcome or ("deadline" if closing_kind == "time" else
                                                 "turn_budget" if closing_kind == "budget" else "answered"),
                         stop_reason=ctx.stop_reason, incomplete_reason=ctx.incomplete_reason)
    except Exception as exc:
        try:
            from services.llm_errors import LLMProviderError
        except Exception:  # noqa: BLE001
            LLMProviderError = ()  # type: ignore[assignment]
        _pings_note_stop(outcome="error", stop_reason=ctx.stop_reason, error_class=type(exc).__name__,
                         retryable=bool(getattr(exc, "retryable", False)) if isinstance(exc, LLMProviderError) else None)
        raise


async def _closing_call(ctx: LoopContext, kind: str) -> AsyncIterator[Dict[str, Any]]:
    """One call without tools to say what was done and what is left. The
    system prompt is the turn's own, byte for byte; the instruction is a
    trailing message, which is scaffolding and never saved."""
    S = _streaming()
    note = _closing_note(kind, ctx)
    call_messages = [S._make_system_message(ctx.system_prompt, ctx.provider)] + _window(ctx) + [
        scaffolding_note(note)]
    llm = closing_llm(_built(ctx), ctx.tools, ctx.provider)
    print(f"[TOOL LOOP] closing call ({kind}): {ctx.stop_reason}")
    try:
        if ctx.stream:
            text_parts: List[str] = []
            async for chunk in tool_deadlines.aiter_with_deadline(llm.astream(call_messages)):
                content = S._text_of(chunk.content) if getattr(chunk, "content", None) else ""
                if content:
                    text_parts.append(content)
                    yield _ev(S.EventType.CONTENT, ctx.conversation_id, content=content)
                ctx.cost_usd += _cost(chunk)
            ctx.full_response = "".join(text_parts)
        else:
            final = await tool_deadlines.invoke_with_deadline(llm.ainvoke(call_messages))
            S._ledger_note_reasoning(final)
            ctx.cost_usd += _cost(final)
            ctx.full_response = S._text_of(final.content)
    except tool_deadlines.LLMCallTimeout as exc:
        ctx.stop_reason = f"{ctx.stop_reason or 'no answer'}, and the closing call did not answer within {exc.seconds:g}s"
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 — the turn still ends honestly, with its tool results saved
        print(f"[WARNING] closing call failed: {exc}")
        ctx.stop_reason = f"{ctx.stop_reason or 'no answer'}, and the closing call failed ({type(exc).__name__})"
    if ctx.full_response.strip():
        # ``turn_budget`` is this turn's own dollar cap, never the day's
        # spend cap (``LLMSpendCapReached``): its closing words are an answer
        # (review 2026-10-11: the ping runner read ``cap`` as the day's and
        # posted "I've hit my limit for today" over a real answer).
        ctx.outcome = {"time": "deadline", "budget": "turn_budget"}.get(kind, "answered")
