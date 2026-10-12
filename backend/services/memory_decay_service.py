"""Forgetting for Edward's long-term memory.

Every other subsystem here decays — ``decay_stale_links``,
``decay_stale_weak_claims``, ``decay_stale_alignments``,
``decay_stale_negotiations``. Memory never did. Nothing left the memories
table except via consolidation's dedup merge or an explicit
``remember_forget``, which is why 36% of the live store had never been
retrieved once and why a note about someone being at the gym was still filed
as ``timeless`` weeks later.

A store that only grows is not a memory, it is a log. The retrieval side then
pays for it: every junk row is another candidate competing with the memories
that matter.

The model borrows the productive parts of human forgetting:

* **Situational things fade fastest.** ``temporal_nature`` sets the base
  half-life. A dated appointment is not a fact about a person.
* **Use protects, up to a point.** A memory placed in front of the model
  lives longer — but at most ``MAX_USE_MULTIPLIER`` times longer, and use
  never resets the clock: age runs from creation (2026-09-27, below).
* **Being told beats inferring.** ``user_stated`` outlives ``inferred`` — but
  by less when the memory is temporary, because the owner saying "Monday works
  for the dentist" does not make that durable.
* **Consolidation makes memories sticky.** The tier ladder multiplies life.
* **Nothing is forgotten instantly.** Under ``MIN_AGE_DAYS`` a memory is
  untouchable however unpromising it looks.

Retirement stamps ``valid_until``; it never DELETEs. ``retrieve_memories``
already filters on that column, so a retired memory stops surfacing while
staying auditable and revivable with a single UPDATE.

Some things are never forgotten automatically. Instructions are standing
directives — silently dropping one changes Edward's behaviour with no trace
(a one-off that is not the owner's own lapses after
``INSTRUCTION_HORIZON_DAYS``; ``EDWARD_INSTRUCTION_HORIZON_SINCE`` can exempt
an older backlog). Sensitive content — pets, health, family, money — is the
owner's call. Knowledge-tier memories have already proven themselves (unless
what they say was temporary, a dated episode or a reminder echo). And a
durable fact the owner stated outright should survive a long silence; that is
the difference between forgetting and losing something.

**Retrieval is not evidence (2026-09-27).** The memory audit replayed this
pass read-only against the live store and it retired 0 rows. The 100
most-retrieved rows took 70% of all retrievals, 84 of them from before
Aug 15 — a July reminder note retrieved 2,759 times among them. Every
memory placed in the prompt is stamped "used" (``mark_memories_used``),
so the rows retrieval favours had their clock reset on every turn and
their half-life multiplied by 1 + log1p(used) without bound: the more a
stale row was retrieved, the harder it was to forget. Nothing records
that a memory was actually *cited*, only that retrieval returned it, so
age now runs from creation and use lengthens the half-life by at most
``MAX_USE_MULTIPLIER``.
"""

from __future__ import annotations

import functools
import math
import os
import re
from datetime import datetime, timezone
from typing import Any, Optional, Collection

from services.owner import household, owner_name_pattern, owner_names

# Base shelf life by temporal_nature, in days. Ordered on purpose: this is the
# primary signal of how long something stays true.
BASE_HALF_LIFE_DAYS: dict[str, float] = {
    "temporary": 14.0,
    "evolving": 90.0,
    "timeless": 365.0,
}

# Retire below this retention value.
RETENTION_FLOOR = 0.25

# Grace period. Nothing is retired before it has had a chance to be useful.
MIN_AGE_DAYS = 7

# How much harder a stated memory is to forget than an inferred one. Smaller
# for temporary memories — stating a date does not make it durable.
SOURCE_MULTIPLIER: dict[str, dict[str, float]] = {
    "user_stated": {"temporary": 1.5, "evolving": 2.0, "timeless": 3.0},
    "tool_derived": {"temporary": 1.2, "evolving": 1.4, "timeless": 1.5},
    "imported": {"temporary": 1.2, "evolving": 1.4, "timeless": 1.5},
    "inferred": {"temporary": 1.0, "evolving": 1.0, "timeless": 1.0},
}

TIER_MULTIPLIER: dict[str, float] = {
    "observation": 1.0,
    "belief": 2.0,
    "knowledge": 4.0,
}

