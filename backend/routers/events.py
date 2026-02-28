"""REST API endpoints for scheduled events."""

from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from models.schemas import ScheduledEvent, ScheduledEventCreate, ScheduledEventUpdate
from services.scheduled_events_service import (
    create_event,
    list_events,
    get_event,
    cancel_event,
    delete_event,
    update_event,
)

router = APIRouter()


def _model_to_schema(event) -> ScheduledEvent:
    """Convert a SQLAlchemy model to a Pydantic schema."""
    return ScheduledEvent(
        id=event.id,
        conversation_id=event.conversation_id,
        description=event.description,
        scheduled_at=(event.scheduled_at.isoformat() + "Z") if event.scheduled_at else "",
        next_fire_at=(event.next_fire_at.isoformat() + "Z") if event.next_fire_at else "",
        recurrence_pattern=event.recurrence_pattern,
        status=event.status,
        created_by=event.created_by or "edward",
        delivery_channel=event.delivery_channel,
        last_fired_at=(event.last_fired_at.isoformat() + "Z") if event.last_fired_at else None,
        fire_count=event.fire_count or 0,
        last_result=event.last_result,
        created_at=(event.created_at.isoformat() + "Z") if event.created_at else "",
        updated_at=(event.updated_at.isoformat() + "Z") if event.updated_at else "",
    )


@router.get("/events")
async def get_events(
    status: Optional[str] = None,
    conversation_id: Optional[str] = None,
    sort_order: Optional[str] = Query("asc"),
    search: Optional[str] = Query(None),
):
    """List scheduled events with optional filters."""
    events = await list_events(
        status=status,
        conversation_id=conversation_id,
        sort_order=sort_order or "asc",
        search=search,
    )
    return [_model_to_schema(e) for e in events]


@router.get("/events/{event_id}")
async def get_single_event(event_id: str):
    """Get a single scheduled event."""
    event = await get_event(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    return _model_to_schema(event)


@router.post("/events")
async def create_new_event(body: ScheduledEventCreate):
    """Create a new scheduled event."""
    try:
        dt = datetime.fromisoformat(body.scheduled_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid datetime format")

    try:
        event = await create_event(
            description=body.description,
            scheduled_at=dt,
            recurrence_pattern=body.recurrence_pattern,
            conversation_id=body.conversation_id,
            delivery_channel=body.delivery_channel,
            created_by=body.created_by,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return _model_to_schema(event)


@router.patch("/events/{event_id}")
async def update_existing_event(event_id: str, body: ScheduledEventUpdate):
    """Update a scheduled event."""
    kwargs = {}
    if body.description is not None:
        kwargs["description"] = body.description
    if body.scheduled_at is not None:
        try:
            dt = datetime.fromisoformat(body.scheduled_at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            kwargs["scheduled_at"] = dt
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid datetime format")
    if body.recurrence_pattern is not None:
        kwargs["recurrence_pattern"] = body.recurrence_pattern
    if body.status is not None:
        if body.status == "cancelled":
            success = await cancel_event(event_id)
            if not success:
                raise HTTPException(status_code=404, detail="Event not found or already completed")
            event = await get_event(event_id)
            return _model_to_schema(event)
        kwargs["status"] = body.status
    if body.delivery_channel is not None:
        kwargs["delivery_channel"] = body.delivery_channel

    if not kwargs:
        raise HTTPException(status_code=400, detail="No fields to update")

    try:
        event = await update_event(event_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if not event:
        raise HTTPException(status_code=404, detail="Event not found")

    return _model_to_schema(event)


@router.delete("/events/{event_id}")
async def delete_existing_event(event_id: str):
    """Permanently delete a scheduled event."""
    success = await delete_event(event_id)
    if not success:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"status": "deleted"}
