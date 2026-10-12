"""A compact trace of one chat turn, kept on the turn's final AIMessage.

Why (UX pass 2026-09-25): Edward's thinking and tool use stay visible by
design (never behind a debug flag; docs/architecture/app-shell-today-chat.md), but the
reasoning summary, the tool calls and the memory count lived only in the
SSE stream. Reload a conversation and the answer stood alone. The
streaming loop now fills a ``TraceBuilder`` as it goes and stores what it
built at ``additional_kwargs["trace"]`` on the answer it checkpoints;
``routers/conversations.serialize_checkpoint_message`` returns it as
``message.trace``:

    {"reasoning": str | None,             # the reasoning summary, capped
     "tools": [{"name": str,
                "args_summary": str | None,   # ≤ 120 chars
                "status": "ok" | "error",
                "duration_ms": int | None,
                "result_preview": str}],      # ≤ 200 chars
     "memory_hits": int | None,          # memories retrieved for the turn
     "tools_omitted": int}               # only when over MAX_TOOLS

What never goes in: a message body (a send tool is summarised by its
recipient only), a typed browser field (it can be a password), and any
string the egress secret scanner (``services/secret_scan.py``) flags —
that string is replaced whole. Additional kwargs are never sent back to
the model (the Codex adapter reads only ``reasoning_items``), so the
trace costs the next turn nothing.

Pure: no I/O. ``build(known=…)`` takes the live secret values from the
caller.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Mapping, Optional

TRACE_KEY = "trace"
REASONING_MAX_CHARS = 4000
ARGS_SUMMARY_MAX_CHARS = 120
RESULT_PREVIEW_MAX_CHARS = 200
MAX_TOOLS = 40
REDACTED = "[hidden: looked like a secret]"

# A field typed into a page can be a password; a step list can hold one.
PRIVATE_ARG_TOOLS = frozenset({"browser_type", "browser_steps"})
# Where a send tool names who it goes to (the body is never shown).
_RECIPIENT_KEYS = ("to", "recipient", "phone_number", "chat", "chat_id", "chat_name", "contact", "jid")
_ERROR_PREFIXES = ("Tool error:", "Unknown tool:", "BLOCKED:", "Error:")

Summarizer = Callable[[str, Dict[str, Any]], Optional[str]]


def _flat(text: Any) -> str:
    return " ".join(str(text if text is not None else "").split())


def clip(text: Any, limit: int) -> str:
    """Whitespace collapsed, at most ``limit`` chars (an ellipsis when cut)."""
    s = _flat(text)
    return s if len(s) <= limit else s[: max(0, limit - 1)].rstrip() + "…"


def tool_status(result: Any) -> str:
    """``error`` for a failed, refused or unknown call, else ``ok``."""
    return "error" if str(result or "").lstrip().startswith(_ERROR_PREFIXES) else "ok"


def _default_summary(name: str, args: Dict[str, Any]) -> Optional[str]:
    for v in args.values():
        if isinstance(v, str) and v.strip():
            return f'"{v}"'
    return None


def _is_send(name: str) -> bool:
    return "send" in (name or "").lower()


def args_summary(name: str, args: Any, summarize: Optional[Summarizer] = None) -> Optional[str]:
    """A short line for what the call was asked to do, or None when that
    would show something private."""
    if not isinstance(args, dict) or name in PRIVATE_ARG_TOOLS:
        return None
    if name in ("chrome", "browser") and str(args.get("action") or "").lower() in ("type", "steps"):
        return "type" if str(args.get("action")).lower() == "type" else "steps"
    if _is_send(name):
        who = next((args[k] for k in _RECIPIENT_KEYS if isinstance(args.get(k), (str, int)) and str(args[k]).strip()),
                   None)
        return clip(f"to: {who}", ARGS_SUMMARY_MAX_CHARS) if who is not None else None
    try:
        text = (summarize or _default_summary)(name, args)
    except Exception:  # noqa: BLE001 — a summary is decoration
        text = None
    return clip(text, ARGS_SUMMARY_MAX_CHARS) if text else None


def _reasoning_texts(message: Any) -> List[str]:
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


def _scrub(text: Optional[str], known: Optional[Mapping[str, str]]) -> Optional[str]:
    if not text:
        return text
    try:
        from services.secret_scan import find_credential_paths, find_secrets

        if find_secrets(text, known) or find_credential_paths(text):
            return REDACTED
    except Exception:  # noqa: BLE001 — if the scan breaks, keep nothing
        return REDACTED
    return text


class TraceBuilder:
    """Filled by the streaming loop as the turn runs; ``build`` at the end."""

    def __init__(self, summarize: Optional[Summarizer] = None):
        self._summarize = summarize
        self._deltas: List[str] = []
        self._summaries: List[str] = []
        self._tools: List[Dict[str, Any]] = []
        self._omitted = 0

    def note_reasoning_delta(self, text: Any) -> None:
        if isinstance(text, str) and text and sum(len(t) for t in self._deltas) < REASONING_MAX_CHARS * 2:
            self._deltas.append(text)

    def note_response(self, message: Any) -> None:
        """The reasoning summaries a model response carried (once each)."""
        try:
            for text in _reasoning_texts(message):
                if text not in self._summaries:
                    self._summaries.append(text)
        except Exception:  # noqa: BLE001
            pass

    def note_tool(self, name: str, args: Any, result: Any, duration_ms: Optional[float]) -> None:
        if len(self._tools) >= MAX_TOOLS:
            self._omitted += 1
            return
        try:
            if isinstance(result, (dict, list)):
                result = json.dumps(result, default=str)
            self._tools.append({
                "name": str(name or "?"),
                "args_summary": args_summary(str(name or ""), args, self._summarize),
                "status": tool_status(result),
                "duration_ms": int(duration_ms) if duration_ms is not None else None,
                "result_preview": clip(result, RESULT_PREVIEW_MAX_CHARS),
            })
        except Exception:  # noqa: BLE001 — bookkeeping never breaks a turn
            pass

    def _reasoning(self) -> Optional[str]:
        text = "\n\n".join(self._summaries) if self._summaries else "".join(self._deltas).strip()
        if not text:
            return None
        return text if len(text) <= REASONING_MAX_CHARS else text[: REASONING_MAX_CHARS - 1].rstrip() + "…"

    def build(self, memory_hits: Optional[int], known: Optional[Mapping[str, str]] = None) -> Optional[Dict[str, Any]]:
        """The trace, or None when the turn had nothing to show (no
        reasoning, no tools, no memories)."""
        reasoning = self._reasoning()
        if not reasoning and not self._tools and not memory_hits:
            return None
        tools = [{**t, "args_summary": _scrub(t["args_summary"], known),
                  "result_preview": _scrub(t["result_preview"], known)} for t in self._tools]
        trace: Dict[str, Any] = {"reasoning": _scrub(reasoning, known), "tools": tools,
                                 "memory_hits": int(memory_hits) if memory_hits is not None else None}
        if self._omitted:
            trace["tools_omitted"] = self._omitted
        return trace


def trace_kwargs(trace: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """``additional_kwargs`` for the answer: {"trace": …} or {}."""
    return {TRACE_KEY: trace} if trace else {}


def trace_of(message: Any) -> Optional[Dict[str, Any]]:
    """The trace a checkpointed assistant message carries, if any."""
    if getattr(message, "type", None) != "ai":
        return None
    ak = getattr(message, "additional_kwargs", None) or {}
    t = ak.get(TRACE_KEY) if isinstance(ak, dict) else None
    return t if isinstance(t, dict) else None


def known_secrets_safely() -> Optional[Mapping[str, str]]:
    """The live secret values to scrub (``secret_scan.known_secrets``);
    None when they cannot be read (the shape rules still run)."""
    try:
        from services.secret_scan import known_secrets

        return known_secrets()
    except Exception:  # noqa: BLE001
        return None
