"""``bash``'s harness outcomes under the toolset's failure contract (area 2
integration, 2026-10-11).

A non-zero exit is a normal result; a timeout, a kill for memory or disk,
or a shell that died is a ``Tool error`` the model reads *and* the loop
flags (``status="error"``, the ledger's failure count). The executor
decides that from ``result.normalize``, which looks at the start of the
text, so the error line must lead even when there is partial output.
Real ``bash`` under the rendered profile, scratch paths only.
"""

from __future__ import annotations

import os

import pytest

from services.computer import shell
from services.computer.shell import ShellResult
from services.toolset.result import normalize


def _shown(r: ShellResult):
    return normalize(shell.format_for_model(r))


class TestFormat:
    def test_a_timeout_with_partial_output_is_an_error(self):
        out = _shown(ShellResult(text="partial line", exit_code=-1, cwd="/h", timed_out=True, killed=True))
        assert out.is_error and out.text.startswith("Tool error:")
        assert "partial line" in out.text and "ran past" in out.text

    def test_a_kill_for_memory_is_an_error_that_says_why(self):
        r = ShellResult(text="building…", exit_code=-1, cwd="/h", killed=True,
                        kill_reason="it used over 2048 MB of memory")
        out = _shown(r)
        assert out.is_error and "2048 MB of memory" in out.text and "building…" in out.text

    def test_a_shell_that_exited_is_not_an_error(self):
        out = _shown(ShellResult(text="bye", exit_code=-1, cwd="/h", killed=True, exited=True))
        assert not out.is_error
        assert "bye" in out.text and "fresh shell" in out.text

    def test_a_nonzero_exit_is_not_an_error(self):
        out = _shown(ShellResult(text="", exit_code=1, cwd="/h"))
        assert not out.is_error and "[exit 1" in out.text


sandbox_missing = not os.path.exists("/usr/bin/sandbox-exec")


@pytest.fixture
def home_env(tmp_path, monkeypatch):
    ehome = tmp_path / "edward-home"
    for d in ("tmp", "jobs", ".out"):
        (ehome / d).mkdir(parents=True, exist_ok=True)
    vbin = ehome / ".venv" / "bin"
    vbin.mkdir(parents=True, exist_ok=True)
    stub = vbin / "python"
    stub.write_text("#!/bin/sh\nexit 0\n")
    stub.chmod(0o755)
    monkeypatch.setenv("EDWARD_HOME", str(ehome))
    monkeypatch.setenv("EDWARD_COMPUTER_DIR", str(tmp_path / ".edward-computer"))
    monkeypatch.setenv("EDWARD_LIVE_REPO", str(tmp_path / "edward"))
    monkeypatch.setenv("EDWARD_RELEASES_DIR", str(tmp_path / "edward-releases"))
    monkeypatch.setenv("EDWARD_COMPUTER_SANDBOX", "on")
    (tmp_path / "edward").mkdir(parents=True, exist_ok=True)
    shell._pool = shell.SessionPool()
    return ehome


@pytest.mark.skipif(sandbox_missing, reason="sandbox-exec not present")
class TestRealShell:
    async def test_exit_ends_the_session_without_an_error(self, home_env):
        try:
            r = await shell.run("t-err", command="echo bye; exit 3")
            out = _shown(r)
            assert not out.is_error and "bye" in out.text and "fresh shell" in out.text
            again = await shell.run("t-err", command="echo back")
            assert again.exit_code == 0 and "back" in again.text
        finally:
            await shell.shutdown_all()

    async def test_the_memory_watchdog_kill_is_an_error(self, home_env, monkeypatch):
        monkeypatch.setattr(shell, "MAX_RSS_MB", 1)
        monkeypatch.setattr(shell, "_WATCH_SLICE_S", 0.5)
        try:
            r = await shell.run("t-err", command="sleep 5", timeout_s=20)
            out = _shown(r)
            assert out.is_error and "memory" in out.text
        finally:
            await shell.shutdown_all()
