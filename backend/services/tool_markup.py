"""A tool call written as text never reaches a chat (2026-10-11).

The model benchmark (docs/architecture/testing.md#the-model-benchmark)
found that with no tool definitions in the request, 44 of 47 ping replies
wrote ``send_message`` as XML in their text — and the ping runner, which
posts a turn's final text when the turn did not send, would have posted
that into a friend's chat. Any path that runs a turn without its tools
bound (a binding failure, a fallback composer, a stripped retry) does it.

``clean_reply(text)`` strips every shape of a tool call written as text:

* XML-ish blocks the benchmark saw — ``<function_calls>``, ``<invoke …>``,
  ``<invoke_tool>``, ``<parameter …>``, ``<arg …>``, ``<tool_call>``,
  ``<tool_use>`` — with or without an ``antml:`` prefix, and a block
  tagged with a tool's own name (``<send_message …>``); an unterminated
  one is cut to the end of the text;
* a call rendered as JSON (``{"name": "send_message", "arguments": …}``,
  ``{"type": "tool_use", …}``), fenced or not;
* a call rendered as code in prose (``send_message(chat_id="…", …)``,
  ``functions.tell_owner({…})``) — only a known tool name followed by
  arguments, never the word on its own.

It returns what is left and whether that is worth posting: when markup was
found, a trailing lead-in ("Sending it now:") is dropped and what remains
must be at least ``MIN_WORDS`` words. The callers that post a final text —
the ping runner's outcome rules and delivery, the Twilio reply — post the
cleaned text, or their honest fallback line when nothing is left
(``safe_reply``).

Pure: no I/O. The tool names come from ``services.toolset.names`` (pure),
with a fixed list when that can't be imported.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, FrozenSet, List, Optional, Tuple

# Fewer words than this left after the markup is not an answer ("Sure!").
MIN_WORDS = 4

# Tags that only ever wrap a tool call or its arguments.
_CALL_TAGS = ("function_calls", "function_call", "function_results", "function_result", "invoke_tool", "invoke",
              "tool_calls", "tool_call", "tool_use", "tool_result", "tool_input", "parameter", "arguments", "arg")
_FALLBACK_NAMES = frozenset({
    "bash", "str_replace_based_edit_tool", "web_search", "web_fetch", "browser", "chrome", "look", "messages",
    "send_message", "tell_owner", "memory", "schedule", "screen", "plan",
    "send_imessage", "send_sms", "send_whatsapp", "leave_note", "send_push_notification", "wake_me",
    "fetch_page_content", "remember", "remember_search", "schedule_event", "stand_down",
})
_CALL_TYPES = frozenset({"tool_use", "tool_call", "function", "function_call"})
_ARG_KEYS = ("arguments", "parameters", "input", "args", "tool_input")
_NAME_KEYS = ("name", "tool", "tool_name", "function", "recipient_name")


@dataclass(frozen=True)
class Cleaned:
    """``text`` with any written tool call removed; ``found`` when one was;
    ``meaningful`` when what is left is worth posting."""

    text: str
    found: bool
    meaningful: bool


@lru_cache(maxsize=1)
def tool_names() -> FrozenSet[str]:
    """Every name a model may write a call to: the toolset's 14, the
    retired names it still aliases, and the old send tools."""
    names = set(_FALLBACK_NAMES)
    try:
        from services.toolset.names import RETIRED_NAMES, TOOLSET_NAMES

        names |= set(TOOLSET_NAMES) | set(RETIRED_NAMES)
    except Exception:  # noqa: BLE001 - the fixed list still covers the sends
        pass
    return frozenset(n for n in names if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", n))


def _alternation(names) -> str:
    return "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))


@lru_cache(maxsize=1)
def _patterns() -> Tuple[re.Pattern, ...]:
    tags = _alternation(set(_CALL_TAGS) | tool_names())
    prefix = r"(?:antml:)?"
    block = re.compile(rf"<\s*{prefix}(?P<n>{tags})\b[^<>]*?>.*?<\s*/\s*{prefix}(?P=n)\s*>", re.S | re.I)
    self_closing = re.compile(rf"<\s*{prefix}(?:{tags})\b[^<>]*/\s*>", re.I)
    unterminated = re.compile(rf"<\s*{prefix}(?:{_alternation(_CALL_TAGS)})\b.*\Z", re.S | re.I)
    stray_close = re.compile(rf"<\s*/\s*{prefix}(?:{tags})\s*>", re.I)
    call = re.compile(rf"(?:\bfunctions\.)?\b(?:{_alternation(tool_names())})\s*\(")
    return block, self_closing, unterminated, stray_close, call


def _balanced_end(text: str, start: int, open_ch: str, close_ch: str) -> Optional[int]:
    """The index just past the bracket matching ``text[start]``, skipping
    quoted strings; None when it never closes."""
    depth = 0
    quote: Optional[str] = None
    i = start
    while i < len(text):
        ch = text[i]
        if quote:
            if ch == "\\":
                i += 2
                continue
            if ch == quote:
                quote = None
        elif ch in "\"'":
            quote = ch
        elif ch == open_ch:
            depth += 1
        elif ch == close_ch:
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return None


def _is_call(obj: Any) -> bool:
    if not isinstance(obj, dict):
        return False
    name: Any = None
    for key in _NAME_KEYS:
        value = obj.get(key)
        if isinstance(value, dict):  # {"function": {"name": …, "arguments": …}}
            return _is_call({**value, "type": value.get("type") or "function"})
        if isinstance(value, str) and value.strip():
            name = value.strip()
            break
    if name is None:
        return False
    bare = name.rsplit(".", 1)[-1]
    if str(obj.get("type") or "").lower() in _CALL_TYPES:
        return True
    return bare in tool_names() and any(k in obj for k in _ARG_KEYS)


def _strip_json_calls(text: str) -> Tuple[str, bool]:
    found = False
    out: List[str] = []
    i = 0
    while i < len(text):
        if text[i] != "{":
            out.append(text[i])
            i += 1
            continue
        end = _balanced_end(text, i, "{", "}")
        if end is None:
            out.append(text[i:])
            break
        try:
            obj = json.loads(text[i:end])
        except (ValueError, TypeError):
            obj = None
        if _is_call(obj):
            found = True
        else:
            out.append(text[i:end])
        i = end
    return "".join(out), found


def _strip_code_calls(text: str, call: re.Pattern) -> Tuple[str, bool]:
    found = False
    pos = 0
    while True:
        m = call.search(text, pos)
        if m is None:
            return text, found
        paren = m.end() - 1
        after = text[paren + 1:].lstrip()
        # Arguments, not a remark: a quote, a brace, ``)`` or ``name=``.
        if not (after[:1] in ("\"", "'", "{", ")") or re.match(r"[A-Za-z_]\w*\s*=", after)):
            pos = m.end()
            continue
        end = _balanced_end(text, paren, "(", ")")
        start = m.start()
        if end is None:
            end = len(text)
        if start > 0 and end < len(text) and text[start - 1] == "`" and text[end] == "`":
            start, end = start - 1, end + 1
        text = text[:start] + text[end:]
        found = True
        pos = start


def _tidy(text: str) -> str:
    text = re.sub(r"```[A-Za-z0-9_-]*\s*```", "", text)          # fences left empty
    text = re.sub(r"(?<!`)``(?!`)", "", text)                      # empty inline code
    # A line left holding only the brackets and commas around removed calls.
    # A call removed mid-sentence leaves two spaces where it was.
    lines = [re.sub(r"(?<=\S) {2,}(?=\S)", " ", line.rstrip()) for line in text.splitlines()
             if not (line.strip() and re.fullmatch(r"[\s\[\]{}(),;]+", line))]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _drop_lead_ins(text: str) -> str:
    """A trailing line that ends in a colon introduced the call that was
    removed ("Sending it to the group now:")."""
    lines = text.splitlines()
    while lines and (not lines[-1].strip() or lines[-1].rstrip().endswith(":")):
        lines.pop()
    return "\n".join(lines).strip()


def clean_reply(text: Optional[str]) -> Cleaned:
    """``text`` without any tool call written in it. Pure."""
    original = str(text or "").strip()
    if not original:
        return Cleaned("", False, False)
    block, self_closing, unterminated, stray_close, call = _patterns()
    t = original
    found = False

    while True:
        stripped = block.sub("", t)
        if stripped == t:
            break
        found, t = True, stripped
    for pattern in (self_closing, unterminated, stray_close):
        stripped = pattern.sub("", t)
        if stripped != t:
            found, t = True, stripped
    t, json_found = _strip_json_calls(t)
    t, code_found = _strip_code_calls(t, call)
    found = found or json_found or code_found
    if not found:
        return Cleaned(original, False, True)

    t = _drop_lead_ins(_tidy(t))
    words = re.findall(r"[^\W_]+", t)
    return Cleaned(t, True, len(words) >= MIN_WORDS)


def has_tool_markup(text: Optional[str]) -> bool:
    return clean_reply(text).found


def safe_reply(text: Optional[str], fallback: str) -> str:
    """What a final-text-is-reply path may post: ``text`` as it was when it
    holds no written tool call, the rest of it when what is left is an
    answer, else ``fallback`` (an honest line)."""
    cleaned = clean_reply(text)
    if not cleaned.found:
        return cleaned.text
    return cleaned.text if cleaned.meaningful else fallback
