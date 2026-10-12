"""Presence beacons: which of the owner's screens is awake, and are they at it?

``POST /api/presence/beacon`` is on the auth middleware's public list so a
20-line script on another Mac (or a menu-bar app) can call it with the
widget token in the *body* — never in the URL. A signed-in browser tab can
omit the token; its session cookie is checked instead. The widget token is
owner-equivalent for chat (``POST /api/widget/chat`` runs owner turns with
it), so it belongs only on the owner's own devices.
"""

from __future__ import annotations

from typing import List, Optional

import asyncio
import json
from typing import Any, Dict, Iterable, List, Set

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services import auth_service, presence_service
from services.widget_service import verify_token

router = APIRouter()


class BeaconBody(BaseModel):
    token: Optional[str] = None
    device_id: str = Field(..., min_length=1, max_length=120)
    device_name: Optional[str] = None
    kind: str
    state: str = "active"
    idle_seconds: int = 0
    app: Optional[str] = None
    capabilities: List[str] = Field(default_factory=list)


async def _authorized(request: Request, body: BeaconBody) -> bool:
    if body.token:
        return await verify_token(body.token, touch=False)
    cookie = request.cookies.get("edward_token")
    return bool(cookie and auth_service.verify_token(cookie))


@router.post("/presence/beacon")
async def post_beacon(body: BeaconBody, request: Request):
    if not await _authorized(request, body):
        raise HTTPException(status_code=401, detail="Invalid token")
    try:
        await presence_service.record_beacon(body.model_dump(exclude={"token"}))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    state = await presence_service.get_attention_state(fresh=True)
    return {"ok": True, "attention": state.as_dict()}


@router.get("/presence/state")
async def get_state():
    state = await presence_service.get_attention_state(fresh=True)
    return {"attention": state.as_dict(), "beacons": [b.as_dict() for b in presence_service.list_beacons()]}


# ---- live stream for the menu-bar companion (Session F) ----------------------------

STREAM_HEARTBEAT_SECONDS = 15


def format_sse(event: str, data: Any) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def stream_frames_once(*, device_id: str, attention: Dict[str, Any], seen: Set[str]) -> List[str]:
    """One tick of the stream: a state frame. The ``interrupt`` frames went
    with the interrupt ladder (2026-10-11); ``seen`` stays for the shape."""
    return [format_sse("state", attention)]


@router.get("/presence/stream")
async def presence_stream(token: str = Query(...), device_id: str = Query(..., min_length=1, max_length=120)):
    """Server-sent events for a companion app: `state` every few seconds.
    Token in the query is unavoidable for EventSource (the access log
    redacts it, services/access_log.py). It is the widget token, which is
    NOT read-only: ``POST /api/widget/chat`` runs owner turns with it, so it
    is owner-equivalent for chat — keep it private and regenerate it
    (``POST /api/widget/token/regenerate``) if it leaks."""
    if not await verify_token(token, touch=False):
        raise HTTPException(status_code=401, detail="Invalid token")

    async def gen():
        seen: Set[str] = set()
        while True:
            try:
                state = await presence_service.get_attention_state()
                for frame in stream_frames_once(device_id=device_id, attention=_summary(state), seen=seen):
                    yield frame
            except Exception as exc:  # noqa: BLE001
                yield format_sse("error", {"message": str(exc)})
            await asyncio.sleep(STREAM_HEARTBEAT_SECONDS if not _has_pending(device_id) else 3)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


def _summary(state) -> Dict[str, Any]:
    d = state.as_dict()
    d.pop("fresh_beacons", None)
    # How many messages Edward was holding before acting: the settle-before-
    # act queue went with the legacy triage engine, so nothing is held. The
    # key stays for the companion app's frame shape.
    d["watching"] = 0
    return d


def _has_pending(device_id: str) -> bool:
    """Nothing is ever queued for a device since the ladder went (2026-10-11)."""
    return False
