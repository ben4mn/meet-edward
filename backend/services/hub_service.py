"""DB access for the household hub's pet station.

Pure board/alerting logic lives in ``hub_pet_service``; this module only
loads rows, writes events, and seeds the household on first use.
"""

import json
import logging
import os
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import delete, select

from services.database import (
    CareEventModel,
    CareItemModel,
    PetModel,
    async_session,
)
from services.hub_pet_service import (
    build_board,
    care_day_for,
    household_tz,
    items_to_log,
)

logger = logging.getLogger(__name__)


def _now_local() -> datetime:
    return datetime.now(household_tz())


def _now_utc_naive() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------------------------------------------------------------------------
# Seed — the household's pets and their care, from a JSON file
# ---------------------------------------------------------------------------

# ADAPT: the pets and their care schedule are configured by a seed file, not in the app.
#   How it works here: on the first board read with no pets in the database, the
#   file at EDWARD_HUB_SEED is planted once (unset: the board starts empty).
#   To adapt: copy backend/hub_seed.example.json, describe your pets, set
#   EDWARD_HUB_SEED to its path; later edits need the pets/care_items rows cleared
#   first, or ask Claude to build a pets-and-care editor on /hub/settings.

# Relative seed paths are read from the backend directory.
_BACKEND_DIR = Path(__file__).resolve().parents[1]

_SCHEDULE_TYPES = ("window", "timed", "prn")
_WINDOWS = ("am", "pm")
_ITEM_KINDS = ("feeding", "med", "supplement")

# A bad seed is logged once per file version, not on every board poll.
_seed_warned: set = set()


class SeedError(ValueError):
    """The seed file cannot be planted as written."""


def seed_path() -> Optional[Path]:
    """``EDWARD_HUB_SEED`` as a path, or None when no seed is configured."""
    raw = (os.getenv("EDWARD_HUB_SEED") or "").strip()
    if not raw:
        return None
    path = Path(raw).expanduser()
    return path if path.is_absolute() else _BACKEND_DIR / path


def _parse_clock(raw: Any, where: str) -> time:
    try:
        hour, minute = str(raw).strip().split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        raise SeedError(f"{where}: at_time must be HH:MM, got {raw!r}")


