"""
Codex OAuth service for ChatGPT subscription-based GPT-5.4 access.

PKCE flow with local callback server on port 1455. Tokens stored in database.
Uses chatgpt.com/backend-api/codex/responses endpoint (not the standard OpenAI API).

References:
- Cline OpenAI Codex OAuth (merged PR #8664)
- opencode-openai-codex-auth (7-step fetch flow)
- OpenAI Codex Auth Docs (developers.openai.com/codex/auth/)
"""

import asyncio
import base64
import hashlib
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx
from sqlalchemy import select

from services.database import async_session, CodexOAuthTokenModel

# OAuth constants (matches Cline, opencode-auth, and official Codex CLI)
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
AUTH_URL = "https://auth.openai.com/oauth/authorize"
TOKEN_URL = "https://auth.openai.com/oauth/token"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPES = "openid profile email offline_access"
CALLBACK_PORT = 1455
CODEX_API_URL = "https://chatgpt.com/backend-api/codex/responses"

# In-memory state for pending OAuth flow (only one flow at a time)
_pending_auth: dict = {}


def _generate_pkce() -> tuple[str, str]:
    """Generate PKCE verifier and S256 challenge."""
    verifier = secrets.token_urlsafe(32)[:43]
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()
    return verifier, challenge


def _decode_jwt_claims(token: str) -> dict:
    """Decode JWT payload without signature verification (we just need claims)."""
    parts = token.split(".")
    if len(parts) != 3:
        return {}
    payload = parts[1]
    # Add padding for base64
    payload += "=" * (4 - len(payload) % 4)
    try:
        return json.loads(base64.urlsafe_b64decode(payload))
    except Exception:
        return {}


async def start_auth_flow() -> str:
    """Start the OAuth PKCE flow.

    Returns the authorization URL to open in the user's browser.
    Starts a temporary callback server on port 1455.
    """
    from urllib.parse import urlencode

    verifier, challenge = _generate_pkce()
    state = secrets.token_urlsafe(16)

    _pending_auth.clear()
    _pending_auth["verifier"] = verifier
    _pending_auth["state"] = state

    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "login",
    }

    auth_url = f"{AUTH_URL}?{urlencode(params)}"

    # Start callback server in background
    asyncio.create_task(_run_callback_server())

    return auth_url


async def _run_callback_server():
    """Run a temporary HTTP server to receive the OAuth callback."""
    from aiohttp import web

    app = web.Application()
    app.router.add_get("/auth/callback", _handle_callback)

    runner = web.AppRunner(app)
    await runner.setup()

    try:
        site = web.TCPSite(runner, "localhost", CALLBACK_PORT)
        await site.start()
        print(f"[CODEX OAUTH] Callback server listening on port {CALLBACK_PORT}")
    except OSError as e:
        print(f"[CODEX OAUTH] Failed to start callback server on port {CALLBACK_PORT}: {e}")
        await runner.cleanup()
        _pending_auth["completed"] = True
        _pending_auth["error"] = f"Port {CALLBACK_PORT} is in use"
        return

    try:
        # Wait up to 5 minutes for the callback
        for _ in range(300):
            await asyncio.sleep(1)
            if "completed" in _pending_auth:
                break
    finally:
        await runner.cleanup()
        print("[CODEX OAUTH] Callback server stopped")


async def _handle_callback(request):
    """Handle the OAuth callback from OpenAI auth server."""
    from aiohttp import web

    code = request.query.get("code")
    state = request.query.get("state")
    error = request.query.get("error")

    if error:
        _pending_auth["completed"] = True
        _pending_auth["error"] = error
        return web.Response(
            text="<html><body><h2>Authentication failed</h2>"
                 f"<p>Error: {error}</p>"
                 "<p>You can close this window.</p></body></html>",
            content_type="text/html",
        )

    if state != _pending_auth.get("state"):
        _pending_auth["completed"] = True
        _pending_auth["error"] = "state_mismatch"
        return web.Response(
            text="<html><body><h2>State mismatch</h2>"
                 "<p>Please try again.</p></body></html>",
            content_type="text/html",
            status=400,
        )

    try:
        await _exchange_code(code)
        _pending_auth["completed"] = True
        return web.Response(
            text="<html><body><h2>Connected to OpenAI</h2>"
                 "<p>You can close this window and return to Edward.</p></body></html>",
            content_type="text/html",
        )
    except Exception as e:
        _pending_auth["completed"] = True
        _pending_auth["error"] = str(e)
        return web.Response(
            text=f"<html><body><h2>Error</h2><p>{e}</p></body></html>",
            content_type="text/html",
            status=500,
        )


