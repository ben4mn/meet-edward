"""``schedule`` — remind, run a task later, wake yourself, and the owner's
calendar and Reminders (docs/architecture/scheduler.md; writes 2026-10-11).

* ``add`` with ``kind=task`` / ``remind`` creates a scheduled event (the
  timezone-safe parsing of ``schedule_event``: a time without a zone is
  the owner's, never the host's); ``kind=wake`` is the mind's own wake
  (``mind_wake`` in ``edward-mind``; ``mind_service`` owns what a wake
  does). Every event is stamped ``requested_by_owner = policy.owner_asked``,
  so a task the owner asked for fires with their authority and one Edward
  set for himself does not (``scheduled_events_service`` also keeps its
  ``created_in``).
* A turn whose policy says ``may_schedule=False`` (a surplus job, the
  pulse) cannot add a task or reminder, and one with
  ``may_wake_later=False`` cannot add a wake: the event would fire later
  with reach the turn itself doesn't have (review 2026-10-11).
* ``list`` / ``cancel`` — today's list and cancel.
* ``calendar`` — the owner's calendar through
  ``calendar_service.events_between`` when it exists, else the display's
  apple-mcp read for today; the default window starts at the owner's local
  midnight. ``reminders`` — their open Reminders (``reminders_service``).
  "Unavailable" is said, never an empty day passed off as the truth. Reads
  are open in every turn (design decision, 2026-10-11: restrict little;
  only writes wait for the owner).
* ``event_add`` / ``event_update`` / ``event_delete`` and ``reminder_add`` /
  ``reminder_complete`` / ``reminder_delete`` change the owner's calendar
  and Reminders through the EventKit helper (``eventkit_service``) when the
  owner asks: one check, ``policy.may_change_owners_calendar()``. Times are
  the owner's zone; a
  date alone is all day (a floating due date for a reminder). Every line of
  the reads carries the id the writes take (``cal-<UUID>-<occurrence>``,
  ``rem-<id>``); before a write Python re-reads that row and hands the
  helper its title and occurrence as hints. ``span=future`` reaches a
  repeating event's later repeats; the same add twice in ten minutes goes
  in once; a helper that gave no answer is "it may or may not have gone
  through", never "it didn't happen" (review 2026-10-11).
"""

from __future__ import annotations

import importlib
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from langchain_core.tools import tool

from services.owner import owner_doc, owner_name, owner_text
from services.toolset import policy as policy_mod
from services.toolset.result import ToolFailure

SCHEDULE_ACTIONS = ("add", "list", "cancel", "calendar", "reminders", "event_add", "event_update", "event_delete",
                    "reminder_add", "reminder_complete", "reminder_delete")
WRITE_ACTIONS = frozenset({"event_add", "event_update", "event_delete", "reminder_add", "reminder_complete",
                           "reminder_delete"})
CHANGE_REFUSAL = owner_text("changing {OWNER}'s calendar or Reminders is for when they ask — this turn isn't "
                             "theirs. tell_owner what you'd change.")
KINDS = ("task", "remind", "wake")
CALENDAR_DEFAULT_DAYS = 7
MAX_CALENDAR_EVENTS = 60
MAX_REMINDERS = 60
DATE_ONLY = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _legacy():
    from services.graph import tools as legacy

    return legacy


def _owners_head() -> str:
    """The owner's possessive at the start of a line ("Sam's", "The owner's")."""
    name = owner_name()
    return name[:1].upper() + name[1:] + "'s"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_when(when: Optional[str], name: str = "when", action: str = "add") -> datetime:
    """Aware UTC from an ISO-8601 time; zone-less is the owner's local time."""
    from services.timezone_utils import local_naive_to_utc

    raw = (when or "").strip()
    if not raw:
        raise ToolFailure(f"{action} needs {name} (an ISO-8601 time in {owner_name()}'s local time, "
                          "e.g. 2026-10-12T09:00:00)")
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError as exc:
        raise ToolFailure(f"{name} must be an ISO-8601 time like 2026-10-12T09:00:00 (got {when!r})") from exc
    return local_naive_to_utc(dt)


