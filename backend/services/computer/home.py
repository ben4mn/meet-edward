"""``~/edward-home`` — Edward's own place on the host Mac, and the
allowlisted environment his shell runs in.

Everything here is path and environment plumbing: pure getters plus
``ensure_home`` (which creates the layout) and ``shell_env`` (which builds
the shell's environment). No secrets ever reach the shell's environment —
reading files is allowed by design (broad access, 2026-10-11) but for
Edward's own credentials (``rails.SECRET_FILES``), the model sees redacted
output, and nothing valuable is sitting in ``os.environ`` for a command to
echo.

``~/edward-home`` is the shell's to change, so the backend (which runs
outside the sandbox) never writes there through a path the shell could
have pointed elsewhere: ``ensure_home`` creates its two files only when
they don't exist, never through a link, and never inside a protected root.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Dict

from services import host_config, sandbox_env
from services.owner import owner_text

# ---------------------------------------------------------------- paths --

# The owner's home directory on this Mac. The rails are written against it;
# a test points everything at a tmp dir with ``EDWARD_HOME`` and ``HOME``.
def host_home() -> Path:
    return Path(os.path.expanduser("~")).resolve()


def edward_home() -> Path:
    """``~/edward-home`` (override with ``EDWARD_HOME``)."""
    env = os.getenv("EDWARD_HOME")
    if env:
        return Path(os.path.expanduser(env)).resolve()
    return (host_home() / "edward-home").resolve()


def computer_dir() -> Path:
    """Backend-owned, not writable by the shell: the profile, pidfiles,
    cached keg list. Overridable with ``EDWARD_COMPUTER_DIR`` for tests."""
    env = os.getenv("EDWARD_COMPUTER_DIR")
    if env:
        return Path(os.path.expanduser(env)).resolve()
    return (host_home() / ".edward-computer").resolve()


def live_repo() -> Path:
    """The live checkout the backend serves from (``EDWARD_LIVE_REPO``,
    default: this checkout — ``host_config.repo_root``)."""
    return host_config.repo_root().resolve()


def homebrew_prefix() -> str:
    """Where Homebrew lives: ``HOMEBREW_PREFIX``, else ``/opt/homebrew``
    when it exists (Apple silicon), else ``/usr/local`` (Intel)."""
    env = (os.getenv("HOMEBREW_PREFIX") or "").strip().rstrip("/")
    if env:
        return os.path.expanduser(env)
    if os.path.isdir("/opt/homebrew"):
        return "/opt/homebrew"
    return "/usr/local"


def releases_dir() -> Path:
    return Path(os.path.expanduser(os.getenv("EDWARD_RELEASES_DIR", "~/edward-releases"))).resolve()


def evolve_dir() -> Path:
    roots = os.getenv("EDWARD_COMPUTER_WORKTREE_ROOTS") or os.getenv("EDWARD_CODEX_WORKTREE_ROOTS") or "~/edward-evolve"
    first = roots.split(os.pathsep)[0]
    return Path(os.path.expanduser(first)).resolve()


def scratch_dir() -> Path:
    return Path(os.path.expanduser(os.getenv("EDWARD_SCRATCH_DIR", "~/edward-scratch"))).resolve()


# The subdirectories under ~/edward-home that ``ensure_home`` creates.
_HOME_SUBDIRS = (
    "bin", "skills", "projects", "downloads", "jobs", "tmp",
    ".npm-global", ".out", ".trash", "browser/profile",
)


# Rendered by ``readme_text``: ``{REPO}`` is the live checkout, ``{OWNER}``
# the owner's name.
_README = """\
# This is Edward's home

~/edward-home is yours. Build things here, install what you need, keep notes
and tools you want to reuse.

Layout
- bin/        your executables (on your PATH)
- skills/     things you built to reuse: <name>/SKILL.md + scripts
- projects/   work in progress
- downloads/  where your browser saves files
- jobs/       background job logs (nohup CMD > jobs/NAME.log 2>&1 &)
- tmp/        scratch space (your TMPDIR)
- .venv/      your Python 3.12 virtualenv
- .out/       full output of commands that were cut short for the chat

The rails (your computer enforces these; a refusal names the rail)
- You can't write to your live code ({REPO} — read it, change it
  in a clone under ~/edward-evolve) or to ~/edward-releases, or to the
  data {OWNER}'s running services live on.
- You can't launch or script apps, or kill what you didn't start.
- You can't reach Edward's databases (production on :5432, the test
  cluster on :5433) or his own API; what you know is in the memory tool.
  A project that needs Postgres runs its own in ~/edward-home.
