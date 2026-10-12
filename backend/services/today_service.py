"""Today — the feed Edward curates for the app's front door.

Three bands:

  needs          things with an action for the owner, ranked
  did            a digest of what Edward did today (not a log)
  conversations  the owner's recent threads (Edward's own are counted, not listed)

``compose_today`` is pure: plain dicts in, plain dicts out, ranked by rules
that are pinned in ``tests/test_today_service.py``. ``get_today`` gathers
the inputs from the existing services, each behind its own try/except so
one broken source degrades the feed instead of taking it down.

Ranking (highest first):
  1. overdue *timed* pet dose         (warn)   free-fed items never appear
  2. escalated heartbeat, last 24h    (warn)
  3. open loop, not snoozed, >= .5    (warn)   by priority, max 3
  4. one-shot event in next 24h       (info)   recurring housekeeping never
  5. worker finished, last 24h        (edward)
  6. timed dose due within 45 min     (info)

The digest also carries what Edward wished for today (``wishes``): he
cannot change his own code, so a wish is a note to the owner, never a decision.

And every wake that chose to act in the last 24 h (``actions``, the
thought journal, 2026-09-25): one row per entry, titled like the entry,
the first thing it did as the detail, linking to the entry itself.

UX pass (2026-09-25, from an audit of a live day):

* Needs never flood. Notes sharing a title are one card with a ``count``
  (seven identical "Which is right?" cards landed within 41 s); at most
  ``MAX_NOTE_NEEDS`` note cards and ``MAX_HEARTBEAT_NEEDS`` threads, the
  rest counted in ``overflow``. Every note and message card has a
  mutating "Done" (a note expires now; a message thread is stamped as
  seen, the mark the mind already uses). A contradiction question carries
  a ``choice`` the owner can answer in place.
* A calendar row is a ``calendar`` need ("Dentist in 25 min"), never
  "someone needs a reply" — ``calendar_urgent`` from the old triage, or any
  row from the calendar listener (the mind path escalates those too).
* "What I did" is one window (``did_since``: local midnight or 18 h ago,
  whichever is earlier), every row with its ``at``; scheduler plumbing
  (pulse, surplus, ticks, self-wakes) never appears, the mind's wakes are
  one folded row, and notes sharing a title are one row with a ``count``
  (a dismissed question is history, once).
* ``presence`` says what Edward is listening to and when his mind last
  woke and will wake next; ``status`` is only the two fields the page
  used to read, not the heartbeat's 80-key config.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from services.owner import household

logger = logging.getLogger(__name__)

# Recurring events are Edward's housekeeping. They fire on their own and
# never need the owner, however soon the next fire is.
MAX_OPEN_LOOPS = 3
MAX_EVENTS = 3
MAX_WORKERS = 2
MAX_CONVERSATIONS = 8
MAX_DID = 12
ACTION_WINDOW_HOURS = 24
# Goals and Edward's ideas had their own lists on Today (2026-09-24); both
# went with the proactivity rebuild (2026-10-11: 0 goals and 1 idea in two
# weeks). An old idea note is still
# neither a need nor history.
IDEA_KIND = "idea"
OPEN_LOOP_MIN_PRIORITY = 0.5
DOSE_HEADS_UP_MINUTES = 45
# The flood caps (UX pass 2026-09-25): past these the rest is a count.
MAX_NOTE_NEEDS = 4
MAX_HEARTBEAT_NEEDS = 5
HEARTBEAT_READ_ROWS = 60
# "What I did": since local midnight, or this many hours back when that is
# earlier (so a 7 am open still shows last evening).
DID_MIN_HOURS = 18
# A starting-soon calendar event stops being a need this long after it began.
CALENDAR_STALE_MINUTES = 60
CALENDAR_SOON_PREFIX = "[STARTING SOON]"
CALENDAR_STARTS_AT = " — starts at "
CALENDAR_SEP = " — "
CALENDAR_SPAN_TO = " to "
CALENDAR_HREF = "/hub/today"

# Scheduled events that are Edward's own plumbing: the pulse, the sweep, the
# old surplus tick, the nightly and the wakes he asked for. They are never a need
# and never a "what I did" row (the mind's wakes are one folded row from
# its ledger instead). Every type ``scheduler_service.event_route`` routes
# is here (pinned by a test).
PLUMBING_EVENT_TYPES = frozenset({"expression_pulse", "widget_refresh", "mind_wake", "wakeup_tick", "surplus_tick",
                                  "proactive_sweep"})
WAKE_EVENT_TYPES = ("wakeup_tick", "mind_wake")
LISTEN_TRACKS = ("imessage", "email", "gmail")

# The owner said "Done" on a message card: stamped like a wake that considered it.
OWNER_DONE_BATCH_ID = "owner:done"

OWNER_SOURCES = ("user", "external_message")


@dataclass
class TodayInputs:
    board: Dict[str, Any] = field(default_factory=dict)
    heartbeat: List[Dict[str, Any]] = field(default_factory=list)
    open_loops: List[Dict[str, Any]] = field(default_factory=list)
    events: List[Dict[str, Any]] = field(default_factory=list)
    workers: List[Dict[str, Any]] = field(default_factory=list)
    fired_events: List[Dict[str, Any]] = field(default_factory=list)
    triage_counts: Dict[str, int] = field(default_factory=dict)
    conversations: List[Dict[str, Any]] = field(default_factory=list)
    status: Dict[str, Any] = field(default_factory=dict)
    # Edward's wishlist (2026-09-12, replaces evolution proposals): what he
    # wished for in the last 24h, shown in the digest for the owner to read.
    wishes: List[Dict[str, Any]] = field(default_factory=list)
    # Edward's notes (2026-09-12): the templated services speak here now.
    # A note that needs the owner and has not expired is a need; the rest is digest.
    notes: List[Dict[str, Any]] = field(default_factory=list)
    # Sends held for the owner's approval (taint gate, 2026-09-24): pending rows.
    approvals: List[Dict[str, Any]] = field(default_factory=list)
    # The thought journal (2026-09-25): wakes that chose to act, each with
    # its entry (thought_journal.recent_action_entries): ledger_id,
    # thought_id, title, first_line, at.
    actions: List[Dict[str, Any]] = field(default_factory=list)
    # The mind's ledger (mind_service.recent): id, at, source (the reason),
    # outcome, note (his closing line). Newest first.
    wakes: List[Dict[str, Any]] = field(default_factory=list)
    # The memory flags live notes point at (``?flag=<id>``): id ->
    # {resolved, memory_a, memory_b}. None when they could not be read
    # (the notes then stay, without an inline choice); a flag missing from
    # a dict that was read is answered or gone, and its note leaves Today.
    flags: Optional[Dict[str, Dict[str, Any]]] = None


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _parse(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, datetime):
        dt = ts
    else:
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _action(id_: str, label: str, method: str, url: str, body: Optional[dict] = None, primary: bool = False) -> dict:
    a = {"id": id_, "label": label, "method": method, "url": url, "primary": primary}
    if body is not None:
        a["body"] = body
    return a


def _open_link(id_: str, href: str, label: str = "Open") -> dict:
    return {"id": id_, "label": label, "method": "GET", "url": href, "primary": False}


def _next_morning(now: datetime, tz: ZoneInfo) -> datetime:
    local = now.astimezone(tz)
    morning = (local + timedelta(days=1)).replace(hour=8, minute=0, second=0, microsecond=0)
    return morning.astimezone(timezone.utc)


def _clock(dt: datetime) -> str:
    """"7:34 pm" — the one clock format on Today (review 2026-09-26: it
    showed "Due 18:00", "Starts at 7:34 PM" and "6:32 pm" on one screen)."""
    return f"{dt.hour % 12 or 12}:{dt.minute:02d} {'am' if dt.hour < 12 else 'pm'}"


def _clock_hhmm(value: Any) -> str:
    """"18:00" → "6:00 pm"; anything unparseable as it came."""
    try:
        hh, mm = str(value).split(":")[:2]
        return _clock(datetime(2000, 1, 1, int(hh), int(mm)))
    except (ValueError, TypeError):
        return str(value)


def _humanize_minutes(m: Optional[int]) -> str:
    if m is None:
        return ""
    if m < 60:
        return f"{m} min"
    h, r = divmod(m, 60)
    return f"{h}h {r:02d}m" if r else f"{h}h"


# --------------------------------------------------------------------------
# needs
# --------------------------------------------------------------------------

def _dose_needs(board: Dict[str, Any], now: datetime, tz: ZoneInfo) -> List[dict]:
    out: List[dict] = []
    for pet in board.get("pets") or []:
        for med in pet.get("meds") or []:
            status = med.get("status")
            name = med.get("name") or "dose"
            dose = f" {med['dose']}" if med.get("dose") else ""
            # The care day it is for: a feed cached last night and tapped
            # after the 3 am roll-over is refused by the hub route (409),
            # never logged against today (review 2026-09-26).
            log = _action("log", "Log given", "POST", f"/api/hub/care/{med.get('id')}/log",
                          {"given_by": household()[0], "status": "given", "for_care_day": board.get("care_day")},
                          primary=True)
            board_link = _open_link("board", "/hub/pets", "Open board")
            if status == "overdue":
                late = _humanize_minutes(med.get("minutes_late"))
                out.append({
                    "id": f"dose:{med.get('id')}",
                    "kind": "dose",
                    "tone": "warn",
                    "rank": 1,
                    "priority": 1.0,
                    "title": f"{pet.get('name')} · {name}{dose}",
                    "detail": (f"Due {_clock_hhmm(med.get('at_time'))}, {late} late." if late and med.get("at_time")
                               else f"{late} late." if late else "Overdue."),
                    "href": "/hub/pets",
                    "actions": [log, board_link],
                })
            elif status == "due" and med.get("at_time"):
                # Timed dose coming up: a heads-up, not an alarm.
                try:
                    hh, mm = str(med["at_time"]).split(":")
                    local_now = now.astimezone(tz)
                    due = local_now.replace(hour=int(hh), minute=int(mm), second=0, microsecond=0)
                    mins = int((due - local_now).total_seconds() // 60)
                except (ValueError, TypeError):
                    continue
                if 0 <= mins <= DOSE_HEADS_UP_MINUTES:
                    out.append({
                        "id": f"dose:{med.get('id')}",
                        "kind": "dose",
                        "tone": "info",
                        "rank": 7,
                        "priority": 0.2,
                        "title": f"{pet.get('name')} · {name}{dose}",
                        "detail": f"Due at {_clock_hhmm(med.get('at_time'))}, in {mins} min.",
                        "href": "/hub/pets",
                        "actions": [log, board_link],
                    })
    return out


# The mind stamps what it considered (``notified_at``, batch "mind:<ledger
# id>"): it pushed, replied, noted or chose to leave it, and the owner can read
# that in the Thoughts panel. With mind_enabled every surviving inbound
# message is escalated, so without this busy threads flooded Today. What the
# mind gave up on after three failed wakes nobody judged — that stays.
GAVE_UP_BATCH_ID = "mind:gave-up"


def _unseen_by_the_mind(e: Dict[str, Any]) -> bool:
    return not e.get("notified_at") or e.get("notification_batch_id") == GAVE_UP_BATCH_ID


def _done(url: str, others: List[Any], primary: bool = True) -> dict:
    """The "Done" on a card that stands for several rows: the card's own id
    is in the URL, the rest ride in ``also`` (so a row that arrives after
    Today loaded is a new card, never silently cleared)."""
    also = [str(o) for o in others]
    return _action("done", "Done", "POST", url, {"also": also} if also else None, primary=primary)


def _is_calendar(e: Dict[str, Any]) -> bool:
    """A calendar row, whatever triage called it: the old path marks a
    starting-soon one ``calendar_urgent``; the mind path (``mind_enabled``)
    marks every surviving event ``escalated``, calendar ones included."""
    return e.get("triage_status") == "calendar_urgent" or e.get("source") == "calendar"


def _live_escalations(events: List[Dict[str, Any]], now: datetime, statuses: Tuple[str, ...],
                      calendar: bool = False) -> List[tuple]:
    """Unseen rows in ``statuses`` from the last 24 h, newest first: only
    calendar rows when ``calendar``, never them otherwise."""
    cutoff = now - timedelta(hours=24)
    live: List[tuple] = []
    for e in events:
        if e.get("triage_status") not in statuses or _is_calendar(e) != calendar:
            continue
        if not _unseen_by_the_mind(e):
            continue
        at = _parse(e.get("created_at"))
        if at is None or at < cutoff:
            continue
        live.append((at, e))
    live.sort(key=lambda pair: pair[0], reverse=True)
    return live


EMAIL_SOURCES = ("email", "gmail")
EMAIL_NO_SUBJECT = "(no subject)"
NEED_TITLE_MAX = 140


def email_title(e: Dict[str, Any]) -> str:
    """An escalated email's card title: "<sender> emailed you: <subject>".
    The listeners store no contact and no thread for mail, only the sender
    and ``summary = "From <sender>: <subject>"``; a "Name <address>" sender
    shows the name. No subject, no colon. Clipped to the card. Pure."""
    sender = str(e.get("sender") or "").strip()
    name, _, _addr = sender.partition("<")
    who = e.get("contact_name") or (name.strip().strip('"') if name.strip() else sender) or "Someone"
    summary = str(e.get("summary") or "").strip()
    prefix = f"From {sender}: "
    if sender and summary.startswith(prefix):
        subject = summary[len(prefix):]
    elif summary.startswith("From ") and ": " in summary:
        subject = summary.partition(": ")[2]
    else:
        subject = summary
    subject = " ".join(subject.split())
    if subject == EMAIL_NO_SUBJECT:
        subject = ""
    title = f"{who} emailed you" + (f": {subject}" if subject else "")
    if len(title) > NEED_TITLE_MAX:
        title = title[:NEED_TITLE_MAX - 1].rstrip() + "…"
    return title


def _heartbeat_needs(events: List[Dict[str, Any]], now: datetime) -> Tuple[List[dict], int]:
    """One need per *thread*, carried by its newest escalated message.

    A busy group chat escalates every line (2026-09-21: 31 from one person, 28
    from another in a day) and Today showed the same person five times.
    Messages sharing a conversation — or, without one, a chat identifier —
    fold into the newest, and the count is in the detail. At most
    ``MAX_HEARTBEAT_NEEDS`` threads; returns (needs, how many more).
    """
    live = _live_escalations(events, now, ("escalated", "mention"))

    out: List[dict] = []
    members: Dict[str, List[Any]] = {}
    need_by_key: Dict[str, dict] = {}
    for at, e in live:
        key = e.get("conversation_id") or e.get("chat_identifier") or f"event:{e.get('id')}"
        if key in need_by_key:
            members[key].append(e.get("id"))
            continue
        who = e.get("contact_name") or e.get("chat_name") or e.get("sender") or "Someone"
        conv = e.get("conversation_id")
        href = f"/chat?c={conv}" if conv else "/settings/heartbeat"
        # The mind path escalates mail too: an email is not "needs a reply",
        # and its summary is only the subject again (2026-09-26).
        is_email = e.get("source") in EMAIL_SOURCES
        need = {
            "id": f"heartbeat:{e.get('id')}",
            "kind": "email" if is_email else "heartbeat",
            "tone": "warn",
            "rank": 2,
            "priority": 0.9,
            "title": email_title(e) if is_email else f"{who} needs a reply",
            "detail": "" if is_email else (e.get("summary") or "")[:140],
            "href": href,
            "at": _iso(at),
            "actions": [_open_link("open", href, "Open thread")],
        }
        need_by_key[key] = need
        members[key] = [e.get("id")]
        out.append(need)
    for key, need in need_by_key.items():
        ids = members[key]
        need["count"] = len(ids)
        need["actions"].append(_done(f"/api/today/heartbeat/{ids[0]}/done", ids[1:]))
        if len(ids) > 1:
            summary = (need["detail"] or "").strip()
            need["detail"] = f"{len(ids)} messages" + (f" · {summary}"[:126] if summary else "")
    return out[:MAX_HEARTBEAT_NEEDS], max(0, len(out) - MAX_HEARTBEAT_NEEDS)


def calendar_title_and_start(summary: Optional[str]) -> Tuple[str, Optional[datetime]]:
    """The calendar listener's summary back to (title, start). Two forms:
    "[STARTING SOON] Dentist — starts at <iso>" and "Dentist — <iso> to
    <iso>". Pure; a start that does not parse is None (a naive one is UTC,
    as the listener reads it), and the title is then the whole summary."""
    s = (summary or "").strip()
    if s.startswith(CALENDAR_SOON_PREFIX):
        s = s[len(CALENDAR_SOON_PREFIX):].strip()
    title, sep, rest = s.partition(CALENDAR_STARTS_AT)
    if sep:
        start = _parse(rest.strip()) if rest.strip() else None
        return (title.strip() or "Calendar event"), start
    # The listener's other form: "Title — <start> to <end>".
    head, sep, span = s.rpartition(CALENDAR_SEP)
    start_text, to, _end = span.partition(CALENDAR_SPAN_TO)
    start = _parse(start_text.strip()) if sep and to else None
    if start is not None:
        return (head.strip() or "Calendar event"), start
    return (s or "Calendar event"), None


def _calendar_needs(events: List[Dict[str, Any]], now: datetime, tz: ZoneInfo) -> List[dict]:
    """A calendar event about to start is an event, not a message: "Dentist
    in 25 min", its local start time, and Done. Gone an hour after it began."""
    out: List[dict] = []
    for created, e in _live_escalations(events, now, ("calendar_urgent", "escalated", "mention"), calendar=True):
        title, start = calendar_title_and_start(e.get("summary"))
        detail = ""
        if start is not None:
            mins = int(round((start - now).total_seconds() / 60))
            if mins < -CALENDAR_STALE_MINUTES:
                continue
            if mins > 0:
                headline = f"{title} in {_humanize_minutes(mins)}"
            elif mins >= -5:
                headline = f"{title} now"
            else:
                headline = f"{title} started {_humanize_minutes(-mins)} ago"
            local = start.astimezone(tz)
            detail = f"{'Starts' if mins > 0 else 'Started'} at {_clock(local)}"
        else:
            if created < now - timedelta(minutes=CALENDAR_STALE_MINUTES):
                continue
            headline = title
        out.append({
            "id": f"calendar:{e.get('id')}",
            "kind": "calendar",
            "tone": "info",
            "rank": 2,
            "priority": 0.95,
            "title": headline[:140],
            "detail": detail,
            "href": CALENDAR_HREF,
            "at": _iso(start or created),
            "count": 1,
            "actions": [_open_link("open", CALENDAR_HREF, "Open calendar"),
                        _done(f"/api/today/heartbeat/{e.get('id')}/done", [])],
        })
    return out


