"""Environment for anything Edward spawns to work on his own code.

pytest and Codex run inside a git worktree or a scratch directory. On
2026-09-07 the environment they inherited carried the live
``DATABASE_URL``; the test suite's ``clean_db`` fixture trusted it and
truncated nearly every table of the production database. Every spawn now
goes through :func:`sandbox_env`, which points DATABASE_URL at the test
database, and ``tests/conftest.py`` refuses a non-``_test`` database on
its own.

It is an allowlist since 2026-09-27 (Evolution v2 Phase 0): it used to copy
all of ``os.environ`` — Twilio, VAPID, the JWT secret, the OpenAI and
Anthropic keys, GitHub's token — into every ``codex exec``, so every
session carried them in its environment, where any command it ran (and
any test it started) inherited them. A spawned session gets what a shell
and Codex need (Codex finds its auth in ``$HOME/.codex``, or
``CODEX_HOME`` when set), the test database, and the switches that keep a
test run off the live graph and the embedding model. Nothing else, and
never a ``.env``.

What this does not do (review, the same day): keep the same secrets out
of a session's *reach*. Codex's seatbelt allows full-disk reads in every
mode, so a session can still read the live ``.env``, ``~/.codex/auth.json``
and ``backend/.jwt_secret`` by path (a read-only ``codex sandbox`` did).
What comes back is scanned before the turn sees it
(``cc_manager_service.withhold_secrets``); keeping them unreadable needs a
restricted-read profile or a separate macOS user (Evolution v2, later).

PATH is filtered too (review, the same day): the live backend runs from
``start.sh``, which activates ``backend/.venv``, so its PATH carries
``<live repo>/backend/.venv/bin`` — the only ``pip`` and
``python`` on this machine's PATH. A session's ``pip install`` then aimed
at the venv production runs on, one approved escalation away. Every entry
that is, or is inside, Edward's live code or a release is dropped; a
session that needs Edward's dependencies calls that python by its full
path, read-only.

The test database lives on its own Postgres cluster on :5433 with fsync
off (``scripts/test-pg-cluster.sh``). A full suite run on the production
cluster (:5432) made its checkpointer fsync hundreds of thousands of
relation files and starved the live backend's pool (2026-09-26 21:31–21:40
and six earlier days), so a URL on :5432 is never a test URL, whatever
its name.
"""

from __future__ import annotations

import os
from typing import Optional

from services import codex_paths

PRODUCTION_PG_PORT = 5432
TEST_PG_PORT = 5433
DEFAULT_TEST_DATABASE_URL = f"postgresql+asyncpg://edward:edward@localhost:{TEST_PG_PORT}/edward_test"
# Starts (and initialises) the test cluster; ``ensure`` is idempotent.
TEST_CLUSTER_SCRIPT = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir, "scripts", "test-pg-cluster.sh"))


def db_port(url: Optional[str]) -> int:
    """The TCP port ``url`` connects to; Postgres's default when it names none."""
    if not url:
        return PRODUCTION_PG_PORT
    rest = url.split("://", 1)[-1]
    hostpart = rest.split("/", 1)[0].rsplit("@", 1)[-1]
    if hostpart.startswith("["):  # [::1]:5433
        _, _, after = hostpart.partition("]")
        port = after.lstrip(":")
    else:
        port = hostpart.rpartition(":")[2] if ":" in hostpart else ""
    try:
        return int(port) if port else PRODUCTION_PG_PORT
    except ValueError:
        return PRODUCTION_PG_PORT


def _db_name(url: str) -> str:
    return url.rsplit("/", 1)[-1].split("?", 1)[0]


def is_test_db_url(url: Optional[str]) -> bool:
    """True when ``url`` names a ``*_test`` database *off* the production
    cluster — a test database on :5432 still stalls production."""
    if not url:
        return False
    return _db_name(url).endswith("_test") and db_port(url) != PRODUCTION_PG_PORT


