"""
Webhook handlers for external services.

Currently supports:
- Twilio SMS inbound webhooks
- Twilio WhatsApp inbound webhooks
- WhatsApp Bridge @mention webhooks
"""

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request, Form, BackgroundTasks
from fastapi.responses import Response
import uuid

from services.twilio_service import (
    validate_webhook_signature, send_sms, send_whatsapp, is_configured
)
from services.graph import get_graph, chat_with_memory
from services.settings_service import get_settings
from services.conversation_service import create_conversation

router = APIRouter()

# Rate limiting state (simple in-memory, use Redis in production)
_rate_limit_cache: dict = {}
RATE_LIMIT_MESSAGES = int(os.getenv("TWILIO_RATE_LIMIT", "10"))  # messages per window
RATE_LIMIT_WINDOW = int(os.getenv("TWILIO_RATE_LIMIT_WINDOW", "60"))  # seconds


async def get_or_create_contact_conversation(
    phone_number: str,
    channel: str = "sms"
) -> tuple[str, bool]:
    """
    Get or create a conversation ID for an external contact.

    Args:
        phone_number: The contact's phone number (E.164)
        channel: The channel this message arrived on ("sms" or "whatsapp")

    Returns:
        Tuple of (conversation_id, is_new)
    """
    from services.database import async_session, ExternalContactModel
    from sqlalchemy import select

    async with async_session() as session:
        # Look up existing contact
        result = await session.execute(
            select(ExternalContactModel).where(
                ExternalContactModel.phone_number == phone_number
            )
        )
        contact = result.scalar_one_or_none()

        if contact:
            from sqlalchemy import update, func

            # Check if conversation is stale (>24h since last contact)
            now = datetime.now(timezone.utc)
            last_contacted = contact.last_contacted
            if last_contacted and last_contacted.tzinfo is None:
                last_contacted = last_contacted.replace(tzinfo=timezone.utc)

            is_stale = (
                last_contacted is None
                or (now - last_contacted) > timedelta(hours=24)
            )

            if is_stale:
                # Create a new conversation for this contact
                new_conversation_id = str(uuid.uuid4())
                await session.execute(
                    update(ExternalContactModel)
                    .where(ExternalContactModel.id == contact.id)
                    .values(
                        conversation_id=new_conversation_id,
                        last_contacted=func.now(),
                        last_channel=channel,
                    )
                )
                await session.commit()

                # Create the conversation record
                channel_label = "WhatsApp" if channel == "whatsapp" else "SMS"
                await create_conversation(
                    conversation_id=new_conversation_id,
                    title=f"{channel_label}: {contact.phone_number}"
                )
                print(f"Rotated conversation for {contact.phone_number} (stale >24h)")
                return new_conversation_id, True

            # Still fresh — update timestamps and keep existing conversation
            await session.execute(
                update(ExternalContactModel)
                .where(ExternalContactModel.id == contact.id)
                .values(last_contacted=func.now(), last_channel=channel)
            )
            await session.commit()
            return contact.conversation_id, False

        # Create new contact and conversation
        conversation_id = str(uuid.uuid4())
        contact_id = str(uuid.uuid4())

        new_contact = ExternalContactModel(
            id=contact_id,
            phone_number=phone_number,
            conversation_id=conversation_id,
            platform=channel,
            last_channel=channel,
        )
        session.add(new_contact)
        await session.commit()

        # Create the conversation record
        channel_label = "WhatsApp" if channel == "whatsapp" else "SMS"
        await create_conversation(
            conversation_id=conversation_id,
            title=f"{channel_label}: {phone_number}"
        )

        return conversation_id, True


def check_rate_limit(phone_number: str) -> bool:
    """
    Check if a phone number has exceeded the rate limit.

    Returns:
        True if within limits, False if rate limited
    """
    import time

    current_time = time.time()

    # Clean old entries
    cutoff = current_time - RATE_LIMIT_WINDOW
    _rate_limit_cache[phone_number] = [
        t for t in _rate_limit_cache.get(phone_number, [])
        if t > cutoff
    ]

    # Check limit
    if len(_rate_limit_cache.get(phone_number, [])) >= RATE_LIMIT_MESSAGES:
        return False

    # Record this request
    if phone_number not in _rate_limit_cache:
        _rate_limit_cache[phone_number] = []
    _rate_limit_cache[phone_number].append(current_time)

    return True


