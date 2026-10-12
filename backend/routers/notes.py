"""``/api/notes`` — Edward's notes on Today (UX pass 2026-09-25).

A note that needs the owner is a card on Today. "Done" expires it now (with the
rest of its card, ``also``: notes sharing a title are one card); the list
is what a "and 3 more" link opens.
"""

from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import notes_service as ns

router = APIRouter()


class DismissBody(BaseModel):
    also: Optional[List[str]] = None


@router.get("/notes")
async def list_notes(hours: float = 24, limit: int = 60):
    """Live notes, newest first (ideas have their own list)."""
    rows = await ns.recent_notes(hours=hours, limit=max(1, min(limit, 200)), unexpired_only=True)
    return {"notes": [ns.note_dict(n) for n in rows if n.kind != ns.IDEA_KIND]}


@router.post("/notes/{note_id}/dismiss")
async def dismiss_note(note_id: str, body: Optional[DismissBody] = None):
    n = await ns.dismiss_notes(note_id, (body.also if body else None) or [])
    if n is None:
        raise HTTPException(status_code=404, detail="Note not found")
    return {"ok": True, "dismissed": n}
