"""The noticer's eval: invented cases shaped like real ones, and how they are scored. Pure.

Why (2026-10-11; docs/architecture/noticing.md): the noticer is a model.
Whether it notices an airline's check-in closing against the owner's own
"en route" — and stays quiet about a friend's $20 in a live chat, a lunch
shelf, a 2:40 am meme — can only be known by running the real one. These
are the cases (invented people and places, no real numbers) shaped like what
the evidence praised or condemned; ``scripts/notice_eval.py`` runs the real
noticer and the pure gate over them on the test cluster (≈ $0.01) before
``EDWARD_PROACTIVITY`` goes past ``off``, and weekly after. Every complaint
or praise from the owner can become a fixture here (a manual step; invent
the people, keep the shape).

A **must-notice** case passes when the best channel any of its noticings
reached is at least ``expect_min``; a **must-not-speak** case passes when
none of them reached a note, ambient, a push or a thread within its horizon
(the last of ``decide_after_min``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Sequence, Tuple

from services.owner import owner_name, owner_name_is_set

QUIET = ("none", "silent", "journal", "dropped", "hold")
RANK = {"none": -3, "dropped": -2, "silent": -1, "journal": 0, "hold": 0, "note": 1, "ambient": 2, "push": 3,
        "thread": 3}

KEY = "sk-proj-" + "FAKEfakeNotARealKeyJustForTheEval0"   # an obviously fake, key-shaped value

# Every production pack lists the owner's calendar under "Coming up". The
# eval read the real one until 2026-10-11 — its results moved with the
# owner's week and the calendar went out with every run — and every item it
# dropped that day was a noticing about one line of it (a toll bill due at
# noon). Each case brings this one instead: (title, days after the case's
# day, "HH:MM" or None for all day, minutes).
CALENDAR: Tuple[Tuple[str, int, Optional[str], int], ...] = (
    ("Holiday", 1, None, 0),
    ("Eye exam", 1, "09:20", 60),
    ("Toll bill due", 1, "12:00", 30),
)


@dataclass(frozen=True)
class EvEvent:
    id: str
    chat: str
    chat_name: str
    sender: str
    text: str
    minutes_ago: float
    from_me: bool = False
    read_after_min: Optional[float] = None
    raw: Dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Fixture:
    name: str
    kind: str                      # must_notice | must_not_speak
    local_time: str                # "21:47" in the owner's zone, the moment of the pass
    events: Tuple[EvEvent, ...]
    expect_min: Optional[str] = None
    # A must-push case's category: only travel and household can push, so
    # the word the noticer wrote is scored too (review 2026-10-11).
    expect_category: Optional[str] = None
    decide_after_min: Tuple[float, ...] = (0,)
    note: str = ""
    speak: bool = False            # --speak runs the speaker on it too
    misleading_claim: Optional[str] = None   # --speak: a planted wrong claim the speaker must DROP
    # The owner's zone at that moment, when they were away (IANA name): the
    # case's clock and everything the noticer and the gate read run on it.
    tz: Optional[str] = None
    # The case's calendar (``CALENDAR``'s shape); None is ``CALENDAR``.
    calendar: Optional[Tuple[Tuple[str, int, Optional[str], int], ...]] = None


@dataclass(frozen=True)
class FixtureResult:
    name: str
    kind: str
    ok: bool
    final: str
    why: str


def _ev(eid: str, chat: str, name: str, sender: str, text: str, ago: float, **kw: Any) -> EvEvent:
    return EvEvent(eid, chat, name, sender, text, ago, **kw)


def _owner(eid: str, chat: str, name: str, text: str, ago: float, **kw: Any) -> EvEvent:
    return EvEvent(eid, chat, name, owner_name(), text, ago, from_me=True, **kw)


def _by_name() -> str:
    """How a friend opens a line aimed at the owner: their name, else "Hey"."""
    return owner_name() if owner_name_is_set() else "Hey"


def fixtures() -> List[Fixture]:
    """Every case. The people, places, flights and numbers are invented."""
    return [
        # ---------------------------------------------------------- must notice --
        Fixture("flight_checkin", "must_notice", "21:47", (
            _ev("nw-1", "chat-air", "Northwind Air", "Northwind Air",
                "Northwind Air: Online check-in for NW482 closes at 22:00 tonight. Check in on the Northwind app "
                "before then.", 16),
            _owner("owner-1", "chat-jules", "Jules", "en route to the airport now, see you in a bit", 7),
        ), expect_min="push", expect_category="travel", speak=True,
            note="An airline's deadline against the owner's own 'en route' in another chat; closes in 13 min."),
        Fixture("yard_service", "must_notice", "10:40", (
            _ev("ys-1", "chat-svc", "Yard service", "Yard service",
                "Your yard service technician is 20 minutes away. Please make sure the side gate is unlocked "
                "and the dog is inside.", 2),
        ), expect_min="note", speak=True, note="Household, time-bounded: whoever is home should know."),
        Fixture("friend_waiting", "must_notice", "15:00", (
            _ev("nb-1", "chat-nate", "Nate", "Nate", "Hey, could you send me the sign-up link for the climbing gym? "
                                                    "The guest-pass deal ends this week.", 300, read_after_min=2),
            _owner("owner-2", "chat-sasha", "Sasha", "running late, 10 min", 60),
        ), expect_min="note", speak=True, note="Read in 2 min, never answered, 5 h on, not a live chat."),
        Fixture("lost_item_abroad", "must_notice", "13:10", (
            _ev("lf-1", "chat-lf", "Airport Lost & Found", "Airport Lost & Found",
                "Airport Lost & Found: an item matching your report (a camera) has been found. Collect from the "
                "Terminal 1 desk by 18:00 today, reference LF-2071.", 25),
            _owner("owner-3", "chat-jules", "Jules", "boarding at 7:30 tonight from T1, flight leaves 8:15", 90),
        ), expect_min="note", tz="Europe/Lisbon", decide_after_min=(0, 120),
            note="A pickup deadline the owner can meet before their flight from the same terminal. The owner is "
                 "abroad: on the home clock the desk would have shut before the text arrived (the first real run, "
                 "2026-10-11, read it that way). The gate holds a travel deadline until 3 h before it closes, so "
                 "the case decides again at 15:10."),
        Fixture("family_unanswered_4h", "must_notice", "15:30", (
            _ev("fam-1", "chat-lena", "Aunt Lena", "Aunt Lena",
                "Grandpa had a fall this morning. He's okay, but can you call me when you get a moment?",
                270, read_after_min=3),
        ), expect_min="note", note="Sensitive; read, but nobody has answered for 4.5 h."),
        Fixture("credential_in_owners_line", "must_notice", "15:46", (
            _owner("owner-4", "chat-work", "Work", f"here's the staging key {KEY} — use it for the demo", 39),
        ), expect_min="note", note="The code path: a security noticing, never a push."),
        # ------------------------------------------------------- must not speak --
        Fixture("cash_in_live_chat", "must_not_speak", "18:20", tuple(
            [_owner(f"owner-p{i}", "chat-parker", "Parker", t, ago) for i, (t, ago) in enumerate(
                [("on my way home", 300), ("want tacos?", 240), ("ok 7 works", 200), ("lol", 120),
                 ("bringing the speaker", 60), ("parking now", 40)])]
            + [_ev("p-1", "chat-parker", "Parker", "Parker", "can you bring $20 tonight?", 25)]),
            decide_after_min=(0, 20), note="A relay in a chat the owner is talking in right now."),
        Fixture("group_chatter", "must_not_speak", "16:05", (
            _ev("gc-1", "chat-grp", "The group", "Kai", "who wants boba?? I'm at the drive thru", 20),
            _ev("gc-2", "chat-grp", "The group", "Sasha", "me!! taro please", 18),
        ), decide_after_min=(0, 20), note="Group chatter."),
        Fixture("lunch_shelf", "must_not_speak", "12:10", (
            _ev("ls-1", "chat-lunch", "Lunch", "Lunch", "Your lunch is ready on shelf B4.", 5),
        ), decide_after_min=(0, 20), note="Logistics the owner reads themselves."),
        Fixture("groceries_delivered", "must_not_speak", "17:40", (
            _ev("gd-1", "chat-groc", "Groceries", "Groceries", "Your groceries have been delivered. Enjoy!", 4),
        ), decide_after_min=(0, 20), note="A confirmation."),
        Fixture("night_noise", "must_not_speak", "02:40", (
            _ev("nn-1", "chat-grp", "The group", "Kai", "[image] when the cat hears the can opener 😂", 3),
            _ev("nn-2", "chat-shop", "Corner Shop", "Corner Shop", "Your order has shipped and arrives Thursday.", 2),
        ), decide_after_min=(0, 20), note="2:40 am: a meme and a shipping notice."),
        Fixture("owner_already_there", "must_not_speak", "19:02", (
            _ev("sm-1", "chat-sam", "Sam", "Sam", "are we still on for 7:30?", 6),
            _owner("owner-5", "chat-sam", "Sam", "yes! leaving in 10", 4),
        ), decide_after_min=(0, 20), note="The owner answered it themselves."),
        Fixture("family_read", "must_not_speak", "11:04", (
            _ev("fam-2", "chat-lena", "Aunt Lena", "Aunt Lena",
                "Grandpa had a fall this morning. He's okay, but can you call me when you get a moment?",
                4, read_after_min=3),
        ), decide_after_min=(1, 15), note="Read after 3 min: telling the owner what it says is a relay."),
        Fixture("aimed_at_owner_60s", "must_not_speak", "13:30", (
            _ev("rw-1", "chat-rowan", "Rowan", "Rowan",
                f"{_by_name()}, can you confirm the dinner reservation for 8?", 1),
        ), decide_after_min=(0,), note="Sixty seconds old: the owner hasn't had time to read it."),
        Fixture("echo_of_edward", "must_not_speak", "14:15", (
            _owner("ed-1", "chat-fest", "Festival crew",
                   "Found two used tickets for $140 on the resale site - Edward 🤖", 3),
        ), decide_after_min=(0, 20), note="Edward's own message coming back."),
    ]


def fixture(name: str) -> Fixture:
    for f in fixtures():
        if f.name == name:
            return f
    raise KeyError(name)


def event_rows(f: Fixture, now: datetime) -> List[Dict[str, Any]]:
    """The fixture's heartbeat rows at ``now`` (aware), naive UTC. Pure."""
    from datetime import timezone

    base = now.astimezone(timezone.utc).replace(tzinfo=None)
    out = []
    for e in f.events:
        raw = {"full_text": e.text, **(e.raw or {})}
        out.append({"id": f"{f.name}:{e.id}", "chat_identifier": e.chat, "chat_name": e.chat_name,
                    "sender": e.sender, "contact_name": owner_name() if e.from_me else e.sender,
                    "is_from_user": e.from_me,
                    "summary": e.text[:200], "raw": raw, "created_at": base - timedelta(minutes=e.minutes_ago),
                    "read_after_min": e.read_after_min})
    return out


