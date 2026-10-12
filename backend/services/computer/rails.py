"""The rails: one table of what Edward's computer refuses, rendered both
into the Seatbelt profile (:mod:`services.computer.profile`, for the shell)
and into ``can_read``/``can_write``/``open_checked`` (for the in-process
editor, ``look`` and ``send_message``'s files). A parity test keeps the two
in step.

Design decision (2026-10-11): Edward gets broad access. So the rails here
are only the ones that stop real, unattended harm; the cautious read/write
blocklists of the original design are gone. Reads are allowed everywhere
(output is redacted before the model reads it) but for one narrow
exception, Edward's own credentials. What remains:

- **No reading his own credentials** (2026-10-12 review): the backend's
  session-signing key (``backend/.jwt_secret``), the first-password setup
  code (``backend/.setup_code``) and ``.env`` (``<repo>/.env``,
  ``backend/.env``). Redaction hides a key from the model, but code run in
  the shell never shows it to the model: a friend's ping that talks the
  shell into signing a session with the key would act as the owner. The
  files can't be read, replaced, moved or linked, and neither can any
  directory above them be renamed (the ancestors are pinned, below).
- **Never Edward's own API, nor a local service that sends as the owner**
  — no TCP from the shell to the backend's or the frontend's port
  (``APP_PORTS``, 8000 and 3000) or the WhatsApp bridge
  (``bridge_ports``, 8080: it sends to anyone and reads any file it is
  handed), so a minted or borrowed session has nowhere local to go.
- **No writes** to the live repo (``EDWARD_LIVE_REPO``, default this
  checkout) or the checkout this code runs from, the releases
  (``~/edward-releases``), Edward's own rails dir (``~/.edward-computer``),
  the test database's cluster (``EDWARD_TEST_PGDATA``), or the data/kegs
  that the running Postgres and the Full Disk Access Python live on (their
  ``opt/`` links included). **No renaming any directory above them** (the
  ancestors, up to ``/``): Seatbelt matches the path at the time of each
  operation, so ``mv ~/code ~/code2`` would otherwise carry the live repo
  and its credentials out from under every rule. Files can still be
  created, renamed and removed *inside* those ancestors.
- **Never launch or script Google Chrome** (a second instance hijacks
  ``open -a`` and AppleScript), and no app launches or AppleScript control
  of any app at all — sends go through ``send_message``'s guard, never the
  shell.
- **Signal only his own processes** — never Postgres, the backend, the
  frontend, the tunnel or Chrome.
- **Never a database of Edward's**: production on :5432, nor the test
  cluster on :5433 (its OS-user superuser trusts loopback, and a
  superuser's ``pg_read_file`` / ``COPY … TO PROGRAM`` would run outside
  the sandbox; review 2026-10-12). His shell has no ``DATABASE_URL``.
- **Never ``brew upgrade`` python@3.12 or postgresql@16** — those break the
  Full Disk Access Python and the source-built pgvector (a command
  precheck, since the kegs must stay writable for a legitimate reinstall by
  the owner).

**Identity, not spelling** (review 2026-10-12): macOS volumes usually
compare names case-insensitively, and ``realpath`` keeps the case the
caller typed, so ``<repo>/BACKEND/.JWT_SECRET`` opened the real key while
a string comparison let it through. Every check here compares what is on
disk: the ``(st_dev, st_ino)`` of a path, or of its nearest existing
ancestor plus the rest of its components folded the way that volume
compares names (``identity``). Seatbelt resolves case itself. The editor
and ``look`` go further and open through ``open_checked``: the parent
directory by descriptor, the file with ``O_NOFOLLOW``, and the opened
file's own canonical path (``F_GETPATH``) and inode checked again before a
byte is read or written, so a link swapped in after the check is never
followed.

**What the rails do not stop** (docs/architecture/computer.md, "What the
sandbox does not prevent"): the shell runs as the owner's macOS user and
may write the rest of the owner's home and the Homebrew prefix (Edward may
``brew install`` by design). Unsandboxed programs that run from there — a
shell rc file, ``~/Library/LaunchAgents``, the Homebrew ``bin`` that
launchd jobs and the backend search after the system's own directories,
caches the backend loads code or binaries from — are a way out. For strong
isolation run Edward as a separate macOS user.
"""

