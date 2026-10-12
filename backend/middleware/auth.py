"""Authentication middleware for protecting API routes.

Every ``/api`` route needs the owner's session cookie except the public
ones below (each of those checks its own token, a signature, or serves
nothing private). That holds before a password is set too: the API used to
be wide open until setup, while the backend and ``next start`` listen on
every interface and the LaunchAgents start them before the owner has chosen
a password, so anyone on the LAN (or behind an early tunnel) could run
turns, flip skills or claim the install. Now an install without a password
answers only what the first-run flow needs — ``/health``, ``/``,
``/api/auth/status`` and ``/api/auth/setup`` (which routers/auth.py accepts
only with the one-time code in backend/.setup_code, from a page on this
Mac) — plus the public routes, and 401s the rest. A valid
session cookie is still honoured (only the signing key can mint one).
"""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from services import auth_service, photo_door_auth
from services.database import async_session

# The household photo door (services/photo_door_auth.py).
PHOTO_DOOR_PREFIX = "/api/photo-door/"
PHOTO_DOOR_PUBLIC_ROUTES = {
    "/api/photo-door/auth/status",
    "/api/photo-door/auth/login",
    "/api/photo-door/auth/logout",
}

# Routes that don't require authentication
PUBLIC_ROUTES = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/webhook/twilio",
    "/api/webhook/twilio/whatsapp",
    "/api/push/vapid-key",
    "/api/push/status",
    "/api/widget",
    "/api/widget/chat",
    "/api/presence/beacon",
    "/api/presence/stream",
    "/api/alexa",
    "/api/gmail/oauth/callback",
    # The watchdog's mail check (no cookie): a verdict and warnings only.
    "/api/health/email",
}

# The 401 an install without a password answers (the first-run flow only
# needs /api/auth/status and /api/auth/setup, both public).
NOT_SET_UP = (
    "Edward has no password yet. Set one on the Mac Edward runs on: "
    "open http://localhost:3000 there."
)

# Route prefixes that are always public
PUBLIC_PREFIXES = [
    "/docs",
    "/redoc",
    "/openapi.json",
]


class AuthMiddleware(BaseHTTPMiddleware):
    """Middleware to check authentication on API routes."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Skip non-API routes
        if not path.startswith("/api/"):
            return await call_next(request)

        # Skip public routes
        if path in PUBLIC_ROUTES:
            return await call_next(request)

        # Skip public prefixes
        for prefix in PUBLIC_PREFIXES:
            if path.startswith(prefix):
                return await call_next(request)

        # The photo door (services/photo_door_auth.py): its cookie opens
        # only /api/photo-door/*; the owner's session opens it too.
        if path.startswith(PHOTO_DOOR_PREFIX):
            return await self._photo_door(request, call_next)

        # Verify authentication token — with or without a password set:
        # before setup nothing but the public routes answers.
        token = request.cookies.get("edward_token")
        if not token or not auth_service.verify_token(token):
            if not await self._password_configured():
                return JSONResponse(status_code=401, content={"detail": NOT_SET_UP})
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required" if not token else "Invalid or expired token"}
            )

        response = await call_next(request)
        # A session in use keeps living: past half its life, re-issue it.
        if auth_service.should_renew_session(token):
            auth_service.set_session_cookie(response, auth_service.create_access_token())
        return response

    @staticmethod
    async def _password_configured() -> bool:
        configured = auth_service.is_password_configured_cached()
        if configured is None:
            try:
                async with async_session() as session:
                    configured = await auth_service.is_password_configured(session)
            except Exception:  # noqa: BLE001 — unknown: the plain 401
                return True
        return bool(configured)

    async def _photo_door(self, request: Request, call_next):
        if request.url.path in PHOTO_DOOR_PUBLIC_ROUTES:
            return await call_next(request)

        # A door that is off has no rooms behind it, for anyone.
        if not photo_door_auth.enabled():
            return JSONResponse(status_code=404, content={"detail": "Not found"})

        door = request.cookies.get(photo_door_auth.COOKIE_NAME)
        if photo_door_auth.verify_token(door):
            response = await call_next(request)
            if photo_door_auth.should_renew(door):
                photo_door_auth.set_cookie(response, photo_door_auth.create_token())
            return response

        owners = request.cookies.get("edward_token")
        if owners and auth_service.verify_token(owners):
            return await call_next(request)

        return JSONResponse(status_code=401, content={"detail": "Authentication required"})
