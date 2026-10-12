"""
Robust JSON payload extraction for background-LLM responses.

Context: GPT-5.4 via the Codex Responses API returns JSON in several shapes
that the original non-greedy regex (``re.search(r'\\[[\\s\\S]*?\\]', ...)``)
cannot handle:

  - Dict-wrapped payloads: ``{"memories": [...]}``
  - Nested arrays inside objects: ``{"memories": [{"tags": ["a","b"]}]}``
  - Preamble text before the JSON
  - Markdown code fences

This helper tries a series of strategies, each more tolerant than the last,
and returns an empty container (``[]`` or ``{}``) when nothing recoverable is
found — logging a WARNING so ops can see the failure. The non-greedy regex
returned ``[]`` silently, which is how memory extraction rotted for weeks.

Usage:
    from services.json_parsing import extract_json_payload
    memories = extract_json_payload(llm_response_text, expected="array")
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

logger = logging.getLogger(__name__)

# Keys under which LLMs commonly nest the array we actually want.
# Order matters — checked in this sequence.
_WRAPPER_KEYS = (
    "memories",
    "items",
    "results",
    "clusters",
    "queries",
    "tags",
    "data",
    "output",
)


def extract_json_payload(
    text: str | None,
    expected: Literal["array", "object"] = "array",
    subsystem: str = "unknown",
) -> Any:
    """Parse a (potentially messy) LLM response into a list or dict.

    Parameters
    ----------
    text : str | None
        The raw ``response.content`` from the LLM.
    expected : "array" | "object"
        What shape we want back. "array" unwraps ``{"memories": [...]}``-style
        wrappers; "object" returns the top-level dict as-is.
    subsystem : str
        Tag used in WARNING logs (e.g. "memory_extraction", "triage") so
        ops can trace which subsystem produced a bad payload.

    Returns
    -------
    list or dict
        Empty ``[]`` / ``{}`` on failure (never raises).
    """
    empty: Any = [] if expected == "array" else {}

    if not text:
        return empty

    cleaned = _strip_markdown_fences(text).strip()
    if not cleaned:
        return empty

    # Strategy 1 — try parsing the whole thing as-is.
    whole = _try_parse(cleaned)
    if whole is not None:
        # If full parse succeeds but the shape is wrong, don't fall back to
        # scanning for nested structures — that would surface whatever
        # inner array happens to come first, which is rarely what the caller
        # wants. Trust the LLM's top-level decision and fail cleanly.
        result = _coerce(whole, expected)
        if result is not None:
            return result
        logger.warning(
            "[json_parsing] LLM returned valid JSON with unexpected shape "
            "(subsystem=%s, expected=%s, got=%s)",
            subsystem,
            expected,
            type(whole).__name__,
        )
        return empty

    # Strategy 2 — walk the string looking for the first complete top-level
    # array or object. Handles preamble/postamble text and ignores strings.
    for want in _parse_order(expected):
        chunk = _find_balanced(cleaned, want)
        if chunk is None:
            continue
        parsed = _try_parse(chunk)
        if parsed is None:
            continue
        result = _coerce(parsed, expected)
        if result is not None:
            return result

    # Everything failed — log the raw response (truncated for sanity) so ops
    # can see exactly what the LLM returned.
    snippet = text if len(text) <= 1500 else text[:1500] + " ...[truncated]"
    logger.warning(
        "[json_parsing] Failed to parse LLM response (subsystem=%s, expected=%s). "
        "Raw response: %s",
        subsystem,
        expected,
        snippet,
    )
    return empty


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


_FENCE_JSON_RE = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL | re.IGNORECASE)
_FENCE_PLAIN_RE = re.compile(r"```\s*(.*?)\s*```", re.DOTALL)


def _strip_markdown_fences(text: str) -> str:
    """Extract content from ```json ... ``` or ``` ... ``` fences if present."""
    m = _FENCE_JSON_RE.search(text)
    if m:
        return m.group(1)
    m = _FENCE_PLAIN_RE.search(text)
    if m:
        return m.group(1)
    return text


def _try_parse(text: str) -> Any:
    """Return parsed JSON or None if parse fails."""
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None


def _coerce(parsed: Any, expected: Literal["array", "object"]) -> Any:
    """Shape the parsed value to match ``expected``, unwrapping if needed.

    Returns None if the shape can't be satisfied — caller will keep trying.
    """
    if expected == "array":
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            for key in _WRAPPER_KEYS:
                value = parsed.get(key)
                if isinstance(value, list):
                    return value
            # Unrecognized wrapper key — not an array we can use.
            return None
        return None

    # expected == "object"
    if isinstance(parsed, dict):
        return parsed
    return None


def _parse_order(expected: Literal["array", "object"]) -> tuple[str, ...]:
    """Which container shapes to try when walking the string.

    Arrays preferred first when the caller wants a list, since the common case
    is a bare JSON array. Objects fall back to unwrap dict-wrapped payloads.
    """
    if expected == "array":
        return ("array", "object")
    return ("object", "array")


def _find_balanced(text: str, shape: str) -> str | None:
    """Return the first balanced JSON array or object substring, or None.

    Walks the string tracking nesting depth. Skips over string literals so
    brackets inside strings don't disturb the count. Handles escapes.
    """
    open_ch, close_ch = ("[", "]") if shape == "array" else ("{", "}")

    start = -1
    depth = 0
    in_string = False
    escape = False

    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            if in_string:
                escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == open_ch:
            if depth == 0:
                start = i
            depth += 1
        elif ch == close_ch:
            if depth == 0:
                continue  # unbalanced close — skip
            depth -= 1
            if depth == 0 and start >= 0:
                return text[start : i + 1]

    return None