def ensure_test_cluster(url: Optional[str], runner=None) -> Optional[str]:
    """Start the test cluster ``url`` connects to, and create its database
    when it is not ``edward_test``; None when that worked or ``url`` is not
    on the test cluster, else what went wrong.

    Only ``with-test-db-lock.sh`` started it, and nothing brings it back
    after a reboot, so the judgement check — a deploy gate — failed on a
    refused connection before any scenario ran (pre-deploy review,
    2026-09-27)."""
    import subprocess

    if not url or db_port(url) != TEST_PG_PORT:
        return None
    run = runner or subprocess.run
    steps = [[TEST_CLUSTER_SCRIPT, "ensure"]]
    db = _db_name(url)
    if db != _db_name(DEFAULT_TEST_DATABASE_URL):
        steps.append([TEST_CLUSTER_SCRIPT, "createdb", db])
    for cmd in steps:
        try:
            done = run(cmd, capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.SubprocessError) as exc:
            return f"could not run {' '.join(cmd)}: {exc}"
        if done.returncode != 0:
            detail = (done.stderr or done.stdout or "").strip().splitlines()
            return (f"{' '.join(cmd)} failed ({done.returncode})"
                    + (f": {detail[-1]}" if detail else "") + " — see /tmp/edward-test-pg.log")
    return None


def refuse_production_cluster(url: Optional[str]) -> None:
    """Raise when ``url`` would put test churn on the production cluster."""
    if db_port(url) == PRODUCTION_PG_PORT:
        raise RuntimeError(
            f"refusing to run tests on the production Postgres cluster (:{PRODUCTION_PG_PORT}); "
            f"edward_test lives on :{TEST_PG_PORT} — start it with scripts/test-pg-cluster.sh ensure"
        )


def safe_test_database_url(inherited: Optional[str]) -> str:
    """The URL a test run may use: the inherited one if it already names a
    test database, else an explicit TEST_DATABASE_URL if *that* does, else
    the default ``edward_test``."""
    if is_test_db_url(inherited):
        return inherited  # type: ignore[return-value]
    explicit = os.getenv("TEST_DATABASE_URL")
    if is_test_db_url(explicit):
        return explicit  # type: ignore[return-value]
    return DEFAULT_TEST_DATABASE_URL


# What a shell and Codex need from ``base``. Each name is passed only when
# set; every ``LC_*`` locale variable rides with LANG.
ALLOWED_ENV = ("PATH", "HOME", "CODEX_HOME", "USER", "LOGNAME", "SHELL", "TMPDIR", "LANG", "LC_ALL", "TERM")
ALLOWED_ENV_PREFIXES = ("LC_",)
# Fixed for every spawned run: a test run never loads the embedding model
# or writes to the live Graphiti graph (tests/conftest.py pins the same).
FIXED_ENV = {
    "EDWARD_FAKE_EMBEDDINGS": "1",
    "GRAPHITI_ENABLED": "false",
    "GRAPHITI_INGEST_ENABLED": "false",
}


def path_without_live_code(path: str) -> str:
    """``path`` without the entries that are, or are inside, a protected root
    (``codex_paths.protected_roots``: the running checkout, the live repo,
    the releases). Relative and empty entries resolve in the session's own
    directory and are left alone."""
    roots = codex_paths.protected_roots()
    kept = []
    for entry in path.split(os.pathsep):
        if entry and os.path.isabs(os.path.expanduser(entry)):
            real = codex_paths._real(entry)
            if any(codex_paths.is_under(real, root) for root in roots):
                continue
        kept.append(entry)
    return os.pathsep.join(kept)


def sandbox_env(base: Optional[dict] = None) -> dict:
    """The allowlisted part of ``base`` (default: ``os.environ``), with
    DATABASE_URL and TEST_DATABASE_URL both set to the test database on the
    test cluster, PATH without Edward's live code, and ``FIXED_ENV`` on
    top."""
    source = os.environ if base is None else base
    env = {
        name: value for name, value in source.items()
        if name in ALLOWED_ENV or name.startswith(ALLOWED_ENV_PREFIXES)
    }
    if "PATH" in env:
        env["PATH"] = path_without_live_code(env["PATH"])
    explicit = source.get("TEST_DATABASE_URL")
    test_url = explicit if is_test_db_url(explicit) else DEFAULT_TEST_DATABASE_URL
    env["DATABASE_URL"] = test_url
    env["TEST_DATABASE_URL"] = test_url
    env.update(FIXED_ENV)
    return env
