"""``tell_owner`` — the one way Edward reaches the owner (2026-10-11).

Why (docs/architecture/push-and-notes.md, noticing.md): twelve code paths
used to ring the owner's phone and none of them could see what the others
had said; none of the 33 pushes in the evidence window was opened. Every
reach now goes through this door, and the rules that make it bearable are
code, not prompt:

* **where it came from** (the origin table, ``RULES``): the owner's own
  asks, ping-failure notices and care alerts are exempt from the budget;
  Edward's own turns are linted and counted; the surfaces pulse cannot
  reach the owner;
* **the egress secret scan**: a model may never put a secret on the lock
  screen (refused); a templated notice has it redacted;
* **the high-risk gate** (``push_safety_gate``) on urgent autonomous reaches;
* **the offer grammar** (``offer_grammar.lint``) for Edward's own words;
* **one follow-up**: the same thing twice in 24 h is refused to a model and
  skipped for code;
* **the push budget, only when one is set** (design decision, 2026-10-11:
  "push, with no limit") — by default there is no daily count;
  ``EDWARD_PUSH_BUDGET=<n>`` caps Edward's counted pushes of a local day;
* **quiet hours** (``EDWARD_QUIET_HOURS`` in the owner's zone, else 22:00–07:00)
  for the origins that hold them;
* **speak can only lower**: inside ``speak_scope`` the gate's channel is the
  ceiling.

A budget or quiet-hours downgrade is a success with the reason ("Left it as
a note — quiet hours (10 pm–7 am)…"). A refusal is a ``Tool error:`` string
the model can act on. It raises only when nothing at all could be delivered.
Every call writes one ``ledger_events`` row (``kind="reach"``).
"""

from __future__ import annotations

import contextvars
import json
import logging
import os
import re
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional, Sequence, Tuple

from services.owner import owner_text

logger = logging.getLogger(__name__)

PUSH_BUDGET_ENV = "EDWARD_PUSH_BUDGET"
UNLIMITED = ("", "unlimited", "none", "off", "no", "false", "-1")
FOLLOW_UP_WINDOW_H = 24
NIGHTLY_WINDOW_H = 12
SOURCE_PREFIX = "reach:"
# Pushes that never count against the budget (push_log.source values).
EXEMPT_SOURCES = ("reach:owner", "reach:ping:owner", "reach:ping:other", "reach:care")
# The owner's own turns, their self-chat included (``toolset.policy.OWNER_SOURCES``).
OWNER_TURN_SOURCES = ("chat", "voice", "widget", "alexa", "ping_owner")
# The household hub's live voice (``toolset.policy.HUB_VOICE_SOURCE``): its
# own origin, ``hub`` — never the owner's.
HUB_TURN_SOURCE = "hub_voice"
SENSITIVE_CATEGORIES = frozenset({"health", "family"})
CHANNEL_ORDER = {"journal": 0, "silent": 0, "note": 1, "ambient": 2, "push": 3, "thread": 3}

# What ``tell_owner`` returns on a delivered push, and when ``note_fallback``
# is off and the push did not land (care_watch reads both; its
# ``PUSHED_PREFIX`` is rendered from the same text).
PUSHED = owner_text("Pushed to {OWNER}'s phone")
NOT_DELIVERED = "Not delivered"

# What the model reads back (rendered once at import). ``{risk}`` and
# ``{clock}`` are filled per call with ``str.format``; the owner's name is
# already in by then.
REFUSE_NO_REACH = owner_text("Tool error: this turn can't reach {OWNER}")
REFUSE_SECRET = owner_text("Tool error: that would put a secret on {OWNER}'s lock screen — say it without the value.")
REFUSE_EMPTY = owner_text("Tool error: nothing to tell {OWNER} — say what you saw and one question.")
REFUSE_HIGH_RISK = owner_text("Tool error: this push would carry high-risk content ({risk}); a phone banner is the "
                              "wrong place for it. Reply directly to the person, or leave {OWNER} a note, instead.")
REFUSE_REPEAT = owner_text("Tool error: you told {OWNER} this at {clock} — once is enough unless something new "
                           "happened.")
