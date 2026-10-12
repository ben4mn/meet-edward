"""Screensaver settings for the hub.

Persisted as one JSON row (DisplayConfigModel with id="hub") so shape
changes never need a migration.

Everything arriving here comes from a touchscreen slider or an HTTP body,
so it is all untrusted: coerce, clamp, fall back. A bad value must never
blank the screensaver or spin the album at event-loop speed.
"""

import logging
import math
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

DEFAULT_SLIDE_SECONDS = 30
MIN_SLIDE_SECONDS = 5
MAX_SLIDE_SECONDS = 600

TRANSITIONS = ("crossfade", "kenburns", "slide")
DEFAULT_TRANSITION = "kenburns"

DEFAULT_EDWARD_PRESENTS = True

CONFIG_ID = "hub"


def _as_int(value: Any, fallback: int) -> int:
    """Ints, floats and numeric strings (forms send strings). NaN/inf are
    not numbers we can clamp, so they take the fallback."""
    if isinstance(value, bool):
        return fallback
    try:
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return fallback
        return int(float(value))
    except (TypeError, ValueError):
        return fallback


def _as_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    if isinstance(value, (int, float)):
        return bool(value)
    return fallback


def normalize(raw: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Coerce a stored/submitted blob into the exact settings shape."""
    data = raw if isinstance(raw, dict) else {}

    seconds = _as_int(data.get("slide_seconds"), DEFAULT_SLIDE_SECONDS)
    seconds = max(MIN_SLIDE_SECONDS, min(MAX_SLIDE_SECONDS, seconds))

    transition = data.get("transition")
    if transition not in TRANSITIONS:
        transition = DEFAULT_TRANSITION

    # Only known keys are returned — an unknown key is never persisted.
    return {
        "slide_seconds": seconds,
        "transition": transition,
        "edward_presents": _as_bool(
            data.get("edward_presents"), DEFAULT_EDWARD_PRESENTS
        ),
    }


# ---------------------------------------------------------------------------
# Persistence (reuses display_config's free-form JSON row, id="hub")
# ---------------------------------------------------------------------------

import json  # noqa: E402

from services.database import DisplayConfigModel, async_session  # noqa: E402


async def get_settings() -> Dict[str, Any]:
    async with async_session() as session:
        row = await session.get(DisplayConfigModel, CONFIG_ID)
        raw = {}
        if row is not None:
            try:
                raw = json.loads(row.data or "{}")
            except json.JSONDecodeError:
                logger.warning("hub settings row was not valid JSON; using defaults")
    return normalize(raw)


async def update_settings(patch: Dict[str, Any], updated_by: str = "user") -> Dict[str, Any]:
    """Merge a partial update over what is stored, normalizing the result."""
    current = await get_settings()
    merged = normalize({**current, **(patch or {})})

    async with async_session() as session:
        row = await session.get(DisplayConfigModel, CONFIG_ID)
        if row is None:
            row = DisplayConfigModel(id=CONFIG_ID, data=json.dumps(merged))
            session.add(row)
        else:
            row.data = json.dumps(merged)
        row.updated_by = updated_by
        await session.commit()

    return merged
