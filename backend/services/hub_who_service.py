"""Who is at the hub — the household, not a user (2026-09-24).

The pet-station hub themes itself per person from a small toggle
(``hub/lib/whoStore.ts``), but only the panel knew who was standing there,
so Edward's pack and pulse talked about the owner whoever tapped the
screen. The hub now posts the selection (``POST /api/hub/who``) and every
write it makes on someone's behalf (a dose, a list line) refreshes it. The
stay clears after ``IDLE_MINUTES`` without activity: the toggle is
deliberately sticky on the panel (a stale name corrected with one tap
beats blocking a 6 am log), but "who is at the hub right now" must not be
yesterday's tap.

The household is ``owner.household()`` (``EDWARD_HOUSEHOLD``, owner
first); ``GET /api/hub/who`` serves it as ``people`` and the panel draws
its toggle from that list.

Pure: the stored state is a dict ``{"person", "since", "last_seen"}`` with
ISO-8601 UTC timestamps; ``hub_service`` loads and saves it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone, tzinfo
from typing import Any, Dict, Optional, Tuple

from services.owner import household

IDLE_MINUTES = 30

# The word the code uses for the owner ("owner" in a log or a test) names
# the first person in the household.
_OWNER_WORD = "owner"


def hub_people() -> Tuple[str, ...]:
    """The people at the hub, owner first (``EDWARD_HOUSEHOLD``), read per call."""
    return tuple(household())


def __getattr__(name: str):
    # ``HUB_PEOPLE`` was a constant; it now follows the environment.
    if name == "HUB_PEOPLE":
        return hub_people()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def normalize_person(raw: Any) -> Optional[str]:
    """The canonical name of someone in the household, or None."""
    if not isinstance(raw, str):
        return None
    key = raw.strip().lower()
    if not key:
        return None
    people = hub_people()
    for name in people:
        if name.lower() == key:
            return name
    if key == _OWNER_WORD and people:
        return people[0]
    return None


def _parse(ts: Any) -> Optional[datetime]:
    if not isinstance(ts, str) or not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _aware(now: datetime) -> datetime:
    return now if now.tzinfo else now.replace(tzinfo=timezone.utc)


def current_hub_person(last: Optional[Dict[str, Any]], now: datetime) -> Optional[str]:
    """Who is at the hub now: the last person seen there, unless the panel
    has been idle for more than ``IDLE_MINUTES``."""
    if not last:
        return None
    person = normalize_person(last.get("person"))
    seen = _parse(last.get("last_seen"))
    if person is None or seen is None:
        return None
    if _aware(now) - seen > timedelta(minutes=IDLE_MINUTES):
        return None
    return person


def record_selection(last: Optional[Dict[str, Any]], person: str, now: datetime) -> Dict[str, str]:
    """The state after ``person`` picks (or acts at) the hub at ``now``.

    The same person still present keeps the start of their stay; anyone
    else, or the same person back after going idle, starts a new one.
    """
    now = _aware(now)
    stamp = now.isoformat()
    if current_hub_person(last, now) == person and _parse((last or {}).get("since")):
        return {"person": person, "since": str(last["since"]), "last_seen": stamp}
    return {"person": person, "since": stamp, "last_seen": stamp}


def presence_line(last: Optional[Dict[str, Any]], now: datetime, tz: tzinfo) -> Optional[str]:
    """``"At the hub: Rowan (since 10:02)"`` or None when nobody is there."""
    person = current_hub_person(last, now)
    since = _parse((last or {}).get("since"))
    if person is None or since is None:
        return None
    return f"At the hub: {person} (since {since.astimezone(tz):%H:%M})"
