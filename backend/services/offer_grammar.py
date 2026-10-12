"""The offer grammar: the one shape a reach to the owner from Edward's own turns takes.

Why (2026-10-11; docs/architecture/noticing.md): none of the 33 pushes in
the evidence window was opened. The reaches the owner valued had one shape
— what Edward saw and where it came from (whose message, when), what it
means, what is missing, what he prepared with the irreversible step held
back, and one question the owner can answer with a tap. This lint holds that
shape in code. It is pure: ``lint`` returns the problems in words (empty
when the text is fine), and ``reach_service.tell_owner`` hands them back to the
model as a ``Tool error`` it can fix once. The owner's own asks, care alerts and
the templated notices are never linted.
"""

from __future__ import annotations

import re
from typing import List, Optional, Sequence

from services.owner import owner_text

PUSH_MAX_CHARS = 180
TITLE_MAX_CHARS = 60
NOTE_MAX_CHARS = 600

TOO_LONG_PUSH = "too long for a push: say the rest in the note"
TOO_LONG_NOTE = f"too long for a note: keep it under {NOTE_MAX_CHARS} characters"
TOO_LONG_TITLE = f"title too long: keep it under {TITLE_MAX_CHARS} characters"
NO_GREETING = "no greeting or filler — lead with what you saw"
NEEDS_SOURCE = "say whose message and when ('Rowan's 9:31 pm text …')"
ONE_QUESTION = owner_text("end with one question {OWNER} can answer yes/no or by choosing")
HELD_STEP = "say what you held back ('not sent')"
EMPTY = "nothing to say: write what you saw and one question"

_GREETING = re.compile(r"^\W*(hey|hi|hello|good (morning|afternoon|evening))\b", re.I)
_FILLER = re.compile(
    r"just (checking|wanted)|let me know|if you need anything|hope (you|this)|happy to help|no worries", re.I)
_TIME = re.compile(r"\b\d{1,2}(:\d{2})?\s?(am|pm)\b", re.I)
_SOURCE_NOUN = re.compile(r"\b(text|message|email|call|calendar|sms|voicemail|note|post)s?\b", re.I)
# How close a source noun must sit to the time ("Rowan's 9:31 pm text",
# "a text at 9:31 pm") to count as naming the message.
_NEAR_CHARS = 40
_TRAILING_PAREN = re.compile(r"\s*\([^()]*\)\s*$")
_PREPARED = re.compile(r"\b(draft(ed)?|prepared|wrote|filled|ready|lined up)\b", re.I)
_HELD = re.compile(r"not sent|not done|haven['’]t sent|nothing sent|not booked|not paid", re.I)


def _names_someone(text: str, who: Sequence[str]) -> bool:
    low = text.lower()
    for name in who or ():
        n = str(name or "").strip().lower()
        if n and re.search(r"(?<!\w)" + re.escape(n) + r"(?!\w)", low):
            return True
    return False


def _time_beside_source(text: str) -> bool:
    for m in _TIME.finditer(text):
        window = text[max(0, m.start() - _NEAR_CHARS): m.end() + _NEAR_CHARS]
        if _SOURCE_NOUN.search(window):
            return True
    return False


def has_source_reference(text: str, who: Sequence[str] = ()) -> bool:
    """Names one of ``who``, or has a clock time next to a source noun. Pure."""
    return _names_someone(text, who) or _time_beside_source(text)


def ends_with_one_question(text: str) -> bool:
    """After one trailing parenthetical ("(not sent)") is stripped, the text
    ends with ``?``, and the whole text has exactly one ``?``. Pure."""
    body = _TRAILING_PAREN.sub("", text, count=1).rstrip()
    return body.endswith("?") and text.count("?") == 1


def asks_a_question(text: Optional[str]) -> bool:
    """Does the text end in a question once a trailing "(not sent)" is set
    aside? A note that does needs the owner. Pure."""
    return _TRAILING_PAREN.sub("", (text or "").strip(), count=1).rstrip().endswith("?")


def lint(text: Optional[str], *, who: Sequence[str] = (), kind: str = "push") -> List[str]:
    """The problems with ``text`` as a reach of ``kind``, in words; ``[]``
    when it is fine. Pure.

    * ``push`` — a push body: the whole grammar, 180 characters;
    * ``offer`` — what the speaker leaves as a note: the whole grammar, 600;
    * ``note`` — a note that tells (a follow-through's report, the
      nightly's one note, a friend's relay): 600 characters, no greeting or
      filler, and "not sent" when something was prepared — no source or
      question required (review 2026-10-11: the grammar was kept for
      can't-wait reach; on every note it refused statements that ask
      nothing);
    * ``title`` — 60 characters, no greeting."""
    t = (text or "").strip()
    if not t:
        return [EMPTY]
    problems: List[str] = []
    k = (kind or "push").strip().lower()
    if k == "title":
        if len(t) > TITLE_MAX_CHARS:
            problems.append(TOO_LONG_TITLE)
        if _GREETING.search(t):
            problems.append(NO_GREETING)
        return problems
    as_note = k in ("note", "offer")
    if len(t) > (NOTE_MAX_CHARS if as_note else PUSH_MAX_CHARS):
        problems.append(TOO_LONG_NOTE if as_note else TOO_LONG_PUSH)
    if _GREETING.search(t) or _FILLER.search(t):
        problems.append(NO_GREETING)
    if k != "note":
        if not has_source_reference(t, who):
            problems.append(NEEDS_SOURCE)
        if not ends_with_one_question(t):
            problems.append(ONE_QUESTION)
    if _PREPARED.search(t) and not _HELD.search(t):
        problems.append(HELD_STEP)
    return problems


def problems_text(problems: Sequence[str]) -> str:
    """One line for a ``Tool error``: the problems joined with ``; ``."""
    return "; ".join(p for p in problems if p)
