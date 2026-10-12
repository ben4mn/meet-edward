"""The uniform failure contract (docs/architecture/tool-loop.md; 2026-10-11).

Every tool in the toolset fails the same way: it raises ``ToolFailure``
(or returns ``ToolResult(is_error=True)``), and the executor turns that —
or any other exception, or an error-shaped string from an older tool —
into ``Tool error: <the real error>`` with ``is_error`` set. The model reads
the real stderr or exception, the turn ledger counts every failure (it
keys on the ``Tool error:`` prefix), and the Claude adapter sends
``is_error: true`` on the ``tool_result`` block.

Before this, only results that happened to start ``Tool error:`` counted;
an audit found ~115 other failure shapes, and ``fetch_page_content``
failing 18% of the time was recorded as 0%.

Pure: no I/O.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, List, Union

ERROR_PREFIX = "Tool error:"
# Results that are failures whoever wrote them. ``BLOCKED:`` is the old
# circuit breaker's word; ``Unknown tool:`` the executor's.
FAILURE_PREFIXES = (ERROR_PREFIX, "Unknown tool:", "BLOCKED:")
NO_OUTPUT = "(no output)"

Content = Union[str, List[dict]]


class ToolFailure(Exception):
    """A tool failed. The message is what the model reads, after
    ``Tool error: `` — say what went wrong and, when there is one, what to
    do instead."""


@dataclass
class ToolResult:
    """What a tool returns when it needs more than a string: content
    blocks (an image, a PDF) or an explicit error flag."""

    content: Content
    is_error: bool = False

    @property
    def text(self) -> str:
        return text_of(self.content)


def text_of(content: Any) -> str:
    """The text of a result: a string as is; for blocks, the text blocks
    joined with ``[image]`` / ``[document]`` placeholders. For logs, the
    ledger and the work rail — never sent to the model."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                kind = block.get("type")
                if kind == "text":
                    parts.append(str(block.get("text") or ""))
                elif kind in ("image", "image_url"):
                    parts.append("[image]")
                elif kind in ("document", "file"):
                    parts.append("[document]")
                elif "text" in block:
                    parts.append(str(block.get("text") or ""))
        return "\n".join(p for p in parts if p)
    return str(content)


def is_error_text(text: Any) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(FAILURE_PREFIXES)


def _prefixed(message: str) -> str:
    message = (message or "").strip()
    if message.startswith(FAILURE_PREFIXES):
        return message
    return f"{ERROR_PREFIX} {message}" if message else ERROR_PREFIX


def failure_text(exc: BaseException) -> str:
    """``Tool error: …`` for an exception: a ``ToolFailure``'s own words, any
    other exception's type and message (never a swallowed one)."""
    if isinstance(exc, ToolFailure):
        return _prefixed(str(exc))
    message = str(exc).strip()
    name = type(exc).__name__
    return f"{ERROR_PREFIX} {name}: {message}" if message else f"{ERROR_PREFIX} {name}"


def _blocks(items: list) -> List[dict]:
    out: List[dict] = []
    for item in items:
        if isinstance(item, dict):
            out.append(item)
        elif item is not None:
            out.append({"type": "text", "text": str(item)})
    return out


def _flag_blocks(blocks: List[dict]) -> List[dict]:
    """Error-flagged blocks: the first text block carries the prefix (one
    is added when there is none), so a log or the ledger reading the text
    sees a failure."""
    out = [dict(b) for b in blocks]
    for b in out:
        if b.get("type") == "text":
            b["text"] = _prefixed(str(b.get("text") or ""))
            return out
    return [{"type": "text", "text": ERROR_PREFIX}] + out


def normalize(raw: Any) -> ToolResult:
    """Whatever a tool produced → ``ToolResult`` under the contract."""
    if isinstance(raw, ToolResult):
        content = raw.content
        if isinstance(content, list):
            blocks = _blocks(content)
            if raw.is_error:
                return ToolResult(_flag_blocks(blocks), True)
            return ToolResult(blocks, False)
        text = "" if content is None else str(content)
        if raw.is_error:
            return ToolResult(_prefixed(text), True)
        return ToolResult(text or NO_OUTPUT, is_error_text(text))
    if isinstance(raw, BaseException):
        return ToolResult(failure_text(raw), True)
    if raw is None:
        return ToolResult(NO_OUTPUT, False)
    if isinstance(raw, list):
        return ToolResult(_blocks(raw), False)
    if isinstance(raw, dict):
        try:
            text = json.dumps(raw, default=str, ensure_ascii=False)
        except (TypeError, ValueError):
            text = str(raw)
        return ToolResult(text, False)
    text = str(raw)
    return ToolResult(text if text else NO_OUTPUT, is_error_text(text))