# Memory types that are never auto-retired — except a one-off
# (``temporary``) instruction that is not the owner's own, which lapses
# ``INSTRUCTION_HORIZON_DAYS`` after it was written (2026-09-27). In one
# store 38 instructions were temporary with no horizon, mostly reminder
# echoes ("For <contact>'s message, Edward should only send the owner an
# in-app chat reminder…", 809 retrievals); two blanket "in-app only, never
# contact anyone" rules came from reminder turns too, because extraction
# read Edward's own reminder trigger as the owner speaking. The owner's own
# — confirmed by them (``source_ref`` kind ``owner_confirmed``, what a
# memory audit writes for the rules the owner approves) or heard in their
# own chat — never lapses.
#
# The horizon covers instructions written from ``INSTRUCTION_HORIZON_SINCE``
# on. A fresh install has no backlog, so the default is every instruction.
# An install migrated from an older Edward may have a backlog of
# instructions written before provenance was recorded: an owner's own
# instruction said over iMessage was backfilled ``read``, so not theirs by
# provenance, and the horizon would retire it unasked. Such an install sets
# ``EDWARD_INSTRUCTION_HORIZON_SINCE`` (an ISO date, e.g. ``2026-09-28``) to
# the day it upgraded, reviews the older instructions by hand and stamps the
# ones the owner keeps ``owner_confirmed``.
PROTECTED_TYPES = frozenset({"instruction"})
INSTRUCTION_HORIZON_DAYS = 30
# "Every instruction": far enough back that nothing was written before it.
DEFAULT_INSTRUCTION_HORIZON_SINCE = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _instruction_horizon_since() -> datetime:
    """``EDWARD_INSTRUCTION_HORIZON_SINCE`` as an aware UTC datetime, else
    the default (every instruction). A value that is no date is ignored."""
    raw = (os.getenv("EDWARD_INSTRUCTION_HORIZON_SINCE") or "").strip()
    if not raw:
        return DEFAULT_INSTRUCTION_HORIZON_SINCE
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        print(f"[decay] EDWARD_INSTRUCTION_HORIZON_SINCE={raw!r} is not an ISO date; "
              "the horizon covers every instruction")
        return DEFAULT_INSTRUCTION_HORIZON_SINCE
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


INSTRUCTION_HORIZON_SINCE = _instruction_horizon_since()
OWNER_CONFIRMED_KIND = "owner_confirmed"
# Turn sources where the speaker is the owner (``owner_ping``: their own
# ``@Edward`` text from iMessage; ``ping_owner``: their self-chat ping turn;
# 2026-10-11 — memory_service.OWNER_SPEAKS_SOURCES, pinned equal by a test).
OWNERS_TURN_SOURCES = frozenset({"chat", "voice", "widget", "alexa", "ping_owner", "owner_ping"})
# Turns that are never the owner's own word, even in a conversation of
# theirs: the household hub's live voice runs in a ``user`` conversation (it
# shows in the owner's sidebar), but whoever is at the panel is not
# necessarily the owner (2026-10-11).
NOT_OWNERS_TURN_SOURCES = frozenset({"hub_voice"})

