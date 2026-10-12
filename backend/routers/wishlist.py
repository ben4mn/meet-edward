"""``/api/wishlist`` — Edward's wishlist, read and managed by the owner.

Edward logs wishes through the ``wish_for_feature`` tool; this router is
the Settings → Work → Wishlist page. The owner can add their own
(``source: "owner"``), edit any field, move items through the statuses, or
delete.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from services import wishlist_service as ws

router = APIRouter()


class WishCreate(BaseModel):
    title: str = Field(..., max_length=ws.MAX_TITLE)
    why: str = ""
    detail: Optional[str] = None
    priority: Optional[int] = 2


class WishUpdate(BaseModel):
    status: Optional[str] = None
    title: Optional[str] = Field(None, max_length=ws.MAX_TITLE)
    why: Optional[str] = None
    detail: Optional[str] = None
    priority: Optional[int] = None


def _status_or_400(status: Optional[str]) -> Optional[str]:
    if status is None:
        return None
    try:
        return ws.normalize_status(status)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get("/wishlist")
async def list_wishlist(status: Optional[str] = None):
    """Every wish, open ones first; ``?status=`` narrows to one status."""
    return {"items": await ws.list_wishes(status=_status_or_400(status))}


@router.post("/wishlist")
async def add_wish(body: WishCreate):
    """The owner adds a wish of their own."""
    if not body.title.strip():
        raise HTTPException(status_code=400, detail="a wish needs a title")
    return await ws.add_wish(
        title=body.title,
        why=body.why,
        detail=body.detail,
        priority=body.priority if body.priority is not None else 2,
        source="owner",
    )


@router.patch("/wishlist/{wish_id}")
async def update_wish(wish_id: str, body: WishUpdate):
    """Edit any field; the status must be one of the allowed list."""
    status = _status_or_400(body.status)
    item = await ws.update_wish(
        wish_id,
        status=status,
        title=body.title,
        why=body.why,
        detail=body.detail,
        priority=body.priority,
    )
    if item is None:
        raise HTTPException(status_code=404, detail="No such wish")
    return item


@router.delete("/wishlist/{wish_id}")
async def delete_wish(wish_id: str):
    return {"deleted": await ws.delete_wish(wish_id)}