def _open_loop_needs(loops: List[Dict[str, Any]], now: datetime, tz: ZoneInfo) -> List[dict]:
    live: List[Dict[str, Any]] = []
    for l in loops:
        if (l.get("status") or "open") != "open":
            continue
        snooze = _parse(l.get("snooze_until"))
        if snooze is not None and snooze > now:
            continue
        if float(l.get("priority") or 0) < OPEN_LOOP_MIN_PRIORITY:
            continue
        live.append(l)
    # Fresh loops first: one nudged 20 times is one the owner has been declining
    # for months, and it should not sit above something new.
    live.sort(key=lambda l: (int(l.get("nudge_count") or 0), -float(l.get("priority") or 0)))
    out: List[dict] = []
    snooze_until = _iso(_next_morning(now, tz))
    for i, l in enumerate(live[:MAX_OPEN_LOOPS]):
        conv = l.get("source_conversation_id")
        href = f"/chat?c={conv}" if conv else "/settings/proactive"
        out.append({
            "id": f"open_loop:{l['id']}",
            "kind": "open_loop",
            "tone": "warn",
            "rank": 3,
            # Position-based so the fresh-first order survives the global sort.
            "priority": 0.85 - i * 0.01,
            "title": (l.get("description") or "Open loop")[:140],
            "detail": "Still open." if not l.get("nudge_count") else f"Nudged {l['nudge_count']}×.",
            "href": href,
            "actions": [
                _open_link("open", href, "Open thread"),
                _action("snooze", "Snooze", "PATCH", f"/api/proactive/open-loops/{l['id']}",
                        {"status": "snoozed", "snooze_until": snooze_until}),
                _action("done", "Done", "PATCH", f"/api/proactive/open-loops/{l['id']}",
                        {"status": "resolved"}, primary=True),
            ],
        })
    return out