# Sensitive content is never auto-retired (2026-09-27; a memory audit's
# first principle): pets and their meds, health, family, money — and
# someone in distress ("#sad", "I'm struggling", "I need a friend"), which
# the audit held for the owner too. A wrong forget costs most here, and
# retiring one is the owner's line-item call. Word ends on the short words
# so "moment", "pillow" and "veteran" are not family, pills and vets.
# Matching errs toward keeping — but a match that never ends is a row
# never retired, so the review (2026-09-27) took out what matched
# chatter: "motherboard", "diagnostics", and the bare affect words
# ("<a friend> was worried they could become a scapegoat", "in BIOS and
# needed help", "a pain in the neck"). Pain is kept where it is a body's
# ("her ear hurts", "in a lot of pain", a migraine).
#
# The words are generic; the names are the household's own: pets, family
# and anyone else whose mentions should be held, from
# ``EDWARD_SENSITIVE_NAMES`` (comma-separated, e.g. ``Biscuit,Maple,Grandma
# June``) plus the people in ``EDWARD_HOUSEHOLD`` other than the owner
# (``_sensitive_names_re``). The owner's own name is never one: nearly every
# row names them.
_SENSITIVE_RE = re.compile(
    r"\b(?:vets?\b|veterinar\w*|medications?\b|"
    r"meds\b|medicine\w*|dos(?:e|es|ed|ing)\b|pills?\b|pilled|pilling|insulin|surger\w*|renal|"
    r"carprofen|steroids?\b|allerg\w*|diagnos(?:is|es|ed)\b|hospital\w*|doctors?\b|"
    r"(?:ear|eye|nose|nasal) drops\b|physician\w*|clinic\w*|pregnan\w*|grandm\w*|grandpa\w*|grandfather\w*|grandparent\w*|"
    r"mothers?\b|fathers?\b|sisters?\b|brothers?\b|moms?\b|dads?\b|parents?\b|funeral\w*|therap\w*|"
    r"prescri\w*|illness\w*|sick\w*|injur\w*|insomnia|depress\w*|anxiety|panic|sober\w*|"
    r"sobriety|rehab\w*|vulnerable|bloodwork|mri\b|zelle|loans?\b|debts?\b|rent\b|salary|"
    r"bonus\w*|layoff\w*|sad\b|struggl\w*|crying|cried|lonely|needs? a friend|needed a friend|"
    r"not doing well|headaches?\b|migraines?\b|in (?:so much |a lot of )?pain\b|"
    r"(?:ears?|head|back|stomach|tummy|tooth|teeth|chest|knees?|hips?|neck|throat|legs?|feet|foot|arms?|"
    r"shoulders?|wrists?|ankles?)\s+(?:hurts?|hurting|aches?|aching|pain)\b)",
    re.IGNORECASE,
)


def sensitive_names() -> tuple:
    """The names whose mention holds a row: ``EDWARD_SENSITIVE_NAMES`` plus
    the household (``EDWARD_HOUSEHOLD``) other than the owner. Read per call."""
    raw = os.getenv("EDWARD_SENSITIVE_NAMES") or ""
    names = [n.strip() for n in raw.split(",") if n.strip()]
    if (os.getenv("EDWARD_HOUSEHOLD") or "").strip():
        own = owner_names()
        names += [p for p in household() if p.strip().lower() not in own]
    seen, out = set(), []
    for n in names:
        if n.lower() not in seen:
            seen.add(n.lower())
            out.append(n)
    return tuple(out)


@functools.lru_cache(maxsize=8)
def _compile_names(names: tuple) -> Optional[re.Pattern]:
    if not names:
        return None
    alternation = "|".join(re.escape(n) for n in sorted(names, key=len, reverse=True))
    return re.compile(r"(?<!\w)(?:" + alternation + r")(?!\w)", re.IGNORECASE)


def _sensitive_names_re() -> Optional[re.Pattern]:
    return _compile_names(sensitive_names())


def is_sensitive(content: Optional[str]) -> bool:
    """Pets, health, family or money in the text, or a name the household
    holds (``sensitive_names``). Pure but for the environment."""
    content = content or ""
    if _SENSITIVE_RE.search(content):
        return True
    names_re = _sensitive_names_re()
    return bool(names_re and names_re.search(content))


# Whose record the hold keeps (2026-09-27 review): what the owner said
# (``heard``, the default) or what Edward read about the owner's world
# (``read``) — the owner's data, which the audit's rule is about. Edward's
# own ``thought`` and ``did`` rows, and every intention, are Edward's notes
# to himself and decay as Phase 9 designed: held for every row, the hold
# made 34 of one store's 177 thought/did/intention rows permanent, and an
# intention that mentions a dose never aged out once its event had fired. An intention has its own
# hygiene: a pending event holds it (``protected_ids``), a horizon ends it.
_OWNERS_WORLD_ORIGINS = frozenset({"heard", "read"})


def _held_as_sensitive(memory: Any) -> bool:
    if getattr(memory, "memory_type", None) == "intention":
        return False
    if (getattr(memory, "origin", None) or "heard") not in _OWNERS_WORLD_ORIGINS:
        return False
    return is_sensitive(getattr(memory, "content", None))


