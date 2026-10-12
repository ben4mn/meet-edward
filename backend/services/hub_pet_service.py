"""Pet board logic for the household hub (`/hub`).

Pure functions: no DB, no I/O. The router loads rows and hands them here.

Two rules drive everything, and both exist to protect a single scarce
resource — the household's willingness to trust an amber badge:

  1. ``schedule_type="window"`` items (cat wet food, given "twice daily,
     whenever") NEVER go overdue. A dim bowl is information; it is not an
     alarm. If free-fed wet food could raise an alert, the alert colour
     would be meaningless within a week.

  2. ``schedule_type="timed"`` items (a 06:00/18:00 med protocol) DO go
     overdue, after ``OVERDUE_GRACE_MINUTES``, and are the only thing that
     lands in ``board["alerts"]``.

The care day starts at 3am local rather than midnight, so a late-night
feeding lights the bowl for the night it happened instead of jumping the
next morning. "Local" is the owner's timezone from settings
(``timezone_utils.get_user_tz``): the hub is in the owner's home.
"""

from datetime import date, datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, List, Optional

# Bowls and doses reset at 3am local, not midnight.
CARE_DAY_START_HOUR = 3

# Windows split at noon.
WINDOW_SPLIT_HOUR = 12

# How late a *timed* dose may run before it reads as overdue.
OVERDUE_GRACE_MINUTES = 30

# Past this, a dose stops being actionable and stops alerting: it becomes
# "missed" and is kept only for the adherence record. Whether to catch up a
# long-late dose is a conversation with a vet, not something a red badge on
# a kitchen screen should be nagging about — and an alert nobody can act on
# is exactly what teaches people to ignore the colour.
MISSED_AFTER_MINUTES = 240

_UTC = timezone.utc


def household_tz() -> tzinfo:
    """The household's zone: the owner's timezone from settings, read on
    every call (a settings change applies at once)."""
    from services.timezone_utils import get_user_tz

    return get_user_tz()


class _HouseholdZone(tzinfo):
    """A tzinfo that is always ``household_tz()``, looked up when used.

    For callers that want a zone object rather than a function
    (``now.astimezone(HOUSEHOLD_TZ)``); new code calls ``household_tz()``.
    """

    def _zone(self) -> tzinfo:
        return household_tz()

    def utcoffset(self, dt):
        return self._zone().utcoffset(None if dt is None else dt.replace(tzinfo=None))

    def dst(self, dt):
        return self._zone().dst(None if dt is None else dt.replace(tzinfo=None))

    def tzname(self, dt):
        return self._zone().tzname(None if dt is None else dt.replace(tzinfo=None))

    def fromutc(self, dt):
        zone = self._zone()
        return zone.fromutc(dt.replace(tzinfo=zone)).replace(tzinfo=self)

    def __repr__(self) -> str:
        return f"<household zone: {self._zone()}>"


HOUSEHOLD_TZ = _HouseholdZone()


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------

def _tz_of(now: datetime) -> Any:
    return now.tzinfo or household_tz()


def _as_local(now: datetime) -> datetime:
    """Coerce to an aware local datetime."""
    if now.tzinfo is None:
        return now.replace(tzinfo=household_tz())
    return now


def care_day_for(now: datetime) -> date:
    """The care day a moment belongs to. Before 3am, that's yesterday."""
    local = _as_local(now)
    if local.hour < CARE_DAY_START_HOUR:
        return (local - timedelta(days=1)).date()
    return local.date()


def current_window(now: datetime) -> str:
    """"am" or "pm" for the care day. Pre-3am is the previous day's pm."""
    local = _as_local(now)
    if local.hour < CARE_DAY_START_HOUR:
        return "pm"
    return "am" if local.hour < WINDOW_SPLIT_HOUR else "pm"


def _event_local(given_at: datetime, tz) -> datetime:
    """DB timestamps are naive UTC; render them in household local time."""
    if given_at.tzinfo is None:
        given_at = given_at.replace(tzinfo=_UTC)
    return given_at.astimezone(tz)


def _fmt_clock(dt: datetime) -> str:
    """6:40a / 12:05p — compact enough for a 275px column."""
    hour = dt.hour % 12 or 12
    suffix = "a" if dt.hour < 12 else "p"
    return f"{hour}:{dt.minute:02d}{suffix}"


def _marker_for(name: str) -> str:
    """Short badge for a bundled item: "Vitamin E" -> "+E", "Probiotic powder" -> "+P"."""
    tokens = (name or "").split()
    if tokens and len(tokens[-1]) <= 2:
        return f"+{tokens[-1]}"
    return f"+{name[:1].upper()}" if name else "+"


# ---------------------------------------------------------------------------
# Board
# ---------------------------------------------------------------------------

