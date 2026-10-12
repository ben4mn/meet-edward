"""Conversation history windowing for the chat tool loop.

The chat path sends `[system] + messages` in full on every tool-loop
iteration. Heartbeat conversations are per-contact and long-lived, so for a
high-volume contact that list grows without bound — Rowan's thread reached
2,333 messages / 3.73M characters (~1M tokens) by 2026-08-25, at which point
gpt-5.4 began returning empty completions with no text and no tool calls.
Each failed turn appended an empty AIMessage plus a nudge HumanMessage, so
the thread degraded further with every retry.

Three problems, three functions:

* `drop_dead_turns` removes the empty-turn/nudge wreckage a previous failure
  left behind, so a poisoned thread recovers instead of re-reading its own
  silence as precedent. `strip_scaffolding` goes further: every nudge the
  loop injects is scaffolding, never conversation, so none of it is sent
  back as history or written to the checkpoint (2026-09-24).
* `trim_history` bounds what actually goes over the wire, keeping the newest
  messages and never orphaning a tool result from the call that produced it.
  Inside a turn the window is `turn_window.window_for_call`, which applies
  this to the history *before* the turn only and never evicts the turn's
  own trigger.
* `stale_message_ids` is what the checkpoint trim removes.
"""

import json
import re
from typing import Any, Iterable, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

# Every nudge the tool loop injects when the model answers without calling
# tools (see streaming.py). They are scaffolding, not conversation. The
# plan nudge had no "[SYSTEM]" prefix until 2026-09-24; old threads still
# carry that form, so it is matched by pattern too.
_NUDGE_PREFIXES = (
    "[SYSTEM] You have tools available but didn't use any",
    "[SYSTEM] You have not yet called the required tool(s):",
    "[SYSTEM] You executed tools but didn't provide a response",
    "[SYSTEM] You still have ",
    # The toolset loop's scaffolding (2026-10-11, ``graph/tool_loop.py``):
    # the step-back note, the reply check and the closing instructions.
    "[SYSTEM] Three rounds in a row failed",
    "[SYSTEM] You were asked",
    "[SYSTEM] Stop here",
    "[SYSTEM] You ran tools but gave no answer",
)
_PLAN_NUDGE_RE = re.compile(r"^You still have \d+ incomplete plan step\(s\):")

# ~100K tokens of history. Well inside gpt-5.4's window, and far enough below
# it that memory context and the system prompt still fit alongside.
DEFAULT_MAX_HISTORY_CHARS = 400_000


# What an image or a PDF page costs in the window's arithmetic: about 1.6K
# tokens (a 1600 px JPEG), in characters — never its base64 (2026-10-11).
IMAGE_COST_CHARS = 6_400
_MEDIA_TYPES = ("image", "image_url", "document", "file")


def _text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict) and block.get("type") not in _MEDIA_TYPES:
                parts.append(str(block.get("text") or ""))
        return "\n".join(parts)
    return str(content)


def _media_blocks(message: Any) -> int:
    content = getattr(message, "content", "")
    if not isinstance(content, list):
        return 0
    return sum(1 for b in content if isinstance(b, dict) and b.get("type") in _MEDIA_TYPES)


def message_cost(message: Any) -> int:
    """Characters a message puts on the wire: its text plus, for a
    tool-calling turn, each call's name and JSON arguments. A 10K
    ``browser_type`` or ``save_document`` argument used to cost nothing
    in the window's arithmetic (2026-09-24). An image or a PDF
    counts as ``IMAGE_COST_CHARS``, not its base64."""
    cost = len(_text(message)) + IMAGE_COST_CHARS * _media_blocks(message)
    if isinstance(message, AIMessage):
        for call in getattr(message, "tool_calls", None) or []:
            if not isinstance(call, dict):
                continue
            try:
                args = json.dumps(call.get("args") or {}, ensure_ascii=False, default=str)
            except (TypeError, ValueError):
                args = str(call.get("args"))
            cost += len(str(call.get("name") or "")) + len(args)
    return cost


def _is_dead_ai_turn(message: Any) -> bool:
    """An assistant turn with no text AND no tool calls — the model said
    nothing at all. An empty AIMessage that carries tool_calls is a normal
    tool-calling turn and must be kept, or its results become orphans."""
    if not isinstance(message, AIMessage):
        return False
    if getattr(message, "tool_calls", None):
        return False
    return not _text(message).strip()


# The marker on every note the tool loop adds (the closing instruction, the
# step-back, the reply check): scaffolding, never the owner speaking. The Claude
# adapter keeps the turn's raw blocks (thinking, server-tool results)
# across a marked note, so the closing call is not written blind and the
# cached prefix holds (review 2026-10-11).
SCAFFOLDING_KEY = "edward_scaffolding"


def scaffolding_note(text: str) -> HumanMessage:
    """A loop note, marked as scaffolding."""
    return HumanMessage(content=text, additional_kwargs={SCAFFOLDING_KEY: True})