def _event_needs(events: List[Dict[str, Any]], now: datetime) -> List[dict]:
    horizon = now + timedelta(hours=24)
    soon: List[tuple] = []
    for e in events:
        if e.get("recurrence_pattern"):
            continue
        if e.get("type") in PLUMBING_EVENT_TYPES:
            continue  # a wake Edward asked for is his, not the owner's
        if (e.get("status") or "pending") != "pending":
            continue
        at = _parse(e.get("next_fire_at"))
        if at is None or at > horizon or at < now - timedelta(minutes=5):
            continue
        soon.append((at, e))
    soon.sort(key=lambda p: p[0])
    out: List[dict] = []
    for at, e in soon[:MAX_EVENTS]:
        mins = int((at - now).total_seconds() // 60)
        when = "now" if mins <= 0 else f"in {_humanize_minutes(mins)}"
        out.append({
            "id": f"event:{e['id']}",
            "kind": "event",
            "tone": "info",
            "rank": 4,
            "priority": 0.5,
            "title": (e.get("description") or "Scheduled")[:140],
            "detail": f"Scheduled {when}.",
            "href": "/settings/events",
            "at": _iso(at),
            "actions": [_open_link("open", "/settings/events", "Open events")],
        })
    return out


def _worker_needs(workers: List[Dict[str, Any]], now: datetime) -> List[dict]:
    cutoff = now - timedelta(hours=24)
    done: List[tuple] = []
    for w in workers:
        if w.get("status") != "completed":
            continue
        at = _parse(w.get("completed_at"))
        if at is None or at < cutoff:
            continue
        done.append((at, w))
    done.sort(key=lambda p: p[0], reverse=True)
    out: List[dict] = []
    for at, w in done[:MAX_WORKERS]:
        conv = w.get("worker_conversation_id")
        href = f"/chat?c={conv}" if conv else "/settings/orchestrator"
        out.append({
            "id": f"worker:{w['id']}",
            "kind": "worker",
            "tone": "edward",
            "rank": 6,
            "priority": 0.4,
            "title": f"Finished: {(w.get('task_description') or 'a task')[:110]}",
            "detail": (w.get("result_summary") or "Output is in the thread.")[:140],
            "href": href,
            "at": _iso(at),
            "actions": [_open_link("open", href, "Open")],
        })
    return out


# --------------------------------------------------------------------------
# did
# --------------------------------------------------------------------------

# Where a note without a link of its own opens: the thread it came from.
_NOTE_HREF_BY_KIND = {"mind": "/chat?c=edward-mind", "whisper": "/settings/heartbeat"}


# Contradiction questions used to link here; the flag lives in the Dreaming
# panel (``/settings/dreaming?flag=<id>``), which opens it.
_LEGACY_FLAG_PATH = "/settings/memories"
FLAG_PATH = "/settings/dreaming"


def _note_href(n: dict) -> str:
    href = n.get("href")
    if isinstance(href, str) and href and href != "/today":
        if href.startswith(_LEGACY_FLAG_PATH + "?") and "flag=" in href:
            return FLAG_PATH + href[len(_LEGACY_FLAG_PATH):]
        return href
    return _NOTE_HREF_BY_KIND.get(str(n.get("kind") or ""), "/today")


def note_flag(href: Any) -> Tuple[Optional[str], Optional[str]]:
    """(flag id, ask) from a note's link: ``…?flag=<id>[&ask=rewrite]``. Pure."""
    if not isinstance(href, str) or "flag=" not in href:
        return None, None
    try:
        q = parse_qs(urlsplit(href).query)
    except ValueError:
        return None, None
    return (q.get("flag") or [None])[0], (q.get("ask") or [None])[0]


def _title_key(title: Any) -> str:
    return " ".join(str(title or "").lower().split())


def _flag_choice(fid: str, question: str, flag: Dict[str, Any]) -> Optional[Tuple[dict, List[dict]]]:
    """The two memories as options the owner can pick between, each resolving the
    flag through ``POST /api/consolidation/flags/<id>/resolve {keep}`` (the
    Dreaming panel's own call: keep one, retire the other)."""
    a, b = flag.get("memory_a"), flag.get("memory_b")
    if not a or not b:
        return None
    url = f"/api/consolidation/flags/{fid}/resolve"
    options = [
        {"id": "a", "label": "Keep this", "text": str(a)[:300], "action_id": "keep_a"},
        {"id": "b", "label": "Keep this", "text": str(b)[:300], "action_id": "keep_b"},
    ]
    actions = [
        _action("keep_a", "First is right", "POST", url, {"keep": "a"}),
        _action("keep_b", "Second is right", "POST", url, {"keep": "b"}),
    ]
    return {"flag_id": fid, "question": question, "options": options}, actions


def _note_needs(notes: List[dict], now: datetime,
                flags: Optional[Dict[str, Dict[str, Any]]] = None) -> Tuple[List[dict], int]:
    """A need always carries ``href`` and ``tone``: the page hands ``href`` to
    Next's <Link>, which throws on undefined in a production build — the
    mind's link-less notes turned the PWA's start page into "Application
    error" on 2026-09-21/22.

    Notes sharing a title are one card (the newest carries it, ``count``
    says how many, "Done" clears them all); at most ``MAX_NOTE_NEEDS``
    cards, newest first. Returns (needs, how many more cards)."""
    live: List[tuple] = []
    for n in notes:
        if not n.get("needs_you") or n.get("kind") == IDEA_KIND:
            continue
        exp = _parse(n.get("expires_at"))
        if exp is not None and exp <= now:
            continue
        fid, ask = note_flag(n.get("href"))
        if fid and flags is not None:
            flag = flags.get(fid)
            if flag is None or flag.get("resolved"):
                continue  # answered (here or in Dreaming), or the flag is gone
        live.append((_parse(n.get("created_at")) or now, n, fid, ask))
    live.sort(key=lambda t: t[0], reverse=True)

    groups: Dict[str, List[tuple]] = {}
    for item in live:
        groups.setdefault(_title_key(item[1].get("title")), []).append(item)

    out: List[dict] = []
    for members in groups.values():
        at, n, fid, ask = members[0]
        href = _note_href(n)
        ids = [m[1]["id"] for m in members]
        detail = (n.get("body") or "")[:200]
        need = {
            "id": f"note:{n['id']}",
            "kind": "note",
            "tone": "edward",
            "title": n.get("title") or "A note from Edward",
            "detail": detail,
            "href": href,
            "at": _iso(at),
            "priority": 0.6,
            "rank": 3.5,
            "count": len(ids),
            "note_ids": ids,
            "actions": [{"id": "open", "label": "Open", "method": "GET", "url": href, "primary": False}],
        }
        choice = None
        if fid and flags is not None and ask != "rewrite":
            choice = _flag_choice(fid, n.get("body") or n.get("title") or "", flags[fid])
        if choice is not None:
            need["choice"], keep_actions = choice
            need["actions"] += keep_actions
        need["actions"].append(_done(f"/api/notes/{ids[0]}/dismiss", ids[1:], primary=choice is None))
        out.append(need)
    return out[:MAX_NOTE_NEEDS], max(0, len(out) - MAX_NOTE_NEEDS)


def did_since(now: datetime, tz: ZoneInfo) -> datetime:
    """Where "What I did" starts: local midnight, or ``DID_MIN_HOURS`` back
    when that is earlier — at 8 pm the day so far, at 7 am last evening
    too. One window for every row (it used to mix since-midnight counts
    with rolling 24 h lists). Pure."""
    midnight = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0)
    return min(midnight.astimezone(timezone.utc), now - timedelta(hours=DID_MIN_HOURS))