from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import sys
import unicodedata
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

from services import host_config
from services.computer import home

try:  # F_GETPATH: the kernel's own path for an open descriptor (macOS).
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore

# ADAPT: the protected kegs are the reference host's: Homebrew python@3.12 holds Full Disk Access, postgresql@16 carries a source-built pgvector
#   How it works here: backups, chat.db reads and Chrome automation run under Homebrew's python3.12, whose
#   macOS privacy grants are path-based; pgvector is built against postgresql@16. Edward's shell may not write
#   or upgrade either keg, nor the Postgres data directory.
#   To adapt: set HOMEBREW_PREFIX if Homebrew is not at /opt/homebrew or /usr/local, and edit PROTECTED_FORMULAE
#   to the Python and Postgres formulae your host actually runs production on.
# Kegs whose upgrade/reinstall would break production: the FDA-granted
# Python and the source-built pgvector against postgresql@16.
PROTECTED_FORMULAE: Tuple[str, ...] = ("python@3.12", "postgresql@16")

# Homebrew paths holding those kegs and the live Postgres data directory.
# Writing here can corrupt what production runs on, so the shell may not.
# The ``opt/`` entries are links: the profile denies both the link (so it
# can't be pointed elsewhere: launchd's PATH runs postgresql@16's tools
# through it) and what it resolves to.
#
# Not here, by design: Homebrew's own ``bin``/``sbin`` and the rest of the
# prefix. Edward may ``brew install`` what he needs, so those stay
# writable, and an unsandboxed program that runs a binary from them by name
# can be handed a planted one. The launchers search the system's own
# directories first for that reason (backend/start.sh,
# scripts/lib/edward-env.sh: edward_service_path); what is left is in the
# docs' threat model.
_HOMEBREW = home.homebrew_prefix()
PROTECTED_WRITE_PATHS: Tuple[str, ...] = (
    f"{_HOMEBREW}/var/postgresql@16",
    f"{_HOMEBREW}/Cellar/postgresql@16",
    f"{_HOMEBREW}/opt/postgresql@16",
    f"{_HOMEBREW}/Cellar/python@3.12",
    f"{_HOMEBREW}/opt/python@3.12",
)

# Edward's own credentials, relative to a checkout: whoever reads one can
# act as the owner (sign a session, set the first password, use the API
# keys). The one exception to "reads are open".
SECRET_FILES: Tuple[str, ...] = (
    "backend/.jwt_secret",   # services/auth_service.py: the session-signing key
    "backend/.setup_code",   # services/setup_code.py: the first password's code
    ".env",                  # every key and token (JWT_SECRET_KEY too, when set there)
    "backend/.env",
)

# The ports the backend (start.sh: uvicorn) and the frontend (next start)
# serve on. The shell may not connect to them, on any address.
APP_PORTS: Tuple[int, ...] = (8000, 3000)
# The WhatsApp bridge's default port (services.mcp_client.WHATSAPP_BRIDGE_ADDR).
_DEFAULT_BRIDGE_PORT = 8080

# backend/services/computer/rails.py -> the checkout this code runs from.
_CODE_CHECKOUT = Path(__file__).resolve().parents[3]

# Homebrew core directories that an ``rm -rf`` would wreck. A mistake guard,
# not a security boundary.
_HOMEBREW_CORE_DIRS = tuple(
    f"{_HOMEBREW}/{d}" for d in ("Cellar", "opt", "bin", "lib", "include", "share", "etc", "var")
)

# Free space below which heavy commands are refused (Postgres and the
# nightly pg_dump need the disk).
MIN_FREE_GB = float(os.getenv("EDWARD_SHELL_MIN_FREE_GB", "5"))

