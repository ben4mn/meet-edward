"""The persistent sandboxed shell. These run real ``bash`` under the
rendered Seatbelt profile against scratch paths only — never :5432, never a
live path, never osascript or Chrome."""

from __future__ import annotations

import os
import shutil
import time

import pytest

sandbox_missing = not os.path.exists("/usr/bin/sandbox-exec")
pytestmark = pytest.mark.skipif(sandbox_missing, reason="sandbox-exec not present")


@pytest.fixture
def home_env(tmp_path, monkeypatch):
    ehome = tmp_path / "edward-home"
    (ehome / "tmp").mkdir(parents=True, exist_ok=True)
    (ehome / "jobs").mkdir(parents=True, exist_ok=True)
    (ehome / ".out").mkdir(parents=True, exist_ok=True)
    # A stub venv python so init skips the slow `python -m venv`.
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
    # Fresh pool per test.
    from services.computer import shell
    shell._pool = shell.SessionPool()
    return {"home": ehome}


async def _run(cmd=None, cid="t-shell", **kw):
    from services.computer import shell
    return await shell.run(cid, command=cmd, **kw)


@pytest.mark.asyncio
async def test_persistent_cwd_and_env(home_env):
    await _run("mkdir -p sub && cd sub")
    r = await _run("pwd")
    assert r.text.strip().endswith("/sub")
    assert r.exit_code == 0


@pytest.mark.asyncio
async def test_nonzero_exit_is_normal(home_env):
    r = await _run("grep nothere /dev/null; echo done")
    assert r.exit_code == 0  # last command (echo) succeeded
    r2 = await _run("false")
    assert r2.exit_code == 1 and not r2.timed_out and not r2.killed


@pytest.mark.asyncio
async def test_stdin_cannot_eat_the_sentinel(home_env):
    # `cat` with no redirect would read the shell's stdin; the harness feeds
    # it /dev/null, so it returns instead of swallowing the sentinel.
    r = await _run("echo hi | cat")
    assert "hi" in r.text and r.exit_code == 0


@pytest.mark.asyncio
async def test_his_shell_is_pointed_at_no_database(home_env, monkeypatch):
    """Review 2026-10-12: the test cluster's OS-user superuser trusts
    loopback, so the shell no longer gets a DATABASE_URL or libpq's PG*
    (the profile refuses :5432 and :5433), whatever the backend's own
    environment holds."""
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://edward:edward@localhost:5432/edward")
    monkeypatch.setenv("PGPORT", "5433")
    monkeypatch.setenv("PGPASSWORD", "hunter2")
    r = await _run('echo "DBURL=[$DATABASE_URL] TEST=[$TEST_DATABASE_URL] PG=[$PGHOST$PGPORT$PGUSER$PGDATABASE$PGPASSWORD]"')
    assert "DBURL=[] TEST=[] PG=[]" in r.text, r.text


