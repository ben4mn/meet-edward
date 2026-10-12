"""
Webhook handlers for external services.

Currently supports:
- Twilio SMS inbound webhooks
- Twilio WhatsApp inbound webhooks

Who gets a turn: only the owner. A Twilio turn reads like any other (every
turn's reads are open — bash, memory, messages, the calendar;
services/toolset/policy.py) and its final text is texted back to the
sender, so answering anyone let a stranger who found Edward's number ask
for the owner's calendar, memories or messages and get them by SMS. Now a
text from the owner's own handles (``EDWARD_OWNER_HANDLES`` plus the
heartbeat config's ``imessage_owner_handles``, compared like the iMessage
listener compares them) gets a turn; anyone else's is held: logged without
its text, recorded in the ledger (kind ``twilio_inbound``, outcome
``held``, with the text) and left unanswered. ``EDWARD_TWILIO_ANSWER_ANYONE``
restores answering everyone (see ``answer_anyone``). The signature check
comes first and fails closed either way.
"""

import json
import os
import re
from typing import Iterable, Optional
from pathlib import Path
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, HTTPException, Request, Form, BackgroundTasks
from fastapi.responses import Response
import uuid

from services.twilio_service import (
    check_webhook_signature, send_sms, send_whatsapp, is_configured
)
from services.graph import get_graph, chat_with_memory
from services.settings_service import get_settings
from services.conversation_service import create_conversation
from services import owner, tool_markup
from services.pings.hooks import chat_key

router = APIRouter()

EMPTY_TWIML = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'

# Answer texts from anyone, not just the owner. RISK: a turn's reads are
# open (bash can read any file the backend can, memory, messages, the
# calendar) and the reply goes back to the sender, so with this on anyone
# who learns Edward's number can try to talk him into texting them the
# owner's private data. Their turns get a line telling Edward the sender is
# not the owner, which is a prompt, not a wall. Off by default.
ANSWER_ANYONE_ENV = "EDWARD_TWILIO_ANSWER_ANYONE"
HELD_REASON = "not one of the owner's handles"


def answer_anyone() -> bool:
    return (os.getenv(ANSWER_ANYONE_ENV) or "off").strip().lower() in ("on", "1", "true", "yes")


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value or "")


def is_owner_handle(sender: str, handles: Iterable[str]) -> bool:
    """Is ``sender`` (an E.164 number, ``whatsapp:`` already stripped) one
    of ``handles``? Compared by ``pings.hooks.chat_key`` like the iMessage
    listener (a phone with or without its country code matches), except that
    two numbers which both carry a country code must agree on every digit.
    Pure."""
    key = chat_key(sender)
    if not key:
        return False
    sender_digits = _digits(sender)
    for handle in handles:
        if not str(handle or "").strip() or chat_key(handle) != key:
            continue
        handle_digits = _digits(str(handle))
        if key.startswith("p:") and len(sender_digits) > 10 and len(handle_digits) > 10 \
                and sender_digits != handle_digits:
            continue
        return True
    return False


async def _stored_owner_handles() -> list[str]:
    """heartbeat_config's ``imessage_owner_handles`` (a JSON list, or a
    comma-separated string). Never raises: unreadable is none."""
    try:
        from services.database import HeartbeatConfigModel, async_session

        async with async_session() as session:
            cfg = await session.get(HeartbeatConfigModel, "default")
        raw = getattr(cfg, "imessage_owner_handles", None) if cfg is not None else None
        if not raw:
            return []
        try:
            parsed = json.loads(raw)
        except ValueError:
            parsed = str(raw).split(",")
        items = parsed if isinstance(parsed, list) else [parsed]
        return [str(h).strip() for h in items if str(h).strip()]
    except Exception as exc:  # noqa: BLE001
        print(f"[Twilio] owner handles unreadable (EDWARD_OWNER_HANDLES still counts): {exc}")
        return []


async def owner_handles() -> list[str]:
    return (await _stored_owner_handles()) + list(owner.owner_handles())


async def is_from_owner(sender: str) -> bool:
    return is_owner_handle(sender, await owner_handles())


async def hold_inbound(sender: str, body: str, channel: str, media_count: int = 0) -> None:
    """A text from someone who is not the owner: no turn, no reply, no
    conversation. Why no reply, not even a polite line: a reply tells
    whoever is probing that the number is live and automated, costs a
    message each time, and would let anyone make Edward's number text
    whatever number they send from. The log line leaves the text out (logs
    are plain files); the ledger keeps it for the owner."""
    from services import event_ledger

    print(f"[Twilio] held a {channel} text from {sender} ({HELD_REASON}; no turn, no reply)")
    await event_ledger.record_event_now(
        kind="twilio_inbound", source=channel, outcome="held", reason=HELD_REASON,
        detail={"from": sender, "text": (body or "")[:400], "media": int(media_count or 0)},
    )


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