def _times(n: int) -> str:
    return {1: "once", 2: "twice"}.get(n, f"{n} times")


def _wakes_row(wakes: List[Dict[str, Any]]) -> Optional[dict]:
    """Every wake of the mind in the window as one row: "Woke 6 times ·
    acted on 2". A wake skipped for quota did not happen."""
    rows = []
    for w in wakes:
        at = _parse(w.get("at"))
        if at is None or w.get("outcome") == "skipped":
            continue
        rows.append((at, w))
    if not rows:
        return None
    rows.sort(key=lambda p: p[0], reverse=True)
    n = len(rows)
    acted = sum(1 for _, w in rows if w.get("outcome") == "acted")
    failed = sum(1 for _, w in rows if w.get("outcome") == "failed")
    parts = [f"Woke {_times(n)}"]
    if acted:
        parts.append("acted" if n == 1 else f"acted on {acted}")
    elif not failed:
        parts.append("left things alone")
    if failed:
        parts.append(f"{failed} failed")
    latest_at, _ = rows[0]
    # What he last *said*: a failed wake's note is an error class, and the
    # title already counts the failures (review 2026-09-26).
    said = next((w for _, w in rows if w.get("outcome") != "failed"), {})
    return {
        "id": "wakes:recent",
        "kind": "wakes",
        "title": " · ".join(parts),
        "detail": " ".join(str(said.get("note") or "").split())[:160],
        "at": _iso(latest_at),
        "href": "/settings/thoughts",
        "count": n,
        "sort": latest_at,
    }