# A dated episode or a reminder echo earns nothing from use (2026-09-27).
# Of the 100 most-retrieved rows in one store, 24 were dated July/August
# episodes ("On July 9, 2026, the owner was waiting for a friend to
# reply…", 1,997 retrievals) and 29 were reminder-shaped ("the owner needed
# an immediate in-app chat reminder to answer…"). Retrieval promoted them up the tier ladder
# and the knowledge tier was never retired. What such a row says is fixed
# in time — being retrieved again does not make it true again — so its
# tier and its use lengthen nothing, and knowledge does not hold it.
#
# An episode names an explicit date: "July 9, 2026", "July 10–12, 2026",
# "in August 2026", "2026-08-09". It must sit near the memory's creation
# (``EPISODE_WINDOW_DAYS``) — "born on March 5, 1990" is a fact, not an
# episode — and be at least ``EPISODE_PAST_DAYS`` behind us. Anything
# recurring or habitual is never an episode or an echo.
#
# A preference can be an episode (memory audit, preferences, 2026-09-27):
# "prefers" and "likes" were recurring words here, so "the owner prefers a
# low-interruption weekend on September 26–27, 2026" was never fixed in
# time and kept its tier and use credit for good. They now keep a row out
# of the episodes only when it is not ``temporary`` (``fixed_in_time``) —
# a lasting taste may cite a dated example: 7501df79, 41 times reinforced,
# "…as done on September 26, 2026" — and out of a reminder echo always
# (``_PREFERENCE_RE``): a taste about reminders is not a reminder's text.
EPISODE_WINDOW_DAYS = 45
EPISODE_PAST_DAYS = 14

_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
_MONTH = r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?"
_DAY_DATE_RE = re.compile(
    r"\b" + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?(?:\s*[-–]\s*(\d{1,2})(?:st|nd|rd|th)?)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
_MONTH_DATE_RE = re.compile(
    r"\b(?:in|during|as of|since|for|throughout)\s+(?:early |mid-|mid |late )?" + _MONTH + r"\s+(\d{4})\b",
    re.IGNORECASE,
)
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_RECURRING_RE = re.compile(
    r"\b(?:birthdays?|anniversar\w*|born|recurring|regularly|usually|always|annual\w*|daily|weekly|monthly|"
    r"every (?:day|week|month|year|morning|evening|night)|useful)\b",
    re.IGNORECASE,
)
_PREFERENCE_RE = re.compile(r"\b(?:prefers?|likes?)\b", re.IGNORECASE)
# "remind(ed) <the owner>": the owner's names come from the environment
# (``owner_name_pattern``), so the pattern is built on first use, per name.
_REMINDER_ECHO_SOURCE = (
    r"in[- ]app(?:/chat)?(?: chat)? (?:reminder|notification|alert)|in-app/chat|in-app only"
    r"|chat reminders? only|\breminders? (?:about|for|on|that)\b"
    r"|\bremind(?:ed)? (?:(?:the )?owner|{owner}|him|her|them)\b"
)


@functools.lru_cache(maxsize=8)
def _compile_reminder_echo(owner_pattern: str) -> re.Pattern:
    return re.compile(_REMINDER_ECHO_SOURCE.replace("{owner}", owner_pattern), re.IGNORECASE)


def _reminder_echo_re() -> re.Pattern:
    return _compile_reminder_echo(owner_name_pattern())


def _dates_named(content: str) -> list:
    """Every date the text names. One that is no date — "Jan 5, 0000"
    (``datetime`` has no year 0), "Feb 30" — is skipped: this runs for
    every live row inside one pass with no per-row guard, so a raise here
    stopped the whole pass every week."""
    import calendar

    out = []
    for m in _DAY_DATE_RE.finditer(content):
        month = _MONTHS.index(m.group(1).lower()[:3]) + 1
        year = int(m.group(4))
        day = int(m.group(3) or m.group(2))
        try:
            out.append(datetime(year, month, day, tzinfo=timezone.utc))
        except ValueError:
            pass
    for m in _MONTH_DATE_RE.finditer(content):
        month = _MONTHS.index(m.group(1).lower()[:3]) + 1
        year = int(m.group(2))
        try:
            out.append(datetime(year, month, calendar.monthrange(year, month)[1], tzinfo=timezone.utc))
        except ValueError:
            pass
    for m in _ISO_DATE_RE.finditer(content):
        try:
            out.append(datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc))
        except ValueError:
            pass
    return out


def episode_date(content: Optional[str], created_at: Optional[datetime],
                 now: Optional[datetime] = None) -> Optional[datetime]:
    """The latest date a dated episode names, when the memory is one;
    else None. Pure."""
    content = content or ""
    created = _aware(created_at)
    if created is None or _RECURRING_RE.search(content):
        return None
    now = _aware(now) or datetime.now(timezone.utc)
    near = [d for d in _dates_named(content) if abs((d - created).days) <= EPISODE_WINDOW_DAYS]
    if not near:
        return None
    latest = max(near)
    return latest if (now - latest).days >= EPISODE_PAST_DAYS else None


