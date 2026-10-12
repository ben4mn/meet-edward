"""A custom MCP server gets an allowlisted environment, then its own env_vars.

Why (Evolution v2 Phase 0, evolution-v2-design.md §3.2.3, 2026-09-27):
``_build_server_launch_spec`` built ``env = {**os.environ, **env_vars}``,
so every ``npx``/``uvx`` server Edward installed — code chosen by a
package name, restarted on every boot — ran with the backend's whole
environment: the live ``DATABASE_URL``, Twilio, VAPID, the JWT secret, the
Cloudflare API tokens, every model key. Now it inherits only what a
package runner needs to start (PATH to find node/uv, HOME for their config,
the user, the shell, TMPDIR, the locale, TERM), then the server's own
``env_vars``, then the runtime cache directories.
"""

from __future__ import annotations

import json

import pytest

from services import custom_mcp_service
from services.database import CustomMCPServerModel

pytestmark = pytest.mark.unit

SECRETS = {
    "DATABASE_URL": "postgresql+asyncpg://edward:edward@localhost:5432/edward",
    "TWILIO_AUTH_TOKEN": "twilio-secret",
    "VAPID_PRIVATE_KEY": "vapid-live",
    "JWT_SECRET_KEY": "jwt-live",
    "OPENAI_API_KEY": "sk-live",
    "ANTHROPIC_API_KEY": "sk-ant-live",
    "CF_API_TOKEN_MASTER": "cf-live",
    "POSTGRES_PASSWORD": "pg-live",
    "FOO_SECRET": "random",
}


@pytest.fixture
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(custom_mcp_service, "MCP_RUNTIME_ROOT", tmp_path / "mcp-runtime")
    for name, value in SECRETS.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setenv("PATH", "/opt/homebrew/bin:/usr/bin:/bin")
    monkeypatch.setenv("HOME", "/Users/me")
    monkeypatch.setenv("TMPDIR", "/var/folders/xx/T/")
    monkeypatch.setenv("LC_CTYPE", "UTF-8")
    monkeypatch.setattr(custom_mcp_service.shutil, "which", lambda name: f"/opt/homebrew/bin/{name}")

    def build(runtime="npx", env_vars=None):
        server = CustomMCPServerModel(
            id="s1", name="thing", package_name="@acme/thing-mcp", runtime=runtime,
            env_vars=json.dumps(env_vars) if env_vars else None, tool_prefix="thing", enabled=True,
        )
        return custom_mcp_service._build_server_launch_spec(server).connection["env"]

    return build


@pytest.mark.parametrize("runtime", ["npx", "uvx"])
def test_no_backend_secret_reaches_a_custom_server(env, runtime):
    spec_env = env(runtime=runtime)
    for name in SECRETS:
        assert name not in spec_env, name
    assert "sk-live" not in " ".join(spec_env.values())


def test_the_servers_own_env_vars_do(env):
    spec_env = env(env_vars={"THING_API_KEY": "its-own", "DATABASE_URL": "sqlite:///its-own.db"})
    assert spec_env["THING_API_KEY"] == "its-own"
    assert spec_env["DATABASE_URL"] == "sqlite:///its-own.db"


def test_what_a_package_runner_needs_does(env):
    spec_env = env()
    assert spec_env["PATH"] == "/opt/homebrew/bin:/usr/bin:/bin"
    assert spec_env["HOME"] == "/Users/me"
    assert spec_env["TMPDIR"] == "/var/folders/xx/T/"
    assert spec_env["LC_CTYPE"] == "UTF-8"


def test_the_runtime_caches_are_still_set(env, tmp_path):
    npx = env(runtime="npx")
    assert npx["npm_config_cache"].startswith(str(tmp_path))
    assert npx["NPM_CONFIG_CACHE"].startswith(str(tmp_path))
    assert npx["XDG_CACHE_HOME"].startswith(str(tmp_path))
    uvx = env(runtime="uvx")
    assert uvx["UV_CACHE_DIR"].startswith(str(tmp_path))
