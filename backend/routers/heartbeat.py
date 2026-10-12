"""REST API endpoints for the heartbeat system."""

import json
from typing import Optional

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, Query

from services.timezone_utils import format_iso_utc

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
        created_at=format_iso_utc(event.created_at) if event.created_at else "",
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
        created_at=format_iso_utc(result.created_at) if result.created_at else "",
    )


@router.get("/heartbeat/status")
async def get_status():
    """Get current heartbeat system status."""
    from services.heartbeat.heartbeat_service import get_heartbeat_status

    status = await get_heartbeat_status()
    return status


@router.post("/heartbeat/triage/run")
async def run_triage_now():
    """Ask the running heartbeat loop for an immediate triage cycle.

    Goes through ``request_triage`` (same path an inbound message uses) so
    the request coalesces with listener bursts and never runs a cycle
    concurrently with the loop. Use after re-opening a failed event
    (``triage_status='pending'``) instead of pinging the thread again.
    """
    from services.heartbeat import heartbeat_service

    heartbeat_service.request_triage("operator request")
    return {"requested": True, "reason": "operator request"}


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
        prev_email = config.email_enabled
        prev_gmail = config.gmail_enabled

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
        # Stored for compatibility; the mind path is the only path now.
        if getattr(body, "mind_enabled", None) is not None:
            config.mind_enabled = body.mind_enabled
        if body.allowed_senders is not None:
            config.allowed_senders = json.dumps(
                [s.model_dump() for s in body.allowed_senders]
            )
        if body.imessage_enabled is not None:
            config.imessage_enabled = body.imessage_enabled
        if body.imessage_poll_seconds is not None:
            config.imessage_poll_seconds = body.imessage_poll_seconds
        if body.imessage_thread_limit_direct is not None:
            if body.imessage_thread_limit_direct < 1:
                raise HTTPException(
                    status_code=400,
                    detail="imessage_thread_limit_direct must be at least 1",
                )
            config.imessage_thread_limit_direct = body.imessage_thread_limit_direct
        if body.imessage_thread_limit_group is not None:
            if body.imessage_thread_limit_group < 1:
                raise HTTPException(
                    status_code=400,
                    detail="imessage_thread_limit_group must be at least 1",
                )
            config.imessage_thread_limit_group = body.imessage_thread_limit_group
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
        # Gmail's "watch my inbox" (GmailPanel): the PATCH ignored both until
        # 2026-10-11, so the switch never reached the listener.
        if body.gmail_enabled is not None:
            config.gmail_enabled = body.gmail_enabled
        if body.gmail_poll_seconds is not None:
            config.gmail_poll_seconds = body.gmail_poll_seconds

        await session.commit()
        await session.refresh(config)
        await _sync_gmail_listener(config, prev_gmail)

        # Hot-start/stop listeners when enabled state changes
        await _sync_listeners(config, prev_imessage, prev_email)

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
            imessage_thread_limit_direct=config.imessage_thread_limit_direct,
            imessage_thread_limit_group=config.imessage_thread_limit_group,
            calendar_enabled=config.calendar_enabled,
            calendar_poll_seconds=config.calendar_poll_seconds,
            calendar_lookahead_minutes=config.calendar_lookahead_minutes,
            email_enabled=config.email_enabled,
            email_poll_seconds=config.email_poll_seconds,
            gmail_enabled=config.gmail_enabled,
            gmail_poll_seconds=config.gmail_poll_seconds,
        )


async def _sync_listeners(config, prev_imessage: bool, prev_email: bool) -> None:
    """Start or stop listeners when their enabled state changes. (The
    calendar track is gone, 2026-10-11: its columns still save.)"""
    from services.heartbeat.listener_imessage import start_imessage_listener, stop_imessage_listener
    from services.heartbeat.listener_email import start_email_listener, stop_email_listener

    # iMessage
    if config.imessage_enabled and not prev_imessage:
        await start_imessage_listener()
    elif not config.imessage_enabled and prev_imessage:
        await stop_imessage_listener()

    # Email
    if config.email_enabled and not prev_email:
        await start_email_listener(config)
    elif not config.email_enabled and prev_email:
        await stop_email_listener()


@router.get("/heartbeat/jarvis-metrics")
async def get_jarvis_metrics(days: int = Query(7, ge=1, le=90)):
    """Owner-preempt rate: how often Edward acted on a thread the owner was
    already answering (``decision_trail``)."""
    from services.heartbeat.decision_trail import compute_preempt_metric, load_preempt_inputs

    actions, replies = await load_preempt_inputs(days)
    return {"days": days, **compute_preempt_metric(actions, replies).as_dict()}


@router.get("/heartbeat/act-queue")
async def get_act_queue_status():
    """Settle-before-act worker: retired with the legacy triage engine (the
    mind path holds nothing), so always empty. The shape stays for old
    clients."""
    return {
        "worker_alive": False,
        "queued": 0,
        "queued_ids": [],
        "pending_batch": 0,
        "next_due_at": None,
        "last_orphan_sweep_age_s": None,
        "settle": {},
        "retired": True,
    }


# ---------------------------------------------------------------------------
# Feedback — "right call?" from the heartbeat digest view
# ---------------------------------------------------------------------------

class FeedbackBody(BaseModel):
    verdict: str
    note: Optional[str] = None


@router.post("/heartbeat/events/{event_id}/feedback")
async def event_feedback(event_id: str, body: FeedbackBody):
    from services.heartbeat_feedback_service import record_feedback

    try:
        return await record_feedback(event_id, body.verdict, body.note)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except LookupError:
        raise HTTPException(status_code=404, detail="event not found")


@router.get("/heartbeat/feedback")
async def feedback_list(limit: int = Query(200, ge=1, le=1000)):
    from services.heartbeat_feedback_service import list_feedback

    return await list_feedback(limit=limit)


async def _sync_gmail_listener(config, prev_gmail: Optional[bool]) -> None:
    """Start or stop the Gmail listener when "watch my inbox" changes. It
    starts only once Gmail is connected (``listener_gmail.ensure_started``)."""
    from services.heartbeat import listener_gmail

    if config.gmail_enabled and not prev_gmail:
        await listener_gmail.ensure_started()
    elif not config.gmail_enabled and prev_gmail:
        await listener_gmail.stop_gmail_listener()
