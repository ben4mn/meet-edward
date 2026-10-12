"""Today — the curated front door of the app (replaces the inbox)."""

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services.today_service import get_today, mark_heartbeat_done

router = APIRouter()


@router.get("/today")
async def today():
    """Needs-you items, today's digest and the owner's recent conversations."""
    return await get_today()


class DoneBody(BaseModel):
    also: Optional[List[str]] = None


@router.post("/today/heartbeat/{event_id}/done")
async def heartbeat_done(event_id: str, body: Optional[DoneBody] = None):
    """"Done" on a message (or calendar) card: its events are marked seen,
    so the card leaves Today and the next wake does not list them."""
    n = await mark_heartbeat_done(event_id, (body.also if body else None) or [])
    if n is None:
        raise HTTPException(status_code=404, detail="Event not found")
    return {"ok": True, "done": n}