@pytest.mark.asyncio
async def test_no_secrets_in_env(home_env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-should-not-leak-xxxxxxxxxxxx")
    r = await _run('echo "val=[$ANTHROPIC_API_KEY]"')
    assert "val=[]" in r.text


@pytest.mark.asyncio
async def test_output_redaction(home_env, monkeypatch):
    secret = "Zx9Yw8Vu7Ts6Rq5Po4Nm3Lk2"
    monkeypatch.setenv("FAKE_SERVICE_TOKEN", secret)
    from services.computer import shell
    r = await _run(f"echo 'leaked {secret}'")
    shown = shell.format_for_model(r)
    assert secret not in shown
    assert "[redacted:" in shown


@pytest.mark.asyncio
async def test_restart_resets(home_env):
    await _run("cd tmp")
    from services.computer import shell
    r = await shell.run("t-shell", command=None, restart=True)
    assert "restarted" in r.text
    r2 = await _run("pwd")
    # Back at edward-home root, not tmp.
    assert r2.text.strip().rstrip("/").endswith("edward-home")


@pytest.mark.asyncio
async def test_precheck_refuses_protected_brew(home_env):
    from services.toolset.result import ToolFailure
    with pytest.raises(ToolFailure):
        await _run("brew upgrade postgresql@16")


@pytest.mark.asyncio
async def test_syntax_error_is_quick(home_env):
    from services.toolset.result import ToolFailure
    start = time.time()
    with pytest.raises(ToolFailure) as exc:
        await _run("echo 'unbalanced")
    assert time.time() - start < 10
    assert "syntax" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_timeout_kills_the_group(home_env):
    r = await _run("sleep 20 & echo PID=$!; sleep 20", timeout_s=2)
    assert r.timed_out
    pid = None
    for line in r.text.splitlines():
        if line.startswith("PID="):
            pid = int(line.split("=", 1)[1])
    assert pid is not None
    # Give the group kill a moment, then the background sleep must be gone.
    time.sleep(0.5)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


@pytest.mark.asyncio
async def test_output_cap_and_spill(home_env):
    from services.computer import shell
    r = await _run("for i in $(seq 1 40000); do echo 'xxxxxxxxxxxxxxxx'; done", timeout_s=30)
    shown = shell.format_for_model(r)
    assert "full output:" in shown
    assert len(shown) < shell.OUTPUT_CAP + 2000


def test_startup_sweep_spares_unrelated_process(home_env, tmp_path):
    import subprocess
    from services.computer import shell, home
    proc = subprocess.Popen(["sleep", "60"], start_new_session=True)
    try:
        pgid = os.getpgid(proc.pid)
        sdir = home.computer_dir() / "sessions"
        sdir.mkdir(parents=True, exist_ok=True)
        pf = sdir / "stale.pid"
        pf.write_text(f"{pgid}\nsome-other-session\n{time.time()}\n")
        shell.sweep_stale_sessions()
        # The unrelated process is still alive, and the pidfile is gone.
        assert proc.poll() is None
        assert not pf.exists()
    finally:
        proc.kill()
        proc.wait()


class TestEviction:
    """A full pool never kills a command that is running in another thread
    (review 2026-10-11: a long build was cut off mid-way and reported as
    the command having exited)."""

    @pytest.mark.asyncio
    async def test_a_busy_session_is_never_evicted(self, home_env, monkeypatch):
        import asyncio

        from services.computer import shell
        from services.toolset.result import ToolFailure

        monkeypatch.setattr(shell, "MAX_SESSIONS", 1)
        long = asyncio.create_task(shell.run("t-long", command="echo started; sleep 3; echo finished"))
        await asyncio.sleep(1.0)
        with pytest.raises(ToolFailure, match="busy"):
            await shell.run("t-other", command="echo other")
        r = await long
        assert r.exit_code == 0 and not r.killed and not r.exited
        assert "finished" in r.text

    @pytest.mark.asyncio
    async def test_an_idle_session_makes_room(self, home_env, monkeypatch):
        from services.computer import shell

        monkeypatch.setattr(shell, "MAX_SESSIONS", 1)
        await shell.run("t-a", command="echo a")
        r = await shell.run("t-b", command="echo b")
        assert r.exit_code == 0 and "b" in r.text
        assert [s["conversation_id"] for s in shell._pool.snapshot()] == ["t-b"]

    @pytest.mark.asyncio
    async def test_a_harness_kill_mid_command_is_a_tool_error(self, home_env):
        import asyncio

        from services.computer import shell

        session = shell.ShellSession("t-kill")
        await session.start()
        running = asyncio.create_task(session.run("echo started; sleep 5; echo finished"))
        await asyncio.sleep(1.0)
        await session.kill(reason="evicted for another thread")
        r = await running
        assert r.killed and not r.exited and r.kill_reason == "evicted for another thread"
        assert shell.format_for_model(r).startswith("Tool error: the command was killed — evicted")