async def _send_inbound_push_notification(
    from_number: str, message_body: str, channel: str, conversation_id: Optional[str] = None
):
    """Send a push notification for an inbound message, pointing at the
    contact's conversation so a tap lands on the thread."""
    try:
        from services.push_service import send_push_notification, is_configured
        if is_configured():
            channel_name = "WhatsApp" if channel == "whatsapp" else "SMS"
            await send_push_notification(
                title=f"New {channel_name} from {from_number}",
                body=message_body[:100] + ("..." if len(message_body) > 100 else ""),
                url=f"/chat?c={conversation_id}" if conversation_id else "/today",
                tag=f"inbound-{channel}-{from_number}",
                source="twilio_inbound",
                conversation_id=conversation_id,
            )
    except Exception as e:
        print(f"Failed to send push notification for inbound {channel}: {e}")


def parse_media_params(form: dict) -> list[tuple[str, str]]:
    """Twilio sends NumMedia + MediaUrl{i}/MediaContentType{i}. Pure."""
    try:
        n = int(str(form.get("NumMedia") or "0"))
    except (TypeError, ValueError):
        n = 0
    media = []
    for i in range(max(0, min(n, 10))):
        url = form.get(f"MediaUrl{i}")
        if url:
            media.append((str(url), str(form.get(f"MediaContentType{i}") or "application/octet-stream")))
    return media


async def fetch_twilio_media(url: str) -> bytes:
    """Twilio media URLs need HTTP basic auth with the account SID/token."""
    import httpx
    from services.twilio_service import TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        r = await client.get(url, auth=(TWILIO_ACCOUNT_SID or "", TWILIO_AUTH_TOKEN or ""))
        r.raise_for_status()
        return r.content


async def _attachments_from_media(media: list[tuple[str, str]]) -> list[dict]:
    """Download MMS media and shape it like a chat upload (base64 JPEG for images)."""
    import base64
    import tempfile
    from services.attachment_vision import prepare_image_for_model
    out: list[dict] = []
    for i, (url, mime) in enumerate(media[:4]):
        try:
            data = await fetch_twilio_media(url)
        except Exception as exc:  # noqa: BLE001
            print(f"[mms] fetch failed for media {i}: {exc}")
            continue
        if mime.startswith("image/"):
            suffix = ".jpg" if "jpeg" in mime or "jpg" in mime else "." + mime.split("/")[-1].split(";")[0]
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as fh:
                fh.write(data)
                tmp = fh.name
            try:
                block = prepare_image_for_model(tmp, mime_type=mime, name=f"mms-{i}{suffix}")
            finally:
                Path(tmp).unlink(missing_ok=True)
            if block:
                out.append(block)
                continue
            # sips unavailable or failed: pass the original bytes through as-is
            out.append({"filename": f"mms-{i}{suffix}", "mime_type": mime, "data": base64.b64encode(data).decode("ascii"), "size": len(data)})
        elif mime == "application/pdf":
            out.append({"filename": f"mms-{i}.pdf", "mime_type": mime, "data": base64.b64encode(data).decode("ascii"), "size": len(data)})
    return out


CREDENTIAL_REPLY = "I can't send that — it contained what looks like a credential."
# The turn's final text was only a tool call written as text
# (services.tool_markup).
GARBLED_REPLY = "Sorry — my answer came out garbled on my side, so I didn't send it. Please ask me again."


def scrub_outbound_reply(text: str) -> str:
    """The Twilio reply is sent here, not by a tool, so it never passed the
    tool loop's egress secret scan (2026-09-24). A reply that carries a
    credential is replaced, and the ledger records the kinds only."""
    try:
        from services import secret_scan, taint_service
        if not taint_service.secret_scan_enabled():
            return text
        hits = secret_scan.find_secrets(text, secret_scan.known_secrets())
    except Exception as exc:  # noqa: BLE001 — a broken scan must not leak
        print(f"[webhooks] reply secret scan failed; withholding the reply: {exc}")
        return CREDENTIAL_REPLY
    if not hits:
        return text
    from services import event_ledger
    event_ledger.record_event(
        kind="egress", source="twilio_reply", outcome="blocked",
        reason=", ".join(sorted({h.kind for h in hits})),
    )
    return CREDENTIAL_REPLY


