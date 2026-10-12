"""What Edward spawns to work on code gets an allowlisted environment.

Why (Evolution v2 Phase 0, evolution-v2-design.md §4.4, 2026-09-27):
``sandbox_env()`` used to copy all of ``os.environ`` into ``codex exec`` —
Twilio, VAPID, the JWT secret, the OpenAI/Anthropic keys, GitHub's token —
and only swapped the live ``DATABASE_URL`` for the test database (the
2026-09-07 truncation). A coding session given a task built from a web
page could have read and sent any of them. Now it gets only what a shell
and Codex need — PATH, HOME (Codex finds its auth in ``$HOME/.codex`` or
``CODEX_HOME``), the user, the shell, TMPDIR, the locale, TERM — plus the
test database on the test cluster and the switches that keep a test run
off the live graph and the embedding model. Nothing else, and never a
``.env``.
"""

from __future__ import annotations

import pytest

from services.sandbox_env import DEFAULT_TEST_DATABASE_URL, TEST_PG_PORT, db_port, sandbox_env

pytestmark = pytest.mark.unit

LIVE_URL = "postgresql+asyncpg://edward:edward@localhost:5432/edward"
SECRETS = {
    "TWILIO_AUTH_TOKEN": "twilio-secret",
    "TWILIO_ACCOUNT_SID": "AC123",
    "OPENAI_API_KEY": "sk-live",
    "ANTHROPIC_API_KEY": "sk-ant-live",
    "JWT_SECRET_KEY": "jwt-live",
    "VAPID_PRIVATE_KEY": "vapid-live",
    "GITHUB_TOKEN": "ghp_live",
    "ELEVENLABS_API_KEY": "el-live",
    "BRAVE_SEARCH_API_KEY": "brave-live",
    "HTML_HOSTING_API_KEY": "host-live",
    "LANGCHAIN_API_KEY": "lsv2-live",
    "FOO_SECRET": "random",
    "AWS_SECRET_ACCESS_KEY": "aws",
    "PYTHONPATH": "/Users/me/edward/backend",
    "VIRTUAL_ENV": "/Users/me/edward/backend/.venv",
    "EDWARD_TAINT_APPROVALS": "on",
}
BASICS = {
    "PATH": "/opt/homebrew/bin:/usr/bin:/bin",
    "HOME": "/Users/me",
    "USER": "me",
    "LOGNAME": "me",
    "SHELL": "/bin/zsh",
    "TMPDIR": "/var/folders/xx/T/",
    "LANG": "en_US.UTF-8",
    "LC_ALL": "en_US.UTF-8",
    "LC_CTYPE": "UTF-8",
    "TERM": "xterm-256color",
}


def _base(**extra):
    return {**BASICS, **SECRETS, "DATABASE_URL": LIVE_URL, **extra}


def test_no_secret_reaches_a_spawned_session():
    env = sandbox_env(_base())
    for name in SECRETS:
        assert name not in env, name
    assert "sk-live" not in " ".join(env.values())


def test_what_a_shell_and_codex_need_is_kept():
    env = sandbox_env(_base(CODEX_HOME="/Users/me/.codex"))
    for name, value in BASICS.items():
        assert env[name] == value, name
    assert env["CODEX_HOME"] == "/Users/me/.codex"


def test_codex_home_is_only_passed_when_set():
    assert "CODEX_HOME" not in sandbox_env(_base())


def test_the_database_is_the_test_database_on_the_test_cluster():
    env = sandbox_env(_base(TEST_DATABASE_URL="postgresql+asyncpg://edward:edward@localhost:5432/edward_test"))
    assert env["DATABASE_URL"] == env["TEST_DATABASE_URL"] == DEFAULT_TEST_DATABASE_URL
    assert db_port(env["DATABASE_URL"]) == TEST_PG_PORT
    explicit = "postgresql+asyncpg://edward:edward@localhost:5433/ci_test"
    env = sandbox_env(_base(TEST_DATABASE_URL=explicit))
    assert env["DATABASE_URL"] == env["TEST_DATABASE_URL"] == explicit


def test_a_spawned_test_run_stays_off_the_graph_and_the_embedding_model():
    env = sandbox_env(_base(GRAPHITI_ENABLED="true", GRAPHITI_INGEST_ENABLED="true", EDWARD_FAKE_EMBEDDINGS="0"))
    assert env["EDWARD_FAKE_EMBEDDINGS"] == "1"
    assert env["GRAPHITI_ENABLED"] == "false"
    assert env["GRAPHITI_INGEST_ENABLED"] == "false"


