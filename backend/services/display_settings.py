"""Read/write the always-on display config (single-row JSON store).

Settings live in the ``display_config`` table as a JSON blob so the shape
can evolve without migrations. Written from two places: the display's gear
panel (``user``) and Edward via ``update_display_settings`` (``edward``).
"""

import json
from typing import Any, Dict, Optional

from sqlalchemy import select

from services.database import async_session, DisplayConfigModel


# All panels from CLAUDE_CODE_PROMPT.md.
ALL_PANELS = (
    "clock",
    "alerts",
    "calendar",
    "heartbeat",
    "now_thinking",
    "recents",
    "workers",
    "vitals",
    "memory_ticker",
)

ALLOWED_ACCENTS = {"green", "blue", "amber", "violet"}
ALLOWED_TOD = {"auto", "morning", "day", "dusk", "night"}
ALLOWED_DENSITY = {"loose", "normal", "dense"}
ALLOWED_AVATAR_SIZES = {"sm", "md", "lg", "xl"}
# The stage (2026-09-28): the owner's own switches on the screen's panel —
# Edward's thinking on the screen, and his lines spoken aloud.
BOOL_SETTINGS = ("show_thinking", "speak_aloud")


def default_settings() -> Dict[str, Any]:
    return {
        "accent": "green",
        "time_of_day": "auto",
        "density": "normal",
        "avatar_size": "lg",
        "show_panels": {p: True for p in ALL_PANELS},
        "show_thinking": True,
        "speak_aloud": True,
    }


def _merge_with_defaults(stored: Dict[str, Any]) -> Dict[str, Any]:
    """Backfill missing keys so older stored blobs still render correctly."""
    out = default_settings()
    for k, v in stored.items():
        if k == "show_panels" and isinstance(v, dict):
            out["show_panels"] = {**out["show_panels"], **v}
        elif k in out:
            out[k] = v
    return out


async def get_settings() -> Dict[str, Any]:
    async with async_session() as session:
        row = (await session.execute(
            select(DisplayConfigModel).where(DisplayConfigModel.id == "default")
        )).scalar_one_or_none()
        if row is None:
            return default_settings()
        try:
            stored = json.loads(row.data) if row.data else {}
        except json.JSONDecodeError:
            stored = {}
        return _merge_with_defaults(stored)


def _validate(updates: Dict[str, Any]) -> Dict[str, Any]:
    """Drop keys with invalid values rather than 400-ing — Edward's tool calls
    can be a little sloppy and we'd rather the rest of the patch land than reject
    the whole thing."""
    cleaned: Dict[str, Any] = {}
    if "accent" in updates and updates["accent"] in ALLOWED_ACCENTS:
        cleaned["accent"] = updates["accent"]
    if "time_of_day" in updates and updates["time_of_day"] in ALLOWED_TOD:
        cleaned["time_of_day"] = updates["time_of_day"]
    if "density" in updates and updates["density"] in ALLOWED_DENSITY:
        cleaned["density"] = updates["density"]
    if "avatar_size" in updates and updates["avatar_size"] in ALLOWED_AVATAR_SIZES:
        cleaned["avatar_size"] = updates["avatar_size"]
    if "show_panels" in updates and isinstance(updates["show_panels"], dict):
        cleaned_panels = {
            k: bool(v) for k, v in updates["show_panels"].items() if k in ALL_PANELS
        }
        if cleaned_panels:
            cleaned["show_panels"] = cleaned_panels
    for key in BOOL_SETTINGS:
        if isinstance(updates.get(key), bool):
            cleaned[key] = updates[key]
    return cleaned


async def update_settings(
    updates: Dict[str, Any],
    updated_by: str = "user",
) -> Dict[str, Any]:
    cleaned = _validate(updates)

    async with async_session() as session:
        row = (await session.execute(
            select(DisplayConfigModel).where(DisplayConfigModel.id == "default")
        )).scalar_one_or_none()

        if row is None:
            current = default_settings()
            row = DisplayConfigModel(id="default", data=json.dumps(current))
            session.add(row)
        else:
            try:
                current = _merge_with_defaults(json.loads(row.data) if row.data else {})
            except json.JSONDecodeError:
                current = default_settings()

        # Merge: show_panels is a dict-merge; everything else is replace.
        for k, v in cleaned.items():
            if k == "show_panels":
                current["show_panels"] = {**current["show_panels"], **v}
            else:
                current[k] = v

        row.data = json.dumps(current)
        row.updated_by = updated_by

        await session.commit()
        return current
