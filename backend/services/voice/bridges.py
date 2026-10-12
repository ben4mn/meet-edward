"""What Edward says out loud while the model is still thinking.

Fun and randomized by design (2026-09-05). Keep them short: a
bridge that outlasts the pause it covers is worse than silence.
"""

from __future__ import annotations

import random
from typing import Optional

BRIDGES = (
    "Hmm, let me think.",
    "One sec, digging that up.",
    "Good question. Give me a moment.",
    "Hang on, checking.",
    "Let me have a look.",
    "Right, thinking cap on.",
    "Bear with me a second.",
    "Working on it.",
    "Give me a beat.",
    "Let me rummage through my notes.",
    "Okay, consulting the archives.",
    "Hold that thought.",
    "Just a moment, pulling that together.",
    "Let me see what I've got.",
    "Hmm. One moment.",
    "Checking the back of my mind.",
)

DEFAULT_BRIDGE_THRESHOLD_S = 1.0


def pick_bridge(rng: Optional[random.Random] = None, last: Optional[str] = None) -> str:
    r = rng or random
    choices = [b for b in BRIDGES if b != last] if last and len(BRIDGES) > 1 else list(BRIDGES)
    return r.choice(choices)


def bridge_due(
    *,
    elapsed_s: float,
    first_token_seen: bool,
    already_spoken: bool,
    threshold_s: float = DEFAULT_BRIDGE_THRESHOLD_S,
) -> bool:
    if first_token_seen or already_spoken:
        return False
    return elapsed_s >= threshold_s