async def _remember_why(why: str, what: str, repeat: Optional[str], at_utc: datetime) -> Optional[str]:
    """The reason as an intention of Edward's, handed back when the event
    fires (``schedule_event``'s ``why``)."""
    try:
        from services.graph.tools import get_current_conversation_id
        from services.memory_service import Memory, store_memories_with_conflict_detection
        from services.provenance_service import turn_source_ref
        from services.scheduled_events_service import intention_valid_until

        conversation_id = get_current_conversation_id()
        naive_at = at_utc.astimezone(timezone.utc).replace(tzinfo=None)
        stored = await store_memories_with_conflict_detection([Memory(
            id=None,
            content=f"{why} — scheduled: {what.strip()[:160]}",
            memory_type="intention",
            importance=0.7,
            temporal_nature="evolving",
            source_type="inferred",
            confidence=0.7,
            source_conversation_id=conversation_id,
            valid_until=intention_valid_until(repeat, naive_at, datetime.utcnow()),
            origin="thought",
            about="self",
            source_ref=turn_source_ref("schedule", conversation_id=conversation_id),
        )])
        return stored[0].id if stored else None
    except Exception as exc:  # noqa: BLE001 — the event still goes in
        print(f"[schedule] could not remember the intention: {exc}")
        return None


async def _add(when: Optional[str], what: Optional[str], kind: Optional[str], repeat: Optional[str],
               memory_id: Optional[str], why: Optional[str], channel: Optional[str]) -> str:
    from services import scheduled_events_service as ses
    from services.graph.tools import get_current_conversation_id

    k = (kind or "task").strip().lower()
    if k not in KINDS:
        raise ToolFailure(f"kind must be one of {', '.join(KINDS)}")
    policy = policy_mod.current_policy()
    if (k == "wake" and not policy.may_wake_later) or (k != "wake" and not policy.may_schedule):
        # The event would fire later as its own turn, able to reach people
        # this one can't (review 2026-10-11).
        raise ToolFailure("this turn can't schedule anything — what you'd schedule would run later with reach "
                          f"this turn doesn't have. Leave it as a note for {owner_name()} (tell_owner) instead")
    text = " ".join((what or "").split())
    if not text:
        raise ToolFailure("add needs what (what to do, or why to wake, in a sentence)")
    at = _parse_when(when)
    if at <= datetime.now(timezone.utc) and not repeat:
        raise ToolFailure("when must be in the future")
    meta: dict = {"requested_by_owner": bool(policy.owner_asked), "kind": k}
    if k == "wake":
        meta.update({"type": "mind_wake", "why": text[:600]})
        try:
            event = await ses.create_event(
                description=f"[mind wake] {text[:200]}", scheduled_at=at, conversation_id="edward-mind",
                delivery_channel="chat", created_by="edward", event_metadata=meta,
                memory_id=(memory_id or "").strip() or None)
        except ValueError as exc:
            raise ToolFailure(str(exc)) from exc
        return f"ok — you will wake at {ses._format_local(event.next_fire_at)} (event {event.id})"

    linked = (memory_id or "").strip() or None
    reason = " ".join((why or "").split())
    if reason and not linked:
        linked = await _remember_why(reason, text, repeat, at)
    try:
        event = await ses.create_event(
            description=text, scheduled_at=at, recurrence_pattern=(repeat or None),
            conversation_id=get_current_conversation_id(), delivery_channel=channel or None,
            created_by="edward", event_metadata=meta, memory_id=linked)
    except ValueError as exc:
        raise ToolFailure(str(exc)) from exc
    fire = ses._format_local(event.next_fire_at) if getattr(event, "next_fire_at", None) else when
    repeats = f" (repeats: {repeat})" if repeat else " (one-time)"
    return f"Event scheduled{repeats}. Next fire: {fire}. ID: {event.id}"


def _calendar_service():
    try:
        return importlib.import_module("services.calendar_service")
    except ImportError:
        return None


def _field(ev: Any, *names: str) -> Any:
    for n in names:
        if isinstance(ev, dict) and ev.get(n) not in (None, ""):
            return ev.get(n)
        v = getattr(ev, n, None)
        if v not in (None, ""):
            return v
    return None


def _as_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


def _event_line(ev: Any) -> str:
    from services.timezone_utils import utc_to_local

    title = _field(ev, "title", "summary") or "(untitled)"
    start, end = _as_dt(_field(ev, "start", "start_at", "starts_at")), _as_dt(_field(ev, "end", "end_at", "ends_at"))
    where = _field(ev, "location", "where")
    all_day = bool(_field(ev, "all_day", "allDay", "isAllDay"))
    if start is None:
        when = "time unknown"
    elif all_day:
        when = utc_to_local(start).strftime("%a %b %-d") + " (all day)"
    else:
        s = utc_to_local(start)
        when = s.strftime("%a %b %-d %-I:%M %p").replace("AM", "am").replace("PM", "pm")
        if end is not None:
            when += "–" + utc_to_local(end).strftime("%-I:%M %p").replace("AM", "am").replace("PM", "pm")
    line = f"- {when}: {title}"
    if where:
        line += f" ({where})"
    ev_id = str(_field(ev, "id") or "")
    if ev_id.startswith("cal-"):
        line += f" id={ev_id}"
    return line


