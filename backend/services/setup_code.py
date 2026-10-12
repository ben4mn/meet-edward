"""The one-time setup code: proof that whoever sets the first password can
read a file on the Mac Edward runs on.

Before a password exists, whoever sets it owns the install (a shell on the
host included). The request headers cannot say who that is: Next's
``/api`` proxy passes the client's own ``Host`` through as
``X-Forwarded-Host``, so a LAN device can claim to be ``localhost``
(services/client_ip.py). So while no password is configured the backend
keeps a random code in ``backend/.setup_code`` (mode 0600, created with
``O_EXCL``), and ``POST /api/auth/setup`` takes the first password only
with that code. ``setup.sh``, ``restart.sh`` and
``scripts/install-launchagents.sh`` say where it is; the login page asks
for it.

The code is never written to a log (``/tmp`` logs are world-readable) —
only the file's path. It is deleted once a password is set, and a new one
is made if the password row is ever lost again. Edward's own shell cannot
read or replace the file (services/computer/rails.py).
"""

from __future__ import annotations

import hmac
import logging
import os
import re
import secrets
import stat
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# backend/services/setup_code.py -> backend/.setup_code
SETUP_CODE_FILE = Path(__file__).resolve().parents[1] / ".setup_code"
# How the scripts, the log and the login page name it (relative to the checkout).
SETUP_CODE_DISPLAY = "backend/.setup_code"

# No 0/O, 1/I/L: a code read off a terminal is typed without guessing.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
GROUPS = 3
GROUP_LEN = 4
CODE_LEN = GROUPS * GROUP_LEN

_SEPARATORS = re.compile(r"[\s\-‐-―−_.]")
_VALID = re.compile(rf"^[{ALPHABET}]{{{CODE_LEN}}}$")


def code_path() -> Path:
    return SETUP_CODE_FILE


def generate() -> str:
    """A fresh code, e.g. ``K7QH-M3TX-9PWF`` (about 59 bits)."""
    chars = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    return "-".join(chars[i:i + GROUP_LEN] for i in range(0, CODE_LEN, GROUP_LEN))


def normalize(code: Optional[str]) -> str:
    """Case, dashes and spaces don't matter: ``k7qh m3tx 9pwf`` is the same code."""
    return _SEPARATORS.sub("", str(code or "")).upper()


def _read_trusted(path: Path) -> Optional[str]:
    """The code in ``path`` when it is a regular file only its owner can
    read, holding a well-formed code; otherwise ``None``."""
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        return None
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            return None
        raw = os.read(fd, 256).decode("ascii", errors="replace")
    except OSError:
        return None
    finally:
        os.close(fd)
    return raw.strip() if _VALID.match(normalize(raw)) else None


def _create(path: Path) -> str:
    code = generate()
    fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        os.write(fd, (code + "\n").encode("ascii"))
    finally:
        os.close(fd)
    return code


def _ensure() -> tuple[Optional[str], bool]:
    """(code, made_now)."""
    path = code_path()
    existing = _read_trusted(path)
    if existing is not None:
        return existing, False
    try:
        if path.exists() or path.is_symlink():
            path.unlink()
        return _create(path), True
    except FileExistsError:
        # Another worker made it first.
        return _read_trusted(path), False
    except OSError as exc:
        logger.error("could not write the first-time setup code to %s: %s", path, exc)
        return None, False


def ensure() -> Optional[str]:
    """The current code, making the file when there is none (or when the
    one there is not a private, well-formed code file) and saying where it
    is. ``None`` when the file cannot be written; setup then refuses."""
    code, made = _ensure()
    if made:
        announce()
    return code


def current() -> Optional[str]:
    """The code on disk now, without making one."""
    return _read_trusted(code_path())


def matches(offered: Optional[str]) -> bool:
    """Constant-time comparison of ``offered`` with the code on disk."""
    expected = current()
    given = normalize(offered)
    if not expected or not given:
        return False
    return hmac.compare_digest(given.encode("ascii", "replace"), normalize(expected).encode("ascii"))


def discard() -> None:
    """Delete the code (a password is set). Missing is fine."""
    try:
        code_path().unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning("could not delete %s: %s", code_path(), exc)


def announce() -> None:
    """Say where the code is, never what it is."""
    logger.warning(
        "No password is set yet. First-time setup code: cat %s (%s)",
        SETUP_CODE_DISPLAY, code_path(),
    )


async def ensure_for_startup() -> None:
    """At startup: a code while no password exists, none once one does.
    Best effort — setup makes the code itself when this could not."""
    from services import auth_service
    from services.database import async_session

    async with async_session() as session:
        configured = await auth_service.is_password_configured(session)
    if configured:
        discard()
        return
    code, _made = _ensure()
    if code is not None:
        announce()  # every start, so the log after a restart still says where
