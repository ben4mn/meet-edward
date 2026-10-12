"""
Widget service for Edward's iOS home screen widget via Scriptable.

Manages widget state (what the widget displays) and access tokens
for the Scriptable app to fetch widget content.
"""

import json
import secrets
from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional

from sqlalchemy import select

from services.database import async_session, WidgetStateModel, WidgetTokenModel


def is_configured() -> bool:
    """Check if widget service is configured. Always True (no external deps)."""
    return True


def get_status() -> dict:
    """Get widget service status."""
    return {
        "status": "connected",
        "status_message": "Widget service ready",
    }


async def get_or_create_token() -> str:
    """Get the active widget token, creating one if none exists."""
    async with async_session() as session:
        result = await session.execute(
            select(WidgetTokenModel).where(WidgetTokenModel.is_active == True)
        )
        token_row = result.scalar_one_or_none()

        if token_row:
            return token_row.token

        # Create a new token
        token_row = WidgetTokenModel(
            token=secrets.token_urlsafe(32),
        )
        session.add(token_row)
        await session.commit()
        return token_row.token


async def regenerate_token() -> str:
    """Deactivate all existing tokens and create a new one."""
    async with async_session() as session:
        # Deactivate all existing tokens
        result = await session.execute(
            select(WidgetTokenModel).where(WidgetTokenModel.is_active == True)
        )
        for row in result.scalars().all():
            row.is_active = False

        # Create new token
        token_row = WidgetTokenModel(
            token=secrets.token_urlsafe(32),
        )
        session.add(token_row)
        await session.commit()
        return token_row.token


async def verify_token(token: str, touch: bool = True) -> bool:
    """Verify a widget token and (by default) update last_used_at.

    ``touch=False`` is for callers that are not the iPhone widget — presence
    beacons from other Macs reuse the token but must not look like a widget
    fetch, because ``last_used_at`` is read as an iPhone liveness signal.
    """
    async with async_session() as session:
        result = await session.execute(
            select(WidgetTokenModel).where(
                WidgetTokenModel.token == token,
                WidgetTokenModel.is_active == True,
            )
        )
        token_row = result.scalar_one_or_none()

        if not token_row:
            return False

        if touch:
            token_row.last_used_at = datetime.utcnow()
            await session.commit()
        return True