async def process_inbound_and_respond(
    from_number: str,
    message_body: str,
    conversation_id: str,
    channel: str = "sms",
    media: Optional[list[tuple[str, str]]] = None,
    from_owner: bool = True,
):
    """
    Process an incoming message (SMS or WhatsApp) and send Edward's response.

    This runs as a background task to avoid webhook timeout. ``from_owner``
    is False only with ``EDWARD_TWILIO_ANSWER_ANYONE`` on: the prompt then
    says the sender is someone else.
    """
    # Send push notification for inbound message
    await _send_inbound_push_notification(from_number, message_body, channel, conversation_id)

    try:
        settings = await get_settings()
        graph = await get_graph()

        channel_name = "WhatsApp" if channel == "whatsapp" else "SMS"
        if from_owner:
            msg_context = (
                f"\n\n[CONTEXT: This message is from a {channel_name} conversation. "
                "Keep responses concise and mobile-friendly. "
                f"The user is messaging from {from_number} via {channel_name}.]"
            )
        else:
            name = owner.owner_name()
            msg_context = (
                f"\n\n[CONTEXT: This message is from a {channel_name} conversation with {from_number}, "
                f"who is NOT {name}: someone else texting your number. Your reply is texted to them. "
                f"Never share {name}'s private information (calendar, messages, memories, contacts, "
                "files, anything you read on this machine) and don't act for them. "
                "Keep responses concise and mobile-friendly.]"
            )
        enhanced_prompt = settings.system_prompt + msg_context

        attachments = await _attachments_from_media(media) if media else []
        if attachments and not (message_body or "").strip():
            message_body = f"[{channel_name}: {len(attachments)} attachment(s), no text]"

        response = await chat_with_memory(
            turn_source="twilio",
            message=message_body,
            conversation_id=conversation_id,
            system_prompt=enhanced_prompt,
            model=settings.model,
            temperature=settings.temperature,
            graph=graph,
            provider=settings.provider,
            attachments=attachments or None,
        )

        # Never a tool call written as text (the reply is the final text).
        response = tool_markup.safe_reply(response, GARBLED_REPLY)

        # Truncate if too long
        if len(response) > 1500:
            response = response[:1497] + "..."

        response = scrub_outbound_reply(response)
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


async def _require_twilio_signature(request: Request, configured_url: Optional[str], sender: str) -> None:
    """403 unless the request is signed by Twilio for this account
    (``twilio_service.check_webhook_signature``: fails closed, checks the
    configured URL and the URL as received behind a proxy). Logs why."""
    form_data = await request.form()
    params = {key: value for key, value in form_data.items()}
    ok, why = check_webhook_signature(request_url=str(request.url), headers=request.headers, params=params,
                                      configured_url=configured_url)
    if not ok:
        print(f"[Twilio] refused an inbound webhook from {sender}: {why}")
        raise HTTPException(status_code=403, detail="Invalid signature")


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
    1. Validate Twilio signature (security; fails closed)
    2. Check rate limiting
    3. Hold a text that is not from the owner (no turn, no reply) unless
       EDWARD_TWILIO_ANSWER_ANYONE is on
    4. Look up or create conversation for this phone number
    5. Process message and respond asynchronously
    6. Return empty TwiML (we respond via API, not TwiML)
    """
    # Every request is checked: unsigned, or signed for another account, is 403.
    await _require_twilio_signature(request, os.getenv("TWILIO_WEBHOOK_URL"), From)

    # Rate limiting
    if not check_rate_limit(From):
        print(f"Rate limited: {From}")
        # Return empty response (don't send error to potential spammer)
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    media = parse_media_params(dict(await request.form()))

    # Only the owner gets a turn unless EDWARD_TWILIO_ANSWER_ANYONE is on.
    from_owner = await is_from_owner(From)
    if not from_owner and not answer_anyone():
        await hold_inbound(From, Body, "sms", media_count=len(media))
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    print(f"Received SMS from {From}: {Body[:50]}..." + (f" +{len(media)} media" if media else ""))

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
        "sms",
        media,
        from_owner=from_owner,
    )

    # Return empty TwiML response (we'll respond via API)
    return Response(content=EMPTY_TWIML, media_type="application/xml")


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
    # Every request is checked: unsigned, or signed for another account, is 403.
    await _require_twilio_signature(
        request, os.getenv("TWILIO_WHATSAPP_WEBHOOK_URL", os.getenv("TWILIO_WEBHOOK_URL")), From)

    # Strip whatsapp: prefix to get raw phone number
    raw_number = From
    if raw_number.startswith("whatsapp:"):
        raw_number = raw_number[len("whatsapp:"):]

    # Rate limiting
    if not check_rate_limit(raw_number):
        print(f"Rate limited: {raw_number}")
        return Response(content=EMPTY_TWIML, media_type="application/xml")

    # Only the owner gets a turn unless EDWARD_TWILIO_ANSWER_ANYONE is on.
    from_owner = await is_from_owner(raw_number)
    if not from_owner and not answer_anyone():
        await hold_inbound(raw_number, Body, "whatsapp")
        return Response(content=EMPTY_TWIML, media_type="application/xml")

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
        "whatsapp",
        from_owner=from_owner,
    )

    return Response(content=EMPTY_TWIML, media_type="application/xml")


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
