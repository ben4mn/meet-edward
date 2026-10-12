"""The in-turn window: what the model sees on each tool-loop iteration.

2026-09-24 (docs/architecture/tool-loop.md). The mind and the pulse
pass ``max_history_chars=24_000``, and the non-streaming loop re-applied
that window on every tool iteration with ``trim_history(messages)``. Inside
a turn the oldest group is the trigger — for a wake, the pack, which *is*
the task — so a 6K pack followed by three 14–16K browser reads was off the
wire by iteration 3. The model saw a tool result with no reason for having
called it, re-read and re-opened until the 240 s ``wait_for`` fired, and
the turn-end trim then removed the pack from the thread for good.

Now the window has two parts:

* the history *before* the turn's trigger is bounded by
  ``max_history_chars`` exactly as before (``trim_history``);
* the turn itself — the trigger and every AI/tool message produced since —
  gets its own budget, ``CURRENT_TURN_MAX_CHARS``. Over it, this turn's
  *oldest tool results* are elided to a short head with a marker, oldest
  first. The trigger is never elided and nothing of the turn is dropped,
  so no tool result is orphaned from its call.

Every tool result is also capped where it is appended
(``cap_tool_result``): one browser read can be ~24K characters.

Everything here is pure; the loops in ``streaming.py`` call it.
"""

from __future__ import annotations

from typing import Any, List, Optional

from langchain_core.messages import ToolMessage

from services.graph.history_window import DEFAULT_MAX_HISTORY_CHARS, message_cost, trim_history

# The turn's own budget: the task plus this turn's calls and results. About
# 30K tokens — room for a pack and several full reads, far inside the
# model's window alongside the system prompt and the history.
CURRENT_TURN_MAX_CHARS = 120_000
# One tool result, capped where it is appended to the turn (16K until the
# toolset, 2026-10-11: Claude's window, and result caching makes the
# resend cheap; ``bash`` spills the rest to a file).
TOOL_RESULT_MAX_CHARS = 30_000
# What an elided result keeps: enough to remind the model what it was.
ELIDED_HEAD_CHARS = 1_500

_ELIDED_MARK = "\n[elided: "
_TRUNCATED_MARK = "\n[truncated: "
# Room reserved for the truncation marker so a capped result stays under
# the cap.
_MARK_RESERVE = 140


def cap_tool_result(text: Any, max_chars: Optional[int] = None) -> Any:
    """``text`` as a string of at most ``max_chars`` characters: the head,
    then a marker naming how much is missing. A list of content blocks
    (an image result) keeps its blocks; only its text blocks are capped."""
    limit = TOOL_RESULT_MAX_CHARS if max_chars is None else max_chars
    if isinstance(text, list):
        out = []
        for block in text:
            if isinstance(block, dict) and block.get("type") == "text":
                capped = cap_tool_result(block.get("text") or "", limit)
                out.append(block if capped == block.get("text") else {**block, "text": capped})
            else:
                out.append(block)
        return out
    text = "" if text is None else str(text)
    if len(text) <= limit:
        return text
    head = max(0, limit - _MARK_RESERVE)
    missing = len(text) - head
    return (
        f"{text[:head]}{_TRUNCATED_MARK}{missing} chars not shown — call the tool again with "
        f"narrower arguments if you need them]"
    )


def elide_text(text: Any, head: int = ELIDED_HEAD_CHARS) -> str:
    """The first ``head`` characters and a marker saying what is gone."""
    text = "" if text is None else str(text)
    if is_elided(text) or len(text) <= head + len(_ELIDED_MARK) + 60:
        return text
    return f"{text[:head]}{_ELIDED_MARK}{len(text) - head} chars — call the tool again if you need it]"


def is_elided(text: Any) -> bool:
    return isinstance(text, str) and _ELIDED_MARK in text and text.rstrip().endswith("if you need it]")


def turn_cost(messages: List[Any]) -> int:
    return sum(message_cost(m) for m in messages)


def window_current_turn(turn: List[Any], max_chars: Optional[int] = None) -> List[Any]:
    """The turn as it goes on the wire: whole when it fits, else with its
    oldest tool results elided (copies — the originals, which the
    checkpoint keeps, are untouched). ``turn[0]`` is the trigger and is
    never touched. Returns the turn unchanged when nothing more can be
    elided: the turn is never dropped."""
    limit = CURRENT_TURN_MAX_CHARS if max_chars is None else max_chars
    if not turn:
        return []
    out = list(turn)
    total = turn_cost(out)
    if total <= limit:
        return out
    for i in range(1, len(out)):
        if total <= limit:
            break
        m = out[i]
        if not isinstance(m, ToolMessage) or not isinstance(m.content, str):
            continue
        shorter = elide_text(m.content)
        if shorter == m.content:
            continue
        total -= len(m.content) - len(shorter)
        out[i] = m.model_copy(update={"content": shorter})
    return out


INTERRUPTED_RESULT = "[interrupted — this call was running when the turn stopped; its outcome is unknown]"
NOT_RUN_RESULT = "[not run — the turn stopped before this call]"


def close_open_tool_calls(messages: List[Any], turn_start: int = 0) -> List[Any]:
    """Answer every tool call from ``turn_start`` on that has no result yet.

    A turn cancelled mid-tool leaves an AIMessage whose later calls have no
    ToolMessage; the API rejects such a history. The first unanswered call
    of a group was running (a send may or may not have gone out — the next
    turn has to know that); the rest never started.
    """
    from langchain_core.messages import AIMessage

    out: List[Any] = list(messages[:turn_start])
    i = max(0, turn_start)
    while i < len(messages):
        m = messages[i]
        out.append(m)
        i += 1
        calls = getattr(m, "tool_calls", None) if isinstance(m, AIMessage) else None
        if not calls:
            continue
        answered = set()
        while i < len(messages) and isinstance(messages[i], ToolMessage):
            answered.add(messages[i].tool_call_id)
            out.append(messages[i])
            i += 1
        running = True
        for c in calls:
            cid = c.get("id") if isinstance(c, dict) else None
            if not cid or cid in answered:
                continue
            out.append(ToolMessage(content=INTERRUPTED_RESULT if running else NOT_RUN_RESULT, tool_call_id=cid))
            running = False
    return out


def window_for_call(
    messages: List[Any],
    turn_start: Optional[int],
    history_max_chars: Optional[int] = None,
    turn_max_chars: Optional[int] = None,
    elide_turn: bool = True,
) -> List[Any]:
    """What to send on one LLM call of a turn.

    ``messages[turn_start]`` is the turn's trigger. ``history_max_chars``
    (the caller's ``max_history_chars``; the 400K default when ``None``)
    bounds only what came before it; the turn has ``turn_max_chars``
    (``CURRENT_TURN_MAX_CHARS`` when ``None``). Without a ``turn_start``
    this is the old whole-list ``trim_history``.

    ``elide_turn=False`` sends the turn as it is: under the Claude provider
    an edit to an earlier result mid-turn breaks the thinking blocks'
    prefix binding and the prompt cache, and the per-turn
    dollar cap bounds the turn instead.
    """
    hist_limit = DEFAULT_MAX_HISTORY_CHARS if history_max_chars is None else history_max_chars
    if turn_start is None or not (0 <= turn_start < len(messages)):
        return trim_history(messages, hist_limit)
    history = trim_history(messages[:turn_start], hist_limit, keep_newest=False)
    turn = list(messages[turn_start:])
    if not elide_turn:
        return history + turn
    return history + window_current_turn(turn, turn_max_chars)
