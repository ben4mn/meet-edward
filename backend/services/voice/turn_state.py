"""Server-side reducer for a live voice session.

States: ``listening`` (mic open, nothing pending), ``thinking`` (a turn
is running, no audio yet), ``speaking`` (audio is flowing). The reducer
returns ``(new_state, action)`` and never touches I/O.

Barge-in rule: a partial transcript while Edward speaks only counts if
it has at least two words. One stray word is usually his own voice
leaking into the mic, or a cough.
"""

from __future__ import annotations

from typing import Tuple

BARGE_IN_MIN_WORDS = 2
NOISY_ROOM_BARGE_IN_MIN_WORDS = 3


def barge_in_min_words(sensitivity) -> int:
    """How many words of overlapping speech it takes to cut Edward off.
    In a noisy room ("low" sensitivity) two words is the TV."""
    return NOISY_ROOM_BARGE_IN_MIN_WORDS if sensitivity == "low" else BARGE_IN_MIN_WORDS


def decide(
    state: str,
    event: str,
    text: str = "",
    barge_in_min_words: int = BARGE_IN_MIN_WORDS,
) -> Tuple[str, str]:
    words = len(text.split())

    if event == "interrupt":
        return "listening", "cancel"

    if event == "committed":
        if words == 0:
            return state, "ignore"
        if state == "listening":
            return "thinking", "start_turn"
        return "thinking", "restart_turn"

    if event == "partial":
        if state == "speaking" and words >= barge_in_min_words:
            return "listening", "barge_in"
        return state, "forward"

    if event == "first_audio":
        if state == "thinking":
            return "speaking", "none"
        return state, "none"

    if event == "response_done":
        return "listening", "none"

    return state, "none"