def _today_start() -> datetime:
    """The owner's local midnight today (``calendar_service.day_bounds``'
    start). The window used to start at UTC midnight — the evening before in
    a US zone, and after that hour it skipped the rest of the owner's day."""
    from services.timezone_utils import get_user_tz

    tz = get_user_tz()
    local = _now().astimezone(tz)
    return datetime(local.year, local.month, local.day, tzinfo=tz)


async def _calendar(start: Optional[str], end: Optional[str], query: Optional[str]) -> str:
    begin = _parse_when(start, "start", "calendar") if start else _today_start()
    finish = _parse_when(end, "end", "calendar") if end else begin + timedelta(days=CALENDAR_DEFAULT_DAYS + 1)
    if finish <= begin:
        raise ToolFailure("end must be after start")
    cal = _calendar_service()
    if cal is not None and hasattr(cal, "events_between"):
        try:
            events = await cal.events_between(begin, finish, query)
        except Exception as exc:  # noqa: BLE001 — CalendarUnavailable, a schema change …
            raise ToolFailure(f"{owner_name()}'s calendar can't be read right now ({exc})") from exc
    else:
        from services import display_service

        data = await display_service.get_calendar()
        if (data or {}).get("source") != "apple-mcp":
            raise ToolFailure(f"{owner_name()}'s calendar can't be read right now (apple-mcp did not answer)")
        events = list(data.get("events") or [])
        if query:
            q = query.lower()
            events = [e for e in events if q in str(_field(e, "title", "summary") or "").lower()]
        note = " (today only: the full calendar reader isn't installed)"
        if not events:
            return f"Nothing on {owner_name()}'s calendar today{' matching ' + repr(query) if query else ''}{note}."
        return (f"{_owners_head()} calendar" + note + ":\n"
                + "\n".join(_event_line(e) for e in events[:MAX_CALENDAR_EVENTS]))
    events = list(events or [])
    if not events:
        return f"Nothing on {owner_name()}'s calendar in that window{' matching ' + repr(query) if query else ''}."
    lines = [_event_line(e) for e in events[:MAX_CALENDAR_EVENTS]]
    more = f"\n(+{len(events) - MAX_CALENDAR_EVENTS} more)" if len(events) > MAX_CALENDAR_EVENTS else ""
    return (f"{_owners_head()} calendar ({len(events)} event{'s' if len(events) != 1 else ''}):\n"
            + "\n".join(lines) + more)


# -------------------------------------------- the owner's Reminders (read) --

def _clock(dt: datetime) -> str:
    return dt.strftime("%-I:%M %p").replace("AM", "am").replace("PM", "pm")


def _day(d: date, today: Optional[date] = None) -> str:
    if today is not None:
        if d == today:
            return "today"
        if d == today + timedelta(days=1):
            return "tomorrow"
    return d.strftime("%a %b %-d")


async def _reminders(list_name: Optional[str], query: Optional[str]) -> str:
    from services import reminders_service
    from services.timezone_utils import utc_to_local

    try:
        items = await reminders_service.open_reminders(list_name=list_name, query=query, limit=500)
    except reminders_service.RemindersUnavailable as exc:
        raise ToolFailure(f"{owner_name()}'s Reminders can't be read right now ({exc})") from exc
    where = f" in {list_name.strip()}" if list_name and list_name.strip() else ""
    matching = f" matching {query.strip()!r}" if query and query.strip() else ""
    if not items and where:
        await _list_exists(list_name.strip())
    if not items:
        return f"No open reminders{where}{matching}."
    today = utc_to_local(_now()).date()
    lines = []
    for r in items[:MAX_REMINDERS]:
        if r.due is None:
            due = "no due date"
        elif r.all_day:
            due = f"due {_day(r.due.date(), today)} (all day)"
        else:
            local = utc_to_local(r.due)
            due = f"due {_day(local.date(), today)} {_clock(local)}"
        bits = [r.title, due] + (["flagged"] if r.flagged else [])
        lines.append("- " + " · ".join(bits) + (f" · [{r.list}]" if r.list else "") + f" id=rem-{r.id}")
    more = f"\n(+{len(items) - MAX_REMINDERS} more)" if len(items) > MAX_REMINDERS else ""
    head = f"{_owners_head()} open reminders{where}{matching} ({len(items)}):"
    return head + "\n" + "\n".join(lines) + more


