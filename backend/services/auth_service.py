"""Authentication service for single-user password authentication.

bcrypt (cost 12, about a quarter of a second) runs off the event loop
(``asyncio.to_thread``): on the loop, a burst of guesses froze every turn,
the scheduler and every stream for as long as it lasted."""

import asyncio
import logging
import os
import secrets
import stat
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import bcrypt
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.database import SettingsModel

logger = logging.getLogger(__name__)

# JWT configuration
JWT_ALGORITHM = "HS256"
JWT_EXPIRATION_DAYS = 30

_DEFAULT_DEV_KEY = "dev-secret-key-change-in-production"
_JWT_SECRET_FILE = Path(__file__).resolve().parent.parent / ".jwt_secret"


def _resolve_jwt_secret() -> str:
    """Resolve the JWT secret key with auto-generation fallback.

    Priority:
    1. JWT_SECRET_KEY env var (if set to a real value)
    2. Persisted secret from .jwt_secret file
    3. Auto-generate, persist, and return a new secret
    """
    env_key = os.getenv("JWT_SECRET_KEY", "")

    # Explicit env var that isn't the old default — use it directly
    if env_key and env_key != _DEFAULT_DEV_KEY:
        return env_key

    if env_key == _DEFAULT_DEV_KEY:
        logger.warning(
            "JWT_SECRET_KEY is set to the default dev key — ignoring it and "
            "auto-generating a secure secret"
        )

    # Try reading persisted secret
    if _JWT_SECRET_FILE.exists():
        secret = _JWT_SECRET_FILE.read_text().strip()
        if secret:
            logger.info("Loaded JWT secret from %s", _JWT_SECRET_FILE)
            return secret

    # Auto-generate and persist
    secret = secrets.token_urlsafe(48)
    _JWT_SECRET_FILE.write_text(secret)
    _JWT_SECRET_FILE.chmod(stat.S_IRUSR | stat.S_IWUSR)  # 600
    logger.info("Generated new JWT secret and saved to %s", _JWT_SECRET_FILE)
    return secret


JWT_SECRET_KEY = _resolve_jwt_secret()

# Whoever holds this key can mint the owner's session. Edward's shell and
# editor cannot read backend/.jwt_secret or .env, and his shell cannot reach
# the backend's or the frontend's port (services/computer/rails.py) — those
# are the bar: code run in the shell never shows the key to the model, so
# redaction alone could not stop it. The key is still one of the secrets the
# egress scan blocks and tool output redacts, as defence in depth;
# secret_scan also reads the file itself, for processes that never import
# this module.
try:
    from services import secret_scan as _secret_scan

    _secret_scan.register_known_secret("JWT_SECRET_KEY", JWT_SECRET_KEY)
except Exception as _exc:  # noqa: BLE001 — a scan that can't learn the key must not stop auth
    logger.warning("could not register the session key with the secret scan: %s", _exc)


def hash_password(password: str) -> str:
    """Hash a password using bcrypt."""
    password_bytes = password.encode("utf-8")
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify a password against a hash."""
    try:
        return bcrypt.checkpw(
            plain_password.encode("utf-8"),
            hashed_password.encode("utf-8")
        )
    except Exception:
        return False


def create_access_token(expires_delta: Optional[timedelta] = None) -> str:
    """Create a JWT access token."""
    if expires_delta:
        expire = datetime.now(timezone.utc) + expires_delta
    else:
        expire = datetime.now(timezone.utc) + timedelta(days=JWT_EXPIRATION_DAYS)

    to_encode = {"exp": expire, "sub": "user"}
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)
    return encoded_jwt


def verify_token(token: str) -> bool:
    """Verify a JWT token is valid."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
        return payload.get("sub") == "user"
    except JWTError:
        return False


# The session cookie, shared by the login/setup routes and the middleware's
# renewal so the three never drift apart.
COOKIE_NAME = "edward_token"
COOKIE_MAX_AGE = JWT_EXPIRATION_DAYS * 24 * 60 * 60
# Re-issue the cookie once a valid token has less than half its life left.
# Sessions used to last exactly 30 days from login with nothing renewing
# them, so every device hit a cliff (2026-09-20: the /screen tab went from
# 200s to 85,000 401s a day; the phone was next). A device used weekly
# now never expires; one left in a drawer still does.
SESSION_RENEW_BELOW_SECONDS = COOKIE_MAX_AGE / 2


