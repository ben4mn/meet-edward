"""Shopping / household list logic for the hub. Pure functions, no I/O.

A list on a wall panel is not a list in an app: nobody opens it to tidy
up. So it maintains itself — ticked items sink to the bottom, stay long
enough to be un-ticked after a mis-tap, then clear on their own.

Open items are a queue (oldest first), not a stack: the thing added first
is the thing that has been needed longest.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List

DEFAULT_LISTS: List[str] = ["Shopping", "Household"]

# How long a ticked item lingers before the board clears it.
CLEAR_DONE_AFTER_MINUTES = 12 * 60

_UTC = timezone.utc


def _aware(value: datetime) -> datetime:
    """DB timestamps are naive UTC."""
    return value.replace(tzinfo=_UTC) if value.tzinfo is None else value


def _sort_key(item):
    created = _aware(item.created_at)
    # done sorts after open; within each group, oldest first.
    return (1 if item.done else 0, created)


def visible_items(items, now: datetime) -> List[Any]:
    now = _aware(now)
    cutoff = now - timedelta(minutes=CLEAR_DONE_AFTER_MINUTES)

    kept = []
    for item in items:
        # A done row with no timestamp predates done_at; keep it rather
        # than let a NULL quietly delete someone's list.
        if item.done and item.done_at is not None and _aware(item.done_at) < cutoff:
            continue
        kept.append(item)

    return sorted(kept, key=_sort_key)


def to_dict(item) -> Dict[str, Any]:
    return {
        "id": item.id,
        "list_name": item.list_name,
        "text": item.text,
        "done": bool(item.done),
        "created_by": item.created_by,
        "done_by": item.done_by,
    }


def group_by_list(items, now: datetime) -> Dict[str, List[Dict[str, Any]]]:
    """Every default list appears, even empty — the UI needs somewhere to add."""
    grouped: Dict[str, List[Dict[str, Any]]] = {name: [] for name in DEFAULT_LISTS}
    for item in visible_items(items, now):
        grouped.setdefault(item.list_name, []).append(to_dict(item))
    return grouped
