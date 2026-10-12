"""
Alexa integration router for Edward.

Handles:
- POST /api/alexa — Inbound Alexa custom skill webhook (public)
- GET /api/alexa/status — Connection status for frontend panel (authenticated)
- POST /api/alexa/test — Test announcement on Echo device (authenticated)
"""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from typing import Optional

router = APIRouter()


# ADAPT: SECURITY GAP — inbound Alexa requests are not signature-verified; only the skill id is checked.
#   How it works here: this public route trusts any POST whose body carries ALEXA_SKILL_ID, and an Alexa
#   turn counts as the owner speaking; the skill id is not a secret, so anyone who learns it and the URL
#   can run a turn as the owner. The original host relied on the URL staying private.
#   To adapt: leave ALEXA_SKILL_ID unset unless you use the skill (the route answers 503), or ask Claude to
#   add Amazon's request verification (the SignatureCertChainUrl certificate chain, the Signature-256
#   header over the raw body, and a 150-second timestamp window) before process_alexa_request.
@router.post("/alexa")
async def alexa_webhook(request: Request):
    """
    Handle incoming Alexa skill requests.

    Validates the skill ID and processes LaunchRequest, IntentRequest,
    and SessionEndedRequest types.
    """
    from services.alexa_service import (
        is_configured,
        validate_skill_id,
        process_alexa_request,
    )

    if not is_configured():
        return JSONResponse(
            status_code=503,
            content={"error": "Alexa skill not configured"},
        )

    body = await request.json()

    if not validate_skill_id(body):
        return JSONResponse(
            status_code=403,
            content={"error": "Invalid skill ID"},
        )

    response = await process_alexa_request(body)
    return JSONResponse(content=response)


@router.get("/alexa/status")
async def alexa_status():
    """Get Alexa integration status for the settings panel."""
    from services.home_assistant_service import (
        is_configured as ha_configured,
        health_check,
        get_echo_devices,
        HA_URL,
    )
    from services.alexa_service import (
        is_configured as skill_configured,
        ALEXA_SKILL_ID,
    )

    # Check HA health
    ha_healthy = False
    devices = []
    if ha_configured():
        ha_healthy = await health_check()
        if ha_healthy:
            devices = await get_echo_devices()

    return {
        "outbound": {
            "configured": ha_configured(),
            "healthy": ha_healthy,
            "ha_url": HA_URL if ha_configured() else "",
            "device_count": len(devices),
        },
        "inbound": {
            "configured": skill_configured(),
            "skill_id": (ALEXA_SKILL_ID or "")[:20] + "..." if ALEXA_SKILL_ID else "",
        },
        "devices": devices,
    }


class TestAnnounceRequest(BaseModel):
    message: str
    device: Optional[str] = None


@router.post("/alexa/test")
async def test_announce(body: TestAnnounceRequest):
    """Send a test announcement to an Echo device."""
    from services.home_assistant_service import announce, is_configured

    if not is_configured():
        return {"success": False, "error": "Home Assistant not configured"}

    result = await announce(body.message, body.device)
    return result