_DARWIN = sys.platform == "darwin"
# pathconf's "does this volume compare names case-sensitively": Python does
# not list it on macOS, whose <sys/unistd.h> numbers it 11.
_PC_CASE_SENSITIVE = os.pathconf_names.get("PC_CASE_SENSITIVE", 11 if _DARWIN else None)
_MAXPATHLEN = 1024


class RailRefused(PermissionError):
    """A rail refused a path. ``str()`` is the model-facing reason."""


# ------------------------------------------------------------- helpers --

def _real(path: str | os.PathLike) -> str:
    """Resolve ``~`` (against his home) and symlinks, returning an absolute
    real path. Relative paths resolve against ``~/edward-home``."""
    p = os.path.expanduser(str(path))
    if not os.path.isabs(p):
        p = os.path.join(str(home.edward_home()), p)
    try:
        return os.path.realpath(p)
    except (OSError, ValueError):
        return os.path.abspath(p)


def _folds_case(existing: str) -> bool:
    """Whether the volume holding ``existing`` compares names
    case-insensitively (APFS and HFS+ do unless formatted otherwise)."""
    if _PC_CASE_SENSITIVE is None:
        return False
    try:
        return os.pathconf(existing, _PC_CASE_SENSITIVE) == 0
    except (OSError, ValueError):
        return _DARWIN


def _fold(name: str, folds: bool) -> str:
    if _DARWIN:  # APFS/HFS+ also match names whatever their Unicode normalization
        name = unicodedata.normalize("NFD", name)
    return name.casefold() if folds else name


def _nearest(real: str) -> Tuple[str, Optional[os.stat_result], List[str]]:
    """The nearest existing ancestor-or-self of the absolute path ``real``,
    its stat, and the components below it that don't exist (top down)."""
    rest: List[str] = []
    cur = real
    while True:
        try:
            return cur, os.stat(cur), rest[::-1]
        except (OSError, ValueError):
            parent, name = os.path.split(cur)
            if parent == cur:
                return cur, None, rest[::-1]
            if name:
                rest.append(name)
            cur = parent


Identity = Tuple[Tuple[int, int], Tuple[str, ...]]


def identity(path: str | os.PathLike) -> Identity:
    """What ``path`` is on disk, whatever its spelling: the ``(st_dev,
    st_ino)`` of the path itself when it exists, else of its nearest
    existing ancestor plus the remaining components, folded the way that
    volume compares names. Two spellings of one file (a case variant, a
    symlink, a hard link) have one identity."""
    base, st, rest = _nearest(_real(path))
    ident = (st.st_dev, st.st_ino) if st is not None else (-1, -1)
    folds = _folds_case(base) if rest else False
    return ident, tuple(_fold(n, folds) for n in rest)


def _chain(path: str | os.PathLike) -> Tuple[Identity, set]:
    """``(identity(path), the inodes of path's existing ancestors-or-self)``."""
    base, st, rest = _nearest(_real(path))
    ids = set()
    if st is not None:
        ids.add((st.st_dev, st.st_ino))
    folds = _folds_case(base) if rest else False
    cur = base
    while True:
        parent = os.path.dirname(cur)
        if parent == cur:
            break
        try:
            s = os.stat(parent)
            ids.add((s.st_dev, s.st_ino))
        except OSError:
            pass
        cur = parent
    ident = (st.st_dev, st.st_ino) if st is not None else (-1, -1)
    return (ident, tuple(_fold(n, folds) for n in rest)), ids


def _under(path_ident: Identity, path_ids: set, root: str | os.PathLike) -> bool:
    root_ident = identity(root)
    if root_ident[1]:  # the root doesn't exist (yet): same existing base, same leading components
        return path_ident[0] == root_ident[0] and path_ident[1][:len(root_ident[1])] == root_ident[1]
    return root_ident[0] in path_ids


