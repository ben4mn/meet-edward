"""
Alexa skill service for Edward.

Handles inbound Alexa custom skill requests: validates requests,
manages conversation mapping, races chat_with_memory against Alexa's
8-second timeout, and cleans responses for speech output.
"""

import asyncio
import os
import re
from typing import Optional

ALEXA_SKILL_ID = os.getenv("ALEXA_SKILL_ID")

# Alexa enforces 8s timeout; we race with 6.5s to leave headroom
_RESPONSE_DEADLINE = 6.5


def is_configured() -> bool:
    """Check if Alexa inbound skill is configured."""
    return bool(ALEXA_SKILL_ID)


def get_status() -> dict:
    """Get Alexa inbound skill status."""
    if not ALEXA_SKILL_ID:
        return {
            "status": "error",
            "status_message": "ALEXA_SKILL_ID not set",
        }
    return {
        "status": "connected",
        "status_message": "Skill ID configured",
    }


def validate_skill_id(request_body: dict) -> bool:
    """
    Validate that the request comes from the expected Alexa skill.

    In production, you'd also verify the request signature via
    ask-sdk-webservice. For private use, skill ID check is sufficient.
    """
    if not ALEXA_SKILL_ID:
        return False

    app_id = (
        request_body
        .get("session", {})
        .get("application", {})
        .get("applicationId", "")
    )
    return app_id == ALEXA_SKILL_ID


def get_user_utterance(request_body: dict) -> Optional[str]:
    """Extract the user's spoken text from an IntentRequest."""
    request = request_body.get("request", {})
    intent = request.get("intent", {})
    slots = intent.get("slots", {})

    # AskEdwardIntent uses a SearchQuery slot for freeform speech
    query_slot = slots.get("query", {})
    return query_slot.get("value")


async def process_alexa_request(request_body: dict) -> dict:
    """
    Process an incoming Alexa request and return an Alexa-formatted response.

    Handles LaunchRequest, IntentRequest, and SessionEndedRequest.
    """
    request_type = request_body.get("request", {}).get("type", "")

    if request_type == "LaunchRequest":
        return _build_response(
            "Hey, I'm Edward. What can I help you with?",
            should_end=False,
        )

    if request_type == "SessionEndedRequest":
        return _build_response("", should_end=True)

    if request_type == "IntentRequest":
        intent_name = (
            request_body.get("request", {})
            .get("intent", {})
            .get("name", "")
        )

        # Handle built-in intents
        if intent_name in ("AMAZON.StopIntent", "AMAZON.CancelIntent"):
            return _build_response("Goodbye.", should_end=True)

        if intent_name == "AMAZON.HelpIntent":
            return _build_response(
                "You can ask me anything. For example, say: ask Edward what's on my calendar today.",
                should_end=False,
            )

        # AskEdwardIntent — the main handler
        utterance = get_user_utterance(request_body)
        if not utterance:
            return _build_response(
                "I didn't catch that. Could you say it again?",
                should_end=False,
            )

        return await _handle_query(utterance)

    return _build_response("I'm not sure how to handle that.", should_end=True)


async def _handle_query(utterance: str) -> dict:
    """
    Race Edward's chat_with_memory against the Alexa timeout.

    If Edward responds in time, return the spoken response.
    If timeout, return a fallback and let the background task finish
    (delivering the full answer via push notification).
    """
    from services.graph import get_graph, chat_with_memory
    from services.settings_service import get_settings

    settings = await get_settings()
    graph = await get_graph()

    conversation_id = await _get_or_create_alexa_conversation()

    # Inject Alexa context into system prompt
    system_prompt = (
        f"{settings.system_prompt}\n\n"
        "[CONTEXT: Alexa voice conversation. Keep responses concise, 2-3 sentences max. "
        "No markdown, URLs, code blocks, or special formatting — this will be spoken aloud.]"
    )

    chat_task = asyncio.create_task(
        chat_with_memory(
            turn_source="alexa",
            message=utterance,
            conversation_id=conversation_id,
            system_prompt=system_prompt,
            model=settings.model,
            temperature=settings.temperature,
            graph=graph,
            provider=settings.provider,
        )
    )

    try:
        response = await asyncio.wait_for(chat_task, timeout=_RESPONSE_DEADLINE)
        spoken = clean_for_speech(str(response) if response else "I processed that but had nothing to say.")
        return _build_response(
            spoken,
            should_end=False,
            reprompt="Anything else?",
        )

    except asyncio.TimeoutError:
        # Let the background task finish — it'll store in conversation history
        # Optionally announce via Echo when done
        asyncio.create_task(_deliver_delayed_response(chat_task, conversation_id))

        return _build_response(
            "Let me work on that. I'll send you a notification when I have an answer.",
            should_end=True,
        )