def is_reminder_echo(content: Optional[str]) -> bool:
    """A reminder's own text stored as a memory ("<owner> needed an in-app
    chat reminder to…", "For reminders about <a contact>'s texts…"), not a habit
    or preference about reminders. Pure."""
    content = content or ""
    return bool(_reminder_echo_re().search(content)) and not _RECURRING_RE.search(content) \
        and not _PREFERENCE_RE.search(content)


def fixed_in_time(memory: Any, now: Optional[datetime] = None) -> bool:
    """A dated episode or a reminder echo: its tier and use earn it
    nothing, and the knowledge tier does not hold it. A row that says
    "prefers" or "likes" is an episode only when it is ``temporary``: a
    lasting taste may cite a dated example."""
    content = getattr(memory, "content", None)
    if is_reminder_echo(content):
        return True
    if _PREFERENCE_RE.search(content or "") and \
            (getattr(memory, "temporal_nature", None) or "timeless") != "temporary":
        return False
    return episode_date(content, getattr(memory, "created_at", None), now) is not None

# The most use can lengthen a memory's half-life (2026-09-27). ``used_count``
# counts turns the memory was placed in the prompt — retrieval, not
# citation. The first use or two say it is reachable and relevant; past
# that the count mostly measures how often retrieval returns it, which is
# the loop that kept the stale core alive. 1 + log1p(used) reaches the cap
# at about two uses.
MAX_USE_MULTIPLIER = 2.0


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _decay_anchor(memory: Any) -> Optional[datetime]:
    """Where this memory's age is measured from: its creation.

    ``last_used_at`` and ``last_accessed`` used to reset the clock ("recall
    resets the clock"). Both are stamped by retrieval — placed in the
    prompt, returned by a query — so a row retrieval kept returning never
    aged at all. The later of creation and the last *cited* use would be
    right, and nothing records a citation yet.
    """
    return _aware(getattr(memory, "created_at", None))


def _source_ref(memory: Any) -> dict:
    import json

    raw = getattr(memory, "source_ref", None)
    if not raw:
        return {}
    try:
        ref = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return ref if isinstance(ref, dict) else {}


def owners_own_word(memory: Any, conversation_source: Optional[str] = None) -> bool:
    """Confirmed by the owner, or something the owner said in their own
    chat (a ``user`` conversation, or a chat/voice turn by its provenance).
    Pure.

    ``conversation_source`` is the source of the row's conversation; the
    decay pass's rows carry it from their join as an attribute. The one
    rule for "the owner's own word": the contradiction ranking asks the same
    (``consolidation_service.contradiction_priority``, 2026-09-27 review —
    it had counted any heard + user_stated row, the reminder echoes
    included). A row whose ref names a turn that is never the owner's
    (``NOT_OWNERS_TURN_SOURCES``: the household hub's voice) is not, whatever
    its conversation."""
    ref = _source_ref(memory)
    if ref.get("kind") == OWNER_CONFIRMED_KIND:
        return True
    if (getattr(memory, "origin", None) or "heard") != "heard":
        return False
    if ref.get("turn_source") in NOT_OWNERS_TURN_SOURCES:
        return False
    source = conversation_source or getattr(memory, "conversation_source", None)
    return source == "user" or ref.get("turn_source") in OWNERS_TURN_SOURCES


def owners_own_instruction(memory: Any) -> bool:
    """``owners_own_word`` for a decay-pass row. Pure."""
    return owners_own_word(memory)


def _one_off_instruction(memory: Any) -> bool:
    """A temporary instruction that is not the owner's own, written since
    the horizon began (``INSTRUCTION_HORIZON_SINCE``)."""
    created = _aware(getattr(memory, "created_at", None))
    return (getattr(memory, "memory_type", None) == "instruction"
            and (getattr(memory, "temporal_nature", None) or "timeless") == "temporary"
            and created is not None and created >= INSTRUCTION_HORIZON_SINCE
            and not owners_own_instruction(memory))


