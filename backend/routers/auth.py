"""Authentication endpoints for single-user password authentication."""

import asyncio
import hashlib
import logging
import time
import weakref
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, Response, Cookie
from pydantic import BaseModel, ValidationError
from typing import Optional

from services.database import async_session
from services import auth_service, client_ip, owner, setup_code

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/auth", tags=["auth"])

# Shared with the middleware's renewal (services/auth_service.py).
COOKIE_NAME = auth_service.COOKIE_NAME
COOKIE_MAX_AGE = auth_service.COOKIE_MAX_AGE

# Rate limiting — a sliding window per key. Three stores, each counted on
# its own:
# * ``_login_attempts``: the owner's login and the photo door
#   (routers/photo_door.py), keyed by client (services/client_ip.py), with a
#   ceiling across every client and a cap on how many clients are tracked;
# * ``_change_password_attempts``: /change-password, keyed by the session
#   that asks (it is signed in already), with no global ceiling — a
#   guessing attack on the login never stops the owner rotating the password;
# * ``_setup_code_failures``: wrong setup codes on /setup, keyed by client.
#
# An attempt is *reserved* before the request's first await (``_reserve``):
# a provisional failure under its key, counted against every limit while
# the password is checked. A success clears the key; a failure keeps the
# reservation as its record. Checking first and recording after the
# database and bcrypt let a parallel burst pass every limit at once.
_RATE_LIMIT_WINDOW = 15 * 60  # 15 minutes
_RATE_LIMIT_MAX_ATTEMPTS = 5
# Failures from every client together in one window. A LAN device that goes
# through Next's proxy controls the headers a client is keyed by, so the
# per-client limit alone can be rotated around; this bounds the guessing
# rate whatever the key. Sessions already signed in are not affected.
_RATE_LIMIT_GLOBAL_MAX_FAILURES = 100
# Tracked clients; past this the least recently failed are forgotten.
_RATE_LIMIT_MAX_CLIENTS = 10_000
# Wrong setup codes per client in one window (the code has ~59 bits).
_SETUP_CODE_MAX_FAILURES = 10
_login_attempts: dict[str, list[float]] = {}
_change_password_attempts: dict[str, list[float]] = {}
_setup_code_failures: dict[str, list[float]] = {}


def _get_client_ip(request: Request) -> str:
    """The client a request stands for (``client_ip.client_ip``): the peer,
    or behind a loopback proxy CF-Connecting-IP, else the right-most
    X-Forwarded-For entry that is not loopback — never the client-written
    left-most one."""
    return client_ip.client_ip(request)