async def _list_exists(name: str) -> None:
    """A list that matches none of the owner's (a typo, Groceries vs Shopping) is
    said, never "No open reminders in Groceries" (review 2026-10-11). The
    same rule as the read: exact, else contained, case-insensitive. When the
    names can't be read the empty answer stands."""
    from services import reminders_service

    try:
        names = await reminders_service.list_names()
    except reminders_service.RemindersUnavailable:
        return
    want = name.casefold()
    if any(want == n.casefold() or want in n.casefold() for n in names):
        return
    raise ToolFailure(f"{owner_name()} has no Reminders list called “{name}” — their lists: "
                      f"{', '.join(names) or 'none'}")


# ------------------------------- the owner's calendar and Reminders (write) --

def _tz_name() -> str:
    from services.timezone_utils import get_user_tz

    tz = get_user_tz()
    return str(getattr(tz, "key", None) or tz)


def _z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ToolFailure(f"{name} must be a real date like 2026-10-12 (got {value!r})") from exc


def _is_date(value: Optional[str]) -> bool:
    return bool(value and DATE_ONLY.match(value.strip()))


def _local(value: Any) -> Optional[datetime]:
    """A helper's ISO instant in the owner's zone."""
    from services.timezone_utils import utc_to_local

    dt = _as_dt(value)
    return utc_to_local(dt) if dt is not None else None


def _days(first: date, last: date) -> str:
    """"Mon Oct 12 (all day)", "Mon Oct 12 – Wed Oct 14 (all day)": ``last`` inclusive."""
    return f"{_day(first)} (all day)" if last <= first else f"{_day(first)} – {_day(last)} (all day)"


def _asked_days(payload: Dict[str, Any]) -> Optional[str]:
    """An all-day write's days as asked (``date``/``end_date``, inclusive) —
    the helper's in-memory end is midnight of the last day, which reads one
    day short (review 2026-10-11). None for a timed write."""
    if not payload.get("all_day") or not payload.get("date"):
        return None
    first = date.fromisoformat(payload["date"])
    return _days(first, date.fromisoformat(payload.get("end_date") or payload["date"]))


def _span(start: Optional[datetime], end: Optional[datetime], all_day: bool) -> str:
    """"Mon Oct 12, 9:00–10:00 am", "Mon Oct 12 (all day)", "Mon Oct 12 – Wed Oct 14 (all day)"."""
    if start is None:
        return "time unknown"
    if all_day:
        first = start.date()
        last = end.date() if end is not None else first
        if end is not None and end.time() == datetime.min.time() and last > first:
            last -= timedelta(days=1)  # an exclusive midnight end
        return _days(first, last)
    if end is None:
        return f"{_day(start.date())}, {_clock(start)}"
    if end.date() != start.date():
        return f"{_day(start.date())}, {_clock(start)} – {_day(end.date())}, {_clock(end)}"
    s, e = _clock(start), _clock(end)
    if s[-2:] == e[-2:]:
        s = s[:-3]
    return f"{_day(start.date())}, {s}–{e}"


def _invite_notes(reply: Dict[str, Any]) -> str:
    """What became of the invitees, as far as the helper can see: saved
    means the event Calendar kept has them (read back after the save);
    whether Calendar's server then sent an invitation it can't see, so this
    never says "Invited" (review 2026-10-11)."""
    out = ""
    saved = [str(a) for a in (reply.get("attendees_saved") or []) if a]
    if saved:
        out += (f" Added {', '.join(saved)} as {'invitees' if len(saved) > 1 else 'an invitee'} — Calendar's server "
                "sends the invitation and I can't see whether it did; want me to email them the details too?")
    unverified = [str(a) for a in (reply.get("attendees_unverified") or []) if a]
    if unverified:
        out += (f" Added {', '.join(unverified)} as {'invitees' if len(unverified) > 1 else 'an invitee'} but "
                "couldn't read the event back to check Calendar kept them — want me to email them the details?")
    for f in reply.get("attendees_failed") or []:
        if isinstance(f, dict) and f.get("email"):
            out += (f" Couldn't invite {f['email']} ({f.get('reason') or 'no reason given'}) — want me to email "
                    "them the details?")
    return out


