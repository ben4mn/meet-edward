"""backend/start.sh, backend/start-graph-worker.sh and frontend/run-launchd.sh:
what the unsandboxed processes start with (review 2026-10-12).

* No ``.env`` secret in the start-up environment: any process of the same
  user (Edward's sandboxed shell included) can read another's with sysctl
  KERN_PROCARGS2. The backend and the worker load ``.env`` themselves once
  running; the scripts read only the settings they need, and hand over the
  ``~`` paths ``.env`` holds expanded, as sourcing it used to.
* PATH: the venv's bin, then the system's own directories, then Postgres
  16's tools and Homebrew's — never ``~/.local/bin`` or ``~/bin``, which
  the shell can write; uvicorn and next are run by absolute path.

Each script runs from a scratch copy of the checkout with stub programs
(``uvicorn``, ``node``) that record what they were started with. Nothing
here starts a server, touches a live log, or reads the real ``.env``."""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SECRETS = ("sk-ant-api03-START-SCRIPT-CANARY", "jwt-canary-0123456789", "pw-canary")

DOTENV = f"""# a test .env
ANTHROPIC_API_KEY={SECRETS[0]}
JWT_SECRET_KEY='{SECRETS[1]}'
DATABASE_URL=postgresql://edward:{SECRETS[2]}@localhost:5432/edward
EDWARD_BACKEND_HOST=127.0.0.1
EDWARD_PHOTOS_DIR=~/edward-photos
export EDWARD_SCRATCH_DIR=~/scratch-here
QUOTED_TILDE="~/stays-as-written"
TOKENIZERS_PARALLELISM=true
GRAPHITI_ENABLED=true
"""

RECORDER = """#!/bin/bash
{ echo "ARGV $*"; echo "CWD $PWD"; env; } > "$EDWARD_TEST_OUT"
"""


def _checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "backend" / ".venv" / "bin").mkdir(parents=True)
    (repo / "scripts" / "lib").mkdir(parents=True)
    (repo / "frontend" / "node_modules" / "next" / "dist" / "bin").mkdir(parents=True)
    for rel in ("backend/start.sh", "backend/start-graph-worker.sh", "scripts/lib/edward-env.sh",
                "frontend/run-launchd.sh"):
        shutil.copy(ROOT / rel, repo / rel)
    (repo / ".env").write_text(DOTENV)
    (repo / "backend" / "requirements.txt").write_text("")
    marker = repo / "backend" / ".venv" / ".deps_installed"
    marker.write_text("")
    later = time.time() + 60
    os.utime(marker, (later, later))
    uvicorn = repo / "backend" / ".venv" / "bin" / "uvicorn"
    uvicorn.write_text(RECORDER)
    uvicorn.chmod(0o755)
    (repo / "frontend" / "node_modules" / "next" / "dist" / "bin" / "next").write_text("// stub\n")
    ensure = repo / "frontend" / "ensure-build.sh"
    ensure.write_text("#!/bin/bash\nexit 0\n")
    return repo


def _home(tmp_path: Path) -> Path:
    """A home whose ~/.local/bin and ~/bin hold planted programs."""
    home = tmp_path / "home"
    for d in (home / ".local" / "bin", home / "bin"):
        d.mkdir(parents=True)
        for name in ("uvicorn", "node", "npm", "curl", "sips"):
            p = d / name
            p.write_text("#!/bin/bash\necho PLANTED > \"$EDWARD_TEST_OUT.planted\"\n")
            p.chmod(0o755)
    return home


def _brew(tmp_path: Path) -> Path:
    brew = tmp_path / "brew"
    (brew / "bin").mkdir(parents=True)
    node = brew / "bin" / "node"
    node.write_text(RECORDER)
    node.chmod(0o755)
    return brew


def _run(script: Path, tmp_path: Path, **extra):
    out = tmp_path / "recorded.txt"
    home = _home(tmp_path)
    env = {
        "HOME": str(home),
        # A login shell's PATH with the planted directories first.
        "PATH": f"{home}/.local/bin:{home}/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOMEBREW_PREFIX": str(_brew(tmp_path)),
        "EDWARD_TEST_OUT": str(out),
        **extra,
    }
    r = subprocess.run(["/bin/bash", str(script)], env=env, capture_output=True, text=True, timeout=60)
    recorded = out.read_text() if out.exists() else ""
    return r, recorded, home


def _env_of(recorded: str) -> dict:
    env = {}
    for line in recorded.splitlines():
        if "=" in line and not line.startswith(("ARGV ", "CWD ")):
            k, v = line.split("=", 1)
            env[k] = v
    return env


def _argv(recorded: str) -> str:
    return next(l for l in recorded.splitlines() if l.startswith("ARGV "))[5:]


def _assert_safe_path(path: str, home: Path, first: str) -> None:
    parts = path.split(":")
    assert parts[0] == first, path
    system = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]
    start = 1 if first != "/usr/bin" else 0
    assert parts[start:start + 4] == system, path
    assert f"{home}/.local/bin" not in parts and f"{home}/bin" not in parts, path