def _until_of(raw: Any, tz: Any) -> Optional[datetime]:
    """A section's ``until`` as an aware time; a naive one is the owner's local
    time (``tz``). None when it cannot be read."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    try:
        when = datetime.fromisoformat(text[:-1] + "+00:00" if text.endswith("Z") else text)
    except ValueError:
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=tz or timezone.utc)
    return when


def live_sections(sections: Any, now: datetime, tz: Any = None) -> list:
    """The sections still live at ``now``: a section whose ``until`` has
    passed is dropped (2026-10-11 — the pulse is change-only, so nothing
    else would take a stale "visit at 3:40" down). No ``until``, or one that
    cannot be read, keeps the section. Pure."""
    if not isinstance(sections, list):
        return []
    now = now if now.tzinfo is not None else now.replace(tzinfo=timezone.utc)
    kept = []
    for sec in sections:
        if isinstance(sec, dict) and "until" in sec:
            until = _until_of(sec.get("until"), tz)
            if until is not None and until <= now:
                continue
        kept.append(sec)
    return kept


def _user_tz() -> Any:
    try:
        from services.timezone_utils import get_user_tz
        return get_user_tz()
    except Exception:  # noqa: BLE001
        return timezone.utc


def serve_sections(raw: Optional[str], now: Optional[datetime] = None) -> list:
    """Stored sections JSON → the live ones (``live_sections`` in the owner's zone)."""
    try:
        sections = json.loads(raw) if raw else []
    except (TypeError, ValueError):
        sections = []
    return live_sections(sections, now or datetime.now(timezone.utc), tz=_user_tz())


async def get_widget_state() -> dict:
    """Get the current widget state, falling back to auto-generated content.
    A section past its ``until`` is never served (``live_sections``)."""
    async with async_session() as session:
        result = await session.execute(
            select(WidgetStateModel).where(WidgetStateModel.id == "default")
        )
        state = result.scalar_one_or_none()

    if state:
        response = {
            "title": state.title,
            "subtitle": state.subtitle,
            "theme": json.loads(state.theme) if state.theme else None,
            "sections": serve_sections(state.sections),
            "updated_at": state.updated_at.isoformat() + "Z" if state.updated_at else None,
            "updated_by": state.updated_by,
        }
        if state.script:
            response["script"] = state.script
        return response

    # No custom state — generate default content
    return await _generate_default_content()


async def update_widget_state(
    sections: list,
    title: Optional[str] = None,
    subtitle: Optional[str] = None,
    theme: Optional[dict] = None,
) -> dict:
    """Upsert the widget state. It replaces any raw script: the phone runs a
    script in preference to sections, so a script set once hid every later
    update while those updates renewed its refresh hold (2026-09-27: Friday's
    pet board all weekend). Edward's latest write wins."""
    async with async_session() as session:
        result = await session.execute(
            select(WidgetStateModel).where(WidgetStateModel.id == "default")
        )
        state = result.scalar_one_or_none()

        if not state:
            state = WidgetStateModel(id="default")
            session.add(state)

        state.sections = json.dumps(sections)
        state.script = None
        state.updated_by = "edward"

        if title is not None:
            state.title = title
        if subtitle is not None:
            state.subtitle = subtitle
        if theme is not None:
            state.theme = json.dumps(theme)

        await session.commit()
        await session.refresh(state)

        return {
            "title": state.title,
            "subtitle": state.subtitle,
            "theme": json.loads(state.theme) if state.theme else None,
            "sections": json.loads(state.sections) if state.sections else [],
            "updated_at": state.updated_at.isoformat() + "Z" if state.updated_at else None,
            "updated_by": state.updated_by,
        }


async def update_widget_script(script: str) -> dict:
    """Store raw Scriptable JS code for the widget."""
    async with async_session() as session:
        result = await session.execute(
            select(WidgetStateModel).where(WidgetStateModel.id == "default")
        )
        state = result.scalar_one_or_none()

        if not state:
            state = WidgetStateModel(id="default")
            session.add(state)

        state.script = script
        state.updated_by = "edward"

        await session.commit()
        await session.refresh(state)

        return {
            "script": state.script,
            "updated_at": state.updated_at.isoformat() + "Z" if state.updated_at else None,
        }


async def clear_widget_script() -> dict:
    """Clear the raw script, reverting to structured data or default content."""
    async with async_session() as session:
        result = await session.execute(
            select(WidgetStateModel).where(WidgetStateModel.id == "default")
        )
        state = result.scalar_one_or_none()

        if state and state.script:
            state.script = None
            await session.commit()

    return {"cleared": True}


# ---- the fallback card (2026-09-27, followups L13) ----------------------------
#
# Shown only while no custom widget state is set. It printed
# ``next_fire_at`` — naive UTC, as the column holds it — as if it were local
# ("Sep 27, 10:00 PM" for a 5 pm reminder), greeted the owner by the host's clock,
# and listed Edward's own housekeeping (the surfaces pulse, the surplus tick,
# the mind's wakes and ticks) as "Upcoming" and counted it as "Pending".

def _is_plumbing(event: Any) -> bool:
    """One of Edward's own housekeeping rows — the kinds Today leaves out
    (``today_service.PLUMBING_EVENT_TYPES``). Unreadable metadata is the owner's."""
    from services.today_service import PLUMBING_EVENT_TYPES

    try:
        meta = json.loads(getattr(event, "event_metadata", None) or "{}")
    except (TypeError, ValueError):
        return False
    return isinstance(meta, dict) and meta.get("type") in PLUMBING_EVENT_TYPES


def _fire_label(dt: Optional[datetime]) -> str:
    """"Sep 27, 5:00 pm" in the owner's zone; the column is naive UTC."""
    if dt is None:
        return "TBD"
    from services.timezone_utils import utc_to_local

    local = utc_to_local(dt)
    clock = f"{local.hour % 12 or 12}:{local.minute:02d} {'am' if local.hour < 12 else 'pm'}"
    return f"{local.strftime('%b')} {local.day}, {clock}"


def upcoming_items(events: Iterable[Any], limit: int = 3) -> List[dict]:
    """The owner's next ``limit`` scheduled events for the fallback card,
    soonest first, in their zone; Edward's housekeeping left out. Pure (reads the
    user zone)."""
    mine = [e for e in events if not _is_plumbing(e)]
    mine.sort(key=lambda e: e.next_fire_at or datetime.max)
    return [{"label": str(e.description or "")[:50], "detail": _fire_label(e.next_fire_at), "icon": "calendar"}
            for e in mine[:limit]]


def greeting_for(now: datetime) -> str:
    """The card's greeting by the owner's clock, not the host's — with
    their name when ``EDWARD_OWNER_NAME`` is set ("Good morning, Sam")."""
    from services.owner import owner_name, owner_name_is_set
    from services.timezone_utils import utc_to_local

    hour = utc_to_local(now).hour
    part = "morning" if hour < 12 else "afternoon" if hour < 17 else "evening"
    return f"Good {part}, {owner_name()}" if owner_name_is_set() else f"Good {part}"


async def _generate_default_content() -> dict:
    """Auto-generate widget content from scheduled events and memory stats."""
    from services.timezone_utils import now_utc

    greeting = greeting_for(now_utc())

    sections = []
    pending_events: Optional[int] = None

    # Upcoming events: the owner's, never Edward's own housekeeping.
    try:
        from services.scheduled_events_service import list_events
        events = await list_events(status="pending", limit=500)
        pending_events = sum(1 for e in events if not _is_plumbing(e))
        items = upcoming_items(events)

        if items:
            sections.append({
                "type": "header",
                "title": "Upcoming",
                "icon": "calendar",
            })
            sections.append({
                "type": "list",
                "items": items,
            })
    except Exception:
        pass

    # Stats row
    try:
        from services.database import MemoryModel
        async with async_session() as session:
            from sqlalchemy import func
            mem_count = await session.execute(
                select(func.count()).select_from(MemoryModel)
            )
            memory_total = mem_count.scalar() or 0

        stats = [{"label": "Memories", "value": str(memory_total)}]
        if pending_events is not None:
            stats.append({"label": "Pending", "value": str(pending_events)})
        sections.append({
            "type": "stats_row",
            "stats": stats,
        })
    except Exception:
        pass

    return {
        "title": "Edward",
        "subtitle": greeting,
        "theme": None,
        "sections": sections,
        "updated_at": datetime.utcnow().isoformat() + "Z",
        "updated_by": "system",
    }
