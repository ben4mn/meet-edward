"""REST API endpoints for the heartbeat system."""

import json
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from services.heartbeat.models import (
    HeartbeatEventSchema,
    TriageCycleSchema,
    HeartbeatConfigSchema,
    HeartbeatConfigUpdate,
    HeartbeatStatusSchema,
)

router = APIRouter()


def _event_to_schema(event) -> HeartbeatEventSchema:
    """Convert a SQLAlchemy HeartbeatEventModel to a Pydantic schema."""
    raw = None
    if event.raw_data:
        try:
            raw = json.loads(event.raw_data)
        except (json.JSONDecodeError, TypeError):
            raw = {"raw": event.raw_data}
    return HeartbeatEventSchema(
        id=event.id,
        source=event.source,
        event_type=event.event_type,
        sender=event.sender,
        contact_name=event.contact_name,
        chat_identifier=event.chat_identifier,
        chat_name=event.chat_name,
        summary=event.summary,
        raw_data=raw,
        is_from_user=event.is_from_user or False,
        created_at=event.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if event.created_at else "",
        triage_status=event.triage_status or "pending",
        briefed=event.briefed or False,
    )


def _triage_to_schema(result) -> TriageCycleSchema:
    """Convert a SQLAlchemy TriageResultModel to a Pydantic schema."""
    return TriageCycleSchema(
        id=result.id,
        cycle_number=result.cycle_number,
        events_total=result.events_total or 0,
        events_rule_filtered=result.events_rule_filtered or 0,
        events_dismissed=result.events_dismissed or 0,
        events_noted=result.events_noted or 0,
        events_acted=result.events_acted or 0,
        events_escalated=result.events_escalated or 0,
        layer_reached=result.layer_reached or 1,
        haiku_input_tokens=result.haiku_input_tokens or 0,
        haiku_output_tokens=result.haiku_output_tokens or 0,
        sonnet_wakes=result.sonnet_wakes or 0,
        duration_ms=result.duration_ms or 0,
        summary=result.summary,
        created_at=result.created_at.strftime("%Y-%m-%dT%H:%M:%SZ") if result.created_at else "",
    )


@router.get("/heartbeat/status")
async def get_status():
    """Get current heartbeat system status."""
    from services.heartbeat.heartbeat_service import get_heartbeat_status

    status = await get_heartbeat_status()
    return status


@router.get("/heartbeat/events")
async def get_events(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    source: Optional[str] = None,
    triage_status: Optional[str] = None,
):
    """List heartbeat events with optional filters."""
    from sqlalchemy import select
    from services.database import async_session, HeartbeatEventModel

    async with async_session() as session:
        query = select(HeartbeatEventModel)

        if source:
            query = query.where(HeartbeatEventModel.source == source)
        if triage_status:
            query = query.where(HeartbeatEventModel.triage_status == triage_status)

        query = (
            query.order_by(HeartbeatEventModel.created_at.desc())
            .limit(limit)
            .offset(offset)
        )

        result = await session.execute(query)
        events = list(result.scalars().all())

        return [_event_to_schema(e) for e in events]


@router.get("/heartbeat/triage")
async def get_triage_cycles(
    limit: int = Query(20, ge=1, le=100),
):
    """List recent triage cycles."""
    from sqlalchemy import select
    from services.database import async_session, TriageResultModel

    async with async_session() as session:
        result = await session.execute(
            select(TriageResultModel)
            .order_by(TriageResultModel.created_at.desc())
            .limit(limit)
        )
        cycles = list(result.scalars().all())

        return [_triage_to_schema(c) for c in cycles]


@router.get("/heartbeat/recent-senders")
async def get_recent_senders(
    limit: int = Query(50, ge=1, le=200),
):
    """Return unique recent senders from heartbeat events for autocomplete."""
    from sqlalchemy import select, distinct
    from services.database import async_session, HeartbeatEventModel

    async with async_session() as session:
        result = await session.execute(
            select(
                HeartbeatEventModel.sender,
                HeartbeatEventModel.contact_name,
            )
            .where(
                HeartbeatEventModel.sender.isnot(None),
                HeartbeatEventModel.is_from_user == False,
            )
            .order_by(HeartbeatEventModel.created_at.desc())
            .limit(500)
        )
        rows = result.all()

    # Deduplicate by sender, keeping the first (most recent) contact_name
    seen = {}
    for sender, contact_name in rows:
        if sender and sender not in seen:
            seen[sender] = contact_name or sender

    senders = [
        {"identifier": identifier, "label": label}
        for identifier, label in list(seen.items())[:limit]
    ]

    return senders


