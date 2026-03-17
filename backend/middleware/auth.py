"""Authentication middleware for protecting API routes."""

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from services import auth_service
from services.database import async_session

# Routes that don't require authentication
PUBLIC_ROUTES = {
    "/api/auth/status",
    "/api/auth/login",
    "/api/auth/setup",
    "/api/webhook/twilio",
    "/api/webhook/twilio/whatsapp",
    "/api/webhook/whatsapp",
    "/api/push/vapid-key",
    "/api/push/status",
    "/api/widget",
    "/api/widget/chat",
}

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

        # Check if password is configured (use cache to skip DB session)
        configured = auth_service.is_password_configured_cached()
        if configured is None:
            async with async_session() as session:
                configured = await auth_service.is_password_configured(session)

        # If no password configured, allow access (first-time setup flow)
        if not configured:
            return await call_next(request)

        # Verify authentication token
        token = request.cookies.get("edward_token")
        if not token:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required"}
            )

        if not auth_service.verify_token(token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Invalid or expired token"}
            )

        return await call_next(request)
