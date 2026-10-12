"""REST API endpoints for the always-on display (`/display` page).

All endpoints sit behind the standard cookie auth middleware. The frontend
polls these on cadences defined in CLAUDE_CODE_PROMPT.md.
"""

from typing import Any, Dict

from fastapi import APIRouter
from pydantic import BaseModel

from services import display_service, display_settings, display_signals


router = APIRouter()


@router.get("/display/calendar")
async def calendar() -> Dict[str, Any]:
    return await display_service.get_calendar()


@router.get("/display/vitals")
async def vitals() -> Dict[str, Any]:
    return await display_service.get_vitals()


@router.get("/display/workers")
async def workers() -> Dict[str, Any]:
    return await display_service.get_workers()


@router.get("/display/recents")
async def recents() -> Dict[str, Any]:
    return await display_service.get_recents()


@router.get("/display/memory_ticker")
async def memory_ticker() -> Dict[str, Any]:
    return await display_service.get_memory_ticker()


@router.get("/display/now_thinking")
async def now_thinking() -> Dict[str, Any]:
    item = await display_service.get_now_thinking()
    return {"item": item}


@router.get("/display/alerts")
async def alerts() -> Dict[str, Any]:
    return await display_service.get_alerts()


@router.get("/display/signals")
async def signals() -> Dict[str, Any]:
    return await display_signals.get_signals_summary()


@router.get("/display/settings")
async def get_display_settings() -> Dict[str, Any]:
    return await display_settings.get_settings()


class DisplaySettingsPatch(BaseModel):
    accent: str | None = None
    time_of_day: str | None = None
    density: str | None = None
    avatar_size: str | None = None
    show_panels: Dict[str, bool] | None = None
    show_thinking: bool | None = None
    speak_aloud: bool | None = None


@router.patch("/display/settings")
async def patch_display_settings(patch: DisplaySettingsPatch) -> Dict[str, Any]:
    updates = patch.model_dump(exclude_none=True)
    return await display_settings.update_settings(updates, updated_by="user")