def _did(inputs: TodayInputs, now: datetime, tz: ZoneInfo) -> List[dict]:
    since = did_since(now, tz)
    out: List[dict] = []
    # Notes sharing a title are one row, like the cards they were: "Done"
    # expires a question now, and seven dismissed "Which is right?" cards
    # must not come back as seven rows. The newest carries the row.
    note_rows: Dict[str, dict] = {}
    for n in sorted(inputs.notes, key=lambda n: _parse(n.get("created_at")) or now, reverse=True):
        if n.get("kind") == IDEA_KIND:
            continue  # ideas have their own list; they are not history
        if n.get("needs_you"):
            exp = _parse(n.get("expires_at"))
            if exp is None or exp > now:
                continue  # still a need, not history
        at = _parse(n.get("created_at"))
        if at is None or at < since:
            continue
        key = _title_key(n.get("title"))
        if key in note_rows:
            note_rows[key]["count"] += 1
            continue
        note_rows[key] = {
            "id": f"note:{n['id']}",
            "kind": "note",
            "title": n.get("title") or "A note from Edward",
            "detail": (n.get("body") or "")[:160],
            "at": _iso(at),
            "href": _note_href(n),
            "count": 1,
            "sort": at,
        }
    out.extend(note_rows.values())
    c = inputs.triage_counts or {}
    if c.get("total"):
        # Plain words, not the heartbeat's (review 2026-09-26): "Read 47
        # messages · 4 for you", then what became of the rest.
        total = int(c["total"])
        title = f"Read {total} message{'s' if total != 1 else ''}"
        if c.get("escalated"):
            title += f" · {c['escalated']} for you"
        rest = []
        if c.get("noted"):
            rest.append(f"Remembered {c['noted']}")
        if c.get("handled_by_owner"):
            rest.append(f"you answered {c['handled_by_owner']} yourself")
        if c.get("dismissed"):
            rest.append(f"{c['dismissed']} needed nothing")
        if rest:
            rest[0] = rest[0][:1].upper() + rest[0][1:]
        # Stamped with the newest message it counted, not the page load.
        latest = _parse(c.get("latest_at")) or now
        out.append({
            "id": "triage:today",
            "kind": "triage",
            "title": title,
            "detail": " · ".join(rest),
            "at": _iso(latest),
            "href": "/settings/heartbeat",
            "sort": latest,
        })

    wakes = _wakes_row([w for w in inputs.wakes if (_parse(w.get("at")) or since) >= since])
    if wakes is not None:
        out.append(wakes)

    # Fired events: recurring ones collapse to one row with a count. The
    # scheduler's own plumbing never shows (the wakes are the row above).
    grouped: Dict[str, dict] = {}
    for e in inputs.fired_events:
        if e.get("type") in PLUMBING_EVENT_TYPES:
            continue
        at = _parse(e.get("last_fired_at"))
        if at is None:
            continue
        key = f"rec:{e.get('description')}" if e.get("recurrence_pattern") else f"one:{e.get('id')}:{at.isoformat()}"
        g = grouped.setdefault(key, {"count": 0, "latest": at, "e": e})
        g["count"] += 1
        if at > g["latest"]:
            g["latest"] = at
    for key, g in grouped.items():
        e = g["e"]
        desc = (e.get("description") or "Scheduled event")[:120]
        detail = f"×{g['count']} today" if g["count"] > 1 else ""
        out.append({
            "id": f"fired:{key}",
            "kind": "event_fired",
            "title": desc,
            "detail": detail,
            "at": _iso(g["latest"]),
            "href": "/settings/events",
            "sort": g["latest"],
        })

    # What he chose to do on his own, each linking to the entry that says
    # why (thought journal, 2026-09-25).
    for a in inputs.actions:
        at = _parse(a.get("at"))
        if at is None or not a.get("thought_id") or at < now - timedelta(hours=ACTION_WINDOW_HOURS):
            continue
        out.append({
            "id": f"action:{a.get('ledger_id')}",
            "kind": "action",
            "title": a.get("title") or "Acted on my own",
            "detail": (a.get("first_line") or "")[:160],
            "at": _iso(at),
            "href": f"/chat?c={a['thought_id']}",
            "sort": at,
        })

    for w in inputs.wishes:
        at = _parse(w.get("created_at")) or now
        out.append({
            "id": f"wish:{w.get('id')}",
            "kind": "wish",
            "title": f"Wished for: {w.get('title') or 'untitled'}",
            "detail": (w.get("why") or "")[:160],
            "at": _iso(at),
            "href": "/settings/wishlist",
            "sort": at,
        })

    out = [d for d in out if d["sort"] >= since]
    out.sort(key=lambda d: d["sort"], reverse=True)
    for d in out:
        d.pop("sort", None)
    return out[:MAX_DID]


