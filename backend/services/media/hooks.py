"""The one door into media for code outside services/media (2026-10-11;
docs/architecture/media.md).

What a ping turn and the ``look`` tool need. iMessage attachments are
addressed by their chat.db id (``attachment:<rowid>``), converted locally
and handed to the model as images or a PDF document. A small in-memory
cache holds the descriptions the describer writes (``describe_ref``).

``register_imessage``, ``enqueue``, ``start``/``stop`` and ``report`` are
seams for a persistent media store and pipeline (``services/media/store.py``,
``pipeline.py``, ``metrics.py``), which this build does not ship: they are
no-ops until those modules exist. Every function falls back to plain
behaviour when media is off (EDWARD_MEDIA=off) or missing: a message is
never lost because a photo could not be read.
"""

from __future__ import annotations

import importlib
import logging
import os
import threading
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Sequence

logger = logging.getLogger(__name__)

DESCRIPTION_CACHE_MAX = 5000
_descriptions: "OrderedDict[str, str]" = OrderedDict()
_desc_lock = threading.Lock()


def enabled() -> bool:
    return os.getenv("EDWARD_MEDIA", "on").strip().lower() not in ("off", "0", "false", "no")


def _mod(name: str):
    if not enabled():
        return None
    try:
        return importlib.import_module(f"services.media.{name}")
    except ImportError:
        return None


def note_description(ref: str, text: Optional[str]) -> None:
    """Remember one item's short description (sync). The describer
    calls it with ``attachment:<rowid>`` once it has described a photo."""
    if not ref or not text:
        return
    with _desc_lock:
        _descriptions[ref] = str(text).strip()[:300]
        _descriptions.move_to_end(ref)
        while len(_descriptions) > DESCRIPTION_CACHE_MAX:
            _descriptions.popitem(last=False)


def describe_ref(ref: str) -> Optional[str]:
    """The short description of ``attachment:<rowid>`` (or ``media:<id>``,
    with a media store); None when unknown or off. Sync.
    ``strip_media_blocks`` puts it into saved history."""
    if not ref or not enabled():
        return None
    with _desc_lock:
        found = _descriptions.get(ref)
    if found:
        return found
    present = _mod("present")
    try:
        return present.describe_ref(ref) if present is not None and hasattr(present, "describe_ref") else None
    except Exception:  # noqa: BLE001
        return None


def placeholder(att: Dict[str, Any], default: str) -> str:
    """How a chat.db attachment reads in text: ``default`` (the listener's
    ``[image: IMG_3533.heic attachment:449321]``) with the description
    inside once there is one. Sync; never raises."""
    try:
        rowid = att.get("rowid")
        desc = describe_ref(f"attachment:{rowid}") if rowid is not None else None
        if desc and default.endswith("]"):
            return f'{default[:-1]} - "{desc}"]'
    except Exception:  # noqa: BLE001
        pass
    return default


async def turn_attachments(attachments: Sequence[Dict[str, Any]], *, max_images: int = 6) -> List[Dict[str, Any]]:
    """``chat_with_memory`` attachment dicts (JPEG images, at most one PDF)
    for listener attachment dicts ``{rowid, path, mime_type, name, label}``.
    [] when off or on any error."""
    present = _mod("present")
    if present is None or not attachments:
        return []
    try:
        return list(await present.turn_attachments(list(attachments), max_images=max_images))
    except Exception as exc:  # noqa: BLE001
        logger.warning("media: turn_attachments failed: %s", exc)
        return []


async def register_imessage(session, *, event_id: str, msg: Dict[str, Any]) -> List[int]:
    """media_items rows for one chat.db message, inside the caller's
    transaction under a SAVEPOINT. [] when off, when there is no media store
    (this build) or on error; the message itself is stored either way."""
    store = _mod("store")
    if store is None:
        return []
    try:
        async with session.begin_nested():
            ids = [int(i) for i in await store.register_imessage(session, event_id=event_id, msg=msg)]
        return ids
    except Exception as exc:  # noqa: BLE001
        from services.pings.hooks import brief_error

        logger.warning("media: register failed for event %s: %s", event_id, brief_error(exc))
        return []


def enqueue(media_ids: Sequence[int], *, priority: str = "background") -> None:
    """Start the media pipeline on registered items (after the caller's
    commit); a no-op without one."""
    pipeline = _mod("pipeline")
    if pipeline is None or not media_ids:
        return
    try:
        pipeline.enqueue([int(i) for i in media_ids], priority=priority)
    except Exception as exc:  # noqa: BLE001
        logger.warning("media: enqueue failed: %s", exc)


async def start() -> None:
    pipeline = _mod("pipeline")
    if pipeline is not None:
        await pipeline.start()


async def stop() -> None:
    pipeline = _mod("pipeline")
    if pipeline is not None:
        await pipeline.stop()


async def report(days: int) -> Dict[str, Any]:
    metrics = _mod("metrics")
    if metrics is None:
        # "P1" (a historical phase label) means: no media store in this build.
        return {"enabled": enabled(), "store": "P1"}
    return await metrics.report(days)