def effective_half_life_days(memory: Any, *, fixed: bool = False) -> float:
    """Days for this memory's retention to halve, after every protection.
    A memory ``fixed`` in time (``fixed_in_time``) gets no tier or use
    credit."""
    nature = getattr(memory, "temporal_nature", None) or "timeless"
    base = BASE_HALF_LIFE_DAYS.get(nature, BASE_HALF_LIFE_DAYS["timeless"])

    source = getattr(memory, "source_type", None) or "inferred"
    source_mult = SOURCE_MULTIPLIER.get(source, SOURCE_MULTIPLIER["inferred"]).get(
        nature, 1.0
    )

    importance = getattr(memory, "importance", None)
    importance = 0.5 if importance is None else float(importance)
    importance_mult = 0.5 + importance  # 0.5x at 0.0, 1.5x at 1.0

    tier = getattr(memory, "tier", None) or "observation"
    tier_mult = TIER_MULTIPLIER.get(tier, 1.0)

    # Use protects, with diminishing returns and a ceiling — the 1st and 2nd
    # use say a lot, the 40th says only that retrieval keeps returning it.
    used = getattr(memory, "used_count", None) or 0
    use_mult = min(1.0 + math.log1p(used), MAX_USE_MULTIPLIER)

    if fixed:
        tier_mult = use_mult = 1.0

    return base * source_mult * importance_mult * tier_mult * use_mult


def compute_retention(memory: Any, now: Optional[datetime] = None) -> float:
    """Retention in [0, 1] — 1.0 is fresh, 0.0 is fully faded."""
    now = _aware(now) or datetime.now(timezone.utc)

    anchor = _decay_anchor(memory)
    if anchor is None:
        return 0.0

    age_days = max((now - anchor).total_seconds() / 86400.0, 0.0)
    half_life = max(effective_half_life_days(memory, fixed=fixed_in_time(memory, now)), 0.1)

    return float(0.5 ** (age_days / half_life))


def is_protected(memory: Any, protected_ids: Optional[Collection[str]] = None,
                 now: Optional[datetime] = None) -> bool:
    """True when this memory is never auto-retired, at any age.

    Sensitive content (``is_sensitive``) in a row that records the owner's
    world is held first: retiring it is the owner's call
    (``_held_as_sensitive`` — never Edward's own thoughts, actions or
    intentions). ``protected_ids`` are the intention memories a pending
    scheduled event still points at (``pending_intention_ids``): an intention Edward is
    waiting to act on holds while the event is live and ages out like
    anything else once it has fired (Phase 9). The knowledge tier holds
    everything but a dated episode or a reminder echo (``fixed_in_time``),
    which retrieval promoted there, and a ``temporary`` row (memory audit,
    preferences, 2026-09-27: 36 live temporary rows sat on knowledge for
    good; the tier still lengthens their half-life). Instructions are held
    except a one-off that is not the owner's own, which lapses on its horizon
    (``retire_reason``).
    """
    if _held_as_sensitive(memory):
        return True
    if getattr(memory, "memory_type", None) in PROTECTED_TYPES and not _one_off_instruction(memory):
        return True
    if getattr(memory, "memory_type", None) == "intention" and protected_ids and \
            str(getattr(memory, "id", "")) in protected_ids:
        return True

    nature = getattr(memory, "temporal_nature", None) or "timeless"
    if (getattr(memory, "tier", None) or "observation") == "knowledge" and nature != "temporary" \
            and not fixed_in_time(memory, now):
        return True

    stated = (getattr(memory, "source_type", None) or "inferred") == "user_stated"
    timeless = nature == "timeless"
    if stated and timeless:
        return True

    return False


def retire_reason(memory: Any, now: Optional[datetime] = None,
                  protected_ids: Optional[Collection[str]] = None) -> Optional[str]:
    """Why the forgetting pass would stamp ``valid_until`` on this row —
    ``instruction_horizon`` or ``curve`` — or None to keep it."""
    now = _aware(now) or datetime.now(timezone.utc)

    if getattr(memory, "valid_until", None) is not None:
        return None  # already retired

    if is_protected(memory, protected_ids, now=now):
        return None

    created = _aware(getattr(memory, "created_at", None))
    if created is None:
        return None
    age_days = (now - created).total_seconds() / 86400.0

    if _one_off_instruction(memory):
        return "instruction_horizon" if age_days >= INSTRUCTION_HORIZON_DAYS else None

    if age_days < MIN_AGE_DAYS:
        return None

    return "curve" if compute_retention(memory, now=now) < RETENTION_FLOOR else None


