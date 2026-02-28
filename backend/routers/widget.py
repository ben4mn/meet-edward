"""REST API endpoints for the iOS widget (Scriptable)."""

import uuid

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from services.widget_service import (
    get_or_create_token,
    regenerate_token,
    verify_token,
    get_widget_state,
)

router = APIRouter()


@router.get("/widget")
async def get_widget(token: str = Query(..., description="Widget access token")):
    """Fetch widget JSON for Scriptable. Authenticated via query param token."""
    if not await verify_token(token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    state = await get_widget_state()
    return state


class WidgetChatRequest(BaseModel):
    token: str
    message: str


@router.post("/widget/chat")
async def widget_chat(req: WidgetChatRequest):
    """Send a message to Edward from the Scriptable widget.

    Authenticated via widget token. Returns conversation_id + response text.
    """
    if not await verify_token(req.token):
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    from services.graph import get_graph, chat_with_memory
    from services.settings_service import get_settings
    from services.conversation_service import create_conversation

    settings = await get_settings()
    graph = await get_graph()
    conversation_id = str(uuid.uuid4())

    title = req.message[:50].strip()
    if len(req.message) > 50:
        title += "..."
    await create_conversation(conversation_id, title)

    response = await chat_with_memory(
        message=req.message,
        conversation_id=conversation_id,
        system_prompt=settings.system_prompt,
        model=settings.model,
        temperature=settings.temperature,
        graph=graph,
    )

    return {
        "conversation_id": conversation_id,
        "response": response,
    }


@router.get("/widget/token")
async def get_widget_token():
    """Get the current widget token (cookie-authenticated via middleware)."""
    token = await get_or_create_token()
    return {"token": token}


@router.post("/widget/token/regenerate")
async def regenerate_widget_token():
    """Regenerate the widget token (cookie-authenticated via middleware)."""
    token = await regenerate_token()
    return {"token": token}