def _is_under(path: str | os.PathLike, root: str | os.PathLike) -> bool:
    """True when ``path`` is ``root`` or lives inside it, compared by what is
    on disk (so ``/a/edward-home`` is not under ``/a/edward``, and
    ``/A/EDWARD/x`` is under ``/a/edward`` on a case-insensitive volume)."""
    ident, ids = _chain(path)
    return _under(ident, ids, root)


def _both(path: str | os.PathLike) -> List[str]:
    """``path`` as written (absolute, ``~`` expanded) and as resolved: a
    link's own path is protected as well as what it points at."""
    raw = os.path.abspath(os.path.expanduser(str(path)))
    out = [raw]
    try:
        real = os.path.realpath(raw)
    except (OSError, ValueError):
        real = raw
    if real != raw:
        out.append(real)
    return out


def _dedupe(paths: Iterable[str]) -> List[str]:
    seen: set = set()
    out: List[str] = []
    for p in paths:
        if p and p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _test_pgdata() -> str:
    """The test database's cluster (scripts/test-pg-cluster.sh): its
    postmaster runs outside the sandbox, so a planted postgresql.conf would
    run at its next start."""
    return os.path.expanduser(os.getenv("EDWARD_TEST_PGDATA") or "~/.edward-test-pg")


def _configured_dirs() -> List[str]:
    """Directories the owner configured that unsandboxed jobs run from or
    keep production's data in: ``EDWARD_PG_BIN`` (first on the launchers'
    PATH after the system's own), ``EDWARD_TEST_PG_BIN`` and
    ``EDWARD_PGDATA``."""
    out = []
    for name in ("EDWARD_PG_BIN", "EDWARD_TEST_PG_BIN", "EDWARD_PGDATA"):
        value = (os.getenv(name) or "").strip()
        if value:
            out.append(value)
    return out


def write_deny_roots() -> List[str]:
    """Every subpath the shell and editor may not write to, as written and
    as resolved."""
    roots: List[str] = [
        str(home.live_repo()),
        str(_CODE_CHECKOUT),
        str(home.releases_dir()),
        str(home.computer_dir()),
        _test_pgdata(),
    ]
    roots.extend(PROTECTED_WRITE_PATHS)
    roots.extend(_configured_dirs())
    return _dedupe(p for r in roots for p in _both(r))


def read_deny_files() -> List[str]:
    """Every file the shell and editor may not read: ``SECRET_FILES`` in the
    checkout this code runs from and in the live repo (``EDWARD_LIVE_REPO``),
    as real paths (Seatbelt matches the resolved path)."""
    return _dedupe(os.path.realpath(os.path.join(str(root), rel))
                   for root in (_CODE_CHECKOUT, home.live_repo()) for rel in SECRET_FILES)


def protected_ancestors(write_deny: Optional[List[str]] = None,
                        read_deny: Optional[List[str]] = None) -> List[str]:
    """Every directory above a write-denied root or a credential, up to
    ``/``: none may be renamed, moved or removed, or the path every other
    rule matches would no longer lead to what it protects (review
    2026-10-12: ``mv ~/code ~/code2`` carried the repo and its key out from
    under them). Their contents stay his to change."""
    deny = write_deny if write_deny is not None else write_deny_roots()
    secrets = read_deny if read_deny is not None else read_deny_files()
    out: List[str] = []
    for p in list(deny) + list(secrets):
        cur = os.path.abspath(p)
        while True:
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            out.append(parent)
            cur = parent
    return _dedupe(out)


def bridge_ports() -> Tuple[int, ...]:
    """The WhatsApp bridge's port (``services.mcp_client.WHATSAPP_BRIDGE_ADDR``,
    default 8080): its HTTP API sends to anyone with no login and attaches
    any file it is handed, outside the sandbox. Denied whether or not the
    integration is on — the bridge runs on its own."""
    try:
        from services.mcp_client import WHATSAPP_BRIDGE_ADDR

        return (int(WHATSAPP_BRIDGE_ADDR[1]),)
    except Exception:  # noqa: BLE001 - the default, whatever the import does
        return (_DEFAULT_BRIDGE_PORT,)


