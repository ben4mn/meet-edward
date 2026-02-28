"""
Widget service for Edward's iOS home screen widget via Scriptable.

Manages widget state (what the widget displays) and access tokens
for the Scriptable app to fetch widget content.
"""

import json
import secrets
from datetime import datetime
from typing import Optional

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


async def verify_token(token: str) -> bool:
    """Verify a widget token and update last_used_at."""
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

        token_row.last_used_at = datetime.utcnow()
        await session.commit()
        return True


async def get_widget_state() -> dict:
    """Get the current widget state, falling back to auto-generated content."""
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
            "sections": json.loads(state.sections) if state.sections else [],
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
    """Upsert the widget state."""
    async with async_session() as session:
        result = await session.execute(
            select(WidgetStateModel).where(WidgetStateModel.id == "default")
        )
        state = result.scalar_one_or_none()

        if not state:
            state = WidgetStateModel(id="default")
            session.add(state)

        state.sections = json.dumps(sections)
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


async def _generate_default_content() -> dict:
    """Auto-generate widget content from scheduled events and memory stats."""
    from datetime import datetime

    # Time-of-day greeting
    hour = datetime.now().hour
    if hour < 12:
        greeting = "Good morning, Ben"
    elif hour < 17:
        greeting = "Good afternoon, Ben"
    else:
        greeting = "Good evening, Ben"

    sections = []

    # Upcoming events
    try:
        from services.scheduled_events_service import list_events
        events = await list_events(status="pending")
        # Sort by next_fire_at, take first 3
        events = sorted(events, key=lambda e: e.next_fire_at)[:3]

        if events:
            items = []
            for event in events:
                fire_str = event.next_fire_at.strftime("%b %d, %I:%M %p") if event.next_fire_at else "TBD"
                items.append({
                    "label": event.description[:50],
                    "detail": fire_str,
                    "icon": "calendar",
                })
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
        from services.database import MemoryModel, ScheduledEventModel
        async with async_session() as session:
            from sqlalchemy import func
            mem_count = await session.execute(
                select(func.count()).select_from(MemoryModel)
            )
            memory_total = mem_count.scalar() or 0

            event_count = await session.execute(
                select(func.count()).select_from(ScheduledEventModel).where(
                    ScheduledEventModel.status == "pending"
                )
            )
            pending_events = event_count.scalar() or 0

        sections.append({
            "type": "stats_row",
            "stats": [
                {"label": "Memories", "value": str(memory_total)},
                {"label": "Pending", "value": str(pending_events)},
            ],
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
