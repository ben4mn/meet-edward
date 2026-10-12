"""REST API for the household hub (`/hub`) — the pet-station touchscreen."""

from typing import List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from services import hub_service

router = APIRouter()


@router.get("/hub/board")
async def board():
    """Everything the pet screen renders, in one payload."""
    return await hub_service.get_board()


class LogRequest(BaseModel):
    given_by: str
    status: str = "given"      # "given" | "skipped" | "refused"
    method: str = "tap"        # "tap" | "voice"
    note: Optional[str] = None
    # The care day the tap was for (Today sends the board's; the hub's own
    # taps send none). A different one is refused, never logged as today's.
    for_care_day: Optional[str] = None


def _current_care_day():
    from services.hub_pet_service import care_day_for
    from services.hub_service import _now_local

    return care_day_for(_now_local())


@router.post("/hub/care/{item_id}/log")
async def log_care(item_id: str, req: LogRequest):
    if req.for_care_day and req.for_care_day != _current_care_day().isoformat():
        raise HTTPException(
            status_code=409,
            detail="That dose was for yesterday's care day — refresh Today",
        )
    result = await hub_service.log_care(
        item_id,
        given_by=req.given_by,
        status=req.status,
        method=req.method,
        note=req.note,
    )
    # Whoever logs a dose at the hub is standing at it.
    await hub_service.touch_hub_person(req.given_by)
    return result


@router.delete("/hub/care/{item_id}/log")
async def undo_care(item_id: str):
    """Undo today's log for an item. Prominent on the hub by design."""
    return await hub_service.undo_care(item_id)


# ---------------------------------------------------------------------------
# Who is at the hub (2026-09-24): the household, not a user
# ---------------------------------------------------------------------------

class WhoRequest(BaseModel):
    person: str


@router.get("/hub/who")
async def get_who():
    """Who is at the hub right now (``person``, null after 30 min idle) and
    who lives there (``people``, ``EDWARD_HOUSEHOLD``, owner first)."""
    return await hub_service.get_hub_person()


@router.post("/hub/who")
async def post_who(req: WhoRequest):
    """The hub's who-toggle changed (fire-and-forget from the panel)."""
    try:
        return await hub_service.set_hub_person(req.person)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------

@router.get("/hub/lists")
async def lists():
    return await hub_service.get_lists()


class AddItemRequest(BaseModel):
    text: str
    created_by: str


@router.post("/hub/lists/{list_name}/items")
async def add_list_item(list_name: str, req: AddItemRequest):
    result = await hub_service.add_list_item(list_name, req.text, req.created_by)
    await hub_service.touch_hub_person(req.created_by)
    return result


class ItemDoneRequest(BaseModel):
    done: bool
    done_by: str


@router.patch("/hub/lists/items/{item_id}")
async def set_list_item_done(item_id: str, req: ItemDoneRequest):
    result = await hub_service.set_list_item_done(item_id, req.done, req.done_by)
    await hub_service.touch_hub_person(req.done_by)
    return result


@router.delete("/hub/lists/items/{item_id}")
async def delete_list_item(item_id: str):
    return await hub_service.delete_list_item(item_id)


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

@router.get("/hub/health")
async def health():
    from services import hub_health_service

    return await hub_health_service.collect()


# ---------------------------------------------------------------------------
# Photo screensaver
# ---------------------------------------------------------------------------

@router.get("/hub/photos")
async def photos(seed: int = 0):
    from services import hub_photos_service

    return hub_photos_service.list_photos(seed=seed)


@router.get("/hub/photos/{name}")
async def photo_file(name: str):
    from fastapi.responses import FileResponse
    from services import hub_photos_service

    path = hub_photos_service.safe_photo_path(hub_photos_service.PHOTOS_DIR, name)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")

    # Photos never change under a given name; let the Pi cache them so a
    # slideshow isn't re-downloading the same JPEGs every cycle.
    #
    # nosniff because this directory is now writable from a browser: the
    # extension whitelist decides the Content-Type, and we don't want a
    # sniffer overruling it on something that isn't the JPEG it claims.
    return FileResponse(
        path,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.post("/hub/photos")
async def upload_photos(files: List[UploadFile] = File(...)):
    """Add photos to the screensaver album from the phone.

    Partial success is the normal case — pick twenty photos on an iPhone
    and one of them will be a screen recording — so every file reports its
    own outcome instead of one bad pick failing the batch.
    """
    from services import hub_photos_service

    results = []
    for upload in files:
        data = await upload.read()
        results.append(hub_photos_service.save_upload(data, upload.filename or ""))
        await upload.close()

    saved = [r for r in results if r["ok"]]
    return {
        "saved": saved,
        "rejected": [r for r in results if not r["ok"]],
        "saved_count": len(saved),
        "converted_count": sum(1 for r in saved if r.get("converted")),
    }


@router.delete("/hub/photos/{name}")
async def delete_photo(name: str):
    from services import hub_photos_service

    if not hub_photos_service.delete_photo(name):
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": name}


# ---------------------------------------------------------------------------
# Screensaver settings
# ---------------------------------------------------------------------------

class HubSettingsPatch(BaseModel):
    slide_seconds: Optional[int] = None
    transition: Optional[str] = None
    edward_presents: Optional[bool] = None


@router.get("/hub/settings")
async def hub_settings():
    from services import hub_settings_service

    return await hub_settings_service.get_settings()


@router.patch("/hub/settings")
async def patch_hub_settings(patch: HubSettingsPatch):
    from services import hub_settings_service

    return await hub_settings_service.update_settings(
        patch.model_dump(exclude_none=True)
    )


# ---------------------------------------------------------------------------
# Canvas — Edward's universal expression surface on this screen
# ---------------------------------------------------------------------------


class CanvasRequest(BaseModel):
    kind: str
    title: Optional[str] = None
    source: Optional[str] = None
    body: Optional[str] = None
    seconds: int = 0


@router.get("/hub/canvas")
async def get_canvas():
    from services import hub_canvas_service
    content = await hub_canvas_service.current()
    return {"content": content.as_dict() if content else None}


@router.post("/hub/canvas")
async def post_canvas(req: CanvasRequest):
    from services import hub_canvas_service
    try:
        return await hub_canvas_service.show(kind=req.kind, title=req.title, source=req.source,
                                             body=req.body, seconds=req.seconds, shown_by="user")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))


@router.delete("/hub/canvas")
async def delete_canvas(by: str = "tap"):
    """A tap on the screen, or Settings. Records who dismissed it."""
    from services import hub_canvas_service
    return {"cleared": await hub_canvas_service.clear(by=by)}