def denied_ports() -> Tuple[int, ...]:
    """Every local port the shell may not connect to, beside the databases:
    Edward's own API and the WhatsApp bridge."""
    return tuple(dict.fromkeys(APP_PORTS + bridge_ports()))


# ------------------------------------------------- read / write checks --

def _deny_identities() -> List[Identity]:
    return [identity(f) for f in read_deny_files()]


def can_read(path: str | os.PathLike) -> bool:
    """Reads are allowed everywhere (by design; output is redacted before
    the model sees it) except Edward's own credentials (``read_deny_files``),
    however the path is spelled (case, links, hard links). Mirrors the
    Seatbelt read-deny (parity-tested). Fails closed."""
    try:
        ident = identity(path)
        return ident not in _deny_identities()
    except Exception:  # noqa: BLE001
        return False


def read_refusal(path: str | os.PathLike) -> str:
    """The message a refused read gets."""
    return (f"{_real(path)} holds Edward's own credentials (the session key, the setup code "
            f"or .env) — the backend keeps them; they are not yours to read")


def _write_rail(path: str | os.PathLike) -> Optional[str]:
    """Which rail a write to ``path`` hits ("secret", "ancestor", or the
    protected root), else None."""
    ident, ids = _chain(path)
    if ident in _deny_identities():
        return "secret"
    for root in write_deny_roots():
        if _under(ident, ids, root):
            return root
    ancestors = protected_ancestors()
    if any(ident == identity(a) for a in ancestors):
        return "ancestor"
    return None


def can_write(path: str | os.PathLike) -> bool:
    """Whether the in-process editor may write ``path``. Mirrors the
    Seatbelt write-deny (parity-tested): the protected roots, his own
    credentials, which can't be replaced either, and the directories above
    them, which can't be replaced themselves. Compared by identity, so no
    spelling gets around it. Fails closed."""
    try:
        return _write_rail(path) is None
    except Exception:  # noqa: BLE001
        return False


def write_refusal(path: str | os.PathLike) -> str:
    """The message a refused write gets, naming the rail and the way out."""
    real = _real(path)
    try:
        rail = _write_rail(real)
    except Exception:  # noqa: BLE001
        rail = None
    if rail == "secret":
        return read_refusal(real)
    if rail == "ancestor":
        return (f"{real} holds something your computer protects (your live code, a release, the databases' "
                f"files or your own credentials) — it can't be moved, renamed or replaced; what's inside "
                f"it is still yours to change unless a rail says otherwise")
    if _is_under(real, str(home.live_repo())) or _is_under(real, str(_CODE_CHECKOUT)):
        return (f"{real} is your live code — read it, don't write it; "
                f"work in a clone under {home.evolve_dir()}")
    if _is_under(real, str(home.releases_dir())):
        return f"{real} is a release — immutable; build in {home.evolve_dir()}"
    if _is_under(real, str(home.computer_dir())):
        return f"{real} is your computer's own config — not yours to change"
    if _is_under(real, _test_pgdata()):
        return f"{real} is the test database's own files — not yours to change"
    for p in list(PROTECTED_WRITE_PATHS) + _configured_dirs():
        if _is_under(real, p):
            return (f"{real} is what the running Postgres or the Full Disk Access Python runs on — "
                    f"don't write there; install into your own ~/edward-home/.venv")
    return f"{real} is outside what you may write"


# ---------------------------------------------------- opening a file --

def fd_path(fd: int) -> Optional[str]:
    """The kernel's own path for an open descriptor — on macOS
    (``F_GETPATH``) with the case stored on disk and every link resolved —
    or None where the OS can't say."""
    if fcntl is not None and hasattr(fcntl, "F_GETPATH"):
        try:
            raw = fcntl.fcntl(fd, fcntl.F_GETPATH, bytes(_MAXPATHLEN))
            return os.fsdecode(raw.split(b"\0", 1)[0]) or None
        except OSError:
            return None
    try:
        return os.readlink(f"/proc/self/fd/{fd}")
    except OSError:
        return None


