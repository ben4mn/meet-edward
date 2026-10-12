"""Helpers that turn chat-level signal into ``self_observations`` rows.

The dream-synthesis judge gates on ``occurrences >= 3`` per signature,
so observations need a *stable* signature across reworded variants of
the same complaint. Cheap regex heuristics here intentionally aim for
recall over precision — false positives at this layer cost nothing
beyond an extra row that the judge eventually discards; false negatives
keep the learn-loop starved.
"""

from __future__ import annotations

import re
from typing import Optional

# Each correction kind has a regex pattern (case-insensitive) and a stable
# signature suffix. Order matters — earlier patterns win when multiple match.
_CORRECTION_PATTERNS: list[tuple[re.Pattern, str]] = [
    # "that's wrong" / "that was wrong" / "that is wrong" / "that's incorrect"
    # / "that's not right" / "that was the wrong one" — covers natural-
    # speech corrections including past tense, which the original regex
    # missed (zero rows in prod over 24h despite real corrections).
    (
        re.compile(
            r"\bthat(?:'s|s|\s+(?:is|was))\s+"
            r"(?:wrong|incorrect|not\s+(?:right|true|correct))\b",
            re.I,
        ),
        "thats_wrong",
    ),
    (
        re.compile(r"\b(?:was|is)\s+the\s+wrong\s+(?:one|thing|answer)\b", re.I),
        "thats_wrong",
    ),
    (re.compile(r"^\s*(?:no[,.! ]+)?actually\b", re.I), "actually"),
    (re.compile(r"\bstop\s+(?:doing|saying|that)\b", re.I), "stop_doing"),
    (re.compile(r"\bdon'?t\s+(?:do|say)\s+that\b", re.I), "dont_do_that"),
    (re.compile(r"\bremember\s+(?:that|this)\b", re.I), "remember_that"),
    # Forget — symmetric pair to remember. Captures "forget X", "forget
    # that …", "forget what I said about Y".
    (re.compile(r"\bforget\s+(?:that\b|what\s+i\s+said|about\b|\w)", re.I), "forget_that"),
]


def is_correction_signal(text: Optional[str]) -> bool:
    """Cheap heuristic: does this user message look like a correction?"""
    if not text:
        return False
    for pattern, _ in _CORRECTION_PATTERNS:
        if pattern.search(text):
            return True
    return False


def correction_signature(text: str) -> str:
    """Stable signature for aggregation. Reworded variants of the same
    correction kind share a signature so ``occurrences`` climbs."""
    if not text:
        return "user_correction.unknown"
    for pattern, label in _CORRECTION_PATTERNS:
        if pattern.search(text):
            return f"user_correction.{label}"
    return "user_correction.unknown"


async def emit_correction_observation(text: str) -> Optional[str]:
    """Record a single ``user_correction`` observation. Returns row id
    (or None on storage error).

    Fire-and-forget from the chat node — never raises."""
    if not is_correction_signal(text):
        return None
    try:
        from services.observe import record_observation

        signature = correction_signature(text)
        return await record_observation(
            kind="user_correction",
            source="chat",
            signature=signature,
            description=(text or "").strip()[:280],
            severity=0.5,
            confidence=0.6,
        )
    except Exception:
        return None