def parse_seed(data: Any) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """``(pets, care_items)`` rows from a seed document. Pure; raises ``SeedError``.

    The document (see ``backend/hub_seed.example.json``)::

        {"pets": [{"id": "biscuit", "name": "Biscuit", "species": "dog",
                   "food": ["am", "pm"],
                   "care": [{"slug": "carprofen", "kind": "med", "name": "Carprofen",
                             "dose": "100mg", "schedule_type": "timed",
                             "window": "am", "at_time": "06:00"}]}]}

    ``food`` (default ``["am", "pm"]``, ``[]`` for none) makes a windowed
    "Food" item per window, id ``<pet>-food-<window>``: bowls never go
    overdue. Each ``care`` entry becomes one slot, id ``<pet>-<slug>-<window>``
    (or ``id`` when given); ``bundle_with`` names another slot's id (a
    supplement stirred into a bowl) so one tap logs both.
    """
    if not isinstance(data, dict) or not isinstance(data.get("pets"), list):
        raise SeedError('the seed needs a "pets" list')

    pets: List[Dict[str, Any]] = []
    items: List[Dict[str, Any]] = []
    for order, pet in enumerate(data["pets"]):
        if not isinstance(pet, dict):
            raise SeedError(f"pets[{order}] is not an object")
        pet_id = str(pet.get("id") or "").strip()
        name = str(pet.get("name") or "").strip()
        if not pet_id or not name:
            raise SeedError(f"pets[{order}] needs an id and a name")
        pets.append({
            "id": pet_id,
            "name": name,
            "species": (str(pet["species"]).strip() or None) if pet.get("species") else None,
            "sort_order": int(pet.get("sort_order", order)),
            "notes": pet.get("notes") or None,
        })

        food = pet.get("food", list(_WINDOWS))
        if not isinstance(food, list) or any(w not in _WINDOWS for w in food):
            raise SeedError(f"{pet_id}: food must be a list of \"am\"/\"pm\"")
        # Twice-daily food by default. Windowed on purpose: never overdue.
        for food_order, window in enumerate(food):
            items.append({
                "id": f"{pet_id}-food-{window}",
                "pet_id": pet_id,
                "kind": "feeding",
                "name": "Food",
                "dose": None,
                "schedule_type": "window",
                "window": window,
                "at_time": None,
                "bundle_with_id": None,
                "sort_order": food_order,
                "needs_review": False,
            })

        care = pet.get("care") or []
        if not isinstance(care, list):
            raise SeedError(f"{pet_id}: care must be a list")
        for care_order, entry in enumerate(care):
            where = f"{pet_id}.care[{care_order}]"
            if not isinstance(entry, dict):
                raise SeedError(f"{where} is not an object")
            item_name = str(entry.get("name") or "").strip()
            if not item_name:
                raise SeedError(f"{where} needs a name")
            kind = entry.get("kind", "med")
            if kind not in _ITEM_KINDS:
                raise SeedError(f"{where}: kind must be one of {', '.join(_ITEM_KINDS)}")
            schedule = entry.get("schedule_type", "window")
            if schedule not in _SCHEDULE_TYPES:
                raise SeedError(f"{where}: schedule_type must be one of {', '.join(_SCHEDULE_TYPES)}")
            window = entry.get("window")
            if schedule == "prn":
                window = None
            elif window not in _WINDOWS:
                raise SeedError(f"{where}: window must be \"am\" or \"pm\"")
            at_time = None
            if schedule == "timed":
                if not entry.get("at_time"):
                    raise SeedError(f"{where}: a timed item needs at_time (HH:MM)")
                at_time = _parse_clock(entry["at_time"], where)
            slug = str(entry.get("slug") or item_name).strip().lower().replace(" ", "-")
            item_id = str(entry.get("id") or f"{pet_id}-{slug}" + (f"-{window}" if window else ""))
            items.append({
                "id": item_id,
                "pet_id": pet_id,
                "kind": kind,
                "name": item_name,
                "dose": (str(entry["dose"]) if entry.get("dose") else None),
                "schedule_type": schedule,
                "window": window,
                "at_time": at_time,
                "bundle_with_id": entry.get("bundle_with") or None,
                "sort_order": int(entry.get("sort_order", care_order)),
                # Unconfirmed (a drug name or strength copied from a
                # sitter's note): the hub badges it until a human confirms.
                "needs_review": bool(entry.get("needs_review", False)),
            })

    ids = [i["id"] for i in items]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise SeedError(f"duplicate care item ids: {', '.join(dupes)}")
    pet_ids = [p["id"] for p in pets]
    if len(set(pet_ids)) != len(pet_ids):
        raise SeedError("duplicate pet ids")
    known = set(ids)
    for item in items:
        if item["bundle_with_id"] and item["bundle_with_id"] not in known:
            raise SeedError(f"{item['id']}: bundle_with {item['bundle_with_id']!r} is not a care item id")
    return pets, items


def load_seed(path: Optional[Path] = None) -> Optional[Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]]:
    """The configured seed as rows, or None (unset, missing or invalid — logged)."""
    path = path or seed_path()
    if path is None:
        return None
    version = None
    try:
        version = path.stat().st_mtime
        return parse_seed(json.loads(path.read_text()))
    except (OSError, ValueError) as exc:  # SeedError and JSONDecodeError are ValueErrors
        key = (str(path), version, str(exc))
        if key not in _seed_warned:
            _seed_warned.add(key)
            logger.error("hub: seed %s not planted: %s", path, exc)
        return None


async def seed_if_empty() -> bool:
    """Idempotent. Plants ``EDWARD_HUB_SEED`` into an empty board; returns
    True if it planted anything. Unset, the board starts (and stays) empty."""
    path = seed_path()
    if path is None:
        return False

    async with async_session() as session:
        existing = (await session.execute(select(PetModel.id))).scalars().all()
        if existing:
            return False

        seed = load_seed(path)
        if seed is None or not seed[0]:
            return False
        pets, items = seed
        for pet in pets:
            session.add(PetModel(**pet))
        for item in items:
            session.add(CareItemModel(**item))
        await session.commit()

    logger.info("hub: seeded %d pets and %d care items from %s", len(pets), len(items), path)
    return True