def _check_open(target: str, *, write: bool) -> None:
    if not can_read(target):
        raise RailRefused(read_refusal(target))
    if write and not can_write(target):
        raise RailRefused(write_refusal(target))


def open_checked(path: str | os.PathLike, *, write: bool = False, create: bool = False,
                 exclusive: bool = False, mode: int = 0o666) -> Tuple[int, str]:
    """Open a regular file under the rails, with no window between the check
    and the open. Returns ``(fd, the opened file's canonical path)``; the
    caller closes the descriptor.

    The path is checked; its directory is opened by descriptor and that
    directory's canonical path checked; the file is opened relative to it
    with ``O_NOFOLLOW`` (a link swapped in since is refused, never
    followed); and the opened file's own inode and canonical path are
    checked again before the descriptor is handed back. ``write`` opens it
    read-write and never truncates (the caller truncates once it is
    checked); ``create`` creates a missing file, ``exclusive`` only ever a
    new one (``FileExistsError`` otherwise). A file created here that
    fails the last check is removed again.

    Raises ``RailRefused``, ``FileNotFoundError``, ``FileExistsError``,
    ``IsADirectoryError`` or ``OSError``."""
    real = _real(path)
    _check_open(real, write=write)
    parent, name = os.path.split(real)
    if not name:
        raise IsADirectoryError(real)
    dfd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    fd = -1
    created = False
    try:
        target = os.path.join(fd_path(dfd) or parent, name)
        _check_open(target, write=write)
        flags = os.O_NOFOLLOW | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
        flags |= os.O_RDWR if write else os.O_RDONLY
        try:
            if write and exclusive:
                fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, mode, dir_fd=dfd)
                created = True
            else:
                try:
                    fd = os.open(name, flags, dir_fd=dfd)
                except FileNotFoundError:
                    if not (write and create):
                        raise
                    fd = os.open(name, flags | os.O_CREAT | os.O_EXCL, mode, dir_fd=dfd)
                    created = True
        except OSError as exc:
            if exc.errno == errno.ELOOP:
                raise RailRefused(f"{real} changed into a link while I was opening it — not followed") from None
            raise
        st = os.fstat(fd)
        if stat.S_ISDIR(st.st_mode):
            raise IsADirectoryError(real)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(f"{real} is not a regular file")
        canon = fd_path(fd) or target
        if ((st.st_dev, st.st_ino), ()) in _deny_identities():
            raise RailRefused(read_refusal(canon))
        _check_open(canon, write=write)
        out, fd = fd, -1
        return out, canon
    except BaseException:
        if fd >= 0:
            os.close(fd)
            if created:
                try:
                    os.unlink(name, dir_fd=dfd)
                except OSError:
                    pass
        raise
    finally:
        os.close(dfd)


def read_checked(path: str | os.PathLike, limit: Optional[int] = None) -> Tuple[bytes, str]:
    """``(the file's bytes, its canonical path)`` through ``open_checked``;
    with ``limit``, at most ``limit + 1`` bytes (so the caller can tell
    "too big")."""
    fd, canon = open_checked(path)
    try:
        return read_fd(fd, limit), canon
    finally:
        os.close(fd)


def write_fd(fd: int, data: bytes) -> None:
    """Replace everything in ``fd`` with ``data`` (truncate, then write it
    all from offset 0)."""
    os.ftruncate(fd, 0)
    view = memoryview(data)
    offset = 0
    while offset < len(view):
        offset += os.pwrite(fd, view[offset:], offset)