async def _send_on_channel(channel: str, to_number: str, message: str):
    """Send a message on the appropriate channel (sms or whatsapp)."""
    if channel == "whatsapp":
        await send_whatsapp(to_number, message)
    else:
        await send_sms(to_number, message)


async def _send_inbound_push_notification(from_number: str, message_body: str, channel: str):
    """Send a push notification for an inbound message."""
    try:
        from services.push_service import send_push_notification, is_configured
        if is_configured():
            channel_name = "WhatsApp" if channel == "whatsapp" else "SMS"
            await send_push_notification(
                title=f"New {channel_name} from {from_number}",
                body=message_body[:100] + ("..." if len(message_body) > 100 else ""),
                url="/",
                tag=f"inbound-{channel}-{from_number}",
            )
    except Exception as e:
        print(f"Failed to send push notification for inbound {channel}: {e}")


async def process_inbound_and_respond(
    from_number: str,
    message_body: str,
    conversation_id: str,
    channel: str = "sms"
):
    """
    Process an incoming message (SMS or WhatsApp) and send Edward's response.

    This runs as a background task to avoid webhook timeout.
    """
    # Send push notification for inbound message
    await _send_inbound_push_notification(from_number, message_body, channel)

    try:
        settings = await get_settings()
        graph = await get_graph()

        channel_name = "WhatsApp" if channel == "whatsapp" else "SMS"
        msg_context = (
            f"\n\n[CONTEXT: This message is from a {channel_name} conversation. "
            "Keep responses concise and mobile-friendly. "
            f"The user is messaging from {from_number} via {channel_name}.]"
        )
        enhanced_prompt = settings.system_prompt + msg_context

        response = await chat_with_memory(
            message=message_body,
            conversation_id=conversation_id,
            system_prompt=enhanced_prompt,
            model=settings.model,
            temperature=settings.temperature,
            graph=graph
        )

        # Truncate if too long
        if len(response) > 1500:
            response = response[:1497] + "..."

        await _send_on_channel(channel, from_number, response)
        print(f"Sent {channel_name} response to {from_number}: {response[:50]}...")

    except Exception as e:
        print(f"Error processing {channel} message from {from_number}: {e}")
        try:
            await _send_on_channel(
                channel,
                from_number,
                "Sorry, I encountered an error processing your message. Please try again."
            )
        except Exception:
            pass


@router.post("/webhook/twilio")
async def twilio_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(None),
    AccountSid: str = Form(None),
):
    """
    Handle incoming SMS messages from Twilio.

    Flow:
    1. Validate Twilio signature (security)
    2. Check rate limiting
    3. Look up or create conversation for this phone number
    4. Process message and respond asynchronously
    5. Return empty TwiML (we respond via API, not TwiML)
    """
    # Validate Twilio signature in production
    webhook_url = os.getenv("TWILIO_WEBHOOK_URL")
    signature = request.headers.get("X-Twilio-Signature", "")

    if webhook_url and is_configured():
        # Get form data for validation
        form_data = await request.form()
        params = {key: value for key, value in form_data.items()}

        if not validate_webhook_signature(webhook_url, params, signature):
            print(f"Invalid Twilio signature from {From}")
            raise HTTPException(status_code=403, detail="Invalid signature")

    # Rate limiting
    if not check_rate_limit(From):
        print(f"Rate limited: {From}")
        # Return empty response (don't send error to potential spammer)
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml"
        )

    print(f"Received SMS from {From}: {Body[:50]}...")

    # Get or create conversation for this contact
    conversation_id, is_new = await get_or_create_contact_conversation(From, channel="sms")

    if is_new:
        print(f"Created new conversation {conversation_id} for {From}")

    # Process and respond in background (avoids Twilio timeout)
    background_tasks.add_task(
        process_inbound_and_respond,
        From,
        Body,
        conversation_id,
        "sms"
    )

    # Return empty TwiML response (we'll respond via API)
    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml"
    )