# ---------------------------------------------------------------------------
# Read
# ---------------------------------------------------------------------------

async def get_board(now: Optional[datetime] = None) -> Dict[str, Any]:
    await seed_if_empty()
    now = now or _now_local()

    async with async_session() as session:
        pets = (await session.execute(
            select(PetModel).where(PetModel.active.is_(True))
        )).scalars().all()
        items = (await session.execute(
            select(CareItemModel).where(CareItemModel.active.is_(True))
        )).scalars().all()
        # Two days back comfortably covers the 3am care-day boundary.
        since = _now_utc_naive() - timedelta(days=2)
        events = (await session.execute(
            select(CareEventModel).where(CareEventModel.given_at >= since)
        )).scalars().all()

    return build_board(pets, items, events, now=now)


# ---------------------------------------------------------------------------
# Write
# ---------------------------------------------------------------------------

async def log_care(
    item_id: str,
    given_by: str,
    status: str = "given",
    method: str = "tap",
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a give. (Edward's surfaces are his own call — see expression_service.)"""
    return await _log_care_impl(item_id, given_by=given_by, status=status, method=method, note=note)


async def _log_care_impl(
    item_id: str,
    given_by: str,
    status: str = "given",
    method: str = "tap",
    note: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a give. Expands bundles, so a bowl logs what is stirred into it."""
    async with async_session() as session:
        items = (await session.execute(
            select(CareItemModel).where(CareItemModel.active.is_(True))
        )).scalars().all()

        target_ids = items_to_log(item_id, items)
        if not target_ids:
            return {"logged": [], "error": "unknown_item"}

        stamp = _now_utc_naive()
        for target in target_ids:
            session.add(CareEventModel(
                care_item_id=target,
                given_at=stamp,
                given_by=given_by,
                method=method,
                status=status,
                note=note,
            ))
        await session.commit()

    return {"logged": target_ids, "status": status, "given_by": given_by}


async def undo_care(item_id: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """Remove this care day's events for an item and anything bundled with it.

    Mis-taps are guaranteed at 6am with a dog leaning on you, and a log
    nobody trusts is worse than no log — so undo is cheap and total.
    """
    now = now or _now_local()
    care_day = care_day_for(now)
    # Care day starts at 3am local; convert that boundary to naive UTC.
    start_local = datetime.combine(
        care_day, datetime.min.time(), tzinfo=household_tz(),
    ).replace(hour=3)
    start_utc = start_local.astimezone(timezone.utc).replace(tzinfo=None)

    async with async_session() as session:
        items = (await session.execute(
            select(CareItemModel).where(CareItemModel.active.is_(True))
        )).scalars().all()

        target_ids = items_to_log(item_id, items)
        if not target_ids:
            return {"cleared": [], "error": "unknown_item"}

        await session.execute(
            delete(CareEventModel).where(
                CareEventModel.care_item_id.in_(target_ids),
                CareEventModel.given_at >= start_utc,
            )
        )
        await session.commit()

    return {"cleared": target_ids}


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

from services.database import HubListItemModel  # noqa: E402
from services import hub_list_service  # noqa: E402


async def get_lists() -> Dict[str, Any]:
    async with async_session() as session:
        rows = (await session.execute(select(HubListItemModel))).scalars().all()
    return {
        "lists": hub_list_service.group_by_list(rows, now=datetime.now(timezone.utc)),
        "names": hub_list_service.DEFAULT_LISTS,
    }


async def add_list_item(list_name: str, text: str, created_by: str) -> Dict[str, Any]:
    text = (text or "").strip()
    if not text:
        return {"error": "empty"}

    async with async_session() as session:
        row = HubListItemModel(
            list_name=list_name, text=text, created_by=created_by,
        )
        session.add(row)
        await session.commit()
        return {"id": row.id, "text": row.text, "list_name": list_name}


async def set_list_item_done(item_id: str, done: bool, done_by: str) -> Dict[str, Any]:
    async with async_session() as session:
        row = await session.get(HubListItemModel, item_id)
        if row is None:
            return {"error": "not_found"}
        row.done = done
        row.done_by = done_by if done else None
        row.done_at = _now_utc_naive() if done else None
        await session.commit()
        return {"id": item_id, "done": done}


async def delete_list_item(item_id: str) -> Dict[str, Any]:
    async with async_session() as session:
        row = await session.get(HubListItemModel, item_id)
        if row is None:
            return {"error": "not_found"}
        await session.delete(row)
        await session.commit()
    return {"deleted": item_id}


# ---------------------------------------------------------------------------
# Who is at the hub (2026-09-24) — pure rules in hub_who_service
# ---------------------------------------------------------------------------

from services import hub_who_service  # noqa: E402
from services.database import DisplayConfigModel  # noqa: E402

# One free-form JSON row, like the screensaver settings (id="hub"), so the
# shape can change without a migration.
HUB_WHO_CONFIG_ID = "hub_who"


def _presence_tz():
    from services.timezone_utils import get_user_tz

    return get_user_tz()


async def _load_hub_who() -> Optional[Dict[str, Any]]:
    async with async_session() as session:
        row = await session.get(DisplayConfigModel, HUB_WHO_CONFIG_ID)
        if row is None:
            return None
        try:
            data = json.loads(row.data or "{}")
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, dict) else None


async def _save_hub_who(state: Dict[str, Any]) -> None:
    async with async_session() as session:
        row = await session.get(DisplayConfigModel, HUB_WHO_CONFIG_ID)
        if row is None:
            session.add(DisplayConfigModel(id=HUB_WHO_CONFIG_ID, data=json.dumps(state), updated_by="hub"))
        else:
            row.data = json.dumps(state)
            row.updated_by = "hub"
        await session.commit()


def _who_payload(state: Optional[Dict[str, Any]], now: datetime) -> Dict[str, Any]:
    person = hub_who_service.current_hub_person(state, now)
    return {
        "person": person,
        "since": state.get("since") if person and state else None,
        "last_seen": state.get("last_seen") if person and state else None,
        "people": list(hub_who_service.hub_people()),
    }


async def get_hub_person(now: Optional[datetime] = None) -> Dict[str, Any]:
    """Who is at the hub right now (None after 30 minutes idle)."""
    now = now or datetime.now(timezone.utc)
    return _who_payload(await _load_hub_who(), now)


async def set_hub_person(person: str, now: Optional[datetime] = None) -> Dict[str, Any]:
    """The hub's who-toggle changed. Raises ValueError for anyone outside the household."""
    name = hub_who_service.normalize_person(person)
    if name is None:
        raise ValueError(f"unknown person {person!r}; the hub knows {', '.join(hub_who_service.hub_people())}")
    now = now or datetime.now(timezone.utc)
    state = hub_who_service.record_selection(await _load_hub_who(), name, now)
    await _save_hub_who(state)
    return _who_payload(state, now)


async def touch_hub_person(person: Optional[str], now: Optional[datetime] = None) -> None:
    """Someone did something at the hub (a dose, a list line): they are
    there. Silent for anyone outside the household and on any failure — a
    presence refresh must never fail the log it rides on."""
    name = hub_who_service.normalize_person(person)
    if name is None:
        return
    try:
        now = now or datetime.now(timezone.utc)
        await _save_hub_who(hub_who_service.record_selection(await _load_hub_who(), name, now))
    except Exception as exc:  # noqa: BLE001
        logger.debug("hub: presence refresh failed: %s", exc)


async def hub_presence_line(now: Optional[datetime] = None) -> Optional[str]:
    """``"At the hub: Rowan (since 10:02)"`` for the mind's pack and the
    pulse signals, or None when nobody has been at the hub for 30 minutes."""
    try:
        now = now or datetime.now(timezone.utc)
        return hub_who_service.presence_line(await _load_hub_who(), now, _presence_tz())
    except Exception as exc:  # noqa: BLE001
        logger.debug("hub: presence line failed: %s", exc)
        return None