REFUSE_NIGHTLY = owner_text("Tool error: you've already left {OWNER} tonight's one note — put the rest in your "
                            "closing line for the journal.")
ALREADY_TOLD = owner_text("Already told {OWNER} at {clock} — skipped the repeat.")
LEFT_A_NOTE = owner_text("Left {OWNER} a note on Today.")


# ------------------------------------------------------------- origins --

@dataclass(frozen=True)
class Rule:
    """One row of the origin table."""
    lint: bool                  # Edward's own words: the offer grammar applies
    counts: bool                # against the push budget
    quiet: str                  # "exempt" | "note" (urgent → note in quiet hours) | "gate" (trust the caller)
    may_push: bool = True
    code: bool = False          # templated by code: secrets redacted, repeats skipped silently
    refuse: bool = False        # this turn cannot reach the owner at all
    dedupe: bool = True         # one follow-up per thing
    notes_per_night: Optional[int] = None


MODEL_RULE = Rule(lint=True, counts=True, quiet="note")
RULES: Dict[str, Rule] = {
    "owner": Rule(lint=False, counts=False, quiet="exempt", dedupe=False),
    # Deduped by their callers (review 2026-10-11): the ping runner throttles
    # each notice per hour, and every failed ping is its own thing; care_watch
    # keys each dose and must be able to retry a push that did not land.
    "ping:owner": Rule(lint=False, counts=False, quiet="exempt", code=True, dedupe=False),
    "ping:other": Rule(lint=False, counts=False, quiet="note", code=True, dedupe=False),
    "care": Rule(lint=False, counts=False, quiet="exempt", code=True, dedupe=False),
    "fallback": Rule(lint=False, counts=True, quiet="gate", code=True),
    "speak": Rule(lint=True, counts=True, quiet="gate"),
    "self": MODEL_RULE,
    "manual": MODEL_RULE,
    "mention": MODEL_RULE,
    "scheduled_event": MODEL_RULE,
    # A friend's ping turn (a third party asked): Edward's own words, counted.
    "ping": MODEL_RULE,
    "worker": MODEL_RULE,
    # The household hub's voice (``hub_voice``, 2026-10-11): someone in the
    # owner's home, not necessarily the owner, relaying what matters ("Rowan
    # says Biscuit threw up"). It may not text the owner, so this is how it
    # reaches them: their words, not Edward's offer (no lint), but a reach
    # the owner did not ask for — counted, held to a note overnight, said
    # once, never a secret.
    "hub": Rule(lint=False, counts=True, quiet="note"),
    "nightly": Rule(lint=True, counts=True, quiet="note", may_push=False, notes_per_night=1),
    # Free time (design decision, 2026-10-11: "no push, no send"): what it
    # makes reaches the owner through the next wake, never by itself.
    "free": Rule(lint=False, counts=False, quiet="exempt", may_push=False, refuse=True),
    "surfaces": Rule(lint=False, counts=False, quiet="exempt", may_push=False, refuse=True),
    "other": Rule(lint=False, counts=False, quiet="exempt", may_push=False, refuse=True),
}
_MIND_REASON = re.compile(r"[a-z_]{1,24}")


def rule_for(origin: str) -> Rule:
    """The origin's row; an unlisted origin (a wake reason this table does not
    name) is Edward's own turn: linted and counted. Pure."""
    return RULES.get(origin, MODEL_RULE)


