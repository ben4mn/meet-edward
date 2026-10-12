"""Edward's thinking on his screen (the stage, 2026-09-28).

While a mind wake or the pulse really runs, ``/screen`` shows what he is
thinking: one short line per reasoning summary the model gives, and a
receipt per tool — its name and whether it worked, never its args or its
result. When the turn ends it folds into one line ("Earlier · 2:10 pm —
…"; "I didn't get to finish that." when it failed or was cut off), and
the last three folds are kept. ``GET /api/screen/live`` serves
``snapshot()``; the page polls it every 2 s.

The live stream is in-process and in memory only: nothing is written,
and a restart forgets it. ``turn_ledger``'s hooks feed it from both tool loops (begin, each
response's reasoning, tool start and end, end); only ``mind`` and
``pulse`` turns are followed — the owner's own chat is on their phone,
not on the wall. Every entry point is bounded and never raises, so a broken screen
can never break or slow a turn. A live turn older than
``STALE_AFTER`` drops out of the snapshot: a crashed turn must not look
like thinking (the animation is a claim, so it must be true).

Each line is scrubbed fail-closed the way the chat trace is
(``turn_trace._scrub``): a line that looks like it carries a secret — or
that cannot be scanned — shows as "[hidden: looked like a secret]".

Between turns the lane is never empty (round 2, 2026-09-28: it should
always show what he is thinking or dreaming): ``recent()`` reads
his own recent thoughts from durable rows, so they survive a restart —
the closing line of each wake (``ledger_events`` kind ``mind``, acted or
left alone; the nightly reflection is one). The surplus queue's free
time and research went with the queue (2026-10-11); its ``surplus`` rows
are no longer read. Never the pulse's decisions (surfaces, not thoughts),
never the owner's chat. Cleaned like a live line; a line that looks like a
secret is left out rather than shown hidden; boilerplate ("(no note)",
"nothing to do") is dropped. Cached ``RECENT_TTL_S`` in-process (the page
polls every 2 s); any failure reads as no recent thoughts.

A ``speak`` wake (2026-10-11) is about something he noticed in the
owner's messages, and the stage is a screen anyone in the room can read: its
reasoning is never shown while it runs (its tool receipts are), and its
closing — live or from the ledger — is the fixed ``SPEAK_LINE``.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import time
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Deque, Dict, Iterable, List, Optional, Tuple

from services import wake_state
from services.turn_ledger import SCREEN_THOUGHT_SOURCES

logger = logging.getLogger(__name__)

TRACKED_SOURCES = SCREEN_THOUGHT_SOURCES
MAX_LINES = 8
LINE_MAX_CHARS = 220
MAX_RECEIPTS = 12
MAX_FOLDS = 3
MAX_LIVE = 8  # a turn whose end never came must not pile up
STALE_AFTER = timedelta(minutes=20)
FOLD_FALLBACK = "I looked things over."
# A turn that failed or was cut off (its model call errored, the wake's
# timeout cancelled it) folds to this, never to "I looked things over." or
# to its last half-finished thought (review 2026-09-28).
FOLD_UNFINISHED = "I didn't get to finish that."
UNFINISHED_OUTCOMES = ("error", "stopped")
PULSE_ABOUT = "surfaces"
# Wakes whose thinking is about the owner's private messages (2026-10-11):
# the lane shows their tools and this one line, never their words — the
# same line the wake state, Today and the frontend show (wake_state).
PRIVATE_ABOUTS = ("speak",)
SPEAK_LINE = wake_state.SPEAK_LINE
_SEEN_MAX = 64  # summaries remembered per turn, so a repeat is said once


# ---------------------------------------------------------------- pure --

_BOLD_HEADING = re.compile(r"^\s*(\*\*|__)(?P<head>[^\n]+?)\1\s*(?:\n+|$)")
_HASH_HEADING = re.compile(r"^\s*#{1,6}\s+[^\n]*(?:\n+|$)")
_LINE_HASH = re.compile(r"(?m)^\s*#{1,6}\s*")
_LINE_BULLET = re.compile(r"(?m)^\s*(?:[-*+•]|\d{1,3}[.)])\s+")
_EMPHASIS = re.compile(r"\*\*|__|`+")
_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def _strip_markdown(text: str) -> str:
    text = _LINE_HASH.sub("", text)
    text = _LINE_BULLET.sub("", text)
    text = _EMPHASIS.sub("", text)
    return " ".join(text.split())


def _cap(text: str, limit: int = LINE_MAX_CHARS) -> str:
    """Whole sentences up to ``limit``; else words and an ellipsis."""
    if len(text) <= limit:
        return text
    kept = ""
    for sentence in _SENTENCE_END.split(text):
        joined = f"{kept} {sentence}".strip()
        if len(joined) > limit:
            break
        kept = joined
    if kept:
        return kept
    cut = text[: limit - 1]
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,;:-") + "…"


def thought_line(text: Any) -> Optional[str]:
    """One reasoning summary as a line for the screen: markdown stripped,
    a bold (or ``#``) heading dropped when a body follows it, the first
    sentences within ``LINE_MAX_CHARS``. None when nothing is left.
    Pure; not scrubbed (``_line_for`` does that)."""
    if not isinstance(text, str) or not text.strip():
        return None
    body = text.strip()
    for heading in (_BOLD_HEADING, _HASH_HEADING):
        m = heading.match(body)
        if m and body[m.end():].strip():
            body = body[m.end():]
            break
    flat = _strip_markdown(body)
    return _cap(flat) if flat else None


def _scrubbed(full: str, line: str) -> str:
    """Fail closed: the whole line hides when either the full summary or
    the line shown looks like it carries a secret (a cut can hide the
    tail a pattern needs)."""
    from services.graph import turn_trace

    known = turn_trace.known_secrets_safely()
    for text in (full, line):
        if turn_trace._scrub(text, known) == turn_trace.REDACTED:
            return turn_trace.REDACTED
    return line


def _iso(when: datetime) -> str:
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return when.astimezone(timezone.utc).isoformat()


def _now(now: Optional[datetime]) -> datetime:
    if now is None:
        return datetime.now(timezone.utc)
    return now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------ the state --

class _Live:
    __slots__ = ("turn", "source", "about", "since", "lines", "receipts", "tool_count", "seen")

    def __init__(self, turn: str, source: str, about: Optional[str], since: datetime):
        self.turn = turn
        self.source = source
        self.about = about
        self.since = since
        self.lines: Deque[Dict[str, str]] = deque(maxlen=MAX_LINES)
        self.receipts: Deque[Dict[str, str]] = deque(maxlen=MAX_RECEIPTS)
        self.tool_count = 0
        self.seen: Deque[str] = deque(maxlen=_SEEN_MAX)

    def view(self) -> Dict[str, Any]:
        return {
            "turn": self.turn,
            "source": self.source,
            "about": self.about,
            "since": _iso(self.since),
            "lines": [dict(ln) for ln in self.lines],
            "receipts": [dict(r) for r in self.receipts],
        }


_lock = threading.Lock()
_live: Dict[str, _Live] = {}  # insertion order = the order turns began
_folds: Deque[Dict[str, Any]] = deque(maxlen=MAX_FOLDS)


def _tracked(rec: Any) -> Optional[str]:
    """The turn id when ``rec`` is a mind or pulse turn, else None."""
    if rec is None or getattr(rec, "source", None) not in TRACKED_SOURCES:
        return None
    turn = getattr(rec, "id", None)
    return str(turn) if turn else None


def _about(source: str) -> Optional[str]:
    try:
        from services.provenance_service import current_source_context

        reason = current_source_context().get("wake_reason")
        if isinstance(reason, str) and reason.strip():
            return reason.strip()[:40]
    except Exception:  # noqa: BLE001
        pass
    return PULSE_ABOUT if source == "pulse" else None


# ------------------------------------------------------------ the hooks --

def begin(rec: Any, now: Optional[datetime] = None) -> None:
    """A mind or pulse turn started (``turn_ledger.begin_turn``)."""
    try:
        turn = _tracked(rec)
        if turn is None:
            return
        live = _Live(turn, rec.source, _about(rec.source), _now(now))
        with _lock:
            _live.pop(turn, None)
            _live[turn] = live
            while len(_live) > MAX_LIVE:
                _live.pop(next(iter(_live)))
    except Exception as exc:  # noqa: BLE001 — the screen never breaks a turn
        logger.debug("screen thoughts begin failed: %s", exc)


def note_reasoning(rec: Any, texts: Iterable[Any], now: Optional[datetime] = None) -> None:
    """The reasoning summaries one model response added: a line each."""
    try:
        turn = _tracked(rec)
        if turn is None:
            return
        with _lock:
            live = _live.get(turn)
        if live is None or live.about in PRIVATE_ABOUTS:
            return
        at = _iso(_now(now))
        for text in list(texts or [])[:MAX_LINES]:
            if not isinstance(text, str) or text in live.seen:
                continue
            line = thought_line(text)
            if line is None:
                continue
            line = _scrubbed(text, line)
            with _lock:
                live.seen.append(text)
                live.lines.append({"text": line, "at": at})
    except Exception as exc:  # noqa: BLE001
        logger.debug("screen thoughts reasoning failed: %s", exc)


def tool_started(rec: Any, name: Any, now: Optional[datetime] = None) -> None:
    """A tool began: a ``running`` receipt, by name only."""
    try:
        turn = _tracked(rec)
        if turn is None:
            return
        with _lock:
            live = _live.get(turn)
            if live is None:
                return
            live.receipts.append({"tool": str(name or "?")[:64], "state": "running", "at": _iso(_now(now))})
            live.tool_count += 1
    except Exception as exc:  # noqa: BLE001
        logger.debug("screen thoughts tool start failed: %s", exc)


def tool_finished(rec: Any, name: Any, ok: bool, now: Optional[datetime] = None) -> None:
    """A tool ended: its latest ``running`` receipt settles to ok/error. A
    call answered without running (the loop's precheck) gets its own."""
    try:
        turn = _tracked(rec)
        if turn is None:
            return
        tool = str(name or "?")[:64]
        state = "ok" if ok else "error"
        with _lock:
            live = _live.get(turn)
            if live is None:
                return
            for receipt in reversed(live.receipts):
                if receipt["tool"] == tool and receipt["state"] == "running":
                    receipt["state"] = state
                    return
            live.receipts.append({"tool": tool, "state": state, "at": _iso(_now(now))})
            live.tool_count += 1
    except Exception as exc:  # noqa: BLE001
        logger.debug("screen thoughts tool end failed: %s", exc)


def end(rec: Any, now: Optional[datetime] = None) -> None:
    """The turn ended (``turn_ledger.end_turn``): it leaves ``thinking``
    and folds into one line — its last line, or "I looked things over.";
    a turn that ended ``error`` or ``stopped`` folds to "I didn't get to
    finish that." (the fold reads as a conclusion, so it must be one)."""
    try:
        turn = _tracked(rec)
        if turn is None:
            return
        with _lock:
            live = _live.pop(turn, None)
            if live is None:
                return
            if getattr(rec, "outcome", None) in UNFINISHED_OUTCOMES:
                text = FOLD_UNFINISHED
            elif live.about in PRIVATE_ABOUTS:
                text = SPEAK_LINE
            else:
                text = live.lines[-1]["text"] if live.lines else FOLD_FALLBACK
            _folds.append({
                "text": text,
                "about": live.about,
                "at": _iso(_now(now)),
                "tools": live.tool_count,
            })
    except Exception as exc:  # noqa: BLE001
        logger.debug("screen thoughts end failed: %s", exc)


# ------------------------------------------------------------- the view --

def snapshot(now: Optional[datetime] = None) -> Dict[str, Any]:
    """``{"thinking": <the newest live turn> | None, "folded": [...], "now"}``.
    Lines, receipts and folds are oldest first. A live turn older than
    ``STALE_AFTER`` is dropped (and forgotten)."""
    at = _now(now)
    with _lock:
        for turn in [t for t, live in _live.items() if at - live.since > STALE_AFTER]:
            _live.pop(turn, None)
        newest = next(reversed(_live.values()), None) if _live else None
        return {
            "thinking": newest.view() if newest is not None else None,
            "folded": [dict(f) for f in _folds],
            "now": _iso(at),
        }


def empty_snapshot(now: Optional[datetime] = None) -> Dict[str, Any]:
    """What the route answers when anything goes wrong."""
    return {"thinking": None, "folded": [], "now": _iso(_now(now))}


def reset() -> None:
    """Tests: forget everything."""
    global _recent_cache
    with _lock:
        _live.clear()
        _folds.clear()
    _recent_cache = None


# ----------------------------------------------------- recent thoughts --

MAX_RECENT = 12
RECENT_HOURS = 12
RECENT_TTL_S = 30.0
RECENT_READ_TIMEOUT_S = 3.0
_RECENT_ROWS_CAP = 200
# The mind's wakes that thought (not a failure or a quota skip); the pulse
# records no mind row (``record=False``) and ``surfaces`` is left out anyway.
_MIND_OUTCOMES = ("acted", "left_alone")
_MIND_NOT_THOUGHTS = ("surfaces",)
_BOILERPLATE = re.compile(
    r"^\(?(no note|nothing( to do| new| to add)?|none|n/?a|ok(ay)?|done)\)?[.!]?$", re.I)
_JOB_DEFAULT = re.compile(r"^[a-z_]+ (done|skipped|failed)$")
# Failure reports that close a wake without being a thought: the tool loop's
# ``tool_policy.honest_ending`` (the model never answered, the rounds ran
# out — recorded ``left_alone`` or ``acted``, and retried as a failure by
# ``mind_service.should_retry_wake``), ``mind_service.RETRY_MARK`` echoed
# back, and a tool's ``Tool error:`` answer. Round 1 folds an unfinished
# turn to ``FOLD_UNFINISHED`` for the same reason.
_FAILURE_PREFIXES = ("i stopped before finishing", "(retry ", "tool error:")


def _row_get(row: Any, key: str) -> Any:
    if isinstance(row, dict):
        return row.get(key)
    return getattr(row, key, None)


def _thought_of(row: Any) -> Optional[Tuple[str, str]]:
    """(about, kind) when the row is one of his thoughts, else None."""
    kind, source, outcome = _row_get(row, "kind"), _row_get(row, "source"), _row_get(row, "outcome")
    if kind == "mind" and outcome in _MIND_OUTCOMES and source and source not in _MIND_NOT_THOUGHTS:
        return str(source)[:40], "mind"
    return None


def _boilerplate(text: str) -> bool:
    flat = text.strip()
    return (not flat or bool(_BOILERPLATE.match(flat)) or bool(_JOB_DEFAULT.match(flat))
            or flat.lower().startswith(_FAILURE_PREFIXES))


def recent_lines(rows: Iterable[Any], now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """His own thought lines from ledger rows (dicts or models with kind,
    source, outcome, reason, created_at — naive UTC): the last
    ``RECENT_HOURS``, newest last, at most ``MAX_RECENT``; one line per
    note. A ``speak`` wake's note is ``SPEAK_LINE``, never its own words.
    ``{"text", "at", "about", "kind"}``. Pure but for the secret scan."""
    at_now = _now(now)
    since = at_now - timedelta(hours=RECENT_HOURS)
    found: List[Tuple[datetime, Dict[str, Any]]] = []
    for row in rows or []:
        try:
            what = _thought_of(row)
            created = _row_get(row, "created_at")
            if what is None or not isinstance(created, datetime):
                continue
            when = created if created.tzinfo is not None else created.replace(tzinfo=timezone.utc)
            if when < since or when > at_now + timedelta(minutes=5):
                continue
            reason = _row_get(row, "reason")
            if not isinstance(reason, str) or _boilerplate(reason):
                continue
            if what[0] in PRIVATE_ABOUTS:
                found.append((when, {"text": SPEAK_LINE, "at": _iso(when), "about": what[0], "kind": what[1]}))
                continue
            line = thought_line(reason)
            if line is None or _boilerplate(line):
                continue
            if _scrubbed(reason, line) != line:
                continue  # fail closed: a line that looks like a secret is left out
            found.append((when, {"text": line, "at": _iso(when), "about": what[0], "kind": what[1]}))
        except Exception as exc:  # noqa: BLE001 — one odd row never costs the rest
            logger.debug("screen thoughts: skipped a ledger row: %s", exc)
    found.sort(key=lambda item: item[0])
    seen: set = set()
    out: List[Dict[str, Any]] = []
    for _, line in found:
        key = " ".join(line["text"].lower().split())
        if key in seen:
            continue
        seen.add(key)
        out.append(line)
    return out[-MAX_RECENT:]


async def _read_recent_rows(since: datetime) -> List[Dict[str, Any]]:
    """The candidate ``ledger_events`` rows since ``since`` (newest first,
    bounded). ``created_at`` is naive UTC: the session's zone is UTC."""
    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session

    naive = since.astimezone(timezone.utc).replace(tzinfo=None)
    async with async_session() as s:
        rows = (await s.execute(
            select(LedgerEventModel.kind, LedgerEventModel.source, LedgerEventModel.outcome,
                   LedgerEventModel.reason, LedgerEventModel.created_at)
            .where(LedgerEventModel.kind == "mind", LedgerEventModel.created_at >= naive)
            .order_by(LedgerEventModel.created_at.desc())
            .limit(_RECENT_ROWS_CAP)
        )).all()
    return [{"kind": r.kind, "source": r.source, "outcome": r.outcome, "reason": r.reason,
             "created_at": r.created_at} for r in rows]


def _monotonic() -> float:
    return time.monotonic()


_recent_cache: Optional[Tuple[float, List[Dict[str, Any]]]] = None


async def recent(now: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """His recent thoughts for the lane (``recent_lines`` over the ledger),
    cached ``RECENT_TTL_S``. Never raises: a failure reads as ``[]`` (and
    is cached too, so a sick database is not asked every 2 s)."""
    global _recent_cache
    try:
        cached = _recent_cache
        if cached is not None and _monotonic() - cached[0] < RECENT_TTL_S:
            return [dict(ln) for ln in cached[1]]
        at = _now(now)
        try:
            rows = await asyncio.wait_for(_read_recent_rows(at - timedelta(hours=RECENT_HOURS)),
                                          timeout=RECENT_READ_TIMEOUT_S)
            lines = recent_lines(rows, now=at)
        except Exception as exc:  # noqa: BLE001
            logger.debug("screen thoughts: recent unavailable: %s", exc)
            lines = []
        _recent_cache = (_monotonic(), lines)
        return [dict(ln) for ln in lines]
    except Exception as exc:  # noqa: BLE001
        logger.debug("screen thoughts: recent failed: %s", exc)
        return []
