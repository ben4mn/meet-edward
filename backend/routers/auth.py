"""Authentication endpoints for single-user password authentication."""

import logging
import time

from fastapi import APIRouter, HTTPException, Request, Response, Cookie
from pydantic import BaseModel
from typing import Optional

from services.database import async_session
from services import auth_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

COOKIE_NAME = "edward_token"
COOKIE_MAX_AGE = 30 * 24 * 60 * 60  # 30 days in seconds

# Rate limiting — sliding window per IP
_RATE_LIMIT_WINDOW = 15 * 60  # 15 minutes
_RATE_LIMIT_MAX_ATTEMPTS = 5
_login_attempts: dict[str, list[float]] = {}


def _get_client_ip(request: Request) -> str:
    """Get client IP, respecting X-Forwarded-For from Cloudflare tunnel."""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Check if IP is within rate limit. Returns (allowed, retry_after_seconds)."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW

    # Prune old entries
    if ip in _login_attempts:
        _login_attempts[ip] = [t for t in _login_attempts[ip] if t > cutoff]

    attempts = _login_attempts.get(ip, [])
    if len(attempts) >= _RATE_LIMIT_MAX_ATTEMPTS:
        retry_after = int(attempts[0] - cutoff) + 1
        return False, retry_after

    return True, 0


def _record_failed_attempt(ip: str) -> None:
    """Record a failed authentication attempt."""
    if ip not in _login_attempts:
        _login_attempts[ip] = []
    _login_attempts[ip].append(time.time())


def _clear_attempts(ip: str) -> None:
    """Clear failed attempts for an IP after successful auth."""
    _login_attempts.pop(ip, None)


class PasswordRequest(BaseModel):
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AuthStatusResponse(BaseModel):
    configured: bool
    authenticated: bool


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(edward_token: Optional[str] = Cookie(default=None)):
    """Check if password is configured and if user is authenticated."""
    async with async_session() as session:
        configured = await auth_service.is_password_configured(session)
        authenticated = False
        if edward_token:
            authenticated = auth_service.verify_token(edward_token)
        return AuthStatusResponse(configured=configured, authenticated=authenticated)


@router.post("/setup")
async def setup_password(request: PasswordRequest, req: Request, response: Response):
    """Set up the initial password. Only works if no password is configured."""
    ip = _get_client_ip(req)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed:
        logger.warning("Rate limit hit on /setup from %s", ip)
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    if len(request.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    async with async_session() as session:
        success = await auth_service.setup_password(session, request.password)
        if not success:
            _record_failed_attempt(ip)
            raise HTTPException(status_code=400, detail="Password already configured")

        _clear_attempts(ip)
        # Auto-login after setup
        token = auth_service.create_access_token()
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=True,  # Works behind Cloudflare tunnel
            samesite="lax"
        )
        return {"message": "Password configured successfully"}


@router.post("/login")
async def login(request: PasswordRequest, req: Request, response: Response):
    """Login with password and set session cookie."""
    ip = _get_client_ip(req)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed:
        logger.warning("Rate limit hit on /login from %s", ip)
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    async with async_session() as session:
        token = await auth_service.authenticate(session, request.password)
        if not token:
            _record_failed_attempt(ip)
            logger.warning("Failed login attempt from %s", ip)
            raise HTTPException(status_code=401, detail="Invalid password")

        _clear_attempts(ip)
        response.set_cookie(
            key=COOKIE_NAME,
            value=token,
            max_age=COOKIE_MAX_AGE,
            httponly=True,
            secure=True,  # Works behind Cloudflare tunnel
            samesite="lax"
        )
        return {"message": "Login successful"}


@router.post("/logout")
async def logout(response: Response):
    """Clear the session cookie."""
    response.delete_cookie(key=COOKIE_NAME)
    return {"message": "Logged out successfully"}


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    req: Request,
    response: Response,
    edward_token: Optional[str] = Cookie(default=None)
):
    """Change the password. Requires current password."""
    # Verify authentication
    if not edward_token or not auth_service.verify_token(edward_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    ip = _get_client_ip(req)
    allowed, retry_after = _check_rate_limit(ip)
    if not allowed:
        logger.warning("Rate limit hit on /change-password from %s", ip)
        response.headers["Retry-After"] = str(retry_after)
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    if len(request.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    async with async_session() as session:
        success = await auth_service.change_password(
            session,
            request.current_password,
            request.new_password
        )
        if not success:
            _record_failed_attempt(ip)
            logger.warning("Failed password change attempt from %s", ip)
            raise HTTPException(status_code=400, detail="Current password is incorrect")

        _clear_attempts(ip)
        return {"message": "Password changed successfully"}