@router.patch("/heartbeat/config")
async def update_config(body: HeartbeatConfigUpdate):
    """Update heartbeat configuration."""
    from sqlalchemy import select
    from services.database import async_session, HeartbeatConfigModel

    async with async_session() as session:
        result = await session.execute(
            select(HeartbeatConfigModel).where(HeartbeatConfigModel.id == "default")
        )
        config = result.scalar_one_or_none()

        if not config:
            config = HeartbeatConfigModel(id="default")
            session.add(config)

        # Snapshot previous enabled states for hot-start/stop
        prev_imessage = config.imessage_enabled
        prev_calendar = config.calendar_enabled
        prev_email = config.email_enabled
        prev_whatsapp = config.whatsapp_enabled

        if body.enabled is not None:
            config.enabled = body.enabled
        if body.triage_interval_seconds is not None:
            if body.triage_interval_seconds < 60:
                raise HTTPException(
                    status_code=400,
                    detail="Triage interval must be at least 60 seconds",
                )
            config.triage_interval_seconds = body.triage_interval_seconds
        if body.digest_token_cap is not None:
            config.digest_token_cap = body.digest_token_cap
        if body.allowed_senders is not None:
            config.allowed_senders = json.dumps(
                [s.model_dump() for s in body.allowed_senders]
            )
        if body.imessage_enabled is not None:
            config.imessage_enabled = body.imessage_enabled
        if body.imessage_poll_seconds is not None:
            config.imessage_poll_seconds = body.imessage_poll_seconds
        if body.calendar_enabled is not None:
            config.calendar_enabled = body.calendar_enabled
        if body.calendar_poll_seconds is not None:
            config.calendar_poll_seconds = body.calendar_poll_seconds
        if body.calendar_lookahead_minutes is not None:
            config.calendar_lookahead_minutes = body.calendar_lookahead_minutes
        if body.email_enabled is not None:
            config.email_enabled = body.email_enabled
        if body.email_poll_seconds is not None:
            config.email_poll_seconds = body.email_poll_seconds
        if body.whatsapp_enabled is not None:
            config.whatsapp_enabled = body.whatsapp_enabled
        if body.whatsapp_poll_seconds is not None:
            config.whatsapp_poll_seconds = body.whatsapp_poll_seconds

        await session.commit()
        await session.refresh(config)

        # Hot-start/stop listeners when enabled state changes
        await _sync_listeners(config, prev_imessage, prev_calendar, prev_email, prev_whatsapp)

        # Parse allowed_senders back for response
        allowed_senders = []
        if config.allowed_senders:
            try:
                allowed_senders = json.loads(config.allowed_senders)
            except (json.JSONDecodeError, TypeError):
                pass

        return HeartbeatConfigSchema(
            enabled=config.enabled,
            triage_interval_seconds=config.triage_interval_seconds,
            digest_token_cap=config.digest_token_cap,
            allowed_senders=allowed_senders,
            imessage_enabled=config.imessage_enabled,
            imessage_poll_seconds=config.imessage_poll_seconds,
            calendar_enabled=config.calendar_enabled,
            calendar_poll_seconds=config.calendar_poll_seconds,
            calendar_lookahead_minutes=config.calendar_lookahead_minutes,
            email_enabled=config.email_enabled,
            email_poll_seconds=config.email_poll_seconds,
            whatsapp_enabled=config.whatsapp_enabled,
            whatsapp_poll_seconds=config.whatsapp_poll_seconds,
        )


async def _sync_listeners(config, prev_imessage: bool, prev_calendar: bool, prev_email: bool, prev_whatsapp: bool) -> None:
    """Start or stop listeners when their enabled state changes."""
    from services.heartbeat.listener_imessage import start_imessage_listener, stop_imessage_listener
    from services.heartbeat.listener_calendar import start_calendar_listener, stop_calendar_listener
    from services.heartbeat.listener_email import start_email_listener, stop_email_listener
    from services.heartbeat.listener_whatsapp import start_whatsapp_listener, stop_whatsapp_listener

    # iMessage
    if config.imessage_enabled and not prev_imessage:
        await start_imessage_listener()
    elif not config.imessage_enabled and prev_imessage:
        await stop_imessage_listener()

    # Calendar
    if config.calendar_enabled and not prev_calendar:
        await start_calendar_listener(config)
    elif not config.calendar_enabled and prev_calendar:
        await stop_calendar_listener()

    # Email
    if config.email_enabled and not prev_email:
        await start_email_listener(config)
    elif not config.email_enabled and prev_email:
        await stop_email_listener()

    # WhatsApp
    if config.whatsapp_enabled and not prev_whatsapp:
        await start_whatsapp_listener(config)
    elif not config.whatsapp_enabled and prev_whatsapp:
        await stop_whatsapp_listener()