# --------------------------------------------------------------------------
# compose
# --------------------------------------------------------------------------

def _approval_needs(rows: List[Dict[str, Any]], now: datetime) -> List[dict]:
    """A send the taint gate held is the owner's to answer: Approve / Deny / Open.
    The rows are naive UTC, like the table."""
    if not rows:
        return []
    from services.approvals_service import approval_needs
    return approval_needs(rows, now.astimezone(timezone.utc).replace(tzinfo=None))


def _next_wake(events: List[Dict[str, Any]], now: datetime) -> Tuple[Optional[datetime], Optional[str]]:
    """The mind's next wake from the pending scheduled events: a fixed tick
    (its kind: morning / evening / reflect …) or one he asked for (self)."""
    best: Optional[Tuple[datetime, str]] = None
    for e in events:
        if e.get("type") not in WAKE_EVENT_TYPES or (e.get("status") or "pending") != "pending":
            continue
        at = _parse(e.get("next_fire_at"))
        if at is None or at < now - timedelta(minutes=5):
            continue
        reason = "self" if e.get("type") == "mind_wake" else (e.get("kind") or "tick")
        if best is None or at < best[0]:
            best = (at, reason)
    return best if best is not None else (None, None)


def _presence(inputs: TodayInputs, now: datetime) -> Dict[str, Any]:
    """What Edward is doing, from real signals only (the design contract:
    the animation is a claim). ``listening`` is None when the heartbeat's
    state could not be read, [] when it is not running."""
    st = inputs.status or {}
    listening: Optional[List[str]] = None
    if st:
        tracks = st.get("tracks") or {}
        listening = [t for t in LISTEN_TRACKS if (tracks.get(t) or {}).get("enabled")] if st.get("running") else []
    wakes = sorted(((at, w) for w in inputs.wakes if (at := _parse(w.get("at"))) is not None),
                   key=lambda p: p[0], reverse=True)
    last_at, last = wakes[0] if wakes else (None, {})
    next_at, next_reason = _next_wake(inputs.events, now)
    return {
        "listening": listening,
        "last_look_at": _iso(_parse(st.get("last_triage_at"))),
        "mind": {
            "last_wake_at": _iso(last_at),
            "last_reason": last.get("source"),
            "last_outcome": last.get("outcome"),
            "last_line": " ".join(str(last.get("note") or "").split())[:200] or None,
            "next_wake_at": _iso(next_at),
            "next_wake_reason": next_reason,
        },
    }


def _compact_status(st: Dict[str, Any]) -> Dict[str, Any]:
    """The two heartbeat fields the page read before ``presence`` (the raw
    status was ~80 keys of triage configuration)."""
    if not st:
        return {}
    return {"running": bool(st.get("running")), "last_triage_at": _iso(_parse(st.get("last_triage_at")))}