def read_fd(fd: int, limit: Optional[int] = None) -> bytes:
    """Everything from offset 0 of ``fd`` (at most ``limit + 1`` bytes)."""
    chunks: List[bytes] = []
    total, offset = 0, 0
    cap = None if limit is None else limit + 1
    while cap is None or total < cap:
        want = 1 << 20 if cap is None else min(1 << 20, cap - total)
        chunk = os.pread(fd, want, offset)
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
        offset += len(chunk)
    return b"".join(chunks)


# -------------------------------------------------------- the precheck --

_BREW_RE = re.compile(r"\bbrew\s+(?P<sub>upgrade|reinstall|uninstall|remove|rm)\b(?P<rest>[^\n;|&]*)")
_RM_RF_RE = re.compile(r"\brm\s+(?:-[A-Za-z]*\s+)*-?[A-Za-z]*r[A-Za-z]*f?[A-Za-z]*\b")


def precheck(command: str) -> Optional[str]:
    """A mistake guard that runs before a command. Returns a refusal string,
    or ``None`` to let it run. This is not the security boundary (Seatbelt
    is); it catches the few irreversible mistakes with a clear message.
    """
    if not command or not command.strip():
        return None
    cmd = command

    # A named rule: never upgrade/reinstall the FDA Python or the
    # pgvector-bearing Postgres. A bare ``brew upgrade`` (no formula) upgrades
    # everything, so it is refused too.
    # ``rm`` is brew's alias of uninstall; ``unlink``/``link``/``pin``/``unpin``
    # move the links production runs through (the backups' python3.12).
    for m in re.finditer(r"\bbrew\s+(upgrade|reinstall|uninstall|remove|rm|unlink|link|pin|unpin)\b([^\n;|&]*)",
                         cmd):
        sub, rest = m.group(1), m.group(2)
        formulae = [w for w in rest.split() if w and not w.startswith("-")]
        if sub == "upgrade" and not formulae:
            return ("`brew upgrade` with no formula would upgrade python@3.12 and "
                    "postgresql@16, which breaks Full Disk Access and pgvector. "
                    "Upgrade a specific formula instead.")
        for f in formulae:
            base = f.split("/")[-1]
            if base in PROTECTED_FORMULAE or base.rstrip("@") in {p.split("@")[0] for p in PROTECTED_FORMULAE}:
                if base in PROTECTED_FORMULAE:
                    return (f"`brew {sub} {f}` would break production: {base} carries "
                            f"Full Disk Access (python@3.12) or the source-built pgvector "
                            f"(postgresql@16). Not allowed.")

    # Mistake guard: rm -rf of a Homebrew core directory.
    if _RM_RF_RE.search(cmd):
        for core in _HOMEBREW_CORE_DIRS:
            if re.search(r"(^|\s)" + re.escape(core) + r"(/\*|\b)", cmd):
                return f"refusing rm on {core}: that is Homebrew's install tree, not yours"

    # Disk rail: when space is low, refuse everything that is not cleanup.
    low = _disk_is_low()
    if low is not None:
        first = _first_word(cmd)
        if first not in ("rm", "du", "ls", "df"):
            return (f"the disk is nearly full ({low:.1f} GB free) — Postgres and the "
                    f"backups need it; clean ~/edward-home first (rm/du/ls still run)")
    return None


def _first_word(command: str) -> str:
    for seg in re.split(r"[|;&]", command):
        for word in seg.strip().split():
            if "=" in word and not word.startswith("-"):
                continue  # env assignment prefix
            return os.path.basename(word)
    return ""


def _disk_is_low() -> Optional[float]:
    """Free GB on the data volume when below ``MIN_FREE_GB``, else ``None``."""
    for vol in ("/System/Volumes/Data", "/"):
        try:
            free_gb = shutil.disk_usage(vol).free / (1024 ** 3)
            return free_gb if free_gb < MIN_FREE_GB else None
        except OSError:
            continue
    return None


# ----------------------------------------------------------- rail hint --

_RAIL_MARKERS = ("Operation not permitted", "deny(1)", "sandbox", "not permitted")