def _limit(store: dict, key: str, max_per_key: int, global_max: Optional[int]) -> tuple[bool, int]:
    """(allowed, retry_after_seconds) for one more attempt under ``key``."""
    now = time.time()
    cutoff = now - _RATE_LIMIT_WINDOW

    if key in store:
        store[key] = [t for t in store[key] if t > cutoff]

    attempts = store.get(key, [])
    if len(attempts) >= max_per_key:
        retry_after = int(min(attempts) - cutoff) + 1
        return False, retry_after

    if global_max is not None:
        everyone = [t for times in store.values() for t in times if t > cutoff]
        if len(everyone) >= global_max:
            everyone.sort()
            # Open again once enough of the oldest failures age out.
            oldest_to_expire = everyone[len(everyone) - global_max]
            logger.warning("Login ceiling reached: %d failed attempts from every client in %d min",
                           len(everyone), _RATE_LIMIT_WINDOW // 60)
            return False, int(oldest_to_expire - cutoff) + 1

    return True, 0


def _evict(store: dict, now: float) -> None:
    """Keep ``store`` under ``_RATE_LIMIT_MAX_CLIENTS``: drop the keys whose
    failures have all expired, then the least recently failed."""
    cutoff = now - _RATE_LIMIT_WINDOW
    for key in [k for k, times in store.items() if not any(t > cutoff for t in times)]:
        store.pop(key, None)
    while store and len(store) >= _RATE_LIMIT_MAX_CLIENTS:
        store.pop(next(iter(store)))


def _record(store: dict, key: str) -> float:
    """Record one attempt under ``key`` (most recent key last); returns its stamp."""
    now = time.time()
    attempts = store.pop(key, [])
    if len(store) >= _RATE_LIMIT_MAX_CLIENTS:
        _evict(store, now)
    attempts.append(now)
    store[key] = attempts
    return now


def _reserve(store: dict, key: str, max_per_key: int, global_max: Optional[int]) -> tuple[Optional[float], int]:
    """Check and record in one step, with no await between: (stamp, 0) when
    the attempt may go ahead (it already counts), (None, retry_after) when
    it may not."""
    allowed, retry_after = _limit(store, key, max_per_key, global_max)
    if not allowed:
        return None, retry_after
    return _record(store, key), 0


def _release(store: dict, key: str, stamp: Optional[float]) -> None:
    """Take back one reservation that turned out not to be a guess."""
    times = store.get(key)
    if stamp is None or not times:
        return
    try:
        times.remove(stamp)
    except ValueError:
        return
    if not times:
        store.pop(key, None)


# The login store (the owner's login and the photo door).

def _check_rate_limit(ip: str) -> tuple[bool, int]:
    """Check if IP is within rate limit. Returns (allowed, retry_after_seconds)."""
    return _limit(_login_attempts, ip, _RATE_LIMIT_MAX_ATTEMPTS, _RATE_LIMIT_GLOBAL_MAX_FAILURES)


def _reserve_attempt(ip: str) -> tuple[bool, int]:
    """Reserve one login attempt for ``ip`` before the password is checked:
    (allowed, retry_after_seconds). Clear it with ``_clear_attempts`` on
    success; on failure it stays as the recorded failure."""
    stamp, retry_after = _reserve(_login_attempts, ip, _RATE_LIMIT_MAX_ATTEMPTS, _RATE_LIMIT_GLOBAL_MAX_FAILURES)
    return stamp is not None, retry_after


def _record_failed_attempt(ip: str) -> None:
    """Record a failed authentication attempt (most recent client last)."""
    _record(_login_attempts, ip)


def _clear_attempts(ip: str) -> None:
    """Clear failed attempts for an IP after successful auth."""
    _login_attempts.pop(ip, None)


def _session_key(token: str) -> str:
    """A /change-password limiter key for one signed-in session."""
    return "session:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:32]


# Where the first password may be set from (POST /setup). Before setup the
# API answers nothing else (middleware/auth.py), and whoever sets the
# password owns the install. Request headers cannot prove the client is on
# this Mac (Next passes the client's own Host through as X-Forwarded-Host),
# so the proof is the one-time setup code in backend/.setup_code
# (services/setup_code.py), which only someone at the Mac can read. The
# local-client and Origin checks stay as a second line.
SETUP_LOCAL_ONLY = (
    "The first password can only be set on the Mac Edward runs on. "
    "Open http://localhost:3000 there."
)
SETUP_CODE_WRONG = (
    "That setup code doesn't match. On the Mac Edward runs on: cat "
    + setup_code.SETUP_CODE_DISPLAY
)
# A form post or a typeless fetch body is a "simple" cross-site request a
# browser sends without asking first; JSON forces the CORS preflight.
SETUP_JSON_ONLY = "Send the first password as JSON (Content-Type: application/json)."
SETUP_ALREADY_DONE = "A password is already set. Sign in with it."

_LOOPBACK_ORIGIN_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
# One first-password write at a time (a double submit with the right code).
# A lock per event loop: an asyncio.Lock binds to the loop it first waits in.
_setup_locks: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Lock]" = weakref.WeakKeyDictionary()


def _setup_lock() -> asyncio.Lock:
    loop = asyncio.get_running_loop()
    lock = _setup_locks.get(loop)
    if lock is None:
        lock = _setup_locks[loop] = asyncio.Lock()
    return lock


def _is_loopback_origin(origin: str) -> bool:
    """``http(s)://localhost|127.0.0.1|[::1]`` with any port, nothing else."""
    try:
        parts = urlsplit((origin or "").strip())
        parts.port  # noqa: B018 — raises ValueError on a malformed port
    except ValueError:
        return False
    if parts.scheme not in ("http", "https"):
        return False
    if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username or parts.password:
        return False
    return (parts.hostname or "") in _LOOPBACK_ORIGIN_HOSTS


def _media_type(request: Request) -> str:
    return (request.headers.get("content-type") or "").split(";", 1)[0].strip().lower()


class PasswordRequest(BaseModel):
    password: str


class SetupRequest(BaseModel):
    password: str
    setup_code: Optional[str] = None


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class AuthStatusResponse(BaseModel):
    configured: bool
    authenticated: bool
    # What Edward calls the owner (EDWARD_OWNER_NAME; services/owner.py).
    # The login page greets them by it, so it is served before sign-in.
    owner_name: str = ""
    # No password yet: the setup form asks for the code in backend/.setup_code.
    setup_code_required: bool = False


async def _password_configured() -> bool:
    cached = auth_service.is_password_configured_cached()
    if cached is not None:
        return cached
    async with async_session() as session:
        return await auth_service.is_password_configured(session)


@router.get("/status", response_model=AuthStatusResponse)
async def get_auth_status(edward_token: Optional[str] = Cookie(default=None)):
    """Check if password is configured and if user is authenticated."""
    async with async_session() as session:
        configured = await auth_service.is_password_configured(session)
        authenticated = False
        if edward_token:
            authenticated = auth_service.verify_token(edward_token)
    if not configured:
        # The page is about to ask for the code: make sure it exists.
        try:
            setup_code.ensure()
        except Exception as exc:  # noqa: BLE001 — /setup makes it again
            logger.warning("could not make the first-time setup code: %s", exc)
    return AuthStatusResponse(
        configured=configured,
        authenticated=authenticated,
        owner_name=owner.owner_name() if owner.owner_name_is_set() else "",
        setup_code_required=not configured,
    )