def derive_origin(explicit: Optional[str] = None, *, scope: bool = False, policy_owner_asked: Optional[bool] = None,
                  ping_third_party: Optional[bool] = None, purpose: Optional[str] = None,
                  turn_source: Optional[str] = None, caller_module: Optional[str] = None) -> str:
    """Who is reaching the owner. An explicit origin wins (unknown →
    ``other``, refused); then a live ``speak_scope``; then the turn policy
    saying the owner asked; then the current ping (the owner's own →
    ``owner``, a friend's → ``ping``); then the ``mind:<reason>`` / ``pulse``
    purpose; then the turn's source (the household hub's voice → ``hub``);
    then — for the ping runner, until its call passes ``origin`` — a caller
    in ``services.pings`` is a ping notice
    (``ping:other``: exempt from the budget, a note at night). Nothing at
    all → ``other``. Pure."""
    if explicit:
        e = str(explicit).strip().lower()
        return e if e in RULES else "other"
    if scope:
        return "speak"
    if policy_owner_asked:
        return "owner"
    if ping_third_party is not None:
        return "ping" if ping_third_party else "owner"
    p = (purpose or "").strip().lower()
    if p:
        if p == "pulse" or p.startswith("surplus:"):
            return "surfaces"
        if p.startswith("mind:"):
            reason = p[len("mind:"):]
            if reason == "surfaces":
                return "surfaces"
            if reason.startswith("notice"):      # the noticer and its eval never reach the owner
                return "other"
            if reason in RULES:
                return reason
            return reason if _MIND_REASON.fullmatch(reason) else "other"
    ts = (turn_source or "").strip().lower()
    if ts in OWNER_TURN_SOURCES:
        return "owner"
    if ts == "pulse":
        return "surfaces"
    if ts == "mind":
        return "self"
    if ts in ("scheduled_event", "worker"):
        return ts
    if ts == HUB_TURN_SOURCE:
        return "hub"
    if (caller_module or "").startswith("services.pings"):
        return "ping:other"
    return "other"


def _caller_module() -> Optional[str]:
    """The first ``services.*`` module up the stack that is not this one."""
    import sys

    try:
        frame = sys._getframe(2)
    except ValueError:
        return None
    for _ in range(40):
        if frame is None:
            return None
        name = str(frame.f_globals.get("__name__") or "")
        if name.startswith("services.") and name != __name__:
            return name
        frame = frame.f_back
    return None


def _context() -> Dict[str, Any]:
    """What the running turn says about itself, each piece feature-detected
    (the turn policy, the current ping, the call purpose, the turn record)."""
    out: Dict[str, Any] = {"policy_owner_asked": None, "ping_third_party": None, "purpose": None,
                           "turn_source": None, "caller_module": _caller_module()}
    try:
        from services.toolset.policy import current_policy

        pol = current_policy()
        if pol is not None:
            out["policy_owner_asked"] = bool(getattr(pol, "owner_asked", False))
    except Exception:  # noqa: BLE001 — not merged, or no policy this turn
        pass
    try:
        from services.pings.hooks import current_ping

        ping = current_ping()
        if ping:
            out["ping_third_party"] = bool(ping.get("third_party"))
    except Exception:  # noqa: BLE001
        pass
    try:
        from services.call_purpose import current_purpose

        out["purpose"] = current_purpose()
    except Exception:  # noqa: BLE001
        pass
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
        out["turn_source"] = getattr(rec, "source", None) if rec is not None else None
    except Exception:  # noqa: BLE001
        pass
    return out


# ----------------------------------------------------------- the scope --

@dataclass
class SpeakScope:
    """Set around a speak wake. ``tell_owner`` reads the origin,
    the channel ceiling, ``who`` for the lint, the dedupe key and whether it
    is private; it appends each delivery to ``delivered`` so the caller sees
    what reached the owner (the object is shared with the tool tasks the turn runs
    in, because contextvars copy the reference)."""
    noticing_id: Optional[str]
    channel: str
    floor_push: bool = False
    who: Tuple[str, ...] = ()
    sensitive: bool = False
    dedupe_key: Optional[str] = None
    category: Optional[str] = None
    closes_at: Optional[datetime] = None
    delivered: List[Dict[str, Any]] = field(default_factory=list)


_scope: contextvars.ContextVar[Optional[SpeakScope]] = contextvars.ContextVar("edward_speak_scope", default=None)