def test_the_backend_starts_without_dotenv_in_its_environment(tmp_path):
    repo = _checkout(tmp_path)
    r, recorded, home = _run(repo / "backend" / "start.sh", tmp_path)
    assert recorded, r.stdout + r.stderr
    assert not (tmp_path / "recorded.txt.planted").exists(), "a planted program ran"
    for secret in SECRETS:
        assert secret not in recorded, secret
    env = _env_of(recorded)
    assert "ANTHROPIC_API_KEY" not in env and "JWT_SECRET_KEY" not in env and "DATABASE_URL" not in env
    # The ~ paths, expanded as bash's source used to; a quoted ~ stays the backend's to read.
    assert env["EDWARD_PHOTOS_DIR"] == f"{home}/edward-photos"
    assert env["EDWARD_SCRATCH_DIR"] == f"{home}/scratch-here"
    assert "QUOTED_TILDE" not in env
    # What the process needs at start comes from here: the environment, then .env, then the default.
    assert env["TOKENIZERS_PARALLELISM"] == "true"
    assert env["OBJC_DISABLE_INITIALIZE_FORK_SAFETY"] == "YES"
    venv = os.path.realpath(repo / "backend" / ".venv")
    _assert_safe_path(env["PATH"], home, first=f"{venv}/bin")
    assert _argv(recorded) == "main:app --host 127.0.0.1 --port 8000 --log-config log_config.json"
    assert "Database: localhost:5432/edward" in r.stdout
    assert SECRETS[2] not in r.stdout


def test_the_environment_still_wins_over_dotenv(tmp_path):
    repo = _checkout(tmp_path)
    r, recorded, home = _run(repo / "backend" / "start.sh", tmp_path,
                             EDWARD_BACKEND_HOST="0.0.0.0", EDWARD_PHOTOS_DIR="/srv/photos")
    env = _env_of(recorded)
    assert "--host 0.0.0.0" in _argv(recorded)
    assert env["EDWARD_PHOTOS_DIR"] == "/srv/photos"


def test_the_graph_worker_starts_the_same_way(tmp_path):
    repo = _checkout(tmp_path)
    log = tmp_path / "graph-worker.log"
    r, recorded, home = _run(repo / "backend" / "start-graph-worker.sh", tmp_path,
                             EDWARD_GRAPH_WORKER_LOG=str(log))
    assert recorded, r.stdout + r.stderr
    assert not (tmp_path / "recorded.txt.planted").exists()
    for secret in SECRETS:
        assert secret not in recorded, secret
    env = _env_of(recorded)
    assert "GRAPHITI_ENABLED" not in env, "read for the decision, never exported"
    venv = os.path.realpath(repo / "backend" / ".venv")
    _assert_safe_path(env["PATH"], home, first=f"{venv}/bin")
    assert _argv(recorded).startswith("workers.graph_worker:app --host 127.0.0.1 --port 8001")


def test_the_frontend_runs_next_by_absolute_path_on_a_safe_path(tmp_path):
    repo = _checkout(tmp_path)
    log = tmp_path / "frontend.log"
    r, recorded, home = _run(repo / "frontend" / "run-launchd.sh", tmp_path, EDWARD_FRONTEND_LOG=str(log))
    assert recorded, log.read_text() if log.exists() else r.stderr
    assert not (tmp_path / "recorded.txt.planted").exists(), "a planted node ran"
    env = _env_of(recorded)
    _assert_safe_path(env["PATH"], home, first="/usr/bin")
    next_bin = os.path.realpath(repo / "frontend" / "node_modules" / "next" / "dist" / "bin" / "next")
    argv = _argv(recorded)
    assert argv.split()[0] in (next_bin, str(repo / "frontend" / "node_modules" / "next" / "dist" / "bin" / "next"))
    assert argv.split()[1:] == ["start", "-p", "3000"]


def test_the_launchagents_path_puts_the_system_first():
    script = ROOT / "scripts" / "install-launchagents.sh"
    r = subprocess.run(
        ["/bin/bash", "-c", '. "$1"; render healthcheck', "_", str(script)],
        env={"HOME": "/Users/nobody", "PATH": "/usr/bin:/bin", "HOMEBREW_PREFIX": "/opt/fakebrew",
             "INSTALL_AGENTS_LIB_ONLY": "1", "EDWARD_ENV_FILE": "/nonexistent/.env"},
        capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    assert ("<string>/usr/bin:/bin:/usr/sbin:/sbin:/opt/fakebrew/opt/postgresql@16/bin:/opt/fakebrew/bin:"
            "/opt/fakebrew/sbin:/usr/local/bin</string>") in r.stdout
    assert "/.local/bin" not in r.stdout
    assert "<key>HOMEBREW_PREFIX</key>" in r.stdout


@pytest.mark.parametrize("rel", ["backend/start.sh", "backend/start-graph-worker.sh", "frontend/run-launchd.sh"])
def test_no_script_sources_dotenv(rel):
    text = (ROOT / rel).read_text()
    code = "\n".join(l for l in text.splitlines() if not l.lstrip().startswith("#"))
    assert "source ../.env" not in code and "source .env" not in code and "set -a" not in code
    assert ".local/bin" not in code and "$HOME/bin" not in code