def test_nothing_else_is_passed():
    env = sandbox_env(_base(CODEX_HOME="/c"))
    allowed = set(BASICS) | {"CODEX_HOME", "DATABASE_URL", "TEST_DATABASE_URL",
                             "EDWARD_FAKE_EMBEDDINGS", "GRAPHITI_ENABLED", "GRAPHITI_INGEST_ENABLED"}
    assert set(env) == allowed


def test_the_default_base_is_this_process_environment(monkeypatch):
    monkeypatch.setenv("TWILIO_AUTH_TOKEN", "twilio-secret")
    monkeypatch.setenv("FOO_SECRET", "random")
    env = sandbox_env()
    assert "TWILIO_AUTH_TOKEN" not in env and "FOO_SECRET" not in env
    assert env["PATH"] and env["HOME"]


# PATH without the live tree (review 2026-09-27): the live backend runs from
# start.sh, which activates backend/.venv, so its PATH carries
# /Users/me/edward/backend/.venv/bin — the only `pip` and `python` on
# this machine's PATH. A session's `pip install x` then targeted the venv
# production runs on; the sandbox refuses the write, and --approve-for-me's
# automatic reviewer can approve rerunning a harmless-looking command
# outside it. VIRTUAL_ENV was dropped but the PATH entry kept the effect.

LIVE_REPO = "/Users/me/edward"
LIVE_VENV_BIN = f"{LIVE_REPO}/backend/.venv/bin"


def test_the_live_venv_is_not_on_a_spawned_path(monkeypatch):
    # The live repo is wherever the install names it (the default is this
    # checkout, codex_paths.DEFAULT_LIVE_REPO); name it so the PATH is fixed.
    monkeypatch.setenv("EDWARD_LIVE_REPO", LIVE_REPO)
    path = ":".join(["/Users/me/.local/bin", "/Users/me/bin", LIVE_VENV_BIN,
                     "/Users/me/edward/frontend/node_modules/.bin", "/opt/homebrew/bin", "/usr/bin", "/bin"])
    env = sandbox_env(_base(PATH=path))
    assert env["PATH"] == ":".join(["/Users/me/.local/bin", "/Users/me/bin",
                                    "/opt/homebrew/bin", "/usr/bin", "/bin"])


def test_the_running_checkouts_venv_is_not_on_a_spawned_path_either(monkeypatch, tmp_path):
    from services.codex_paths import running_repo_root

    monkeypatch.setenv("EDWARD_LIVE_REPO", str(tmp_path / "live"))
    running_venv = f"{running_repo_root()}/backend/.venv/bin"
    env = sandbox_env(_base(PATH=f"{running_venv}:{tmp_path}/live/backend/.venv/bin:/usr/bin"))
    assert env["PATH"] == "/usr/bin"


def test_relative_and_empty_path_entries_are_left_alone(monkeypatch):
    # The live repo is wherever the install names it (the default is this
    # checkout, codex_paths.DEFAULT_LIVE_REPO); name it so the PATH is fixed.
    monkeypatch.setenv("EDWARD_LIVE_REPO", LIVE_REPO)
    env = sandbox_env(_base(PATH=f"node_modules/.bin::{LIVE_VENV_BIN}:/usr/bin"))
    assert env["PATH"] == "node_modules/.bin::/usr/bin"


async def test_run_codex_spawns_with_the_allowlisted_environment(monkeypatch, tmp_path):
    from services import codex_cli_service

    seen = {}

    class _Stream:
        async def readline(self):
            return b""

    class _Proc:
        returncode = 0
        stdout = _Stream()
        stderr = _Stream()

        async def wait(self):
            return 0

    async def fake_exec(*cmd, **kwargs):
        seen.update(kwargs)
        return _Proc()

    async def no_db(**kwargs):
        return None

    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("EDWARD_CODEX_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_cli_service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_cli_service, "_save_session_to_db", no_db)
    if hasattr(codex_cli_service, "codex_preflight"):
        monkeypatch.setattr(codex_cli_service, "codex_preflight", lambda: {"ok": True, "version": "test"})
    [e async for e in codex_cli_service.run_codex("look", model="m")]
    env = seen["env"]
    assert not set(SECRETS) & set(env)
    assert env["HOME"] and env["PATH"]
    assert db_port(env["DATABASE_URL"]) == TEST_PG_PORT