@contextmanager
def speak_scope(noticing_id: Optional[str], channel: str, floor_push: bool = False, who: Sequence[str] = (),
                sensitive: bool = False, dedupe_key: Optional[str] = None, *, category: Optional[str] = None,
                closes_at: Optional[datetime] = None) -> Iterator[SpeakScope]:
    scope = SpeakScope(noticing_id=noticing_id, channel=str(channel or "note"), floor_push=bool(floor_push),
                       who=tuple(str(w) for w in (who or ()) if w), sensitive=bool(sensitive),
                       dedupe_key=dedupe_key, category=category, closes_at=closes_at)
    token = _scope.set(scope)
    try:
        yield scope
    finally:
        _scope.reset(token)


def current_scope() -> Optional[SpeakScope]:
    return _scope.get()


# ------------------------------------------------------------- helpers --

def _now() -> datetime:
    """Aware UTC; tests replace it."""
    return datetime.now(timezone.utc)


def _naive(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


def _clock(dt: datetime) -> str:
    """"7:34 pm" in the owner's zone — the one clock format."""
    from services.timezone_utils import utc_to_local

    return utc_to_local(dt).strftime("%-I:%M %p").lower()


def _in_quiet_hours(now: datetime) -> bool:
    from services.speak_gate import in_quiet_hours, quiet_hours
    from services.timezone_utils import utc_to_local

    return in_quiet_hours(utc_to_local(now).hour, *quiet_hours())


def quiet_hours_label() -> str:
    """The quiet hours in the one clock format: "10 pm–7 am" by default. Pure
    but for reading ``EDWARD_QUIET_HOURS`` (``speak_gate.quiet_hours_label``)."""
    from services.speak_gate import quiet_hours_label as _label

    return _label()


_warned_budget: set = set()


def push_budget() -> Optional[int]:
    """Counted autonomous pushes a local day, or ``None`` for no cap — the
    default (design decision, 2026-10-11: "push, with no limit").
    ``EDWARD_PUSH_BUDGET=<n>`` (n ≥ 0) sets one; ``unlimited`` / unset / a
    value that is not a number means none. Read at call time."""
    raw = (os.getenv(PUSH_BUDGET_ENV) or "").strip().lower()
    if raw in UNLIMITED:
        return None
    try:
        return max(0, int(raw))
    except ValueError:
        if raw not in _warned_budget:
            _warned_budget.add(raw)
            logger.warning("%s=%r is not a number — no push budget", PUSH_BUDGET_ENV, raw)
        return None


async def autonomous_pushes_today(now: Optional[datetime] = None) -> Optional[int]:
    """Edward's own pushes that reached a device today (the owner's local
    day): every ``reach:*`` source but the owner's asks, ping notices and
    care alerts.
    ``None`` when the ledger cannot be read."""
    from services import push_ledger

    return await push_ledger.count_sent_today(SOURCE_PREFIX, exclude=EXEMPT_SOURCES, now=now or _now())


def normalize_key(text: Optional[str]) -> str:
    """A dedupe key from words: lowercased alphanumerics, single-spaced,
    clipped to the ledger's ref width. Pure."""
    return " ".join(re.findall(r"[a-z0-9]+", (text or "").lower()))[:80]


def _scan_enabled() -> bool:
    from services.taint_service import SCAN_ENV, _OFF

    return (os.getenv(SCAN_ENV) or "on").strip().lower() not in _OFF


def _find_secrets(text: str) -> list:
    from services import secret_scan

    return secret_scan.find_secrets(text, secret_scan.known_secrets())


_REDACTED_MARKER = re.compile(r"\[redacted: [^\]]*\]")


def redact(text: Optional[str]) -> Optional[str]:
    """Every secret span in ``text`` replaced with ``[secret]`` —
    ``secret_scan.redact_secrets`` when it exists, else the scan's own
    rules and the process's known values. A text the scan still flags after
    that is replaced whole."""
    if not text:
        return text
    from services import secret_scan

    known = secret_scan.known_secrets()
    out: Optional[str] = None
    fn = getattr(secret_scan, "redact_secrets", None)
    if callable(fn):
        for args in ((text, known), (text,)):
            try:
                got = fn(*args)
            except TypeError:
                continue
            except Exception:  # noqa: BLE001
                break
            if isinstance(got, str):
                out = got
                break
            if isinstance(got, tuple) and got and isinstance(got[0], str):
                out = got[0]
                break
        if out is not None:
            # The scan's marker is ``[redacted: <kind>]``; what the owner and
            # the noticer read says ``[secret]`` (no internal vocabulary).
            out = _REDACTED_MARKER.sub("[secret]", out)
    if out is None:
        out = text
        for _kind, pattern, group, validator, _keep in getattr(secret_scan, "_RULES", []):
            def _sub(m: "re.Match[str]", group: int = group, validator: Any = validator) -> str:
                if validator is not None and not validator(m):
                    return m.group(0)
                s, e = m.span(group)
                ms = m.start(0)
                whole = m.group(0)
                return whole[: s - ms] + "[secret]" + whole[e - ms:]
            out = pattern.sub(_sub, out)
        for value in known.values():
            if value and len(value) >= secret_scan.MIN_KNOWN_SECRET_CHARS:
                out = out.replace(value, "[secret]")
    if secret_scan.find_secrets(out, known):
        return "(a message with what looks like a secret in it — open Edward to read it)"
    return out


def _sensitive_body(closes_at: Optional[datetime]) -> str:
    if closes_at is not None:
        return f"Something needs you before {_clock(closes_at)} — tap to read"
    return "Something needs you — tap to read"


def _default_link(link: Optional[str]) -> Tuple[str, Optional[str]]:
    """(where the tap goes, the conversation the push points at). A wake's
    reach opens the journal entry it is writing (2026-09-25); anything
    else, the link given, else Today."""
    thread_id = entry_id = None
    try:
        from services import thought_journal

        entry_id = thought_journal.current_entry_id()
        try:
            from services.graph.tools import get_current_conversation_id

            thread_id = get_current_conversation_id()
        except Exception:  # noqa: BLE001
            thread_id = None
        url = thought_journal.entry_url(link, thread_id, entry_id)
    except Exception:  # noqa: BLE001
        url = link
    return (url or "/today"), (entry_id or thread_id)


async def _write_reach_row(*, origin: str, outcome: str, key: Optional[str], reason: Optional[str],
                           detail: Dict[str, Any], now: datetime) -> Optional[int]:
    """One ``ledger_events`` row (kind ``reach``), stamped with ``now`` so the
    follow-up window reads the same clock. Never raises."""
    try:
        from services.database import LedgerEventModel, async_session
        from services.event_ledger import MAX_REASON_CHARS, bounded_detail

        row = LedgerEventModel(kind="reach", source=str(origin)[:60], outcome=str(outcome)[:20],
                               reason=(" ".join(str(reason).split())[:MAX_REASON_CHARS] if reason else None),
                               ref_id=(key[:80] if key else None), detail_json=bounded_detail(detail),
                               created_at=_naive(now))
        async with async_session() as session:
            session.add(row)
            await session.commit()
            await session.refresh(row)
            return row.id
    except Exception as exc:  # noqa: BLE001
        logger.warning("reach ledger write failed (%s/%s): %s", origin, outcome, exc)
        return None


async def _delivered_at(key: str, now: datetime) -> Optional[datetime]:
    """When this key last reached the owner (push or note) within the follow-up
    window, as aware UTC; ``None`` if it has not."""
    if not key:
        return None
    try:
        from sqlalchemy import select

        from services.database import LedgerEventModel, async_session

        since = _naive(now) - timedelta(hours=FOLLOW_UP_WINDOW_H)
        async with async_session() as session:
            at = (await session.execute(
                select(LedgerEventModel.created_at).where(
                    LedgerEventModel.kind == "reach", LedgerEventModel.ref_id == key[:80],
                    LedgerEventModel.outcome.in_(("push", "note", "downgraded")),
                    LedgerEventModel.created_at >= since, LedgerEventModel.created_at <= _naive(now))
                .order_by(LedgerEventModel.created_at.desc()).limit(1))).scalar_one_or_none()
        return at.replace(tzinfo=timezone.utc) if at is not None else None
    except Exception as exc:  # noqa: BLE001
        logger.warning("reach dedupe read failed: %s", exc)
        return None


async def _nightly_notes(now: datetime) -> int:
    try:
        from sqlalchemy import func, select

        from services.database import LedgerEventModel, async_session

        since = _naive(now) - timedelta(hours=NIGHTLY_WINDOW_H)
        async with async_session() as session:
            return int((await session.execute(
                select(func.count()).select_from(LedgerEventModel).where(
                    LedgerEventModel.kind == "reach", LedgerEventModel.source == "nightly",
                    LedgerEventModel.outcome.in_(("note", "downgraded")),
                    LedgerEventModel.created_at >= since))).scalar() or 0)
    except Exception as exc:  # noqa: BLE001
        logger.warning("reach nightly count failed: %s", exc)
        return NIGHTLY_WINDOW_H  # unreadable → treat tonight's note as used


async def _noticing_key(noticing_id: Optional[str]) -> Optional[str]:
    if not noticing_id:
        return None
    try:
        from sqlalchemy import select

        from services.database import NoticingModel, async_session

        async with async_session() as session:
            return (await session.execute(select(NoticingModel.dedupe_key)
                                          .where(NoticingModel.id == noticing_id))).scalar_one_or_none()
    except Exception:  # noqa: BLE001
        return None


async def _record_on_noticing(noticing_id: Optional[str], *, channel: str, push_log_id: Optional[str],
                              note_id: Optional[str], now: datetime) -> None:
    if not noticing_id:
        return
    try:
        from sqlalchemy import update

        from services.database import NoticingModel, async_session

        values: Dict[str, Any] = {"surfaced_at": _naive(now), "channel": channel, "status": "spoken",
                                  "updated_at": _naive(now)}
        if push_log_id:
            values["push_log_id"] = str(push_log_id)[:36]
        if note_id:
            values["note_id"] = str(note_id)[:36]
        async with async_session() as session:
            await session.execute(update(NoticingModel).where(NoticingModel.id == noticing_id).values(**values))
            await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("reach could not record on noticing %s: %s", noticing_id, exc)


async def _deliver_push(*, title: str, body: str, url: str, origin: str,
                        conversation_id: Optional[str]) -> Dict[str, Any]:
    """The owner's channel when ``services.owner_channel`` exists, else Web
    Push. Quiet hours were already applied here, so the transport never
    holds. Raises on a transport failure."""
    try:
        from services import owner_channel  # absent → push
    except ImportError:
        owner_channel = None  # type: ignore[assignment]
    if owner_channel is not None:
        return await owner_channel.deliver(title=title, body=body, url=url, origin=origin,
                                         conversation_id=conversation_id)
    from services import push_service

    if not push_service.is_configured():
        return {"sent": 0, "error": "push is not configured"}
    return await push_service.send_push_notification(
        title=title, body=body, url=url, tag="edward-reach", source=f"{SOURCE_PREFIX}{origin}"[:40],
        conversation_id=conversation_id, respect_quiet_hours=False)


def _title_from(message: str) -> str:
    first = re.split(r"(?<=[.?!])\s", message.strip(), maxsplit=1)[0]
    return (first[:57] + "…") if len(first) > 60 else first


def _note_linked(args: Dict[str, Any], result: str, *, channel: Optional[str] = None) -> None:
    """A wake's entry is written even when the journal cannot write the full
    one (thought_journal.note_linked) — under the act the journal knows how
    to title ("Pushed you: …", "Left you a note: …")."""
    try:
        from services import thought_journal

        if thought_journal.current_entry_id():
            if channel == "push":
                name, act = "send_push_notification", {"title": args.get("title") or "", "body": args.get("message")}
            elif channel == "note":
                name, act = "leave_note", {"title": args.get("title") or "", "body": args.get("message")}
            else:
                name, act = "tell_owner", args
            thought_journal.note_linked(name, act, result)
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------ tell_owner --

def follow_up_key(message: str) -> str:
    """The one-follow-up key when the caller, the speak scope and the
    noticing give none: the message's own words — never the title, which is
    often generic ("Edward", "Heads up"; review 2026-10-11: every ping
    notice shared the key "edward"). Pure."""
    return normalize_key(message)


async def tell_owner(message: str, title: Optional[str] = None, urgent: bool = False, link: Optional[str] = None,
                   *, origin: Optional[str] = None, noticing_id: Optional[str] = None,
                   dedupe_key: Optional[str] = None, note_fallback: bool = True) -> str:
    """Reach the owner: a note on Today, or ``urgent`` for a push when it can't
    wait. Returns a success sentence (a downgrade says why), or a string
    starting ``Tool error:`` for the model to act on. Raises only when
    neither a push nor a note could be delivered. ``note_fallback=False``
    (a caller retrying a push it already left a note for) returns
    ``NOT_DELIVERED …`` instead of leaving another note."""
    now = _now()
    scope = _scope.get()
    ctx = _context() if not origin else {}
    origin = derive_origin(origin, scope=scope is not None, **ctx)
    rule = rule_for(origin)
    message = (message or "").strip()
    title = (title or "").strip() or None
    noticing_id = noticing_id or (scope.noticing_id if scope else None)
    detail: Dict[str, Any] = {"urgent": bool(urgent), "noticing_id": noticing_id}

    async def refused(reason: str, text: str, key: Optional[str] = None) -> str:
        await _write_reach_row(origin=origin, outcome="refused", key=key, reason=reason, detail=detail, now=now)
        print(f"[reach] refused {origin}: {reason}")
        return text

    if rule.refuse:
        return await refused("this turn can't reach the owner", REFUSE_NO_REACH)
    if not message:
        return await refused("empty", REFUSE_EMPTY)

    # The egress secret scan: never a secret on the lock screen.
    private = False
    if _scan_enabled():
        try:
            hits = _find_secrets(f"{title or ''}\n{message}")
        except Exception as exc:  # noqa: BLE001 — fail closed: nothing leaves unscanned
            hits = [exc]
        if hits:
            if not rule.code:
                kinds = ",".join(sorted({getattr(h, "kind", "scan_error") for h in hits}))
                return await refused(f"secret ({kinds})", REFUSE_SECRET)
            message = redact(message) or ""
            title = redact(title) if title else None

    # The high-risk gate, for an urgent reach the owner did not ask for.
    if urgent and origin != "owner":
        from services.push_safety_gate import classify_high_risk_push_label_text

        risk = classify_high_risk_push_label_text(f"{title or ''} {message}")
        if risk:
            if not rule.code:
                return await refused(f"high risk ({risk})", REFUSE_HIGH_RISK.format(risk=risk))
            private = True   # a templated notice still goes out — with nothing on the lock screen

    # The offer grammar, for Edward's own words.
    if rule.lint:
        from services.offer_grammar import lint, problems_text

        who = scope.who if scope else ()
        # A push and anything the speaker says is an offer (the whole
        # grammar); a note from Edward's other turns may simply tell.
        problems = lint(message, who=who, kind="push" if urgent else ("offer" if origin == "speak" else "note"))
        if title:
            problems += lint(title, kind="title")
        if problems:
            text = problems_text(problems)
            return await refused(f"lint: {text}", f"Tool error: {text} — fix it and call tell_owner again.")

    # One follow-up per thing.
    key = normalize_key(dedupe_key or (scope.dedupe_key if scope else None) or await _noticing_key(noticing_id)
                        or follow_up_key(message))
    if rule.dedupe:
        prior = await _delivered_at(key, now)
        if prior is not None:
            clock = _clock(prior)
            if rule.code:
                await _write_reach_row(origin=origin, outcome="skipped", key=key, reason=f"repeat of {clock}",
                                       detail=detail, now=now)
                return ALREADY_TOLD.format(clock=clock)
            return await refused(f"repeat of {clock}", REFUSE_REPEAT.format(clock=clock), key)

    if rule.notes_per_night is not None and await _nightly_notes(now) >= rule.notes_per_night:
        return await refused("nightly note already left", REFUSE_NIGHTLY, key)

    # The channel: push only when every rule allows it.
    channel = "push" if urgent else "note"
    why: Optional[str] = None
    if channel == "push":
        if not rule.may_push:
            channel, why = "note", f"the {origin} never pushes"
        elif origin == "speak":
            ceiling = scope.channel if scope else "note"
            if CHANNEL_ORDER.get(ceiling, 1) < CHANNEL_ORDER["push"]:
                channel, why = "note", f"the gate allowed a {ceiling}, not a push"
        elif rule.quiet == "note" and _in_quiet_hours(now):
            channel, why = "note", f"quiet hours ({quiet_hours_label()}); it's on Today for the morning"
    budget = push_budget() if (channel == "push" and rule.counts) else None
    if budget is not None:
        used = await autonomous_pushes_today(now)
        if used is None:
            channel, why = "note", "I couldn't count today's pushes"
        elif used >= budget:
            channel, why = "note", f"{min(used, budget)} of {budget} pushes used today"

    sensitive = private
    closes_at = None
    if scope is not None:
        sensitive = sensitive or scope.sensitive or (scope.category or "") in SENSITIVE_CATEGORIES
        closes_at = scope.closes_at

    url, conversation_id = _default_link(link)
    push_log_id = note_id = None
    delivered = None
    if channel == "push":
        push_title = "Edward" if sensitive else (title or "Edward")
        body = _sensitive_body(closes_at) if sensitive else message
        try:
            res = await _deliver_push(title=push_title[:200], body=body, url=url, origin=origin,
                                      conversation_id=conversation_id)
        except Exception as exc:  # noqa: BLE001 — fall back to a note below
            logger.warning("reach push failed (%s): %s", origin, exc)
            res = {"sent": 0, "error": f"{type(exc).__name__}"}
        if int((res or {}).get("sent") or 0) > 0:
            delivered = "push"
            push_log_id = (res or {}).get("ledger_id")
            if conversation_id:
                try:
                    from services.conversation_service import mark_user_notified

                    await mark_user_notified(conversation_id)
                except Exception:  # noqa: BLE001
                    pass
        else:
            err = (res or {}).get("error")
            channel, why = "note", (f"the push failed ({err})" if err and "configured" not in str(err)
                                    else "the push reached no device")
            if not note_fallback:
                await _write_reach_row(origin=origin, outcome="failed", key=key, reason=why, detail=detail, now=now)
                print(f"[reach] {origin} → nothing ({why}; no note asked for)")
                return f"{NOT_DELIVERED} — {why}."

    if channel == "note" or (delivered == "push" and sensitive):
        from services import notes_service
        from services.offer_grammar import asks_a_question

        note_id = await notes_service.leave_note(kind="mind", title=(title or _title_from(message))[:200],
                                                 body=message, url=url, needs_you=asks_a_question(message))
        if note_id is None and delivered is None:
            await _write_reach_row(origin=origin, outcome="failed", key=key, reason="no push, no note",
                                   detail=detail, now=now)
            raise RuntimeError("reach_service: neither a push nor a note could be delivered")
        if delivered is None:
            delivered = "note"

    outcome = "push" if delivered == "push" else ("downgraded" if urgent else "note")
    detail.update({"channel": delivered, "push_log_id": push_log_id, "note_id": note_id})
    await _write_reach_row(origin=origin, outcome=outcome, key=key, reason=why, detail=detail, now=now)
    await _record_on_noticing(noticing_id, channel=delivered, push_log_id=push_log_id, note_id=note_id, now=now)
    if scope is not None:
        scope.delivered.append({"channel": delivered, "push_log_id": push_log_id, "note_id": note_id,
                                "at": now.isoformat()})
    if delivered == "push":
        result = f"{PUSHED}." + (" (A private line on the lock screen; the full offer is in a note.)"
                                             if sensitive else "")
    elif urgent:
        result = f"Left it as a note — {why}." if why else LEFT_A_NOTE
    else:
        result = LEFT_A_NOTE
    print(f"[reach] {origin} → {delivered}" + (f" ({why})" if why else "") + f" · {(title or message)[:60]!r}")
    _note_linked({"title": title or _title_from(message), "message": message[:200], "urgent": bool(urgent)}, result,
                 channel=delivered)
    return result