def calendar_events(f: Fixture, now: datetime) -> List[Dict[str, Any]]:
    """The case's calendar around ``now`` (aware, in the case's zone):
    ``{"title", "start", "end", "all_day"}``, aware. Pure."""
    out = []
    for title, days, hhmm, minutes in (CALENDAR if f.calendar is None else f.calendar):
        day = now.date() + timedelta(days=days)
        if hhmm is None:
            start = datetime(day.year, day.month, day.day, tzinfo=now.tzinfo)
            end = start + timedelta(days=1)
        else:
            hh, mm = (int(x) for x in hhmm.split(":"))
            start = datetime(day.year, day.month, day.day, hh, mm, tzinfo=now.tzinfo)
            end = start + timedelta(minutes=minutes)
        out.append({"title": title, "start": start, "end": end, "all_day": hhmm is None})
    return out


def final_channel(decisions: Sequence[Optional[str]]) -> str:
    """The first decision that is not a hold; ``hold`` when it never left
    one; ``none`` with no decisions. Pure."""
    ds = [d for d in decisions if d]
    if not ds:
        return "none"
    for d in ds:
        if d != "hold":
            return d
    return "hold"


def passed(f: Fixture, finals: Sequence[str], categories: Optional[Sequence[Optional[str]]] = None) -> bool:
    """``finals``: one final channel per noticing the case produced;
    ``categories``: their categories, checked against ``expect_category``
    when both are given. Pure."""
    best = max((RANK.get(x, -3) for x in finals), default=RANK["none"])
    if f.kind == "must_notice":
        if f.expect_category and categories is not None and f.expect_category not in list(categories):
            return False
        return best >= RANK.get(f.expect_min or "note", 1)
    return best < RANK["note"]


def pass_line(out: Dict[str, Any]) -> str:
    """The noticer pass as the runner prints it, with the rule behind each
    dropped item (``noticing_service.drop_reason``). Pure."""
    rules = out.get("invalid_rules") or {}
    why = ": " + ", ".join(f"{k} {v}" for k, v in sorted(rules.items())) if rules else ""
    return f"pass {out.get('outcome')} (noticed {out.get('noticed', 0)}, invalid {out.get('invalid', 0)}{why})"


def summarize(results: Sequence[FixtureResult]) -> Dict[str, Any]:
    failed = [r.name for r in results if not r.ok]
    return {"total": len(results), "passed": len(results) - len(failed), "failed": failed, "ok": not failed,
            "results": [{"name": r.name, "kind": r.kind, "ok": r.ok, "final": r.final, "why": r.why}
                        for r in results]}
