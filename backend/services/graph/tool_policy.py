"""The tool loop's small decisions, as pure functions.

A 2026-09-24 review found the mind "stuck on tool calls" for reasons that
were each one rule in the loop; the toolset (2026-10-11) kept the rules
that protect against real harm and removed the GPT-era coaxing:

* **Kept.** Unparseable arguments are answered, never run as ``{}``
  (``invalid_json_error``); a turn that stops early says why and what ran
  (``honest_ending``), never "I completed the requested actions".
* **Rebuilt.** The circuit breaker is the identical-failure stop
  (``FailureTracker``): the same call that failed twice is refused. It
  works on the uniform failure contract (``services/toolset/result.py``) —
  the old breaker saw only results prefixed ``Tool error:`` and was blind
  to ~80% of failures (0 fires in 8.4 days).
* **Replaced.** The required-tool nudge and the forced ``tool_choice`` (a
  400 on Opus/Sonnet 5.5) are the reply check (``reply_satisfied``,
  ``reply_note``): asked in a chat and ended without a send there or a
  stand-down (a word to the owner counts only in their own turn), the loop says so
  once — it never forces.
* **Removed.** The generic "you MUST use your tools" nudge, the plan
  nudge, the repeat guard and its reuse cache (0 fires; identical commands
  legitimately re-run: polling a log, re-reading a file after an edit).
"""

from __future__ import annotations

import json
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from services.owner import owner_text

_FAILURE_PREFIXES = ("Tool error:", "Unknown tool:", "BLOCKED:")
_UNPRODUCTIVE_PREFIXES = _FAILURE_PREFIXES + ("[repeat call",)
INVALID_JSON_KEY = "__invalid_json__"
# The same call (name + canonical arguments) may fail this many times; the
# next identical call is refused.
IDENTICAL_FAILURES_ALLOWED = 2
FAILURE_HEAD_CHARS = 160

# Names whose successful call answers a chat (the reply check): the
# toolset's and the legacy ones, for ``EDWARD_TOOLSET=legacy``.
REPLY_SEND_TOOLS = frozenset({"send_message", "send_imessage", "send_sms", "send_whatsapp"})
REPLY_TELL_OWNER_TOOLS = frozenset({"tell_owner", "leave_note", "send_push_notification"})


# ------------------------------------------------------------ results -----

def is_failure_result(result: Any) -> bool:
    """A tool result that is a failure: ``Tool error:`` or ``Unknown tool:``."""
    return str(result or "").startswith(_FAILURE_PREFIXES)


def is_unproductive_result(result: Any) -> bool:
    """A result that moved nothing forward: a failure, a circuit-breaker
    block, or a reused repeat. An iteration of only these counts toward the
    consecutive-failure breaker."""
    return str(result or "").startswith(_UNPRODUCTIVE_PREFIXES)


def invalid_json_error(name: str, args: Any) -> Optional[str]:
    """The answer for a call whose arguments could not be parsed (the Codex
    adapter marks them ``{"__invalid_json__": raw}``), else ``None``. Such a
    call is never executed: running it as ``{}`` is a guess."""
    if isinstance(args, dict) and INVALID_JSON_KEY in args:
        raw = str(args.get(INVALID_JSON_KEY) or "")
        return f"Tool error: the arguments for {name} were not valid JSON: {raw[:500]}"
    return None


# ------------------------------------------------- identical failures ---

def call_key(name: str, args: Any) -> str:
    try:
        canon = json.dumps(args if args is not None else {}, sort_keys=True, separators=(",", ":"), default=str)
    except (TypeError, ValueError):
        canon = str(args)
    return f"{name}:{canon}"


def _head(text: Any) -> str:
    t = " ".join(str(text or "").split())
    if t.startswith("Tool error:"):
        t = t[len("Tool error:"):].strip()
    return t[:FAILURE_HEAD_CHARS] + ("…" if len(t) > FAILURE_HEAD_CHARS else "")


class FailureTracker:
    """Per turn: how often each exact call (name + canonical arguments)
    failed. Success never counts and resets nothing."""

    def __init__(self, allowed: int = IDENTICAL_FAILURES_ALLOWED) -> None:
        self.allowed = allowed
        self._fails: Dict[str, int] = {}
        self._last: Dict[str, str] = {}

    def refusal(self, name: str, args: Any) -> Optional[str]:
        key = call_key(name, args)
        if self._fails.get(key, 0) >= self.allowed:
            return (f"Tool error: this exact call failed twice already ({self._last.get(key, 'see above')}). "
                    "Change something, or take another route.")
        return None

    def record(self, name: str, args: Any, *, is_error: bool, text: Any = "") -> None:
        if not is_error:
            return
        key = call_key(name, args)
        self._fails[key] = self._fails.get(key, 0) + 1
        self._last[key] = _head(text)


# --------------------------------------------------------- reply check ---