async def _helper(call: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    """One EventKit helper call; its failures in its own words. A helper
    that never started changed nothing; one that started and gave no answer
    may have saved already, so that is said as "I don't know", with the
    read to do before a retry (review 2026-10-11)."""
    from services import eventkit_service as ek

    what, read = ("Reminders", "reminders") if "reminder" in call else ("calendar", "calendar")
    try:
        return await getattr(ek, call)(payload)
    except ek.EventKitDenied as exc:
        raise ToolFailure(str(exc)) from exc
    except ek.EventKitFailed as exc:
        raise ToolFailure(exc.error) from exc
    except ek.EventKitOutcomeUnknown as exc:
        raise ToolFailure(f"it may or may not have gone through ({exc}) — read {owner_name()}'s {what} "
                          f"(schedule {read}) before trying again, so nothing happens twice") from exc
    except ek.EventKitUnavailable as exc:
        raise ToolFailure(f"I can't change {owner_name()}'s {what} right now ({exc})") from exc


# The same add twice in ten minutes goes in once — send_message's dedupe,
# for the owner's calendar (an event was duplicated on 2026-09-14). Only a
# confirmed add is remembered: after "it may or may not have gone through"
# a retry still runs.
ADD_DEDUPE_WINDOW_S = 600
_RECENT_ADDS: Dict[Tuple[str, ...], Tuple[datetime, str, str]] = {}


def reset_add_history() -> None:
    """Tests: forget the adds."""
    _RECENT_ADDS.clear()


def _add_key(action: str, payload: Dict[str, Any]) -> Tuple[str, ...]:
    fields = ("date", "end_date", "start", "end", "calendar") if action == "event_add" else ("due", "due_date", "list")
    return (action, str(payload.get("title") or "").casefold(),
            *(str(payload.get(f) or "").casefold() for f in fields))


def _already_added(key: Tuple[str, ...]) -> Optional[Tuple[str, str, str]]:
    """(clock, title, id) of the same add within the window, or None."""
    from services.timezone_utils import utc_to_local

    hit = _RECENT_ADDS.get(key)
    if hit is None or not 0 <= (_now() - hit[0]).total_seconds() <= ADD_DEDUPE_WINDOW_S:
        return None
    return _clock(utc_to_local(hit[0])), hit[1], hit[2]


def _note_added(key: Tuple[str, ...], title: str, item_id: str) -> None:
    now = _now()
    for k in [k for k, v in _RECENT_ADDS.items() if (now - v[0]).total_seconds() > ADD_DEDUPE_WINDOW_S]:
        del _RECENT_ADDS[k]
    _RECENT_ADDS[key] = (now, title, item_id)


def _attendees(attendees: Optional[List[str]]) -> List[str]:
    raw = [attendees] if isinstance(attendees, str) else list(attendees or [])
    emails = [str(a).strip() for a in raw if str(a or "").strip()]
    bad = [a for a in emails if "@" not in a or " " in a]
    if bad:
        raise ToolFailure(f"attendees are email addresses to invite (not {', '.join(bad)}) — messages(who) finds "
                          "someone's email")
    return emails


def _event_times(payload: Dict[str, Any], start: Optional[str], end: Optional[str], action: str,
                 new: bool) -> bool:
    """The owner's times into the helper's payload: a date alone is all day
    (floating, no conversion), a time is UTC "Z" (zone-less = the owner's)."""
    s = (start or "").strip()
    e = (end or "").strip()
    if s and _is_date(s):
        first = _parse_date(s, "start")
        if e and not _is_date(e):
            raise ToolFailure("an all-day event's end is a date too (YYYY-MM-DD, its last day)")
        last = _parse_date(e, "end") if e else first
        if last < first:
            raise ToolFailure("end must be on or after start")
        payload.update(all_day=True, date=first.isoformat(), end_date=last.isoformat())
        return True
    if s:
        st = _parse_when(s, "start", action)
        payload.update(all_day=False, start=_z(st))
        if e:
            if _is_date(e):
                raise ToolFailure(f"a timed event's end is a time too (ISO-8601 in {owner_name()}'s local time)")
            en = _parse_when(e, "end", action)
            if en <= st:
                raise ToolFailure("end must be after start")
            payload["end"] = _z(en)
        elif new:
            payload["end"] = _z(st + timedelta(hours=1))
        return True
    if e:
        if new or _is_date(e):
            raise ToolFailure(f"{action} needs start too (ISO-8601 in {owner_name()}'s local time, or a date for "
                              "all day)")
        payload["end"] = _z(_parse_when(e, "end", action))
        return True
    return False


SPANS = ("this", "future")
REPEATS_NOTE = " Only that one — it repeats; span=future reaches the ones after it too."


def _span_arg(span: Optional[str]) -> str:
    """``this`` (the default: that occurrence) or ``future`` (that one and
    every repeat after it)."""
    value = (span or "this").strip().lower()
    if value not in SPANS:
        raise ToolFailure("span is this (that occurrence, the default) or future (that one and every repeat after it)")
    return value


def _cal_id(raw: Optional[str]) -> Tuple[str, Optional[int]]:
    """``cal-<UUID>-<occurrence>`` or ``cal-<UUID>`` → (UUID, Core Data
    seconds or None). A UUID whose last group happens to be all digits is
    not an occurrence."""
    text = (raw or "").strip()
    if not text:
        raise ToolFailure("needs id (from schedule calendar: id=cal-…)")
    body = text[4:] if text.lower().startswith("cal-") else text
    if re.fullmatch(r"[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}", body):
        return body, None
    head, sep, tail = body.rpartition("-")
    if sep and head and tail.isdigit():
        return head, int(tail)
    return body, None


async def _event_hints(uuid: str, cd: Optional[int]) -> Dict[str, Any]:
    """The event as Calendar's database has it, so the helper can find it
    by its occurrence and title when EventKit's id differs."""
    from services import calendar_service

    hints: Dict[str, Any] = {}
    try:
        ev = await calendar_service.event_by_id(uuid, cd)
    except Exception:  # noqa: BLE001 — hints only: unreadable is no hint
        ev = None
    if ev is not None:
        hints["occurrence"] = _z(ev.start)
        hints["hint_title"] = ev.title
    elif cd is not None:
        hints["occurrence"] = _z(calendar_service.CORE_DATA_EPOCH + timedelta(seconds=cd))
    return hints


async def _event_add(what, start, end, calendar, location, notes, attendees) -> str:
    title = " ".join((what or "").split())
    if not title:
        raise ToolFailure("event_add needs what (the event's title)")
    if not (start or "").strip():
        raise ToolFailure(f"event_add needs start (ISO-8601 in {owner_name()}'s local time, or a date YYYY-MM-DD "
                          "for all day)")
    payload: Dict[str, Any] = {"title": title, "timezone": _tz_name()}
    _event_times(payload, start, end, "event_add", new=True)
    for key, value in (("calendar", calendar), ("location", location), ("notes", notes)):
        if (value or "").strip():
            payload[key] = value.strip()
    emails = _attendees(attendees)
    if emails:
        payload["attendees"] = emails
    key = _add_key("event_add", payload)
    if (seen := _already_added(key)) is not None:
        clock, shown, ev_id = seen
        return (f"Already added “{shown}” to {owner_name()}'s calendar at {clock} — not adding it twice. "
                f"id=cal-{ev_id}")
    reply = await _helper("add_event", payload)
    ev = reply.get("event") or {}
    _note_added(key, ev.get("title") or title, str(ev.get("id") or "?"))
    when = _asked_days(payload) or _span(_local(ev.get("start")), _local(ev.get("end")),
                                         bool(ev.get("all_day", payload.get("all_day"))))
    cal = f" ({ev['calendar']})" if ev.get("calendar") else ""
    return (f"Added “{ev.get('title') or title}” to {owner_name()}'s calendar{cal}: {when}. "
            f"id=cal-{ev.get('id') or '?'}"
            + _invite_notes(reply))


async def _event_update(id, what, start, end, calendar, location, notes, attendees, span=None) -> str:
    uuid, cd = _cal_id(id)
    payload: Dict[str, Any] = {"id": uuid, "span": _span_arg(span), "timezone": _tz_name()}
    changed = _event_times(payload, start, end, "event_update", new=False)
    title = " ".join((what or "").split())
    if title:
        payload["title"] = title
    for key, value in (("location", location), ("notes", notes), ("calendar", calendar)):
        if (value or "").strip():  # an empty optional is "not given", never "clear it"
            payload[key] = value.strip()
    emails = _attendees(attendees)
    if emails:
        payload["attendees"] = emails
    if not (changed or title or emails or any(k in payload for k in ("location", "notes", "calendar"))):
        raise ToolFailure("event_update needs what to change: what, start, end, location, notes, calendar or "
                          "attendees")
    payload.update(await _event_hints(uuid, cd))
    reply = await _helper("update_event", payload)
    ev, before = reply.get("event") or {}, reply.get("before") or {}
    name = before.get("title") or payload.get("hint_title") or ev.get("title") or "the event"
    fields = [str(c) for c in reply.get("changed") or []]
    when = _asked_days(payload) or _span(_local(ev.get("start")), _local(ev.get("end")), bool(ev.get("all_day")))
    later = ", and every repeat after it" if ev.get("recurring") and payload["span"] == "future" else ""
    if "time" in fields or changed:
        head = f"Moved “{name}” to {when}{later}."
        rest = [f for f in fields if f != "time"]
        if rest:
            head += f" Also changed: {', '.join(rest)}."
    else:
        head = f"Changed “{name}” ({', '.join(fields) or 'details'}){later}."
    if ev.get("recurring") and payload["span"] == "this":
        head += REPEATS_NOTE
    return f"{head} id=cal-{ev.get('id') or uuid}" + _invite_notes(reply)


async def _event_delete(id, span=None) -> str:
    uuid, cd = _cal_id(id)
    payload: Dict[str, Any] = {"id": uuid, "span": _span_arg(span), "timezone": _tz_name()}
    payload.update(await _event_hints(uuid, cd))
    reply = await _helper("delete_event", payload)
    ev = reply.get("event") or {}
    start = _local(ev.get("start"))
    if start is None:
        when = ""
    elif ev.get("all_day"):
        when = f" ({_day(start.date())}, all day)"
    else:
        when = f" ({_day(start.date())} {_clock(start)})"
    title = ev.get("title") or payload.get("hint_title") or "the event"
    if ev.get("recurring") and payload["span"] == "future":
        return f"Deleted “{title}”{when} and every repeat after it from {owner_name()}'s calendar."
    return (f"Deleted “{title}”{when} from {owner_name()}'s calendar."
            + (REPEATS_NOTE if ev.get("recurring") else ""))


async def _reminder_add(what, when, calendar, notes) -> str:
    title = " ".join((what or "").split())
    if not title:
        raise ToolFailure("reminder_add needs what (the reminder)")
    payload: Dict[str, Any] = {"title": title, "timezone": _tz_name()}
    if (calendar or "").strip():
        payload["list"] = calendar.strip()
    if (notes or "").strip():
        payload["notes"] = notes.strip()
    w = (when or "").strip()
    if w and _is_date(w):
        payload["due_date"] = _parse_date(w, "when").isoformat()
    elif w:
        payload["due"] = _z(_parse_when(w, "when", "reminder_add"))
    key = _add_key("reminder_add", payload)
    if (seen := _already_added(key)) is not None:
        clock, shown, rem_id = seen
        return f"Already added the reminder “{shown}” at {clock} — not adding it twice. id=rem-{rem_id}"
    reply = await _helper("add_reminder", payload)
    r = reply.get("reminder") or {}
    _note_added(key, r.get("title") or title, str(r.get("id") or "?"))
    due_raw = r.get("due")
    due = ""
    if isinstance(due_raw, str) and _is_date(due_raw):
        due = f", due {_day(_parse_date(due_raw, 'due'))}"
    elif due_raw:
        local = _local(due_raw)
        due = f", due {_day(local.date())} {_clock(local)}" if local else ""
    lst = r.get("list") or payload.get("list") or "Reminders"
    return f"Added the reminder “{r.get('title') or title}” to {lst}{due}. id=rem-{r.get('id') or '?'}"


async def _reminder_change(id, complete: bool) -> str:
    from services import reminders_service

    key = (id or "").strip()
    key = key[4:] if key.lower().startswith("rem-") else key
    action = "reminder_complete" if complete else "reminder_delete"
    if not key:
        raise ToolFailure(f"{action} needs id (from schedule reminders: id=rem-…)")
    payload: Dict[str, Any] = {"ids": [key]}
    try:
        found = await reminders_service.reminder_by_id(key)
    except Exception:  # noqa: BLE001 — hints only
        found = None
    if found is not None:
        hints = found.hints or {}
        payload["ids"] = list(dict.fromkeys([key, *[str(i) for i in hints.get("ids") or []]]))
        if hints.get("title"):
            payload["title"] = hints["title"]
        if hints.get("list"):
            payload["list"] = hints["list"]
    reply = await _helper("complete_reminder" if complete else "delete_reminder", payload)
    title = (reply.get("reminder") or {}).get("title") or payload.get("title") or "the reminder"
    return f"Checked off “{title}”." if complete else f"Deleted the reminder “{title}”."


def _checked(reply: Any) -> str:
    text = str(reply if reply is not None else "")
    if text.strip().startswith(("Tool error:", "Could not", "Error:", "Invalid")):
        raise ToolFailure(text.strip().removeprefix("Tool error:").strip())
    return text


async def schedule_impl(action: str, when: Optional[str] = None, what: Optional[str] = None,
                        kind: Optional[str] = None, repeat: Optional[str] = None, id: Optional[str] = None,
                        memory_id: Optional[str] = None, start: Optional[str] = None, end: Optional[str] = None,
                        query: Optional[str] = None, why: Optional[str] = None, calendar: Optional[str] = None,
                        notes: Optional[str] = None, location: Optional[str] = None,
                        attendees: Optional[List[str]] = None, span: Optional[str] = None,
                        status: Optional[str] = None, channel: Optional[str] = None) -> str:
    act = (action or "").strip().lower()
    if act not in SCHEDULE_ACTIONS:
        raise ToolFailure(f"action must be one of {', '.join(SCHEDULE_ACTIONS)}")
    if act in WRITE_ACTIONS:
        if not policy_mod.current_policy().may_change_owners_calendar():
            raise ToolFailure(CHANGE_REFUSAL)
        if act == "event_add":
            return await _event_add(what, start or when, end, calendar, location, notes, attendees)
        if act == "event_update":
            return await _event_update(id, what, start, end, calendar, location, notes, attendees, span)
        if act == "event_delete":
            return await _event_delete(id, span)
        if act == "reminder_add":
            return await _reminder_add(what, when or start, calendar, notes)
        return await _reminder_change(id, complete=(act == "reminder_complete"))
    if act == "reminders":
        return await _reminders(calendar, query)
    if act == "add":
        return await _add(when, what, kind, repeat, memory_id, why, channel)
    legacy = _legacy()
    if act == "list":
        return _checked(await legacy.list_scheduled_events.ainvoke({"status": status}))
    if act == "cancel":
        if not (id or "").strip():
            raise ToolFailure("cancel needs id (from schedule list)")
        return _checked(await legacy.cancel_scheduled_event.ainvoke({"event_id": id}))
    return await _calendar(start, end, query)


@tool
@owner_doc
async def schedule(action: str, when: Optional[str] = None, what: Optional[str] = None, kind: Optional[str] = None,
                   repeat: Optional[str] = None, id: Optional[str] = None, memory_id: Optional[str] = None,
                   start: Optional[str] = None, end: Optional[str] = None, query: Optional[str] = None,
                   why: Optional[str] = None, calendar: Optional[str] = None, notes: Optional[str] = None,
                   location: Optional[str] = None, attendees: Optional[List[str]] = None,
                   span: Optional[str] = None) -> str:
    """Time: reminders, tasks for later, waking yourself, {OWNER}'s calendar and Reminders.

    action:
      add — when (ISO-8601 in {OWNER}'s local time) + what; kind task (default: you do `what` then) | remind (you
        remind {OWNER} then) | wake (you wake to look again; `what` is why); repeat = a cron in {OWNER}'s time;
        why = your reason (kept as an intention); memory_id = the intention a wake serves;
      list — scheduled events; cancel — id;
      calendar — {OWNER}'s calendar: start/end (default today + 7 days), query;
      reminders — {OWNER}'s open Reminders: calendar = a list, query.
    When {OWNER} asks: event_add (what, start, end; a date = all day, end = last day; calendar, location, notes, attendees
      = emails to invite), event_update (id from calendar + what changes), event_delete (id); span=future = later
      repeats too; reminder_add (what, when = due, calendar = list, notes), reminder_complete, reminder_delete (id).
    Work that must outlive a restart is a script plus a schedule."""
    return await schedule_impl(action=action, when=when, what=what, kind=kind, repeat=repeat, id=id,
                               memory_id=memory_id, start=start, end=end, query=query, why=why, calendar=calendar,
                               notes=notes, location=location, attendees=attendees, span=span)
