"""The household photo door: the API behind ``/photos`` (2026-10-11).

A small PWA with its own password (services/photo_door_auth.py) for
someone else in the household. It reaches only the hub's photo album and
its screensaver settings — the same files and the same settings row the
hub reads, through the same services. The middleware gates every route
here except the three auth ones; while the door is off (no
``PHOTO_DOOR_PASSWORD_HASH``) a login is refused and the rest answer 404.
"""

import asyncio
from typing import List, Optional

from fastapi import APIRouter, Cookie, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

# The owner login's limiter (one store, one global ceiling) keyed by the
# real client (services/client_ip.py), never a client-written header. The
# attempt is reserved before the password check (bcrypt, off the event
# loop), so a parallel burst counts while it runs.
from routers.auth import _clear_attempts, _reserve_attempt
from services import client_ip, photo_door_auth

# Who the door's edits are recorded as.
UPDATED_BY = "photo_door"

router = APIRouter(prefix="/photo-door", tags=["photo-door"])


def _door_open() -> None:
    """The album and settings routes exist only while the door is on (the
    middleware answers first; this holds without it)."""
    if not photo_door_auth.enabled():
        raise HTTPException(status_code=404, detail="Not found")


# Everything but the three auth routes.
_gated = APIRouter(dependencies=[Depends(_door_open)])


class PasswordRequest(BaseModel):
    password: str


@router.get("/auth/status")
async def status(photo_door_token: Optional[str] = Cookie(default=None)):
    return {
        "authenticated": photo_door_auth.verify_token(photo_door_token),
        "enabled": photo_door_auth.enabled(),
    }


@router.post("/auth/login")
async def login(body: PasswordRequest, req: Request, response: Response):
    if not photo_door_auth.enabled():
        raise HTTPException(status_code=403, detail=photo_door_auth.disabled_message())
    ip = client_ip.client_ip(req)
    allowed, retry_after = _reserve_attempt(ip)
    if not allowed:
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})
    if not await asyncio.to_thread(photo_door_auth.check_password, body.password):
        # The reservation stays: it is this failure.
        raise HTTPException(status_code=401, detail="Invalid password")
    _clear_attempts(ip)
    photo_door_auth.set_cookie(response, photo_door_auth.create_token())
    return {"message": "Login successful"}


@router.post("/auth/logout")
async def logout(response: Response):
    response.delete_cookie(key=photo_door_auth.COOKIE_NAME)
    return {"message": "Logged out"}


# ---------------------------------------------------------------------------
# The album
# ---------------------------------------------------------------------------


@_gated.get("/photos")
async def photos():
    from services import hub_photos_service

    listing = hub_photos_service.list_photos()
    # The host's paths are not the door's to see.
    return {k: v for k, v in listing.items() if k != "dir"}


@_gated.get("/photos/{name}")
async def photo(name: str):
    from services import hub_photos_service

    path = hub_photos_service.safe_photo_path(hub_photos_service.PHOTOS_DIR, name)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@_gated.get("/photos/{name}/thumb")
async def photo_thumb(name: str):
    from services import hub_photos_service

    path = hub_photos_service.thumbnail_path(name)
    if path is None:
        raise HTTPException(status_code=404, detail="Not found")
    return FileResponse(path, headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@_gated.post("/photos")
async def upload(files: List[UploadFile] = File(...)):
    from services import hub_photos_service

    results = []
    for f in files:
        data = await f.read()
        results.append(hub_photos_service.save_upload(data, f.filename or ""))
        await f.close()
    saved = [r for r in results if r["ok"]]
    return {
        "saved": saved,
        "rejected": [r for r in results if not r["ok"]],
        "saved_count": len(saved),
        "converted_count": sum(1 for r in saved if r.get("converted")),
    }


@_gated.delete("/photos/{name}")
async def delete(name: str):
    from services import hub_photos_service

    if not hub_photos_service.delete_photo(name):
        raise HTTPException(status_code=404, detail="Not found")
    return {"deleted": name}


class RotateRequest(BaseModel):
    direction: str  # "left" | "right"


@_gated.post("/photos/{name}/rotate")
async def rotate(name: str, body: RotateRequest):
    from services import hub_photos_service

    if body.direction not in hub_photos_service.ROTATIONS:
        raise HTTPException(status_code=422, detail="direction must be left or right")
    new = hub_photos_service.rotate_photo(name, body.direction)
    if new is None:
        raise HTTPException(status_code=404, detail="That photo can't be rotated")
    return {"name": new, "previous": name}


# ---------------------------------------------------------------------------
# The screensaver settings (the hub's own row)
# ---------------------------------------------------------------------------


class SettingsPatch(BaseModel):
    slide_seconds: Optional[int] = None
    transition: Optional[str] = None
    edward_presents: Optional[bool] = None


@_gated.get("/settings")
async def get_settings():
    from services import hub_settings_service

    return await hub_settings_service.get_settings()


@_gated.patch("/settings")
async def patch_settings(patch: SettingsPatch):
    from services import hub_settings_service

    return await hub_settings_service.update_settings(patch.model_dump(exclude_none=True), updated_by=UPDATED_BY)


router.include_router(_gated)