def should_retire(memory: Any, now: Optional[datetime] = None,
                  protected_ids: Optional[Collection[str]] = None) -> bool:
    """Whether the forgetting pass should stamp ``valid_until`` on this row."""
    return retire_reason(memory, now=now, protected_ids=protected_ids) is not None


async def pending_intention_ids() -> set[str]:
    """Intention memories a pending or in-flight scheduled event links to."""
    from sqlalchemy import select

    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import linked_memory_id

    async with async_session() as session:
        rows = (await session.execute(
            select(ScheduledEventModel.event_metadata).where(
                ScheduledEventModel.status.in_(("pending", "processing")),
                ScheduledEventModel.event_metadata.isnot(None),
            )
        )).scalars().all()
    out: set[str] = set()
    for meta in rows:
        mid = linked_memory_id(meta)
        if mid:
            out.add(mid)
    return out


async def decay_stale_memories(
    dry_run: bool = True,
    limit: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict:
    """Run the forgetting pass over live memories.

    Defaults to ``dry_run=True``. Forgetting is the one memory operation whose
    mistakes are invisible — a memory that should have been kept simply stops
    appearing, with nothing to notice. So the caller opts in to writing.

    Returns a summary: counts scanned/retired and a breakdown by
    (temporal_nature, source_type) for eyeballing before committing, plus
    ``retired_ids`` and ``stamp`` — the one ``valid_until`` every row this
    pass retired carries (None when nothing was written).
    """
    from sqlalchemy import text

    from services.database import async_session

    now = _aware(now) or datetime.now(timezone.utc)

    async with async_session() as session:
        rows = (
            await session.execute(
                text(
                    # Where each row came from decides whether a one-off
                    # instruction is the owner's own (``owners_own_instruction``).
                    "SELECT m.id, m.content, m.memory_type, m.temporal_nature, m.source_type, "
                    "m.importance, m.tier, m.used_count, m.access_count, m.reinforcement_count, "
                    "m.created_at, m.last_accessed, m.last_used_at, m.valid_until, "
                    "m.origin, m.about, m.source_ref, c.source AS conversation_source "
                    "FROM memories m "
                    "LEFT JOIN conversations c ON c.id = m.source_conversation_id "
                    "WHERE m.valid_until IS NULL "
                    "ORDER BY m.created_at"
                )
            )
        ).fetchall()

    try:
        protected_ids = await pending_intention_ids()
    except Exception as exc:  # noqa: BLE001
        print(f"[decay] could not read pending intentions; holding none: {exc}")
        protected_ids = set()
    reasons = {r.id: retire_reason(r, now=now, protected_ids=protected_ids) for r in rows}
    doomed = [r for r in rows if reasons[r.id]]
    if limit is not None:
        doomed = doomed[:limit]

    breakdown: dict[str, int] = {}
    by_rule: dict[str, int] = {}
    for row in doomed:
        key = f"{row.temporal_nature or 'timeless'}/{row.source_type or 'inferred'}"
        breakdown[key] = breakdown.get(key, 0) + 1
        by_rule[reasons[row.id]] = by_rule.get(reasons[row.id], 0) + 1

    # Every row this pass retires gets the same ``valid_until`` — the
    # stamp. The caller records it (the reflect wake's ledger row carried
    # only counts, 2026-09-27 review), so a wrong forget is found and
    # reverted by that one value, never by hunting for a timestamp.
    retired_ids: list = []
    stamp = None
    if not dry_run and doomed:
        stamp = now.replace(tzinfo=None)
        async with async_session() as session:
            result = await session.execute(
                text(
                    "UPDATE memories SET valid_until = :now "
                    "WHERE id = ANY(:ids) AND valid_until IS NULL "
                    "RETURNING id"
                ),
                {"ids": [r.id for r in doomed], "now": stamp},
            )
            retired_ids = [row[0] for row in result.fetchall()]
            await session.commit()

    return {
        "scanned": len(rows),
        "candidates": len(doomed),
        "retired": len(retired_ids),
        "retired_ids": retired_ids,
        "stamp": stamp.isoformat() if retired_ids else None,
        "dry_run": dry_run,
        "breakdown": breakdown,
        "by_rule": by_rule,
        "sample": [r.content[:100] for r in doomed[:10]],
    }
