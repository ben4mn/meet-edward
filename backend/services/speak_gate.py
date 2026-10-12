"""The gate between noticing and speaking (2026-10-11). Pure.

Why (docs/architecture/noticing.md): the noticer says what it saw; whether
that reaches the owner is decided here, from facts, before any model is
asked to speak. The evidence was specific about what went wrong: Edward
relayed texts the owner had already read (the median read is 2.6 min),
spoke in chats where the owner was mid-conversation, said the same thing
twice, buzzed at night, and stayed silent on the few things that mattered.
So the rules are ordered and the first match wins:

  R0 the spend cap · R1 evidence · R2 stand-downs and open pings · R3 too late
  · R4 the owner is on it · R5 let the read state settle · R6 per-kind rules · R7
  not yet (the lead) · R8 one follow-up · R9 the channel and its ceiling ·
  R10 precision first · R11 quiet hours · R12 the push budget · R13 the speak
  budget.

``channel_ceiling`` folds a category's outcomes into the highest channel it
has earned (the learning ladder); ``ceilings_from_owner`` reads the owner's
own "push me about travel" lines and overrides it. Nothing here does I/O
(quiet hours read ``EDWARD_QUIET_HOURS`` from the environment, per call);
the caller (``noticing_service``) builds the ``GateContext``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Dict, Iterable, Mapping, Optional, Sequence, Tuple

from services.owner import owner_text

SETTLE_MIN = 15
URGENT_MIN = 45
UNANSWERED_MIN_AGE_H = 4
LIVE_CHAT_LINES_24H = 5
OWNER_ON_IT_MIN = 10
FLOOR_PUSH_H = 3
FOLLOW_UP_WINDOW_H = 24
PING_OPEN_HOLD_MIN = 10
DEFAULT_SPEAK_BUDGET = 8
SPEAKS_PER_CHAT_PER_HOUR = 1
# Quiet hours, local: 22:00–07:00 unless ``EDWARD_QUIET_HOURS`` says
# otherwise ("22-7": start and end hour, 0–23; equal hours mean none).
# ``quiet_hours()`` reads it per call; ``QUIET_HOURS`` is the value at
# import, for callers that read a constant (``push_ledger``).
QUIET_HOURS_ENV = "EDWARD_QUIET_HOURS"
DEFAULT_QUIET_HOURS: Tuple[int, int] = (22, 7)


def parse_quiet_hours(raw: Optional[str]) -> Optional[Tuple[int, int]]:
    """``"22-7"`` → ``(22, 7)``; ``None`` when it is not two hours 0–23. Pure."""
    m = re.fullmatch(r"\s*(\d{1,2})\s*[-–]\s*(\d{1,2})\s*", str(raw or ""))
    if not m:
        return None
    start, end = int(m.group(1)), int(m.group(2))
    if not (0 <= start <= 23 and 0 <= end <= 23):
        return None
    return start, end


def quiet_hours() -> Tuple[int, int]:
    """``EDWARD_QUIET_HOURS`` (read per call) when it parses, else 22–7."""
    return parse_quiet_hours(os.getenv(QUIET_HOURS_ENV)) or DEFAULT_QUIET_HOURS


def in_quiet_hours(hour: int, start: int, end: int) -> bool:
    """Is a local ``hour`` inside the window ``[start, end)``, wrapping past
    midnight when ``start > end``? Equal hours: never. Pure."""
    if start <= end:
        return start <= hour < end
    return hour >= start or hour < end


def _hour_label(hour: int) -> str:
    return f"{hour % 12 or 12} {'am' if hour < 12 else 'pm'}"


def quiet_hours_label(hours: Optional[Tuple[int, int]] = None) -> str:
    """The quiet hours in the one clock format — "10 pm–7 am" by default —
    or "none" when the window is empty (equal hours). ``hours`` defaults to
    ``quiet_hours()`` (``EDWARD_QUIET_HOURS``, read per call)."""
    start, end = hours if hours is not None else quiet_hours()
    if start == end:
        return "none"
    return f"{_hour_label(start)}–{_hour_label(end)}"


QUIET_HOURS_TOKEN = "{QUIET_HOURS}"


def quiet_hours_doc(fn):
    """Render ``{QUIET_HOURS}`` in a tool's docstring (its description) with
    ``quiet_hours_label()`` once, at import — like ``owner.owner_doc``, so
    the description stays byte-identical between calls. Apply it innermost."""
    if fn.__doc__:
        fn.__doc__ = fn.__doc__.replace(QUIET_HOURS_TOKEN, quiet_hours_label())
    return fn


QUIET_HOURS = quiet_hours()
PROBATION = timedelta(days=7)

KINDS = ("deadline", "conflict", "unanswered", "security", "household", "follow_through")
CATEGORIES = ("travel", "money", "work", "social", "family", "household", "security", "health", "logistics")
FLOOR_CATEGORIES = frozenset({"travel", "household"})
# What may push inside quiet hours (design decision, 2026-10-11: "except for
# items that cannot wait (care alerts, travel closing within 3 h)"). Care
# alerts are care_watch's, outside this gate; household waits for a note.
NIGHT_PUSH_CATEGORIES = frozenset({"travel"})
CATEGORY_LEAD = {"travel": timedelta(hours=3), "household": timedelta(hours=1)}
KIND_LEAD = {"household": timedelta(hours=1), "conflict": timedelta(hours=3),
             "follow_through": timedelta(hours=1), "deadline": timedelta(hours=24)}
DEFAULT_LEAD = timedelta(hours=3)

# Channels, lowest first. ``thread`` (follow-through only) sits with push.
RANK = {"dropped": -2, "silent": -1, "journal": 0, "note": 1, "ambient": 2, "push": 3, "thread": 3}
LADDER = ("journal", "note", "push")


# ------------------------------------------------------------- the shapes --

@dataclass(frozen=True)
class Source:
    ref: str
    chat: Optional[str]
    at: datetime
    from_me: bool
    read_at: Optional[datetime] = None


@dataclass(frozen=True)
class Noticing:
    id: str
    kind: str
    category: Optional[str]
    claim: str
    sources: Tuple[Source, ...]
    chat: Optional[str]
    who: Tuple[str, ...]
    closes_at: Optional[datetime]
    suggested: Optional[str]
    dedupe_key: Optional[str]
    sensitive: bool
    origin: str = "noticer"


@dataclass(frozen=True)
class Surfaced:
    category: Optional[str]
    channel: Optional[str]
    outcome: Optional[str]
    at: datetime
    dedupe_key: Optional[str]
    chat: Optional[str]
    kind: Optional[str]
    refs: frozenset = frozenset()


@dataclass(frozen=True)
class GateContext:
    now: datetime                                   # aware, the owner's zone
    stood_down: frozenset = frozenset()
    busy_chats: frozenset = frozenset()             # pings open or running there
    owner_last_line: Mapping[str, datetime] = field(default_factory=dict)  # chat → the owner's latest non-echo line
    live_chats: frozenset = frozenset()             # ≥ 5 of the owner's own lines in 24 h
    open_ping_chats: frozenset = frozenset()        # the chat addressed Edward within 7 days
    history: Tuple[Surfaced, ...] = ()              # surfaced noticings, 30 days
    pushes_today: int = 0
    speaks_today: int = 0
    speaks_by_chat_1h: Mapping[str, int] = field(default_factory=dict)
    push_scope: str = "floor"                       # floor | gate
    quota: str = "ok"                               # ok | paced | exhausted
    ceilings_from_owner: Mapping[str, str] = field(default_factory=dict)
    push_budget: Optional[int] = None                # None: no daily push cap (design decision, 2026-10-11)
    speak_budget: int = DEFAULT_SPEAK_BUDGET


@dataclass(frozen=True)
class Decision:
    channel: str                    # dropped | silent | journal | hold | note | ambient | push | thread
    hold_until: Optional[datetime]
    reason: str
    floor_push: bool
    also: Tuple[str, ...] = ()      # ("ambient",) beside a note for a household item past the push budget
    advice: str = ""                # what the ladder says about this category — the speaker weighs it

    @property
    def is_fallback(self) -> bool:
        """A templated push with no model call (the cap, or the speak budget,
        on a floor item)."""
        return self.channel == "push" and self.reason.startswith("fallback")


# ---------------------------------------------------------------- helpers --

def chat_key(chat: Optional[str]) -> str:
    """One spelling per chat (pings.hooks.chat_key's rule): the tail of a
    guid, "p:" plus a phone's last ten digits, else lowercased."""
    t = str(chat or "").strip()
    if ";" in t:
        t = t.rsplit(";", 1)[1].strip()
    digits = "".join(ch for ch in t if ch.isdigit())
    if len(digits) >= 10 and all(ch in "0123456789+()-. " for ch in t):
        return "p:" + digits[-10:]
    return t.lower()


def _keys(chats: Iterable[str]) -> frozenset:
    return frozenset(chat_key(c) for c in chats if c)


def _get(mapping: Mapping[str, object], chat: Optional[str]):
    key = chat_key(chat)
    for k, v in mapping.items():
        if chat_key(k) == key:
            return v
    return None


def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p").lower()


def _min(a: str, b: str) -> str:
    return a if RANK.get(a, 1) <= RANK.get(b, 1) else b


def is_floor_push(n: Noticing, now: datetime) -> bool:
    """Travel or household, closing within 3 h: the only things that may
    push in week one, and push at night when they close before dawn."""
    return (n.category in FLOOR_CATEGORIES and n.closes_at is not None
            and n.closes_at - now < timedelta(hours=FLOOR_PUSH_H))


def may_push_at(n: Noticing, now: datetime) -> bool:
    """May this floor item push now? By day, yes; in quiet hours only travel
    (review 2026-10-11: a courier's gate-code text buzzed the owner at 23:10).
    Pure."""
    return not _in_quiet(now) or n.category in NIGHT_PUSH_CATEGORIES


def lead_for(n: Noticing) -> timedelta:
    """How long before ``closes_at`` it is worth saying (a category lead wins
    over a kind lead)."""
    if n.category in CATEGORY_LEAD:
        return CATEGORY_LEAD[n.category]
    return KIND_LEAD.get(n.kind, DEFAULT_LEAD)


def _in_quiet(now: datetime) -> bool:
    return in_quiet_hours(now.hour, *quiet_hours())


def _next_seven(now: datetime) -> datetime:
    """When quiet hours next end (07:00 by default)."""
    seven = now.replace(hour=quiet_hours()[1], minute=0, second=0, microsecond=0)
    return seven if seven > now else seven + timedelta(days=1)


def _is_household(n: Noticing) -> bool:
    return n.kind == "household" or n.category == "household"


# -------------------------------------------------------------- the ladder --

def ladder_state(category: Optional[str], history: Sequence[Surfaced], now: datetime) -> Dict[str, object]:
    """The fold behind ``channel_ceiling``: ``level``, and while a demotion's
    probation runs, ``restore_to`` (the level it may climb back to) and
    ``probation_ends``. Pure."""
    if category == "security":
        return {"level": "note", "restore_to": None, "probation_ends": None}
    start = LADDER.index("push") if category in FLOOR_CATEGORIES else LADDER.index("note")
    level = start
    ignored = dismissed = 0
    restore_to: Optional[int] = None         # the level a quiet week climbs back toward
    quiet_since: Optional[datetime] = None   # demoted, with no negative since

    def probation(t: datetime) -> None:
        nonlocal level, quiet_since, restore_to
        while quiet_since is not None and restore_to is not None and level < restore_to \
                and t - quiet_since >= PROBATION:
            level += 1
            quiet_since = quiet_since + PROBATION
        if restore_to is None or level >= restore_to:
            quiet_since = restore_to = None

    def demote(to: int, at: datetime) -> None:
        nonlocal level, quiet_since, restore_to
        restore_to = max(restore_to if restore_to is not None else level, level)
        level = to
        quiet_since = at

    items = sorted((h for h in history if h.category == category and h.outcome), key=lambda h: h.at)
    for h in items:
        probation(h.at)
        if h.outcome == "rejected":
            demote(max(0, level - 1), h.at)
            ignored = dismissed = 0
        elif h.outcome == "ignored":
            if h.channel == "push":
                ignored += 1
                if ignored >= 2 and level >= LADDER.index("push"):
                    demote(LADDER.index("note"), h.at)
                    ignored = 0
        elif h.outcome == "dismissed":
            dismissed += 1
            if dismissed >= 2 and level == LADDER.index("note"):
                demote(LADDER.index("journal"), h.at)
                dismissed = 0
        elif h.outcome in ("opened", "acted"):
            level = min(LADDER.index("push"), level + 1)
            ignored = dismissed = 0
            if restore_to is not None and level >= restore_to:
                quiet_since = restore_to = None
    probation(now)
    ends = (quiet_since + PROBATION) if (quiet_since is not None and restore_to is not None) else None
    return {"level": LADDER[level], "restore_to": LADDER[restore_to] if restore_to is not None else None,
            "probation_ends": ends}


def channel_ceiling(category: Optional[str], history: Sequence[Surfaced], now: datetime) -> str:
    """The highest channel a category has earned: a fold over its outcomes,
    oldest first. Travel and household start at push, security is pinned at
    note, everything else starts at note. A rejection lowers one level; two
    ignored pushes in a row lower push to note (ignored notes never demote —
    Today is read once a day); two dismissed notes in a row lower note to
    journal; opened or acted raises one level (max push) and resets the
    counts; seen changes nothing; each seven days at a demoted level with no
    further negative raises it one level back toward where it was. Pure."""
    return str(ladder_state(category, history, now)["level"])


_CAT = r"(" + "|".join(CATEGORIES) + r")"
_ANY = r"(?:anything\s+|any\s+|all\s+|the\s+)?"
_ABOUT = r"(?:about\s+|for\s+|on\s+|when\s+)?"
_OWNER_JOURNAL = (re.compile(rf"\bleave\s+(?:me\s+)?{_ANY}{_CAT}(?:\s+stuff|\s+things)?\s+alone"),
                re.compile(rf"\bstop\s+telling\s+me\s+{_ABOUT}{_ANY}{_CAT}"))
_OWNER_NOTE = (re.compile(rf"\b(?:don'?t|do\s+not|stop|no\s+more)\s+(?:push|ping|text)(?:ing)?\s+(?:me\s+)?"
                        rf"{_ABOUT}{_ANY}{_CAT}"),)
_OWNER_PUSH = (re.compile(rf"\b(?:push|ping|text)\s+me\s+{_ABOUT}{_ANY}{_CAT}"),)


def ceilings_from_owner(lines: Iterable[str]) -> Dict[str, str]:
    """The owner's own words about how to reach them, in a narrow grammar: a
    category after "push/ping/text me (about)" → push; after "don't / stop /
    no more push(ing) me (about)" → note; "leave <category> alone" or "stop
    telling me about <category>" → journal. Later lines win. Pure."""
    out: Dict[str, str] = {}
    for raw in lines or ():
        line = str(raw or "").lower().replace("’", "'")
        this: Dict[str, str] = {}
        for level, patterns in (("journal", _OWNER_JOURNAL), ("note", _OWNER_NOTE), ("push", _OWNER_PUSH)):
            for pat in patterns:
                for m in pat.finditer(line):
                    this.setdefault(m.group(1), level)
        out.update(this)
    return out


# ---------------------------------------------------------------- the gate --

# Reasons and advice the speaker reads (model-facing).
_OWNER = owner_text("{OWNER}")
_OWNERS = owner_text("{OWNER}'s")
_ASKED_NOT_TO_HEAR = owner_text("{OWNER} asked not to hear about this")

def _d(channel: str, reason: str, floor: bool, hold_until: Optional[datetime] = None,
       also: Tuple[str, ...] = (), advice: str = "") -> Decision:
    return Decision(channel=channel, hold_until=hold_until, reason=reason, floor_push=floor, also=also,
                    advice=advice)


def decide(n: Noticing, ctx: GateContext) -> Decision:
    """Where this noticing goes now. First match wins. Pure."""
    now = ctx.now
    floor = is_floor_push(n, now)
    chat = chat_key(n.chat)

    # R0 / R0b: the spend cap. A floor item still reaches the owner, templated.
    if ctx.quota == "exhausted":
        if floor and may_push_at(n, now):
            return _d("push", "fallback: spend cap", floor)
        return _d("journal", "spend cap", floor)
    if ctx.quota == "paced" and not floor:
        return _d("journal", "paced", floor)

    # R1: evidence.
    refs = {s.ref for s in n.sources if s.ref}
    if not refs or n.kind not in KINDS:
        return _d("dropped", "no valid source", floor)
    if n.kind == "conflict" and len(refs) < 2:
        return _d("dropped", "a conflict needs two sources", floor)

    # R2 / R2b: the chat said stay out; a ping is being answered there.
    if chat and chat in _keys(ctx.stood_down):
        return _d("silent", "stand-down", floor)
    if chat and chat in _keys(ctx.busy_chats):
        return _d("hold", "a ping is open there", floor, now + timedelta(minutes=PING_OPEN_HOLD_MIN))

    # R3: too late.
    if n.closes_at is not None and n.closes_at <= now:
        return _d("silent", "too late", floor)

    inbound = [s for s in n.sources if not s.from_me]
    newest_inbound = max((s.at for s in inbound), default=None)
    newest = max(s.at for s in n.sources)

    # R4: the owner is on it — they wrote in that chat after the newest
    # inbound line, or within ten minutes before it.
    own_security = n.kind == "security" and any(s.from_me for s in n.sources)
    if newest_inbound is not None and not own_security and n.kind != "follow_through":
        last = _get(ctx.owner_last_line, n.chat)
        if isinstance(last, datetime) and last >= newest_inbound - timedelta(minutes=OWNER_ON_IT_MIN):
            return _d("silent", f"{_OWNER} replied there at {_clock(last)}", floor)

    # R5: let the read state settle (the median read is 2.6 min).
    if (n.kind != "security" and (n.closes_at is None or n.closes_at > now + timedelta(minutes=URGENT_MIN))
            and now - newest < timedelta(minutes=SETTLE_MIN)):
        return _d("hold", "settling", floor, newest + timedelta(minutes=SETTLE_MIN))

    # R6: per kind.
    cap: Optional[str] = None
    exact: Optional[str] = None
    if n.kind == "unanswered":
        if chat and chat in _keys(ctx.live_chats):
            return _d("silent", "you're talking there", floor)
        ref_time = newest_inbound or newest
        if now - ref_time < timedelta(hours=UNANSWERED_MIN_AGE_H):
            return _d("hold", "give them time to answer", floor, ref_time + timedelta(hours=UNANSWERED_MIN_AGE_H))
        cap = "note"
    elif n.kind == "household" and n.origin == "noticer" and n.closes_at is None:
        return _d("silent", "no deadline: a relay", floor)
    elif n.kind in ("deadline", "conflict") and n.closes_at is None:
        cap = "note"
    elif n.kind == "security":
        exact = "note"

    # R7: not yet.
    if exact is None and n.closes_at is not None and n.closes_at - now > lead_for(n):
        return _d("hold", "not yet", floor, n.closes_at - lead_for(n))

    # R8: one follow-up.
    window = now - timedelta(hours=FOLLOW_UP_WINDOW_H)
    for s in ctx.history:
        if s.at < window:
            continue
        same = bool(n.dedupe_key) and s.dedupe_key == n.dedupe_key
        overlap = (chat and chat_key(s.chat) == chat and s.kind == n.kind and bool(set(s.refs) & refs))
        if (same or overlap) and refs <= set(s.refs):
            return _d("silent", f"said already at {_clock(s.at)}", floor)

    # R9: the channel (the owner's words bind; the ladder advises).
    advice = ""
    suggested = (n.suggested or "none").strip().lower()
    ch = "note" if suggested in ("none", "", "null") else suggested
    if ch not in RANK or RANK[ch] < 0:
        ch = "note"
    if exact is not None:
        ch = exact
    else:
        if ch == "thread" and not (n.kind == "follow_through" and chat and chat in _keys(ctx.open_ping_chats)):
            ch = "push"
        if ch == "ambient" and not (_is_household(n) and not n.sensitive):
            ch = "note"
        if ch != "thread":
            # The owner's own words bind ("don't push me about work"). What
            # the ladder learned from being ignored is advice for the
            # speaker, never a block (design decision, 2026-10-11).
            said = ctx.ceilings_from_owner.get(n.category or "")
            if said:
                ch = _min(ch, said)
            earned = channel_ceiling(n.category, ctx.history, now)
            if RANK.get(earned, 1) < RANK.get(ch, 1):
                advice = (f"{_OWNERS} answers to {n.category or 'this kind of thing'} lately put it at {earned}, "
                          f"not {ch}: weigh that before you speak")
        if cap is not None:
            ch = _min(ch, cap)
    if RANK.get(ch, 1) <= RANK["journal"]:
        return _d("journal", _ASKED_NOT_TO_HEAR, floor)

    # R10: precision first — in week one only floor items push.
    if ctx.push_scope == "floor" and ch == "push" and not floor:
        return _d("note", "push scope is floor", floor, advice=advice)

    # R11: quiet hours.
    if ch == "push" and _in_quiet(now):
        seven = _next_seven(now)
        if floor and may_push_at(n, now) and n.closes_at is not None and n.closes_at <= seven:
            pass
        elif n.closes_at is not None and n.closes_at > seven + timedelta(minutes=30):
            return _d("hold", "quiet hours", floor, seven, advice=advice)
        else:
            also = ("ambient",) if _is_household(n) and not n.sensitive else ()
            return _d("note", "quiet hours", floor, also=also, advice=advice)

    # R12: the push budget.
    if ch == "push" and ctx.push_budget is not None and ctx.pushes_today >= ctx.push_budget:
        also = ("ambient",) if _is_household(n) and not n.sensitive else ()
        return _d("note", f"push budget ({ctx.pushes_today} of {ctx.push_budget} used)", floor, also=also,
                  advice=advice)

    # R13: the speak budget, and one speak per chat an hour.
    if ch in ("note", "push", "thread"):
        per_chat = _get(ctx.speaks_by_chat_1h, n.chat) or 0
        if ctx.speaks_today >= ctx.speak_budget or (chat and int(per_chat) >= SPEAKS_PER_CHAT_PER_HOUR):
            if floor and ch == "push" and may_push_at(n, now):
                return _d("push", "fallback: speak budget", floor, advice=advice)
            return _d("journal", "speak budget", floor, advice=advice)

    # R14.
    return _d(ch, "gate", floor, advice=advice)