def is_scaffolding_note(message: Any) -> bool:
    if not isinstance(message, HumanMessage):
        return False
    ak = getattr(message, "additional_kwargs", None) or {}
    return isinstance(ak, dict) and bool(ak.get(SCAFFOLDING_KEY))


def _is_nudge(message: Any) -> bool:
    if not isinstance(message, HumanMessage):
        return False
    if is_scaffolding_note(message):
        return True
    text = _text(message).lstrip()
    return text.startswith(_NUDGE_PREFIXES) or bool(_PLAN_NUDGE_RE.match(text))


def is_scaffolding(message: Any) -> bool:
    """A loop nudge or a dead assistant turn: never conversation."""
    return _is_nudge(message) or _is_dead_ai_turn(message)


def drop_dead_turns(messages: List[Any]) -> List[Any]:
    """Drop empty assistant turns and the nudge each one provoked."""
    kept: List[Any] = []
    i = 0
    while i < len(messages):
        if _is_dead_ai_turn(messages[i]):
            nxt = i + 1
            # The nudge only exists because of the dead turn; drop the pair.
            i = nxt + 1 if nxt < len(messages) and _is_nudge(messages[nxt]) else nxt
            continue
        kept.append(messages[i])
        i += 1
    return kept


def strip_scaffolding(messages: List[Any]) -> List[Any]:
    """Every nudge and every dead assistant turn removed; the rest in order.

    ``drop_dead_turns`` only removed a nudge that followed an empty turn,
    so a nudge after real text ("I would search…" → "[SYSTEM] You MUST use
    your tools") was checkpointed and read back as precedent on every later
    turn. What the model actually said is kept."""
    return [m for m in messages if not is_scaffolding(m)]


def scaffolding_ids(messages: List[Any]) -> List[str]:
    """Ids of every scaffolding message (see ``strip_scaffolding``) — what a
    RemoveMessage update needs to take old nudges out of a checkpoint."""
    return [m.id for m in messages if is_scaffolding(m) and getattr(m, "id", None)]


def _group(messages: List[Any]) -> List[List[Any]]:
    """Bundle each tool-calling AIMessage with the ToolMessages answering it.

    A group is the trimming unit: keeping half of one would leave a
    tool_call_id with no originating call, which the OpenAI API rejects.
    """
    groups: List[List[Any]] = []
    i = 0
    while i < len(messages):
        message = messages[i]
        if isinstance(message, AIMessage) and getattr(message, "tool_calls", None):
            group = [message]
            i += 1
            while i < len(messages) and isinstance(messages[i], ToolMessage):
                group.append(messages[i])
                i += 1
            groups.append(group)
            continue
        groups.append([message])
        i += 1
    return groups


def trim_history(
    messages: List[Any],
    max_chars: int = DEFAULT_MAX_HISTORY_CHARS,
    keep_newest: bool = True,
) -> List[Any]:
    """Keep the newest messages that fit `max_chars`, oldest dropped first.

    The most recent group is always kept even if it alone exceeds the budget —
    returning nothing would strip the very message being responded to.
    ``keep_newest=False`` drops that guarantee, for windowing the history
    *before* a turn whose own messages are sent regardless.
    """
    if not messages:
        return []

    groups = _group(messages)
    selected: List[List[Any]] = []
    total = 0

    for group in reversed(groups):
        cost = sum(message_cost(m) for m in group)
        if (selected or not keep_newest) and total + cost > max_chars:
            break
        selected.append(group)
        total += cost

    kept = [m for group in reversed(selected) for m in group]

    # Belt and braces: a ToolMessage whose call was trimmed away (or that was
    # already orphaned upstream) must not reach the API.
    live_ids = {
        call["id"]
        for m in kept
        if isinstance(m, AIMessage)
        for call in (getattr(m, "tool_calls", None) or [])
        if isinstance(call, dict) and call.get("id")
    }
    return [
        m
        for m in kept
        if not isinstance(m, ToolMessage) or m.tool_call_id in live_ids
    ]


def dead_turn_ids(messages: List[Any]) -> List[str]:
    """Ids of the dead assistant turns and the nudges they provoked.

    `drop_dead_turns` fixes what goes over the wire but cannot shrink the
    checkpoint: AgentState reduces `messages` with `add_messages`, which
    merges by id and leaves anything omitted in place. Persistently pruning a
    poisoned thread means sending an explicit RemoveMessage for each of these.
    """
    kept = drop_dead_turns(messages)
    kept_ids = {id(m) for m in kept}
    return [
        m.id
        for m in messages
        if id(m) not in kept_ids and getattr(m, "id", None)
    ]