@router.post("/setup")
async def setup_password(req: Request, response: Response):
    """Set the first password. Only while none is configured, only as JSON,
    only from a page on this Mac (``Origin`` loopback when present;
    ``client_ip.is_local_client``), and only with the one-time setup code
    from ``backend/.setup_code``. Wrong codes are limited per client; setup
    never counts against the login limiter."""
    if _media_type(req) != "application/json":
        raise HTTPException(status_code=415, detail=SETUP_JSON_ONLY)

    origin = req.headers.get("origin")
    if origin is not None and not _is_loopback_origin(origin):
        logger.warning("Refused /setup from a page at %r (not this Mac's localhost)", origin[:120])
        raise HTTPException(status_code=403, detail=SETUP_LOCAL_ONLY)

    if not client_ip.is_local_client(req):
        logger.warning("Refused /setup from a client that is not on this Mac (%s)", _get_client_ip(req))
        raise HTTPException(status_code=403, detail=SETUP_LOCAL_ONLY)

    ip = _get_client_ip(req)
    stamp, retry_after = _reserve(_setup_code_failures, ip, _SETUP_CODE_MAX_FAILURES, None)
    if stamp is None:
        logger.warning("Too many wrong setup codes from %s", ip)
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})

    try:
        body = SetupRequest.model_validate(await req.json())
    except (ValueError, ValidationError):
        _release(_setup_code_failures, ip, stamp)
        raise HTTPException(status_code=422, detail="Send a password and the setup code.")

    if await _password_configured():
        _release(_setup_code_failures, ip, stamp)
        raise HTTPException(status_code=400, detail=SETUP_ALREADY_DONE)

    if setup_code.ensure() is None:
        _release(_setup_code_failures, ip, stamp)
        raise HTTPException(status_code=503, detail=(
            "I couldn't write the setup code to " + setup_code.SETUP_CODE_DISPLAY
            + ". Check the backend log on the Mac."))

    if not (body.setup_code or "").strip():
        _release(_setup_code_failures, ip, stamp)
        raise HTTPException(status_code=403, detail=SETUP_CODE_WRONG)
    if not setup_code.matches(body.setup_code):
        # The reservation stays: it is this wrong code.
        logger.warning("Wrong first-time setup code from %s", ip)
        raise HTTPException(status_code=403, detail=SETUP_CODE_WRONG)
    _setup_code_failures.pop(ip, None)

    if len(body.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")

    async with _setup_lock():
        async with async_session() as session:
            success = await auth_service.setup_password(session, body.password)
    if not success:
        raise HTTPException(status_code=400, detail=SETUP_ALREADY_DONE)

    setup_code.discard()
    # Auto-login after setup
    token = auth_service.create_access_token()
    # Secure unless EDWARD_COOKIE_SECURE=off (plain-HTTP LAN installs).
    auth_service.set_session_cookie(response, token)
    return {"message": "Password configured successfully"}


@router.post("/login")
async def login(request: PasswordRequest, req: Request, response: Response):
    """Login with password and set session cookie."""
    ip = _get_client_ip(req)
    # Reserved before the first await: a parallel burst counts as it runs.
    allowed, retry_after = _reserve_attempt(ip)
    if not allowed:
        logger.warning("Rate limit hit on /login from %s", ip)
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})

    async with async_session() as session:
        token = await auth_service.authenticate(session, request.password)
    if not token:
        # The reservation stays: it is this failure.
        logger.warning("Failed login attempt from %s", ip)
        raise HTTPException(status_code=401, detail="Invalid password")

    _clear_attempts(ip)
    # Secure unless EDWARD_COOKIE_SECURE=off (plain-HTTP LAN installs).
    auth_service.set_session_cookie(response, token)
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
    """Change the password. Requires current password. Limited per signed-in
    session only: the login's ceiling across every client never blocks it."""
    # Verify authentication
    if not edward_token or not auth_service.verify_token(edward_token):
        raise HTTPException(status_code=401, detail="Not authenticated")

    if len(request.new_password) < 8:
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters")

    key = _session_key(edward_token)
    stamp, retry_after = _reserve(_change_password_attempts, key, _RATE_LIMIT_MAX_ATTEMPTS, None)
    if stamp is None:
        logger.warning("Rate limit hit on /change-password from %s", _get_client_ip(req))
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.",
                            headers={"Retry-After": str(retry_after)})

    async with async_session() as session:
        success = await auth_service.change_password(
            session,
            request.current_password,
            request.new_password
        )
    if not success:
        # The reservation stays: it is this failure.
        logger.warning("Failed password change attempt from %s", _get_client_ip(req))
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    _change_password_attempts.pop(key, None)
    return {"message": "Password changed successfully"}
