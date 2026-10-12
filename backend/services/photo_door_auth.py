"""The household photo door: a photo-only session for someone else at home.

A second person in the household (``EDWARD_HOUSEHOLD``) gets a small
home-screen app at ``/photos`` with its own password: the hub's
screensaver album and its screensaver settings, nothing else (2026-10-11).
The session is a separate cookie (``photo_door_token``) holding a JWT
whose subject is ``photo_door``. ``auth_service.verify_token`` accepts only
``sub == "user"``, so a door token can never open the owner's app, and
the middleware lets the door cookie through only on ``/api/photo-door/*``.

The door is off unless ``PHOTO_DOOR_PASSWORD_HASH`` holds a bcrypt hash of
its password. Make one with::

    python -c "import bcrypt, getpass; print(bcrypt.hashpw(getpass.getpass().encode(), bcrypt.gensalt()).decode())"

and put it in ``.env`` in single quotes (the scripts read ``.env`` with a shell,
and an unquoted ``$2b$12$…`` would be expanded). While the door is
off, a login is refused with ``disabled_message()``, no door session
verifies, and every other door route answers 404.
"""

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from services import auth_service

logger = logging.getLogger(__name__)

ENV_VAR = "PHOTO_DOOR_PASSWORD_HASH"
SUBJECT = "photo_door"
COOKIE_NAME = "photo_door_token"
TOKEN_DAYS = 90
COOKIE_MAX_AGE = TOKEN_DAYS * 24 * 60 * 60
# Like the owner's session: a session in use is re-issued past half its life.
RENEW_BELOW_SECONDS = COOKIE_MAX_AGE / 2

_BCRYPT_PREFIXES = ("$2a$", "$2b$", "$2y$")
_warned: set = set()


def _password_hash() -> str:
    return (os.getenv(ENV_VAR) or "").strip()


def config_problem() -> Optional[str]:
    """Why the door is off, or None when it is on."""
    hashed = _password_hash()
    if not hashed:
        return f"The photo door is off. Set {ENV_VAR} to a bcrypt hash of its password to turn it on."
    if not hashed.startswith(_BCRYPT_PREFIXES):
        if hashed not in _warned:
            _warned.add(hashed)
            logger.warning("photo door: %s is not a bcrypt hash (quote it in .env); the door stays off", ENV_VAR)
        return (f"The photo door is off: {ENV_VAR} is not a bcrypt hash. "
                "Put it in .env in single quotes so the shell leaves its $ signs alone.")
    return None


def enabled() -> bool:
    """True when ``PHOTO_DOOR_PASSWORD_HASH`` holds a bcrypt hash."""
    return config_problem() is None


def disabled_message() -> str:
    return config_problem() or ""


def check_password(password: str) -> bool:
    if not password or not enabled():
        return False
    return auth_service.verify_password(password, _password_hash())


def create_token(expires_delta: Optional[timedelta] = None) -> str:
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(days=TOKEN_DAYS))
    return jwt.encode(
        {"exp": expire, "sub": SUBJECT},
        auth_service.JWT_SECRET_KEY,
        algorithm=auth_service.JWT_ALGORITHM,
    )


def _seconds_remaining(token: str) -> Optional[float]:
    try:
        payload = jwt.decode(token, auth_service.JWT_SECRET_KEY, algorithms=[auth_service.JWT_ALGORITHM])
    except JWTError:
        return None
    if payload.get("sub") != SUBJECT or "exp" not in payload:
        return None
    remaining = float(payload["exp"]) - datetime.now(timezone.utc).timestamp()
    return remaining if remaining > 0 else None


def verify_token(token: Optional[str]) -> bool:
    """A live door session. Never true while the door is off."""
    return bool(token) and enabled() and _seconds_remaining(token) is not None


def should_renew(token: str) -> bool:
    remaining = _seconds_remaining(token)
    return remaining is not None and remaining < RENEW_BELOW_SECONDS


def set_cookie(response, token: str) -> None:
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=COOKIE_MAX_AGE,
        httponly=True,
        # Meant for HTTPS (a tunnel or a proxy); EDWARD_COOKIE_SECURE=off drops
        # Secure for a plain-HTTP LAN install, as the session cookie does.
        secure=auth_service.cookie_secure(),
        samesite="lax",
    )