@router.post("/webhook/twilio/whatsapp")
async def twilio_whatsapp_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    From: str = Form(...),
    Body: str = Form(...),
    MessageSid: str = Form(None),
    AccountSid: str = Form(None),
):
    """
    Handle incoming WhatsApp messages from Twilio.

    Twilio sends From as "whatsapp:+1234567890". We strip the prefix and
    route to the same contact/conversation as SMS from that number.
    """
    # Validate Twilio signature
    webhook_url = os.getenv("TWILIO_WHATSAPP_WEBHOOK_URL", os.getenv("TWILIO_WEBHOOK_URL"))
    signature = request.headers.get("X-Twilio-Signature", "")

    if webhook_url and is_configured():
        form_data = await request.form()
        params = {key: value for key, value in form_data.items()}
        if not validate_webhook_signature(webhook_url, params, signature):
            print(f"Invalid Twilio signature from {From}")
            raise HTTPException(status_code=403, detail="Invalid signature")

    # Strip whatsapp: prefix to get raw phone number
    raw_number = From
    if raw_number.startswith("whatsapp:"):
        raw_number = raw_number[len("whatsapp:"):]

    # Rate limiting
    if not check_rate_limit(raw_number):
        print(f"Rate limited: {raw_number}")
        return Response(
            content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
            media_type="application/xml"
        )

    print(f"Received WhatsApp from {raw_number}: {Body[:50]}...")

    # Get or create conversation — same contact as SMS, just update last_channel
    conversation_id, is_new = await get_or_create_contact_conversation(
        raw_number, channel="whatsapp"
    )

    if is_new:
        print(f"Created new conversation {conversation_id} for {raw_number} (WhatsApp)")

    # Process and respond in background
    background_tasks.add_task(
        process_inbound_and_respond,
        raw_number,
        Body,
        conversation_id,
        "whatsapp"
    )

    return Response(
        content='<?xml version="1.0" encoding="UTF-8"?><Response></Response>',
        media_type="application/xml"
    )


@router.get("/webhook/twilio/status")
async def twilio_status():
    """Check Twilio webhook configuration status."""
    from services.twilio_service import get_phone_number

    return {
        "configured": is_configured(),
        "phone_number": get_phone_number() if is_configured() else None,
        "webhook_url": os.getenv("TWILIO_WEBHOOK_URL"),
        "whatsapp_webhook_url": os.getenv("TWILIO_WHATSAPP_WEBHOOK_URL"),
        "rate_limit": f"{RATE_LIMIT_MESSAGES} messages per {RATE_LIMIT_WINDOW} seconds"
    }


# ─── WhatsApp Bridge Webhook ─────────────────────────────────────────────────

@router.post("/webhook/whatsapp")
async def whatsapp_bridge_webhook(request: Request):
    """
    Handle incoming @edward mention notifications from the WhatsApp bridge.

    The Baileys bridge detects @edward in real-time and POSTs here.
    We create a HeartbeatEventModel and trigger immediate triage.
    """
    from services.database import async_session, HeartbeatEventModel
    from sqlalchemy import select

    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    chat_id = data.get("chat_id", "")
    chat_name = data.get("chat_name", chat_id)
    sender = data.get("sender", "")
    sender_name = data.get("sender_name", "")
    text = data.get("text", "")
    message_id = data.get("message_id", "")
    is_from_me = data.get("is_from_me", False)

    if not chat_id or not text:
        raise HTTPException(status_code=400, detail="chat_id and text required")

    # Safety net: resolve @lid JIDs if bridge didn't already
    if chat_id.endswith("@lid"):
        try:
            from services.whatsapp_bridge_client import resolve_lid, is_available
            if is_available():
                resolved = await resolve_lid(chat_id)
                if resolved != chat_id:
                    print(f"[Webhook] Resolved LID {chat_id} → {resolved}")
                    chat_id = resolved
        except Exception as e:
            print(f"[Webhook] LID resolution failed: {e}")

    source_id = f"whatsapp:{message_id}" if message_id else f"whatsapp:{chat_id}_{hash(text)}"

    # Dedup and store
    async with async_session() as session:
        existing = await session.execute(
            select(HeartbeatEventModel.id).where(
                HeartbeatEventModel.source_id == source_id
            )
        )
        if existing.scalar_one_or_none():
            return {"status": "duplicate"}

        event = HeartbeatEventModel(
            source="whatsapp",
            event_type="message_received",
            sender=sender,
            contact_name=sender_name or chat_name,
            chat_identifier=chat_id,
            chat_name=chat_name,
            summary=text[:200],
            raw_data=json.dumps(data),
            source_id=source_id,
            is_from_user=bool(is_from_me),
        )
        session.add(event)
        await session.commit()

    print(f"[Webhook] WhatsApp @mention from {sender_name or sender} in {chat_name}: {text[:80]}")

    # Trigger triage in background
    async def _trigger():
        try:
            from services.heartbeat.heartbeat_service import trigger_immediate_triage
            await trigger_immediate_triage()
        except Exception as e:
            print(f"[Webhook] WhatsApp triage trigger error: {e}")

    asyncio.create_task(_trigger())

    return {"status": "accepted"}