def _live_code_re() -> re.Pattern:
    """The live checkout (as configured and as resolved), the running one,
    or a release."""
    paths = dict.fromkeys(str(p).rstrip("/") for p in (home.live_repo(), host_config.repo_root(), _CODE_CHECKOUT))
    alts = "|".join(re.escape(p) for p in paths if p)
    return re.compile(rf"(?i)(?:{alts})(?:/|\b)|edward-releases" if alts else r"edward-releases")


_SECRET_FILE_RE = re.compile(r"(?i)\.jwt_secret|\.setup_code|(?:^|[/\s'\"])\.env\b")


def _port_alts() -> str:
    return "|".join(str(p) for p in denied_ports())


def _app_port_re() -> re.Pattern:
    return re.compile(r"(?i)(?:\bport\s+|:)(?:" + _port_alts() + r")\b")


def _app_port_refused_re() -> re.Pattern:
    # curl and friends say "Failed to connect to localhost port 8000", not EPERM.
    alts = _port_alts()
    return re.compile(
        r"(?i)(?:failed to connect|couldn't connect|connection refused|not permitted|errno 1\b)"
        r"[^\n]*?(?:\bport\s+|:)(?:" + alts + r")\b"
        r"|(?:\bport\s+|:)(?:" + alts + r")\b[^\n]*?"
        r"(?:failed to connect|couldn't connect|not permitted)"
    )


_DB_RE = re.compile(r":543[23]\b|\bport\s+543[23]\b|\.s\.PGSQL\.543[23]|postgresql@16/|edward-test-pg")
_DB_HINT = ("[rail] Edward's databases are off limits to your shell — production (:5432) and the test "
            "cluster (:5433); what you know is in the memory tool. A project that needs Postgres runs "
            "its own in ~/edward-home")


def _hints() -> Tuple[Tuple[re.Pattern, str], ...]:
    """The rail hints, built per call: the live-repo path comes from
    ``EDWARD_LIVE_REPO`` (default this checkout)."""
    return (
        (_SECRET_FILE_RE,
         "[rail] the session key, the setup code and .env are the backend's own credentials — "
         "your shell and editor can't read them"),
        (_app_port_re(),
         "[rail] your shell can't call Edward's own API (ports 8000 and 3000) or the WhatsApp bridge — "
         "use your tools"),
        (re.compile(r"(?i)google chrome|/Applications/Google"),
         "[rail] you can't launch or script Google Chrome from the shell — use the chrome or browser tool"),
        (re.compile(r"(?i)\bopen\b.*(-a|application)|lsopen|launchservices"),
         "[rail] app launches are off — use the browser or chrome tool, not `open`"),
        (re.compile(r"(?i)osascript|appleevent|apple event"),
         "[rail] scripting other apps is off — sends go through send_message, not AppleScript"),
        (_DB_RE, _DB_HINT),
        (re.compile(r"(?i)sudo|/usr/bin/security|launchctl|crontab"),
         "[rail] sudo/launchctl/crontab are off — recurring work goes through the schedule tool"),
        (_live_code_re(),
         "[rail] that path is your live code or a release — read it; change it in a clone under ~/edward-evolve"),
    )


def rail_hint(output: str) -> Optional[str]:
    """One line naming the rail a command just hit, or ``None``. Only fires
    when the output looks like a permission denial."""
    if not output:
        return None
    if not any(marker in output for marker in _RAIL_MARKERS):
        # ``open``/``osascript`` can fail with their own error text, and a
        # refused connection to a denied port reads "Failed to connect";
        # still hint.
        if _app_port_refused_re().search(output):
            return _hints()[1][1]
        if _DB_RE.search(output) and re.search(r"(?i)connection to server|could not connect|failed", output):
            return _DB_HINT
        if not re.search(r"(?i)osascript|appleevent|-54|-600|lsopen", output):
            return None
    for pattern, hint in _hints():
        if pattern.search(output):
            return hint
    return None