def stale_message_ids(
    messages: List[Any],
    max_chars: int = DEFAULT_MAX_HISTORY_CHARS,
    pinned: Optional[Iterable[Any]] = None,
) -> List[str]:
    """Ids of the messages `trim_history` would leave out — what a
    RemoveMessage update needs so the checkpoint matches the window.

    `trim_history` only bounded what went over the wire; the checkpoint kept
    everything, so a busy heartbeat thread wrote a 9.5 MB `messages` blob on
    every turn (2026-09-12). Messages without an id cannot be removed and are
    skipped; add_messages assigns one when it stores them, so they are caught
    on the next turn.

    ``pinned`` messages (by identity) are never stale: the loops pin the
    latest turn's trigger, so a turn whose tool results outgrow the window
    keeps the question it answered (2026-09-24: the mind's 24K trim removed
    the wake pack from ``edward-mind`` for good).
    """
    pinned = list(pinned or ())
    keep = {id(m) for m in trim_history(messages, max_chars)}
    keep.update(id(m) for m in pinned)
    # A pinned message may reach here as a copy (reasoning items stripped
    # before the save); its id still names it.
    pinned_ids = {getattr(m, "id", None) for m in pinned} - {None}
    return [
        m.id
        for m in messages
        if id(m) not in keep and getattr(m, "id", None) and m.id not in pinned_ids
    ]


# ------------------------------------------------------------ media --

_REF_RE = re.compile(r"\b((?:attachment|media|file):[#\w.-]+)")
# A PWA upload's label: ``[Uploaded image: X | file_id: <id>]`` → ``file:<id>``
# (``file_id:`` is not ``file:``; review 2026-10-11).
_FILE_ID_RE = re.compile(r"\bfile_id:\s*([\w-]+)")


def _refs_in(text: str) -> List[str]:
    """The media refs a text block names, in order."""
    found = [(m.start(), m.group(1)) for m in _REF_RE.finditer(text)]
    found += [(m.start(), f"file:{m.group(1)}") for m in _FILE_ID_RE.finditer(text)]
    return [ref for _, ref in sorted(found)]


def _describe(ref: str) -> Optional[str]:
    """The media module's description of a media ref, when it is installed."""
    try:
        from services.media import hooks  # type: ignore[import-not-found]

        fn = getattr(hooks, "describe_ref", None)
        return fn(ref) if callable(fn) else None
    except Exception:  # noqa: BLE001
        return None


def _media_line(block: dict, ref: Optional[str]) -> str:
    kind = "PDF" if block.get("type") in ("document", "file") else "image"
    if not ref:
        return f"[{kind} — not kept in this history; look again to see it]"
    desc = _describe(ref)
    said = f': "{desc}"' if desc else ""
    return f'[{kind} {ref} — not kept in this history{said}. look("{ref}") to see it again]'


def _strip_one(message: Any) -> Any:
    content = getattr(message, "content", None)
    if not isinstance(content, list) or not any(
            isinstance(b, dict) and b.get("type") in _MEDIA_TYPES for b in content):
        return message
    out = []
    ref: Optional[str] = None
    replaced: List[int] = []
    human = isinstance(message, HumanMessage)
    for i, block in enumerate(content):
        if isinstance(block, dict) and block.get("type") == "text":
            # The text block just before a media run names it (its label).
            found = _refs_in(str(block.get("text") or ""))
            ref = found[-1] if found else None
            out.append(block)
        elif isinstance(block, dict) and block.get("type") in _MEDIA_TYPES:
            if human and not ref:
                # Nothing could open it again: something a person sent is
                # kept whole rather than lost to every later turn.
                out.append(block)
                continue
            out.append({"type": "text", "text": _media_line(block, ref)})
            replaced.append(i)
        else:
            out.append(block)
    update: dict = {"content": out}
    ak = getattr(message, "additional_kwargs", None) or {}
    atts = ak.get("attachments") if isinstance(ak, dict) else None
    if isinstance(atts, list) and replaced:
        new_atts = []
        for a in atts:
            if isinstance(a, dict) and a.get("block_index") in replaced:
                a = {**a, "dehydrated": True}
            new_atts.append(a)
        update["additional_kwargs"] = {**ak, "attachments": new_atts}
    return message.model_copy(update=update)


def _media_dehydrate():
    """``services.media.dehydrate``, or None when it is not here."""
    try:
        from services.media import dehydrate  # type: ignore[import-not-found]

        return dehydrate if callable(getattr(dehydrate, "strip_media_blocks", None)) else None
    except Exception:  # noqa: BLE001
        return None


def strip_media_blocks(messages: List[Any]) -> List[Any]:
    """Every image and PDF block in a Human or Tool message replaced, in
    place, by a line naming its ref (``attachment:<rowid>``, ``media:<id>``,
    ``file:<id>`` for a PWA upload's ``file_id`` — named by the text block
    just before it) and, when the media module knows it, its
    description. A Human message's block with no ref is kept whole: look
    could never open it again. Copies what it changes; the originals (the
    live turn) are untouched. For the checkpoint only.

    The one pass (2026-10-11): with the media
    module here this calls ``media.dehydrate.strip_media_blocks`` (the same
    rules, plus ``EDWARD_MEDIA=off``); the window's own code is the
    fallback without it."""
    dehydrate = _media_dehydrate()
    if dehydrate is not None:
        return dehydrate.strip_media_blocks(messages)
    return [_strip_one(m) if isinstance(m, (HumanMessage, ToolMessage)) else m for m in messages]
