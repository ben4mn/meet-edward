"""Voice keeps working: when a spoken turn becomes work, say so and let go.

2026-09-24 ("Voice keeps working"). A spoken turn used to be one turn per
commit: a tool-heavy answer kept the owner listening to silence (the bridge line covers one second,
not thirty), a barge-in cancelled the work, and closing the voice view
threw it away. Now a turn that is still running tools after
``DEFAULT_HANDOFF_SECONDS`` — or at its third tool call — says a short
handoff line and carries on detached from the socket. When it finishes,
the result is spoken if the session is still open and quiet, held for the
next quiet moment if Edward or the owner is mid-sentence, and pushed (linking the
thread, where the full answer already is) if the session has ended.

Everything here is pure; ``routers/voice_ws.py`` owns the tasks and sockets.
"""

from __future__ import annotations

import random
import re
from typing import Optional, Tuple

from services.voice.sentence_feed import SentenceFeed, strip_for_speech

DEFAULT_HANDOFF_SECONDS = 12.0
HANDOFF_TOOL_CALLS = 3

# What a spoken turn is doing right now (the router tracks it per turn).
PHASE_THINKING = "thinking"      # no tool yet, no answer yet
PHASE_TOOLS = "tools"            # a tool ran and the answer has not started since
PHASE_ANSWERING = "answering"    # the answer is streaming (and being spoken)
PHASE_HANDED_OFF = "handed_off"  # detached: finishing on its own

# Said once, when the turn lets go of the session. Short: the owner can talk
# over it, and it has to promise the one thing that happens next.
HANDOFF_LINES = (
    "This'll take a minute. I'll tell you when it's done.",
    "This one needs a bit. I'll let you know when it's finished.",
    "I'm on it. I'll come back to you when it's done.",
    "Give me a minute with this one. I'll report back.",
    "This is going to take a moment. I'll tell you when I've got it.",
    "Leave it with me. I'll let you know when it's done.",
    "On it. Keep talking if you like; I'll tell you when it's done.",
)

# Said when the owner asks something new while a handed-off turn is still
# running. The new turn waits: two turns writing one conversation's
# checkpoint at once would lose one of them.
WAIT_LINES = (
    "I'm still finishing the last thing. I'll get to this right after.",
    "Still on the last one. This is next.",
    "One thing at a time. I'll get to this as soon as the last one's done.",
)

STOPPED_LINE = "Okay, I've stopped that."

SUMMARY_MAX_CHARS = 240
PUSH_BODY_MAX_CHARS = 200

_STOP_PHRASES = frozenset({
    "stop", "stop it", "stop that", "cancel", "cancel it", "cancel that",
    "never mind", "nevermind", "forget it", "forget that", "forget about it",
    "that's enough", "thats enough",
})
_LEADING_FILLER = frozenset({"ok", "okay", "edward", "please", "hey", "actually", "no", "oh"})
_TRAILING_FILLER = frozenset({"please", "edward", "now"})


def should_hand_off(
    elapsed_s: float,
    tool_calls: int,
    state: str,
    *,
    seconds: float = DEFAULT_HANDOFF_SECONDS,
    max_tool_calls: int = HANDOFF_TOOL_CALLS,
) -> bool:
    """True when a spoken turn has become work worth letting go of.

    Only while it is running tools: a slow first token is what the bridge
    lines cover, and an answer already being spoken is never cut off to
    announce that it will be finished later.
    """
    if state != PHASE_TOOLS:
        return False
    if tool_calls >= max_tool_calls:
        return True
    return tool_calls >= 1 and elapsed_s >= seconds


def delivery_for(session_open: bool, session_state: str) -> str:
    """How a finished handed-off turn reaches the owner: ``speak`` | ``hold`` | ``push``.

    ``hold`` is an open session that is mid-turn: the result waits for the
    next quiet moment (the next turn speaks it first, or the session's end
    pushes it) rather than talking over whoever is talking.
    """
    if not session_open:
        return "push"
    if session_state == "listening":
        return "speak"
    return "hold"


def _clip(text: str, max_chars: int) -> str:
    """At most ``max_chars``, cut at a soft boundary, ending like a sentence.

    A long answer with no full stop arrives from the sentence feeder as a
    chunk ending in a comma; spoken as-is it sounds cut off mid-thought.
    """
    head = text
    if len(head) > max_chars:
        head = head[:max_chars]
        for sep in (", ", "; ", ": ", " "):
            idx = head.rfind(sep)
            if idx > 0:
                head = head[:idx]
                break
    head = head.rstrip(" ,;:-")
    if head and head[-1] not in ".!?":
        head += "."
    return head


def summary_for_speech(text: str, max_sentences: int = 2, max_chars: int = SUMMARY_MAX_CHARS) -> str:
    """The first one or two sentences of an answer, flattened for a voice."""
    flat = strip_for_speech(text or "")
    if not flat:
        return ""
    feed = SentenceFeed(max_pending_chars=max_chars)
    sentences = feed.push(flat)
    rest = feed.flush()
    if rest:
        sentences.append(rest)
    out = ""
    for sentence in sentences[:max_sentences]:
        candidate = f"{out} {sentence}".strip()
        if out and len(candidate) > max_chars:
            break
        out = candidate
    return _clip(out, max_chars)


def result_speech(answer: str, failed: bool = False) -> str:
    """What Edward says when a handed-off turn lands while the owner is listening.

    It leads in, because a voice starting up out of silence needs a reason.
    """
    if failed:
        return "Sorry, I couldn't finish that one."
    summary = summary_for_speech(answer)
    if not summary:
        return "Okay, that's done. It's in our chat."
    return f"Okay, that's done. {summary}"


def push_text_for(answer: str, failed: bool = False) -> Tuple[str, str]:
    """(title, body) for the push when the session has already ended."""
    if failed:
        return "Edward", "I couldn't finish what you asked for on voice. It's in our chat."
    body = summary_for_speech(answer, max_chars=PUSH_BODY_MAX_CHARS)
    return "Edward — done", body or "Done. The answer is in our chat."


def _pick(lines: Tuple[str, ...], rng: Optional[random.Random], last: Optional[str]) -> str:
    r = rng or random
    choices = [line for line in lines if line != last] if last and len(lines) > 1 else list(lines)
    return r.choice(choices)


def pick_handoff_line(rng: Optional[random.Random] = None, last: Optional[str] = None) -> str:
    return _pick(HANDOFF_LINES, rng, last)


def pick_wait_line(rng: Optional[random.Random] = None, last: Optional[str] = None) -> str:
    return _pick(WAIT_LINES, rng, last)


def is_stop_phrase(text: str) -> bool:
    """A committed transcript that means "stop the thing you're working on".

    Only consulted while a handed-off turn is running, so a plain "stop"
    has one meaning. Anything with more to it ("stop the music in the
    kitchen") is an ordinary request.
    """
    words = re.sub(r"[^\w\s']", " ", (text or "").lower()).split()
    while words and words[0] in _LEADING_FILLER:
        words.pop(0)
    while words and words[-1] in _TRAILING_FILLER:
        words.pop()
    return " ".join(words) in _STOP_PHRASES
