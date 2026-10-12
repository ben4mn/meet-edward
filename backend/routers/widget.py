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
    The turn is the owner's (``widget`` is an owner source,
    services/toolset/policy.py: every read, sends, pushes), so the widget
    token is owner-equivalent for chat: whoever holds it can talk to Edward
    as the owner. Keep it private; ``POST /api/widget/token/regenerate``
    replaces a leaked one.
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
        turn_source="widget",
        message=req.message,
        conversation_id=conversation_id,
        system_prompt=settings.system_prompt,
        model=settings.model,
        temperature=settings.temperature,
        graph=graph,
        provider=settings.provider,
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


@router.post("/widget/refresh")
async def refresh_widget():
    """Recompose the widget now from the hub board (Settings button / debugging).
    Forces past the custom-content hold and drops any stale script."""
    from services.widget_expression_service import refresh_widget_expression

    out = await refresh_widget_expression("manual", force=True)
    return {"refreshed": out is not None, "subtitle": (out or {}).get("subtitle")}


@router.post("/widget/pulse")
async def pulse_widget():
    """Ask Edward to look at his surfaces now (Settings button / debugging)."""
    from services.expression_service import run_expression_pulse

    return await run_expression_pulse("manual")
