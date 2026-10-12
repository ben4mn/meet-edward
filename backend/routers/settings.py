import os

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
    """Update Edward's settings.

    A model id is checked before it is saved, against the provider that is
    on: on the Claude API (the default) a current ``claude-*`` id
    (``claude_routing.known_claude_model``); on the Codex fallback the Codex
    catalog. A stale or mistyped id used to land in the database and fail
    every turn with HTTP 400 until someone read the log (gpt-5.4, 2026-09-05).
    """
    if settings_update.model is not None:
        problem = validate_model_for_provider(settings_update.model)
        if problem:
            raise HTTPException(status_code=400, detail=problem)
    try:
        settings = await update_settings(settings_update)
        return settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


def validate_model_for_provider(model: str) -> "str | None":
    """None when ``model`` may be saved under the provider that is on,
    else a message naming what may be."""
    from services.claude_routing import ANTHROPIC, known_claude_model, llm_provider

    if llm_provider() == ANTHROPIC:
        if known_claude_model(model):
            return None
        offered = ", ".join(e["id"] for e in claude_picker_entries())
        return f"'{model or ''}' is not a current Claude model. Pick one of: {offered}."
    from services.model_catalog import validate_model_choice

    return validate_model_choice(model)


def _claude_display_name(model_id: str) -> str:
    """``claude-sonnet-5-5`` -> ``Claude Sonnet 5.5``."""
    parts = model_id.split("-")
    if len(parts) < 2 or parts[0] != "claude":
        return model_id
    family = parts[1].capitalize()
    version = ".".join(p for p in parts[2:] if p.isdigit())
    return f"Claude {family} {version}".strip()


def claude_picker_entries() -> list:
    """The Claude models the picker may offer: every route's model (with
    its env overrides), the chat route's first and marked recommended, then
    the other current ids. Each says which routes run on it."""
    from services.claude_routing import KNOWN_CLAUDE_MODELS, PURPOSES, claude_route

    chat = claude_route("chat")
    used_by: dict = {}
    for purpose in PURPOSES:
        used_by.setdefault(claude_route(purpose).model, []).append(purpose)
    order = [chat.model] + [m for m in used_by if m != chat.model] + sorted(
        m for m in KNOWN_CLAUDE_MODELS if m not in used_by)
    entries = []
    for model_id in order:
        routes = used_by.get(model_id, [])
        name = _claude_display_name(model_id)
        entries.append({
            "id": model_id,
            "name": f"{name} (Recommended)" if model_id == chat.model else name,
            "description": ("Runs: " + ", ".join(routes)) if routes else "Not on a route",
            "supports_images": True,
            "default_effort": claude_route(routes[0]).effort if routes else None,
            "context_window": None,
        })
    return entries


@router.get("/settings/models")
async def get_available_models():
    """Models the picker may offer, for the provider that is on.

    On the Claude API (``EDWARD_LLM_PROVIDER`` unset or ``anthropic``) the
    Claude routes (``services/claude_routing``): the chat route's model is
    the recommended one. On the Codex fallback (``codex``) the Codex
    catalog, live from ``~/.codex/models_cache.json``
    (``services/model_catalog``). The provider that is off is listed as
    unavailable, so the picker never offers an id a turn would rewrite.
    """
    from services.claude_routing import ANTHROPIC, claude_route, llm_provider
    from services.llm_utils import background_model, voice_model

    if llm_provider() == ANTHROPIC:
        entries = claude_picker_entries()
        return {
            "provider": ANTHROPIC,
            "providers": {
                # The provider that is on; a missing ANTHROPIC_API_KEY shows
                # up as a failed turn and in the health report, not here.
                "anthropic": {"available": True, "models": entries,
                              "api_key_set": bool((os.getenv("ANTHROPIC_API_KEY") or "").strip())},
                "openai": {"available": False, "models": []},
            },
            # Backwards-compatible flat list.
            "models": [{"id": m["id"], "name": m["name"]} for m in entries],
            "catalog": {
                "live": True,
                "path": None,
                "recommended": claude_route("chat").model,
            },
            "background_model": background_model(),
            "voice_model": voice_model(),
            # What the mind, the pulse and chat actually run on (2026-09-26).
            **(await _routing()),
        }

    from services.codex_auth_service import get_codex_auth_manager
    from services import model_catalog

    codex_manager = get_codex_auth_manager()
    entries = model_catalog.picker_entries()
    return {
        "provider": "codex",
        "providers": {
            "openai": {"available": codex_manager.is_available(), "models": entries},
            "anthropic": {"available": False, "models": []},
        },
        # Backwards-compatible flat list.
        "models": [{"id": m["id"], "name": m["name"]} for m in entries],
        "catalog": {
            "live": model_catalog.catalog_is_live(),
            "path": model_catalog.CATALOG_PATH,
            "recommended": model_catalog.recommended_model(),
        },
        "background_model": background_model(),
        "voice_model": voice_model(),
        # What the mind, the pulse and chat actually run on (2026-09-26).
        **(await _routing()),
    }


async def _routing() -> dict:
    """``mind_model`` / ``mind_effort`` / ``mind_efforts`` / ``pulse_model`` /
    ``pulse_effort`` / ``chat_model`` / ``chat_effort``, resolved as the
    turns resolve them; empty when they cannot be (the picker still works)."""
    try:
        from services.mind_service import routing_summary
        from services import model_catalog

        try:
            settings_model = (await get_settings()).model
        except Exception:  # noqa: BLE001 — no settings row: the chat default
            from services.claude_routing import anthropic_enabled, claude_route

            settings_model = (claude_route("chat").model if anthropic_enabled()
                              else model_catalog.recommended_model())
        return routing_summary(settings_model)
    except Exception as exc:  # noqa: BLE001
        print(f"[settings] model routing unavailable: {exc}")
        return {}


@router.get("/settings/codex-usage")
async def codex_usage():
    """The usage card's numbers: on the Codex fallback the account's quota
    meter and the rate gate's pace decision, with the surplus queue's state
    and the week's top spenders (2026-09-24); on the Claude API the day's
    spend block (``codex_usage_service.get_usage_status``)."""
    from services.codex_usage_service import get_usage_status
    return await get_usage_status(with_surplus=True)