def token_seconds_remaining(token: str) -> Optional[float]:
    """Seconds until a valid token expires; ``None`` for an invalid or expired one."""
    try:
        payload = jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
    except JWTError:
        return None
    if payload.get("sub") != "user" or "exp" not in payload:
        return None
    remaining = float(payload["exp"]) - datetime.now(timezone.utc).timestamp()
    return remaining if remaining > 0 else None


def should_renew_session(token: str) -> bool:
    """Pure decision: renew when the token is valid and past half its life."""
    remaining = token_seconds_remaining(token)
    return remaining is not None and remaining < SESSION_RENEW_BELOW_SECONDS


# ADAPT: the session cookie is Secure, which assumes HTTPS in front of Edward.
#   How it works here: Edward is reached over an HTTPS tunnel or proxy; browsers
#   also accept Secure cookies from http://localhost, and a LAN kiosk over plain
#   HTTP needs a browser flag that treats its origin as secure.
#   To adapt: serve Edward over HTTPS (a tunnel or a reverse proxy), or set
#   EDWARD_COOKIE_SECURE=off for a plain-HTTP LAN setup you trust.
def cookie_secure() -> bool:
    """Whether session cookies carry ``Secure`` (``EDWARD_COOKIE_SECURE``, default on)."""
    return (os.getenv("EDWARD_COOKIE_SECURE") or "on").strip().lower() not in ("off", "0", "false", "no")


def set_session_cookie(response, token: str) -> None:
    """The one way a session cookie is written (login, setup, renewal)."""
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        secure=cookie_secure(),
        samesite="lax",
    )


_password_configured_cache: Optional[bool] = None


def is_password_configured_cached() -> Optional[bool]:
    """Return cached password-configured state without a DB session.

    Returns None if the cache hasn't been populated yet (caller should
    fall back to the async version with a session).
    """
    return _password_configured_cache


async def is_password_configured(session: AsyncSession) -> bool:
    """Check if a password has been set up."""
    global _password_configured_cache
    if _password_configured_cache is not None:
        return _password_configured_cache
    result = await session.execute(
        select(SettingsModel.password_hash).where(SettingsModel.id == "default")
    )
    row = result.first()
    _password_configured_cache = row is not None and row[0] is not None
    return _password_configured_cache


async def setup_password(session: AsyncSession, password: str) -> bool:
    """Set up the initial password. Returns False if already configured."""
    # Check if password is already set
    if await is_password_configured(session):
        return False

    result = await session.execute(
        select(SettingsModel).where(SettingsModel.id == "default")
    )
    settings = result.scalar_one_or_none()

    password_hash = await asyncio.to_thread(hash_password, password)
    if settings:
        settings.password_hash = password_hash
    else:
        # Create settings with password
        settings = SettingsModel(id="default", password_hash=password_hash)
        session.add(settings)

    await session.commit()
    global _password_configured_cache
    _password_configured_cache = True
    return True


async def authenticate(session: AsyncSession, password: str) -> Optional[str]:
    """Authenticate with password and return JWT token if successful."""
    result = await session.execute(
        select(SettingsModel.password_hash).where(SettingsModel.id == "default")
    )
    row = result.first()

    if row is None or row[0] is None:
        return None

    if not await asyncio.to_thread(verify_password, password, row[0]):
        return None

    return create_access_token()


async def change_password(
    session: AsyncSession,
    current_password: str,
    new_password: str
) -> bool:
    """Change the password. Returns False if current password is wrong."""
    result = await session.execute(
        select(SettingsModel).where(SettingsModel.id == "default")
    )
    settings = result.scalar_one_or_none()

    if settings is None or settings.password_hash is None:
        return False

    if not await asyncio.to_thread(verify_password, current_password, settings.password_hash):
        return False

    settings.password_hash = await asyncio.to_thread(hash_password, new_password)
    await session.commit()
    return True
