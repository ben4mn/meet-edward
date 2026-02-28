from fastapi import APIRouter, HTTPException

from models import Settings, SettingsUpdate
from services.settings_service import get_settings, update_settings

router = APIRouter()

@router.get("/settings", response_model=Settings)
async def read_settings():
    """Get the current Edward settings."""
    try:
        settings = await get_settings()
        return settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/settings", response_model=Settings)
async def save_settings(settings_update: SettingsUpdate):
    """Update Edward's settings."""
    try:
        settings = await update_settings(settings_update)
        return settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/settings/models")
async def get_available_models():
    """Get list of available Claude models."""
    return {
        "models": [
            {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6 (Recommended)"},
            {"id": "claude-opus-4-6", "name": "Claude Opus 4.6"},
            {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5"},
            {"id": "claude-sonnet-4-5-20250929", "name": "Claude Sonnet 4.5 (Legacy)"},
        ]
    }
