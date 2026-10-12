"""Gmail OAuth and status endpoints."""

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse, JSONResponse

router = APIRouter()


@router.get("/gmail/status")
async def gmail_status():
    """Get Gmail connection status."""
    from services.gmail_service import get_status
    return await get_status()


@router.get("/gmail/oauth/url")
async def gmail_oauth_url(request: Request):
    """Get the Google OAuth authorization URL."""
    from services.gmail_service import get_oauth_url, GMAIL_CLIENT_ID

    if not GMAIL_CLIENT_ID:
        return JSONResponse(
            status_code=400,
            content={"error": "GMAIL_CLIENT_ID not configured"},
        )

    url = await get_oauth_url()
    return {"url": url}


@router.get("/gmail/oauth/callback")
async def gmail_oauth_callback(code: str = "", state: str = "", error: str = ""):
    """Handle Google OAuth callback — exchange code for tokens.

    Public (Google redirects the browser here), so the ``state`` minted by
    the authenticated ``/gmail/oauth/url`` is what proves the flow is the
    owner's: missing, unknown, expired or reused answers 400 and stores
    nothing.
    """
    from services.gmail_service import GmailOAuthRefused, discard_oauth_state, exchange_code

    if error or not code:
        discard_oauth_state(state)
        return RedirectResponse(url="/settings?gmail=error")

    try:
        await exchange_code(code, state)
    except GmailOAuthRefused as e:
        print(f"[Gmail] OAuth callback refused: {e.reason}")
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        print(f"[Gmail] OAuth callback failed: {e}")
        return RedirectResponse(url="/settings?gmail=error")

    # Refresh tool registry so Gmail tools become available
    try:
        from services.tool_registry import refresh_registry
        await refresh_registry()
    except Exception:
        pass

    # Watch the inbox now if "watch my inbox" is on (2026-10-11): connecting
    # used to leave the listener off until the next restart.
    from services.heartbeat import listener_gmail
    await listener_gmail.ensure_started()

    return RedirectResponse(url="/settings?gmail=connected")


@router.post("/gmail/disconnect")
async def gmail_disconnect():
    """Revoke tokens and disconnect Gmail."""
    from services.gmail_service import revoke_and_disconnect
    from services.heartbeat import listener_gmail

    await revoke_and_disconnect()
    try:
        await listener_gmail.stop_gmail_listener()
    except Exception as e:  # noqa: BLE001 — the disconnect itself is done
        print(f"[Gmail] couldn't stop the listener: {type(e).__name__}")

    # Refresh tool registry
    try:
        from services.tool_registry import refresh_registry
        await refresh_registry()
    except Exception:
        pass

    return {"success": True}
