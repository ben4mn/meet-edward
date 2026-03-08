from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import json
import uuid
import base64

from models import ChatRequest
from services.graph import stream_with_memory, stream_with_memory_events, chat_with_memory, EventType
from services.settings_service import get_settings
from services.conversation_service import (
    create_conversation,
    conversation_exists,
    increment_message_count,
)
from services.memory_service import retrieve_memories

router = APIRouter()

# Max file size for chat uploads (10 MB)
CHAT_UPLOAD_MAX_SIZE = 10 * 1024 * 1024

# Allowed MIME types for chat attachments
CHAT_ATTACHMENT_MIME_TYPES = {
    "image/png", "image/jpeg", "image/gif", "image/webp",
    "application/pdf",
    "text/plain", "text/csv", "application/json",
}


async def _parse_chat_request(request: Request):
    """
    Parse a chat request, handling both JSON and multipart/form-data.

    Returns:
        Tuple of (message, conversation_id, attachments)
        where attachments is a list of dicts with keys: filename, mime_type, data (base64), size, file_id
    """
    content_type = request.headers.get("content-type", "")

    if "multipart/form-data" in content_type:
        form = await request.form()
        message = form.get("message", "")
        conversation_id = form.get("conversation_id") or None

        attachments = []
        # Get uploaded files
        files = form.getlist("files") if hasattr(form, "getlist") else []
        # Also check for single file field
        if not files and "files" in form:
            files = [form["files"]]

        for upload_file in files:
            if hasattr(upload_file, "read"):
                file_data = await upload_file.read()

                if len(file_data) > CHAT_UPLOAD_MAX_SIZE:
                    continue  # Skip oversized files

                mime_type = upload_file.content_type or "application/octet-stream"
                filename = upload_file.filename or "unnamed"

                # Store file in persistent storage
                file_id = None
                try:
                    from services.file_storage_service import store_file, ALLOWED_MIME_TYPES
                    # Temporarily add chat attachment types to allowed list if needed
                    stored = await store_file(
                        file_data=file_data,
                        filename=filename,
                        mime_type=mime_type,
                        category="upload",
                        source="user",
                        conversation_id=conversation_id,
                    )
                    file_id = stored.id
                except Exception as e:
                    print(f"Failed to store uploaded file: {e}")

                attachments.append({
                    "filename": filename,
                    "mime_type": mime_type,
                    "data": base64.standard_b64encode(file_data).decode("utf-8"),
                    "size": len(file_data),
                    "file_id": file_id,
                })

        return message, conversation_id, attachments
    else:
        body = await request.json()
        return body.get("message", ""), body.get("conversation_id"), []


@router.post("/chat")
async def chat(request: Request):
    """Send a message to Edward and get a streaming response.

    Accepts either JSON body or multipart/form-data with file attachments.
    """
    try:
        message, conversation_id, attachments = await _parse_chat_request(request)

        if not message and not attachments:
            raise HTTPException(status_code=400, detail="Message or files required")

        settings = await get_settings()

        is_new_conversation = conversation_id is None
        conversation_id = conversation_id or str(uuid.uuid4())

        # Create or update conversation record
        if is_new_conversation:
            # Use first 50 chars of message as title
            title = (message or "Image upload")[:50].strip()
            if len(title) > 50:
                title += "..."
            await create_conversation(conversation_id, title)
        else:
            # Increment message count for existing conversation
            if await conversation_exists(conversation_id):
                await increment_message_count(conversation_id)

        async def generate():
            # Register active chat so heartbeat defers triage
            from services.heartbeat.heartbeat_service import register_active_chat, unregister_active_chat
            register_active_chat(conversation_id)
            try:
                async for event in stream_with_memory_events(
                    message=message,
                    conversation_id=conversation_id,
                    system_prompt=settings.system_prompt,
                    model=settings.model,
                    temperature=settings.temperature,
                    attachments=attachments if attachments else None,
                ):
                    # Stream the full event object
                    yield f"data: {json.dumps(event)}\n\n"
            finally:
                unregister_active_chat(conversation_id)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/chat/simple")
async def chat_simple(request: Request):
    """Send a message to Edward and get a non-streaming response.

    Accepts either JSON body or multipart/form-data with file attachments.
    """
    try:
        message, conversation_id, attachments = await _parse_chat_request(request)

        if not message and not attachments:
            raise HTTPException(status_code=400, detail="Message or files required")

        settings = await get_settings()

        is_new_conversation = conversation_id is None
        conversation_id = conversation_id or str(uuid.uuid4())

        # Create or update conversation record
        if is_new_conversation:
            title = (message or "Image upload")[:50].strip()
            if len(title) > 50:
                title += "..."
            await create_conversation(conversation_id, title)
        else:
            if await conversation_exists(conversation_id):
                await increment_message_count(conversation_id)

        response = await chat_with_memory(
            message=message,
            conversation_id=conversation_id,
            system_prompt=settings.system_prompt,
            model=settings.model,
            temperature=settings.temperature,
            attachments=attachments if attachments else None,
        )

        return {
            "message": response,
            "conversation_id": conversation_id
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


GREETING_PROMPT = """Generate a very short greeting (1 sentence, under 10 words). You are Edward.
Tone: dry, chill, occasionally sassy or deadpan. Never cheery or motivational.
Think more "what's up" than "let's crush this day!" — you're not a life coach.
If context is provided, you can reference it subtly but don't force it.
Don't ask how you can help.

{context}

Return ONLY the greeting text, nothing else."""


@router.post("/greeting")
async def get_greeting():
    """Generate a personalized greeting based on recent memories."""
    try:
        # Try to retrieve some recent memories for context
        context = ""
        try:
            memories = await retrieve_memories(
                "user name personality hobbies daily routine",
                limit=3,
                min_score=0.3,
                memory_types=["fact", "preference"],
            )
            if memories:
                context = "Context about the user:\n" + "\n".join(
                    f"- {m.content}" for m in memories
                )
        except Exception as e:
            print(f"Failed to retrieve memories for greeting: {e}")
            # Continue without memories

        # Use Haiku for speed and cost efficiency
        from services.llm_client import haiku_call

        prompt = GREETING_PROMPT.format(context=context if context else "No specific context available.")

        greeting = await haiku_call(
            system="",
            message=prompt,
            max_tokens=100,
            temperature=0.7,
        )

        return {"greeting": greeting.strip()}

    except Exception as e:
        print(f"Greeting generation failed: {e}")
        err = str(e).lower()
        if "usage limit" in err or "rate limit" in err:
            return {"greeting": "Hey — I've hit my Anthropic API limit, so I can't chat right now. Check back soon."}
        return {"greeting": "Hey there! Good to see you."}