def compose_today(inputs: TodayInputs, now: Optional[datetime] = None, tz_name: str = "UTC") -> Dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")

    heartbeat_needs, more_threads = _heartbeat_needs(inputs.heartbeat, now)
    note_needs, more_notes = _note_needs(inputs.notes, now, inputs.flags)
    needs: List[dict] = []
    needs += _dose_needs(inputs.board or {}, now, tz)
    needs += _calendar_needs(inputs.heartbeat, now, tz)
    needs += heartbeat_needs
    needs += _open_loop_needs(inputs.open_loops, now, tz)
    needs += _event_needs(inputs.events, now)
    needs += _worker_needs(inputs.workers, now)
    needs += note_needs
    needs += _approval_needs(inputs.approvals, now)
    needs.sort(key=lambda n: (n["rank"], -n["priority"]))
    for n in needs:
        n.pop("rank", None)

    local_today = now.astimezone(tz).date()
    convs: List[dict] = []
    thoughts_today = 0
    for c in inputs.conversations:
        src = c.get("source") or "user"
        at = _parse(c.get("updated_at"))
        if src in OWNER_SOURCES:
            if len(convs) < MAX_CONVERSATIONS:
                convs.append({
                    "id": c["id"],
                    "title": c.get("title") or "Untitled",
                    "source": src,
                    "updated_at": _iso(at) if at else c.get("updated_at"),
                    "href": f"/chat?c={c['id']}",
                })
        elif at is not None and at.astimezone(tz).date() == local_today:
            thoughts_today += 1

    return {
        "generated_at": _iso(now),
        "needs": needs,
        # Cards past the caps, by kind: "and 3 more notes".
        "overflow": {"note": more_notes, "heartbeat": more_threads},
        "did": _did(inputs, now, tz),
        "did_since": _iso(did_since(now, tz)),
        "conversations": convs,
        "thoughts_today": thoughts_today,
        "status": _compact_status(inputs.status or {}),
        "presence": _presence(inputs, now),
    }


# --------------------------------------------------------------------------
# gather (I/O)
# --------------------------------------------------------------------------

async def _safe(label: str, coro, default):
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001 — one source must not kill the feed
        logger.warning("today: %s unavailable: %s", label, exc)
        return default


async def _board():
    from services import hub_service
    return await hub_service.get_board()


async def _heartbeat(now: datetime):
    from sqlalchemy import select, desc, or_
    from services.database import async_session, HeartbeatEventModel, IMessageChatConversationModel

    cutoff = (now - timedelta(hours=24)).replace(tzinfo=None)
    async with async_session() as session:
        rows = (await session.execute(
            select(HeartbeatEventModel)
            .where(HeartbeatEventModel.created_at >= cutoff)
            .where(HeartbeatEventModel.triage_status.in_(["escalated", "mention", "calendar_urgent"]))
            # Filtered here too so what the mind already saw cannot crowd
            # the unseen out (see _unseen_by_the_mind).
            .where(or_(HeartbeatEventModel.notified_at.is_(None),
                       HeartbeatEventModel.notification_batch_id == GAVE_UP_BATCH_ID))
            .order_by(desc(HeartbeatEventModel.created_at))
            # Read well past the card cap: the rows collapse per thread
            # afterwards, and ten rows let one chatty thread hide the rest.
            .limit(HEARTBEAT_READ_ROWS)
        )).scalars().all()
        chat_ids = {r.chat_identifier for r in rows if r.chat_identifier}
        conv_by_chat: Dict[str, str] = {}
        if chat_ids:
            maps = (await session.execute(
                select(IMessageChatConversationModel).where(
                    IMessageChatConversationModel.chat_identifier.in_(list(chat_ids))
                )
            )).scalars().all()
            conv_by_chat = {m.chat_identifier: m.conversation_id for m in maps}
    return [{
        "id": r.id,
        "source": r.source,
        "contact_name": r.contact_name,
        "chat_name": r.chat_name,
        "sender": r.sender,
        "summary": r.summary,
        "triage_status": r.triage_status,
        "created_at": r.created_at,
        "chat_identifier": r.chat_identifier,
        "conversation_id": conv_by_chat.get(r.chat_identifier or ""),
        "notified_at": r.notified_at,
        "notification_batch_id": r.notification_batch_id,
    } for r in rows]


def _naive_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


async def _triage_counts(start: datetime):
    """Counts by triage status since ``start`` (``did_since``), plus the
    newest counted message's time, which stamps the digest row."""
    from sqlalchemy import select, func
    from services.database import async_session, HeartbeatEventModel

    since = _naive_utc(start)
    async with async_session() as session:
        rows = (await session.execute(
            select(HeartbeatEventModel.triage_status, func.count(), func.max(HeartbeatEventModel.created_at))
            .where(HeartbeatEventModel.created_at >= since)
            .where(HeartbeatEventModel.is_from_user == False)  # noqa: E712 — the owner's own texts aren't triaged
            .group_by(HeartbeatEventModel.triage_status)
        )).all()
    counts: Dict[str, Any] = {status or "pending": int(n) for status, n, _ in rows}
    counts["total"] = sum(counts.values())
    latest = max((at for _, _, at in rows if at is not None), default=None)
    counts["latest_at"] = _iso(_parse(latest))
    return counts


async def _notes():
    from services.notes_service import note_dict, recent_notes
    return [note_dict(n) for n in await recent_notes(hours=24, limit=60)]


async def _note_flags(notes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """The memory flags live notes link to, with both memories' text, so a
    contradiction can be answered on Today. A flag that no longer exists is
    absent (its note leaves Today)."""
    from sqlalchemy import select
    from services.database import async_session, MemoryFlagModel, MemoryModel

    ids = {fid for n in notes for fid in [note_flag(n.get("href"))[0]] if fid}
    if not ids:
        return {}
    async with async_session() as session:
        flags = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id.in_(list(ids)))
        )).scalars().all()
        mem_ids = {f.memory_id for f in flags} | {f.related_memory_id for f in flags if f.related_memory_id}
        mems = {}
        if mem_ids:
            mems = {m.id: m.content for m in (await session.execute(
                select(MemoryModel).where(MemoryModel.id.in_(list(mem_ids)))
            )).scalars().all()}
    return {f.id: {"resolved": bool(f.resolved), "memory_a": mems.get(f.memory_id),
                   "memory_b": mems.get(f.related_memory_id) if f.related_memory_id else None}
            for f in flags}


async def mark_heartbeat_done(event_id: str, also: List[str] = ()) -> Optional[int]:
    """The owner pressed "Done" on a message card: its events are stamped as seen
    (``notified_at``, batch ``owner:done``) — the mark the mind puts on what
    it considered, so neither Today nor the next wake's "Waiting on you"
    lists them again. Only the ids the card carried: a message that arrived
    after Today loaded stays a new card. None when ``event_id`` is unknown;
    else how many were stamped."""
    from sqlalchemy import select
    from services.database import async_session, HeartbeatEventModel

    ids = [event_id] + [str(i) for i in (also or []) if i and str(i) != event_id][:100]
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with async_session() as session:
        rows = list((await session.execute(
            select(HeartbeatEventModel).where(HeartbeatEventModel.id.in_(ids))
        )).scalars().all())
        if not any(r.id == event_id for r in rows):
            return None
        done = 0
        for r in rows:
            if r.notified_at is None or r.notification_batch_id == GAVE_UP_BATCH_ID:
                r.notified_at = now
                r.notification_batch_id = OWNER_DONE_BATCH_ID
                done += 1
        await session.commit()
    return done