async def _deliver_delayed_response(
    chat_task: asyncio.Task,
    conversation_id: str,
) -> None:
    """
    Wait for the background chat task to finish, then deliver via push notification
    and optionally announce on Echo.
    """
    try:
        response = await chat_task
        if not response:
            return

        text = clean_for_speech(str(response))

        # Send push notification with the full answer
        try:
            from services.push_service import send_push_notification, is_configured as push_configured
            if push_configured():
                await send_push_notification(
                    title="Edward",
                    body=text[:200],
                    url=f"/chat?c={conversation_id}",
                    tag="alexa-answer",
                    source="alexa",
                )
        except Exception as e:
            print(f"Alexa delayed push notification error: {e}")

        # Optionally announce on Echo if outbound is available
        try:
            from services.home_assistant_service import is_configured as ha_configured, announce
            if ha_configured():
                await announce(text[:500])
        except Exception as e:
            print(f"Alexa delayed announcement error: {e}")

    except Exception as e:
        print(f"Alexa delayed response error: {e}")


def clean_for_speech(text: str) -> str:
    """
    Clean Edward's response for spoken output via Alexa.

    Strips markdown, URLs, code blocks, and truncates at sentence boundaries.
    """
    # Remove code blocks
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`[^`]+`', '', text)

    # Remove markdown formatting
    text = re.sub(r'\*\*(.+?)\*\*', r'\1', text)  # bold
    text = re.sub(r'\*(.+?)\*', r'\1', text)  # italic
    text = re.sub(r'#{1,6}\s+', '', text)  # headers
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)  # links
    text = re.sub(r'!\[([^\]]*)\]\([^)]+\)', '', text)  # images

    # Remove URLs
    text = re.sub(r'https?://\S+', '', text)

    # Remove bullet points and numbered lists formatting
    text = re.sub(r'^\s*[-*+]\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^\s*\d+\.\s+', '', text, flags=re.MULTILINE)

    # Collapse whitespace
    text = re.sub(r'\n{2,}', '. ', text)
    text = re.sub(r'\n', ' ', text)
    text = re.sub(r'\s{2,}', ' ', text)

    text = text.strip()

    # Truncate at sentence boundary if too long (Alexa has ~8000 char limit for SSML)
    if len(text) > 600:
        sentences = re.split(r'(?<=[.!?])\s+', text)
        truncated = []
        total = 0
        for s in sentences:
            if total + len(s) > 550:
                break
            truncated.append(s)
            total += len(s)
        text = ' '.join(truncated) if truncated else text[:550]

    return text


def text_to_ssml(text: str) -> str:
    """
    Convert plain text to SSML with natural pacing.

    Adds break tags at sentence boundaries for more natural speech,
    and wraps in speak tags.
    """
    if not text:
        return '<speak></speak>'

    # Escape XML special characters
    text = text.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')

    # Add short breaks after sentences for natural pacing
    text = re.sub(r'([.!?])\s+', r'\1<break time="300ms"/> ', text)

    # Add a medium break after colons (common in Edward's responses)
    text = re.sub(r':\s+', r':<break time="200ms"/> ', text)

    return f'<speak>{text}</speak>'


def _build_response(
    speech: str,
    should_end: bool = True,
    reprompt: Optional[str] = None,
    use_ssml: bool = True,
) -> dict:
    """Build an Alexa JSON response with optional SSML."""
    if use_ssml:
        output_speech = {
            "type": "SSML",
            "ssml": text_to_ssml(speech),
        }
    else:
        output_speech = {
            "type": "PlainText",
            "text": speech,
        }

    response = {
        "version": "1.0",
        "response": {
            "outputSpeech": output_speech,
            "shouldEndSession": should_end,
        },
    }

    if reprompt:
        if use_ssml:
            reprompt_speech = {
                "type": "SSML",
                "ssml": text_to_ssml(reprompt),
            }
        else:
            reprompt_speech = {
                "type": "PlainText",
                "text": reprompt,
            }
        response["response"]["reprompt"] = {
            "outputSpeech": reprompt_speech,
        }

    return response


async def _get_or_create_alexa_conversation() -> str:
    """
    Get or create a conversation for Alexa interactions.

    Reuses the most recent "alexa" source conversation if it was active
    within the last 24 hours. Otherwise creates a new one.
    """
    import uuid
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, desc
    from services.database import async_session, ConversationModel
    from services.conversation_service import create_conversation

    async with async_session() as session:
        result = await session.execute(
            select(ConversationModel)
            .where(ConversationModel.source == "alexa")
            .order_by(desc(ConversationModel.updated_at))
            .limit(1)
        )
        recent = result.scalar_one_or_none()

        if recent:
            updated = recent.updated_at
            if updated and updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)

            now = datetime.now(timezone.utc)
            if updated and (now - updated) < timedelta(hours=24):
                return recent.id

    # Create new conversation
    conversation_id = str(uuid.uuid4())
    await create_conversation(
        conversation_id,
        title="Alexa Conversation",
        source="alexa",
        channel="voice",
    )
    return conversation_id