async def _exchange_code(code: str):
    """Exchange authorization code for access and refresh tokens."""
    verifier = _pending_auth.get("verifier")
    if not verifier:
        raise ValueError("No PKCE verifier found — auth flow not started")

    async with httpx.AsyncClient(timeout=30.0) as client:
        # CRITICAL: Do NOT include 'state' in the token exchange body.
        # OpenAI rejects it. State is only validated in the callback URL.
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "authorization_code",
                "client_id": CLIENT_ID,
                "code": code,
                "redirect_uri": REDIRECT_URI,
                "code_verifier": verifier,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            raise ValueError(f"Token exchange failed ({response.status_code}): {response.text[:300]}")

        data = response.json()

    access_token = data["access_token"]
    refresh_token = data["refresh_token"]
    expires_in = data.get("expires_in", 3600)

    # Decode JWT to extract account_id and email
    claims = _decode_jwt_claims(access_token)
    auth_claim = claims.get("https://api.openai.com/auth", {})
    account_id = auth_claim.get("chatgpt_account_id", "")
    email = claims.get("email", "")

    if not account_id:
        raise ValueError("Could not extract ChatGPT account ID from access token JWT")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    await _store_tokens(access_token, refresh_token, account_id, email, expires_at)
    print(f"[CODEX OAUTH] Tokens stored (email={email}, expires_in={expires_in}s)")


async def _store_tokens(
    access_token: str,
    refresh_token: str,
    account_id: str,
    email: str,
    expires_at: datetime,
):
    """Store or update OAuth tokens in the database."""
    async with async_session() as session:
        record = await session.get(CodexOAuthTokenModel, "default")
        if record:
            record.access_token = access_token
            record.refresh_token = refresh_token
            record.account_id = account_id
            record.email = email
            record.expires_at = expires_at
        else:
            record = CodexOAuthTokenModel(
                id="default",
                access_token=access_token,
                refresh_token=refresh_token,
                account_id=account_id,
                email=email,
                expires_at=expires_at,
            )
            session.add(record)
        await session.commit()


async def _load_tokens() -> Optional[CodexOAuthTokenModel]:
    """Load stored tokens from the database."""
    async with async_session() as session:
        return await session.get(CodexOAuthTokenModel, "default")


async def has_valid_tokens() -> bool:
    """Check if Codex OAuth tokens exist (may need refresh, but are present)."""
    record = await _load_tokens()
    return record is not None


async def get_account_email() -> Optional[str]:
    """Get the email associated with the stored Codex OAuth tokens."""
    record = await _load_tokens()
    return record.email if record else None


async def get_access_token() -> Optional[str]:
    """Get a valid access token, auto-refreshing if within 5 minutes of expiry.

    Returns None if no tokens are stored or refresh fails permanently.
    """
    record = await _load_tokens()
    if not record:
        return None

    # Check if token needs refresh (5-minute buffer before expiry)
    now = datetime.now(timezone.utc)
    if record.expires_at and (record.expires_at - timedelta(minutes=5)) <= now:
        try:
            await _refresh_tokens(record.refresh_token)
            record = await _load_tokens()
        except Exception as e:
            print(f"[CODEX OAUTH] Token refresh failed: {e}")
            await clear_tokens()
            return None

    return record.access_token if record else None


async def get_account_id() -> Optional[str]:
    """Get the ChatGPT account ID for the API header."""
    record = await _load_tokens()
    return record.account_id if record else None


async def _refresh_tokens(refresh_token: str):
    """Refresh the access token using the refresh token."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "client_id": CLIENT_ID,
                "refresh_token": refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        if response.status_code != 200:
            error_data = {}
            try:
                error_data = response.json()
            except Exception:
                pass
            error_code = error_data.get("error", "unknown")
            # These errors mean the refresh token is permanently invalid
            if error_code in ("refresh_token_expired", "refresh_token_reused", "refresh_token_invalidated"):
                raise ValueError(f"Refresh token permanently invalid: {error_code}")
            raise ValueError(f"Token refresh failed ({response.status_code}): {response.text[:300]}")

        data = response.json()

    access_token = data["access_token"]
    new_refresh_token = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 3600)

    claims = _decode_jwt_claims(access_token)
    auth_claim = claims.get("https://api.openai.com/auth", {})
    account_id = auth_claim.get("chatgpt_account_id", "")
    email = claims.get("email", "")

    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    await _store_tokens(access_token, new_refresh_token, account_id or "", email or "", expires_at)
    print(f"[CODEX OAUTH] Tokens refreshed (expires_in={expires_in}s)")


async def clear_tokens():
    """Clear stored OAuth tokens (logout / force re-auth)."""
    async with async_session() as session:
        record = await session.get(CodexOAuthTokenModel, "default")
        if record:
            await session.delete(record)
            await session.commit()
    print("[CODEX OAUTH] Tokens cleared")
