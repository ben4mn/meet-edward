import os

from fastapi import APIRouter, HTTPException

from models import Settings, SettingsUpdate
from services.settings_service import get_settings, update_settings

router = APIRouter()

# Hardcoded model lists (curated, easy to extend — adding a model is a one-line change)
ANTHROPIC_MODELS = [
    {"id": "claude-sonnet-4-6", "name": "Claude Sonnet 4.6", "provider": "anthropic", "recommended": True},
    {"id": "claude-opus-4-6", "name": "Claude Opus 4.6", "provider": "anthropic"},
    {"id": "claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "provider": "anthropic"},
]

OPENAI_MODELS = [
    {"id": "gpt-5.4", "name": "GPT-5.4", "provider": "openai", "recommended": True},
    {"id": "gpt-5.3-codex", "name": "GPT-5.3 Codex", "provider": "openai"},
]


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
    """Get list of available models, grouped by provider."""
    models = list(ANTHROPIC_MODELS)

    # Show OpenAI models when any form of OpenAI auth is configured
    openai_available = bool(os.getenv("OPENAI_API_KEY"))
    if not openai_available:
        try:
            from services.codex_oauth_service import has_valid_tokens
            openai_available = await has_valid_tokens()
        except (ImportError, Exception):
            pass

    if openai_available:
        models.extend(OPENAI_MODELS)

    return {
        "models": models,
        "openai_available": openai_available,
    }

@router.get("/settings/openai/status")
async def get_openai_status():
    """Check OpenAI authentication status."""
    has_api_key = bool(os.getenv("OPENAI_API_KEY"))
    codex_connected = False
    codex_email = None
    try:
        from services.codex_oauth_service import has_valid_tokens, get_account_email
        codex_connected = await has_valid_tokens()
        if codex_connected:
            codex_email = await get_account_email()
    except (ImportError, Exception):
        pass

    return {
        "has_api_key": has_api_key,
        "codex_connected": codex_connected,
        "codex_email": codex_email,
    }

@router.post("/settings/openai/login")
async def start_openai_login():
    """Start Codex OAuth flow. Returns auth URL to open in browser."""
    try:
        from services.codex_oauth_service import start_auth_flow
        auth_url = await start_auth_flow()
        return {"auth_url": auth_url}
    except ImportError:
        raise HTTPException(status_code=501, detail="Codex OAuth service not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/settings/openai/logout")
async def openai_logout():
    """Disconnect Codex OAuth (clear stored tokens)."""
    try:
        from services.codex_oauth_service import clear_tokens
        await clear_tokens()
        return {"status": "ok"}
    except ImportError:
        raise HTTPException(status_code=501, detail="Codex OAuth service not available")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
