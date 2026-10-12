"""Edward's screen (the `/screen` page, shown on the host Mac's monitor).

`GET /api/screen` is what the page polls: the stage — Edward's placed
scene, the lines he said, the owner's hold — and what is on top. The page
can dismiss the show (click / Escape). The owner (settings, the operator)
can set a scene or a show by hand, and that holds the screen like their ask in chat;
Edward does it with his tools (compose_screen, say_on_screen,
show_on_screen…). His hands in Chrome are on `/api/browser/*`
(routers/browser_ext.py).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from services import browser_hands, screen_service, screen_window_service

router = APIRouter()


class ShowRequest(BaseModel):
    kind: str
    title: Optional[str] = None
    source: Optional[str] = None
    body: Optional[str] = None
    seconds: int = 0


class SceneRequest(BaseModel):
    pieces: List[Dict[str, Any]]
    me: Optional[Dict[str, Any]] = None
    why: str = ""
    hold_minutes: int = 0


@router.get("/screen")
async def get_screen() -> Dict[str, Any]:
    return await screen_service.get_snapshot()


@router.post("/screen/show")
async def post_show(req: ShowRequest) -> Dict[str, Any]:
    try:
        return await screen_service.show(kind=req.kind, title=req.title, source=req.source,
                                         body=req.body, seconds=req.seconds, shown_by="user", by="owner")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/screen/show")
async def delete_show(by: str = "tap") -> Dict[str, Any]:
    # A person at the screen (the page's tap) is never Edward's own turn, so
    # the hold never refuses it.
    return {"cleared": await screen_service.clear(by="tap" if by == "edward" else by)}


@router.put("/screen/scene")
async def put_scene(req: SceneRequest) -> Dict[str, Any]:
    """The owner sets the scene by hand; it holds the screen (hold_minutes, default two hours)."""
    try:
        out = await screen_service.compose(req.pieces, me=req.me, why=req.why, hold_minutes=req.hold_minutes,
                                           by="owner")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {**out, "held_until": out["held_until"].isoformat() if out.get("held_until") else None}


@router.delete("/screen/scene")
async def delete_scene() -> Dict[str, Any]:
    """Back to the stock scene; ends the owner's hold."""
    return {"reset": await screen_service.reset_scene(by="owner")}


@router.get("/screen/look")
async def get_look(at: str = "auto") -> Dict[str, Any]:
    return {"description": await browser_hands.look(at)}


@router.post("/screen/window")
async def post_window() -> Dict[str, Any]:
    """Bring the /screen tab up in Chrome on this Mac (find or open, fill the desktop).
    Needs ``EDWARD_CHROME_HANDS=on``; off, it answers ``ok: false`` and touches nothing."""
    return await screen_window_service.ensure_screen_tab()


# --- the thinking stream (the stage, 2026-09-28) ---

@router.get("/screen/live")
async def get_live() -> Dict[str, Any]:
    """What Edward is thinking while a wake or the pulse runs, the last few
    folded turns, and his recent thoughts from the last 12 hours for the
    lane between turns (``screen_thoughts``); the page polls it every 2 s.
    Never a 500: anything wrong reads as not thinking, and as no recent
    thoughts."""
    from services import screen_thoughts

    try:
        out = screen_thoughts.snapshot()
    except Exception:  # noqa: BLE001
        out = screen_thoughts.empty_snapshot()
    try:
        out["recent"] = await screen_thoughts.recent()
    except Exception:  # noqa: BLE001
        out["recent"] = []
    return out