def _same_chat(a: Any, b: Any) -> bool:
    try:
        from services.graph.tools import same_chat

        return same_chat(a, b)
    except Exception:  # noqa: BLE001
        return bool(a) and str(a).strip().lower() == str(b or "").strip().lower()


def _action(args: Any) -> str:
    return str((args or {}).get("action") or "").strip().lower() if isinstance(args, dict) else ""


def _target(args: Any) -> Optional[str]:
    if not isinstance(args, dict):
        return None
    for key in ("chat_id", "to", "recipient", "contact", "phone_number"):
        if args.get(key):
            return str(args[key])
    return None


def _owners_turn(policy: Any) -> bool:
    """The owner is the one asking (their chat, their own ping): a word to
    them is an answer."""
    return bool(getattr(policy, "owner_asked", False)) or getattr(policy, "source", "") == "ping_owner"


def reply_satisfied(policy: Any, done: Iterable[Tuple[str, Any, bool]]) -> bool:
    """Has this turn answered where it was asked? ``done`` is ``(name, args, ok)`` per call. A
    successful send into a ``reply_to`` chat or a stand-down for it answers
    it; with ``any_reply`` (a legacy ``required_tools``) any successful
    send or stand-down does. A ``tell_owner`` answers only in the owner's
    own turn (review 2026-10-11): in someone else's chat it left the
    person who asked with nothing — tell the owner *and* say so there."""
    reply_to = tuple(getattr(policy, "reply_to", ()) or ())
    any_reply = bool(getattr(policy, "any_reply", False)) or not reply_to
    owners = _owners_turn(policy)

    def _into_reply_chat(target: Optional[str]) -> bool:
        return any_reply or any(_same_chat(target, chat) for chat in reply_to)

    for name, args, ok in done:
        if not ok:
            continue
        if name in REPLY_SEND_TOOLS and _into_reply_chat(_target(args)):
            return True
        if (name == "messages" and _action(args) == "stand_down") or name == "stand_down":
            if _into_reply_chat(_target(args)):
                return True
        if name in REPLY_TELL_OWNER_TOOLS and owners:
            return True
    return False


_REPLY_NOTE_UNNAMED = owner_text(
    "[SYSTEM] You were asked to answer this message and haven't. Reply with send_message, even if only "
    "to say what you couldn't do or that you're checking with {OWNER} — or stand down if someone there asked "
    "you to stay out.")


def reply_note(policy: Any) -> str:
    """The once-per-turn scaffolding when the reply check fails."""
    reply_to = tuple(getattr(policy, "reply_to", ()) or ())
    if reply_to:
        chat = reply_to[0]
        return (f"[SYSTEM] You were asked in {chat} and haven't replied there. Reply with "
                f"send_message(chat_id=\"{chat}\"), even if only to say what you couldn't do — or "
                "messages(action=\"stand_down\") if someone there asked you to stay out.")
    return _REPLY_NOTE_UNNAMED


# ------------------------------------------------------------ endings -----

def summarize_tool_log(tool_log: Sequence[Tuple[str, Any]]) -> str:
    """``browser_open, browser_read ×2 (1 failed), send_imessage (failed)``."""
    order: List[str] = []
    counts: Dict[str, List[int]] = {}
    for name, result in tool_log:
        if name not in counts:
            order.append(name)
            counts[name] = [0, 0]
        counts[name][0] += 1
        if is_unproductive_result(result):
            counts[name][1] += 1
    parts = []
    for name in order:
        n, failed = counts[name]
        if n == 1:
            parts.append(f"{name} (failed)" if failed else name)
        else:
            parts.append(f"{name} ×{n}" + (f" ({failed} failed)" if failed else ""))
    return ", ".join(parts)


def honest_ending(reason: str, tool_log: Sequence[Tuple[str, Any]]) -> str:
    """What a turn says when it stops without an answer from the model: why,
    and what actually ran. Replaces "I completed the requested actions",
    which Edward said whether or not he had."""
    reason = (reason or "the model returned nothing").rstrip(". ")
    if tool_log:
        return f"I stopped before finishing: {reason}. Done so far: {summarize_tool_log(tool_log)}."
    return f"I stopped before finishing: {reason}. No tools ran."


# ---------------------------------------------------- the noticer's shims --
# The noticing path imports these: the tools that can
# reach a person or a surface, and the whole toolset. A caller that
# subtracts REACH_TOOLS from a legacy ``allowed_tools`` gets a quiet turn
# (``toolset.policy.legacy_policy`` translates it).

REACH_TOOLS = frozenset({"send_message", "tell_owner", "chrome", "screen"})


def agent_tools() -> Tuple[str, ...]:
    """Every toolset tool's name, in order (``toolset.names.TOOL_ORDER``)."""
    from services.toolset.names import TOOL_ORDER

    return TOOL_ORDER