async def _open_loops():
    from services.open_loop_service import get_open_loops
    loops = await get_open_loops(status="open", limit=50)
    return [{
        "id": l.id,
        "description": l.description,
        "priority": l.priority,
        "status": l.status,
        "snooze_until": l.snooze_until,
        "source_conversation_id": l.source_conversation_id,
        "nudge_count": getattr(l, "nudge_count", 0),
    } for l in loops]


def _event_meta(raw: Any) -> Dict[str, Any]:
    """A scheduled event's ``event_metadata`` JSON as a dict ({} when not one)."""
    import json

    if not raw:
        return {}
    try:
        meta = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


async def _events():
    """Pending events with their metadata ``type`` (and a tick's ``kind``):
    Edward's own plumbing is never a need, and the next wake is presence."""
    from services.scheduled_events_service import list_events
    rows = await list_events(status="pending", limit=200)
    out = []
    for e in rows:
        meta = _event_meta(getattr(e, "event_metadata", None))
        out.append({
            "id": e.id,
            "description": e.description,
            "next_fire_at": e.next_fire_at,
            "recurrence_pattern": e.recurrence_pattern,
            "status": e.status,
            "created_by": e.created_by,
            "type": meta.get("type"),
            "kind": meta.get("kind"),
        })
    return out


async def _fired_events(start: datetime):
    """Events that fired since ``start`` (``did_since``), with their type."""
    from sqlalchemy import select, desc
    from services.database import async_session, ScheduledEventModel

    async with async_session() as session:
        rows = (await session.execute(
            select(ScheduledEventModel)
            .where(ScheduledEventModel.last_fired_at >= _naive_utc(start))
            .order_by(desc(ScheduledEventModel.last_fired_at))
            .limit(100)
        )).scalars().all()
    out = []
    for e in rows:
        # A recurring event keeps one row; fire_count within today is not
        # stored, so approximate today's fires from the cron cadence by
        # counting the row once per fire we can see (last_fired_at only).
        out.append({
            "id": e.id,
            "description": e.description,
            "recurrence_pattern": e.recurrence_pattern,
            "last_fired_at": e.last_fired_at,
            "type": _event_meta(e.event_metadata).get("type"),
        })
    return out


async def _mind_wakes(start: datetime):
    """The mind's ledger since ``start``; with none there, its last wake
    (presence still says when he last thought)."""
    from services import mind_service

    rows = await mind_service.recent(limit=300, since=start)
    return rows or await mind_service.recent(limit=1)


async def _workers():
    from services.orchestrator_service import list_tasks
    return await list_tasks(limit=20)


async def _conversations():
    """The owner's recent threads plus every own-source conversation touched in
    the last 36h (so the composer can count today's thoughts)."""
    from sqlalchemy import select, desc, or_
    from services.database import async_session, ConversationModel
    from services.conversation_service import EDWARD_OWN_SOURCES

    since = (datetime.now(timezone.utc) - timedelta(hours=36)).replace(tzinfo=None)
    async with async_session() as session:
        owners = (await session.execute(
            select(ConversationModel)
            .where(or_(ConversationModel.source.in_(list(OWNER_SOURCES)), ConversationModel.source.is_(None)))
            .order_by(desc(ConversationModel.updated_at))
            .limit(MAX_CONVERSATIONS)
        )).scalars().all()
        own = (await session.execute(
            select(ConversationModel)
            .where(ConversationModel.source.in_(list(EDWARD_OWN_SOURCES)))
            .where(ConversationModel.updated_at >= since)
            .order_by(desc(ConversationModel.updated_at))
            .limit(200)
        )).scalars().all()
    rows = list(owners) + list(own)
    return [{
        "id": c.id,
        "title": c.title,
        "source": c.source or "user",
        "updated_at": c.updated_at,
    } for c in rows]


async def _status():
    from services.heartbeat.heartbeat_service import get_heartbeat_status
    st = get_heartbeat_status()
    if hasattr(st, "__await__"):
        st = await st
    return st if isinstance(st, dict) else {}


async def _wishes():
    from services.wishlist_service import wishes_since
    return await wishes_since(hours=24)


async def _pending_approvals():
    from services.approvals_service import list_approvals
    return await list_approvals(status="pending")


async def _mind_actions(now: datetime):
    from services.thought_journal import recent_action_entries
    return await recent_action_entries(hours=ACTION_WINDOW_HOURS, now=now)


async def get_today() -> Dict[str, Any]:
    """Every source at once, each on its own ``_safe`` (one broken source
    degrades the feed, never breaks it); the flags the notes point at are
    the one dependent read."""
    import asyncio
    from services.timezone_utils import current_tz_name

    now = datetime.now(timezone.utc)
    tz_name = current_tz_name()
    try:
        tz = ZoneInfo(tz_name)
    except Exception:
        tz = ZoneInfo("UTC")
    since = did_since(now, tz)

    (board, heartbeat, triage_counts, open_loops, events, fired,
     workers, wishes, conversations, status, notes,
     approvals, actions, wakes) = await asyncio.gather(
        _safe("board", _board(), {}),
        _safe("heartbeat", _heartbeat(now), []),
        _safe("triage", _triage_counts(since), {}),
        _safe("open_loops", _open_loops(), []),
        _safe("events", _events(), []),
        _safe("fired_events", _fired_events(since), []),
        _safe("workers", _workers(), []),
        _safe("wishes", _wishes(), []),
        _safe("conversations", _conversations(), []),
        _safe("status", _status(), {}),
        _safe("notes", _notes(), []),
        _safe("approvals", _pending_approvals(), []),
        _safe("actions", _mind_actions(now), []),
        _safe("wakes", _mind_wakes(since), []),
    )
    flags = await _safe("flags", _note_flags(notes), None)
    inputs = TodayInputs(
        actions=actions,
        notes=notes,
        flags=flags,
        approvals=approvals,
        board=board or {},
        heartbeat=heartbeat,
        triage_counts=triage_counts,
        open_loops=open_loops,
        events=events,
        fired_events=fired,
        workers=workers,
        wishes=wishes,
        conversations=conversations,
        status=status,
        wakes=wakes,
    )
    return compose_today(inputs, now=now, tz_name=tz_name)