- Secrets in command output are redacted before anyone reads them.
"""

_SKILLS_INDEX = """\
# Skills index

One line per thing you built to reuse, pointing at skills/<name>/SKILL.md.
(Empty for now.)
"""


def readme_text() -> str:
    """The README Edward finds in his home, naming this host's live checkout."""
    return owner_text(_README).replace("{REPO}", str(live_repo()))


def ensure_home() -> Path:
    """Create ``~/edward-home`` and its layout if missing. Idempotent; never
    overwrites README.md or SKILLS.md once Edward has them."""
    root = edward_home()
    root.mkdir(parents=True, exist_ok=True)
    for sub in _HOME_SUBDIRS:
        (root / sub).mkdir(parents=True, exist_ok=True)
    _write_new(root / "README.md", readme_text())
    _write_new(root / "SKILLS.md", _SKILLS_INDEX)
    # Backend-owned area (profile, pidfiles). Not under ~/edward-home, so the
    # shell's write-deny on it does not touch his own files.
    cdir = computer_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    (cdir / "sessions").mkdir(parents=True, exist_ok=True)
    return root


def _write_new(path: Path, text: str) -> None:
    """Create ``path`` with ``text`` unless something (a file, a link, even
    a dangling one) is already there; never inside a protected root."""
    from services.computer import rails  # rails imports this module

    try:
        fd, _ = rails.open_checked(path, write=True, create=True, exclusive=True, mode=0o644)
    except (FileExistsError, rails.RailRefused):
        return
    try:
        rails.write_fd(fd, text.encode("utf-8"))
    finally:
        os.close(fd)


# ------------------------------------------------------------- the env --

# Never carried into his shell, whatever the backend's own environment
# holds: a database URL (production's, or the test cluster's — whose
# OS-user superuser would run code outside the sandbox; review 2026-10-12)
# or libpq's connection settings.
_NEVER_IN_SHELL = ("DATABASE_URL", "TEST_DATABASE_URL", "PGHOST", "PGPORT", "PGUSER", "PGDATABASE",
                   "PGPASSWORD", "PGPASSFILE", "PGSERVICE", "PGSERVICEFILE")


def shell_env() -> Dict[str, str]:
    """The shell's environment: an allowlist. No ``.env`` secrets, no API
    keys, and no database: neither ``DATABASE_URL`` nor libpq's ``PG*``
    (the profile refuses both of Edward's clusters, :5432 and :5433)."""
    root = edward_home()
    src = os.environ
    venv = root / ".venv"
    brew = homebrew_prefix()
    dirs = [
        str(root / "bin"),
        str(venv / "bin"),
        str(root / ".npm-global" / "bin"),
        f"{brew}/bin", f"{brew}/sbin",
        "/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin",
    ]
    path = os.pathsep.join(dict.fromkeys(dirs))  # once each, in order
    # Drop any live-code entries that rode in on the base PATH (defence in
    # depth; the explicit list above has none).
    path = sandbox_env.path_without_live_code(path)

    env: Dict[str, str] = {
        "HOME": str(root),
        "SHELL": "/bin/bash",
        "TMPDIR": str(root / "tmp"),
        "TERM": "dumb",
        "PAGER": "cat",
        "GIT_PAGER": "cat",
        "NO_COLOR": "1",
        "PYTHONUNBUFFERED": "1",
        "PATH": path,
        "VIRTUAL_ENV": str(venv),
        "PIP_REQUIRE_VIRTUALENV": "1",
        "NPM_CONFIG_PREFIX": str(root / ".npm-global"),
        "HOMEBREW_NO_AUTO_UPDATE": "1",
        "HOMEBREW_NO_INSTALL_UPGRADE": "1",
        "HOMEBREW_NO_INSTALLED_DEPENDENTS_CHECK": "1",
        "HOMEBREW_NO_ANALYTICS": "1",
        "HOMEBREW_NO_ENV_HINTS": "1",
        "EDWARD_FAKE_EMBEDDINGS": "1",
        "GRAPHITI_ENABLED": "false",
        "GRAPHITI_INGEST_ENABLED": "false",
        "EDWARD_HOME": str(root),
    }
    # Carry a few harmless identity/locale values through from the host.
    for name in ("USER", "LOGNAME", "LANG"):
        if src.get(name):
            env[name] = src[name]
    for name, value in src.items():
        if name.startswith("LC_") and value:
            env[name] = value
    for name in _NEVER_IN_SHELL:
        env.pop(name, None)
    return env
