"""Spoken-turn prompt and model selection."""

from __future__ import annotations

import os

# Luna measured fastest to first token of the Codex tiers on 2026-09-05
# (~1.0 s vs ~1.7 s terra, ~1.8 s astra on a tiny prompt): spoken turns
# favour speed.
VOICE_MODEL = os.getenv("EDWARD_VOICE_MODEL", "gpt-5.6-luna")

VOICE_STYLE_PROMPT = (
    "VOICE MODE: This turn is being read aloud by a text-to-speech voice, "
    "not shown on a screen. Reply the way you would speak: short sentences, "
    "plain words, usually two to four sentences unless asked for more. "
    "No markdown, no bullet lists, no headings, no code, no URLs, no emoji. "
    "Numbers and times should read naturally when spoken. If you need to "
    "run tools, do it, then summarise the result in speech."
)


def voice_system_prompt(base: str) -> str:
    base = base or ""
    if VOICE_STYLE_PROMPT in base:
        return base
    return f"{base.rstrip()}\n\n{VOICE_STYLE_PROMPT}"