def _latest_events_by_item(events, care_day: date, tz) -> Dict[Any, Any]:
    """Keep the most recent event per item, scoped to this care day."""
    latest: Dict[Any, Any] = {}
    for ev in events:
        local = _event_local(ev.given_at, tz)
        if care_day_for(local) != care_day:
            continue
        prior = latest.get(ev.care_item_id)
        if prior is None or _event_local(prior.given_at, tz) < local:
            latest[ev.care_item_id] = ev
    return latest


def _scheduled_at(item, care_day: date, tz) -> Optional[datetime]:
    if getattr(item, "at_time", None) is None:
        return None
    return datetime.combine(care_day, item.at_time, tzinfo=tz)


def _med_entry(item, event, care_day: date, tz, now: datetime) -> Dict[str, Any]:
    scheduled = _scheduled_at(item, care_day, tz)

    if event is not None and event.status == "given":
        status = "given"
    elif event is not None and event.status in ("refused", "skipped"):
        status = event.status
    elif item.schedule_type == "timed" and scheduled is not None:
        late = (now - scheduled).total_seconds() / 60
        if late > MISSED_AFTER_MINUTES:
            status = "missed"
        elif late > OVERDUE_GRACE_MINUTES:
            status = "overdue"
        else:
            status = "due"
    else:
        status = "due"

    return {
        "id": item.id,
        "name": item.name,
        "dose": getattr(item, "dose", None),
        "kind": item.kind,
        "window": item.window,
        "at_time": item.at_time.strftime("%H:%M") if scheduled else None,
        "status": status,
        "by": event.given_by if event is not None else None,
        "at_local": _fmt_clock(_event_local(event.given_at, tz)) if event else None,
        "needs_review": bool(getattr(item, "needs_review", False)),
        "minutes_late": (
            int((now - scheduled).total_seconds() // 60)
            if status in ("overdue", "missed") and scheduled
            else None
        ),
    }


def _feeding_entry(item, event, children, tz) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "item_id": item.id if item else None,
        "done": bool(event is not None and event.status == "given"),
        "by": event.given_by if event is not None else None,
        "at_local": _fmt_clock(_event_local(event.given_at, tz)) if event else None,
        # Feeding is windowed, so this is structurally always False. It is
        # present so the frontend can render bowls and doses with one
        # component without special-casing.
        "overdue": False,
        "marker": None,
        "bundled_item_ids": [],
    }
    if children:
        entry["marker"] = _marker_for(children[0].name)
        entry["bundled_item_ids"] = [c.id for c in children]
    return entry


def build_board(pets, items, events, now: datetime) -> Dict[str, Any]:
    """Render the whole pet station in one payload."""
    tz = _tz_of(now)
    now = _as_local(now)
    care_day = care_day_for(now)
    latest = _latest_events_by_item(events, care_day, tz)

    active = [i for i in items if getattr(i, "active", True)]
    children_of: Dict[Any, List[Any]] = {}
    for item in active:
        parent = getattr(item, "bundle_with_id", None)
        if parent is not None:
            children_of.setdefault(parent, []).append(item)

    board_pets: List[Dict[str, Any]] = []
    alerts: List[Dict[str, Any]] = []

    for pet in sorted(pets, key=lambda p: (getattr(p, "sort_order", 0), p.name)):
        mine = [i for i in active if i.pet_id == pet.id]
        top_level = [i for i in mine if getattr(i, "bundle_with_id", None) is None]

        feedings: Dict[str, Any] = {}
        for window in ("am", "pm"):
            item = next(
                (i for i in top_level if i.kind == "feeding" and i.window == window),
                None,
            )
            feedings[window] = _feeding_entry(
                item,
                latest.get(item.id) if item else None,
                children_of.get(item.id, []) if item else [],
                tz,
            )

        meds = []
        for item in sorted(
            (i for i in top_level if i.kind != "feeding" and i.schedule_type != "prn"),
            key=lambda i: (getattr(i, "sort_order", 0), i.name),
        ):
            entry = _med_entry(item, latest.get(item.id), care_day, tz, now)
            meds.append(entry)
            if entry["status"] == "overdue":
                alerts.append({
                    "pet": pet.name,
                    "item": item.name,
                    "minutes_late": entry["minutes_late"],
                })

        board_pets.append({
            "id": pet.id,
            "name": pet.name,
            "species": getattr(pet, "species", None),
            "photo_url": getattr(pet, "photo_url", None),
            "feedings": feedings,
            "meds": meds,
        })

    return {
        "care_day": care_day.isoformat(),
        "window": current_window(now),
        "pets": board_pets,
        "alerts": alerts,
    }


def items_to_log(item_id, items) -> List[Any]:
    """Which item ids a single tap should record.

    Tapping a parent (a cat's PM bowl) also logs anything bundled into it
    (a probiotic powder stirred into the food) — they are one physical act.
    Tapping a bundled child directly records only the child, which is how
    you note that the cat took the powder but refused the food.
    """
    active = [i for i in items if getattr(i, "active", True)]
    if not any(i.id == item_id for i in active):
        return []
    children = [
        i.id for i in active if getattr(i, "bundle_with_id", None) == item_id
    ]
    return [item_id] + children
