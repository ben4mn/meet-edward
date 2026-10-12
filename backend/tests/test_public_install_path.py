"""A fresh Mac must be able to import the backend. postgresql@16 is
keg-only, so a fresh `brew install` puts neither libpq nor pg_config where
pure-python psycopg looks, and `import psycopg` (the LangGraph checkpointer,
at startup) failed with "no pq wrapper available". Two layers fix it:
psycopg-binary (its own libpq) pinned to psycopg's version, and Postgres
16's bin on the PATH of backend/start.sh, the graph worker and the
LaunchAgents ahead of Homebrew's (psycopg's pg_config fallback, and psql
for the scripts). The system's own directories come first (and the venv's
bin before them in the two Python services): these processes run outside
Edward's sandbox, whose shell can write Homebrew's bin (review
2026-10-12)."""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import stat
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FAKE_BREW = "/opt/fakebrew"
PG_BIN = f"{FAKE_BREW}/opt/postgresql@16/bin"
SYSTEM = ["/usr/bin", "/bin", "/usr/sbin", "/sbin"]


def _pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text().splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)(?:\[[^\]]*\])?==([^\s;#]+)", line.strip())
        if m:
            pins[m.group(1).lower()] = m.group(2)
    return pins


def test_psycopg_binary_is_pinned_to_psycopgs_own_version():
    constraints = _pins(BACKEND / "constraints.txt")
    requirements = _pins(BACKEND / "requirements.txt")
    assert "psycopg" in constraints
    assert constraints.get("psycopg-binary") == constraints["psycopg"]
    # requirements.txt asks for it (so a venv build installs it), at the same version.
    assert requirements.get("psycopg-binary") == constraints["psycopg"]


def _executable(path: Path, text: str) -> None:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _sandbox(tmp_path: Path, script: str) -> tuple[Path, Path]:
    """A checkout with only `script`, the shared shell helpers and a stub venv;
    a fake bin dir whose uvicorn and sleep record the PATH they were run with."""
    (tmp_path / "backend" / ".venv" / "bin").mkdir(parents=True)
    (tmp_path / "scripts" / "lib").mkdir(parents=True)
    shutil.copy(BACKEND / script, tmp_path / "backend" / script)
    shutil.copy(ROOT / "scripts" / "lib" / "edward-env.sh", tmp_path / "scripts" / "lib" / "edward-env.sh")
    (tmp_path / "backend" / ".venv" / "bin" / "activate").write_text("")
    # start.sh reinstalls dependencies when requirements.txt is newer than the marker.
    req = tmp_path / "backend" / "requirements.txt"
    req.write_text("")
    marker = tmp_path / "backend" / ".venv" / ".deps_installed"
    marker.write_text("")
    past = time.time() - 60
    os.utime(req, (past, past))
    fake = tmp_path / "fakebin"
    fake.mkdir()
    out = tmp_path / "path.txt"
    recorder = f'#!/bin/sh\nprintf "%s" "$PATH" > "{out}"\nexit 0\n'
    _executable(fake / "uvicorn", recorder)
    _executable(fake / "sleep", recorder)
    # The scripts run the venv's uvicorn by absolute path, and their PATH
    # starts with the venv's bin (never the caller's): the recorders go there.
    venv_bin = tmp_path / "backend" / ".venv" / "bin"
    _executable(venv_bin / "uvicorn", recorder)
    _executable(venv_bin / "sleep", recorder)
    (tmp_path / "home").mkdir()
    return fake, out


def _run(tmp_path: Path, script: str, extra_env: dict[str, str] | None = None) -> list[str]:
    fake, out = _sandbox(tmp_path, script)
    env = {
        "PATH": f"{fake}:/usr/bin:/bin",
        "HOME": str(tmp_path / "home"),
        "HOMEBREW_PREFIX": FAKE_BREW,
    }
    env.update(extra_env or {})
    proc = subprocess.run(
        ["/bin/bash", str(tmp_path / "backend" / script)],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert out.exists(), proc.stdout + proc.stderr
    return out.read_text().split(":")


def _venv_bin(tmp_path: Path) -> str:
    return f"{tmp_path}/backend/.venv/bin"


def test_start_sh_puts_postgres16s_bin_ahead_of_homebrews(tmp_path):
    path = _run(tmp_path, "start.sh")
    assert path[0] == _venv_bin(tmp_path)
    assert path[1:5] == SYSTEM
    assert path[5] == PG_BIN
    assert path[6] == f"{FAKE_BREW}/bin"
    assert str(tmp_path / "fakebin") not in path, "never the caller's PATH"


def test_start_sh_takes_another_postgres16_from_edward_pg_bin(tmp_path):
    path = _run(tmp_path, "start.sh", {"EDWARD_PG_BIN": "/opt/pg16/bin"})
    assert path[1:6] == SYSTEM + ["/opt/pg16/bin"]


def test_the_graph_worker_puts_postgres16s_bin_ahead_of_homebrews(tmp_path):
    # GRAPHITI_ENABLED unset: the worker sleeps (the recorder) and exits
    # before it touches /tmp/edward-graph-worker.log.
    path = _run(tmp_path, "start-graph-worker.sh", {"GRAPHITI_ENABLED": "false"})
    assert path[0] == _venv_bin(tmp_path)
    assert path[1:6] == SYSTEM + [PG_BIN]


def test_the_launchagents_path_puts_the_system_then_postgres16s_bin_first(tmp_path):
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "HOMEBREW_PREFIX": FAKE_BREW,
        "EDWARD_ENV_FILE": str(tmp_path / "no-such.env"),
        "EDWARD_LAUNCHAGENTS_DIR": str(tmp_path / "agents"),
        "INSTALL_AGENTS_LIB_ONLY": "1",
    }
    for svc in ("backend", "frontend", "graph-worker", "healthcheck"):
        proc = subprocess.run(
            ["/bin/bash", "-c", 'f="$1" svc="$2"; . "$f"; render "$svc"', "_",
             str(ROOT / "scripts" / "install-launchagents.sh"), svc],
            env=env, capture_output=True, timeout=60,
        )
        assert proc.returncode == 0, proc.stderr
        plist = plistlib.loads(proc.stdout)
        path = plist["EnvironmentVariables"]["PATH"].split(":")
        assert path[:4] == SYSTEM, (svc, path)
        assert path[4] == PG_BIN, (svc, path)
        assert path[5] == f"{FAKE_BREW}/bin", (svc, path)
        assert plist["EnvironmentVariables"]["HOMEBREW_PREFIX"] == FAKE_BREW
