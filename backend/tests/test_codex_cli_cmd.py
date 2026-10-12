"""``codex exec`` argv must match the installed CLI.

codex-cli 0.153 dropped ``--full-auto``; every evolution cycle on
2026-09-05 died with "unexpected argument '--full-auto'". The equivalent
is ``--approve-for-me`` (automatic approval review inside the
workspace-write sandbox). 0.153.2 then rejected *combining* it with
``--sandbox`` ("the argument '--sandbox <SANDBOX_MODE>' cannot be used
with '--approve-for-me'"), which killed the 2026-09-07 00:09 cycle in
0.8 s — ``--approve-for-me`` already implies workspace-write.

Evolution v2 Phase 0 (2026-09-27): the backend's Codex must never inherit
Ellis's own Codex setup — his ``~/.codex/config.toml`` enables the
computer-use, unified-computer-use, chrome and browser plugins, an MCP
server and a ``notify`` hook — so every session passes
``--ignore-user-config`` (auth still comes from ``CODEX_HOME``) and
``--ephemeral`` (no session files). The default cwd is a scratch
directory, not a git repo, and without ``--skip-git-repo-check`` codex
exec refuses to start there ("Not inside a trusted directory"; the trust
Ellis gave /Users/me/edward lives in the config it no longer reads). A
read-only request is ``-s read-only``; a write request is ``-s
workspace-write``, and neither may escalate (``approval_policy="never"``,
no ``--approve-for-me``).
Every flag is in ``codex exec --help`` for 0.153.2 — an unknown flag kills
every session (``codex_cli_service.codex_preflight`` checks that at run
time).
"""

import pytest

from services.codex_cli_service import _build_codex_cmd

pytestmark = pytest.mark.unit


def test_no_removed_full_auto_flag():
    cmd = _build_codex_cmd("do a thing")
    assert "--full-auto" not in cmd


def test_a_write_request_is_workspace_write_that_never_escalates():
    """No ``--approve-for-me`` (2026-09-27): it routes every request to leave
    the sandbox — a write outside the worktree, the network — to an
    automatic reviewer that can say yes, and while the taint gate runs in
    shadow a tainted turn can still start Codex. ``approval_policy="never"``
    turns such a request into a plain failure instead; a smoke run on
    0.153.2 wrote inside its scratch directory and got "operation not
    permitted" writing to ~."""
    cmd = _build_codex_cmd("do a thing", model="gpt-5.6-terra", cwd="/tmp/wt")
    assert cmd[:2] == ["codex", "exec"] and cmd[-2:] == ["--", "do a thing"]
    assert "--json" in cmd
    assert "--approve-for-me" not in cmd
    assert "--dangerously-bypass-approvals-and-sandbox" not in cmd
    assert cmd[cmd.index("-s") + 1] == "workspace-write"
    configs = [cmd[i + 1] for i, t in enumerate(cmd) if t in ("-c", "--config")]
    assert 'approval_policy="never"' in configs
    assert cmd[cmd.index("-m") + 1] == "gpt-5.6-terra"
    assert cmd[cmd.index("-C") + 1] == "/tmp/wt"


def test_a_read_only_request_never_escalates_either():
    cmd = _build_codex_cmd("look only", read_only=True)
    configs = [cmd[i + 1] for i, t in enumerate(cmd) if t in ("-c", "--config")]
    assert 'approval_policy="never"' in configs


@pytest.mark.parametrize("read_only", [False, True])
def test_never_reads_owners_codex_config_or_keeps_session_files(read_only):
    cmd = _build_codex_cmd("do a thing", model="gpt-5.6-terra", cwd="/tmp/wt", read_only=read_only)
    assert "--ignore-user-config" in cmd
    # --ignore-user-config skips only config.toml; execpolicy .rules (an
    # "always allow" Ellis gives in his own Codex, a .codex/rules on a
    # worktree branch) are a separate switch (review 2026-09-27).
    assert "--ignore-rules" in cmd
    assert "--ephemeral" in cmd
    assert "--skip-git-repo-check" in cmd
    assert cmd[:2] == ["codex", "exec"] and cmd[-2:] == ["--", "do a thing"]


def test_a_read_only_request_is_a_read_only_sandbox_without_approve_for_me():
    cmd = _build_codex_cmd("look only", model="gpt-5.6-terra", cwd="/tmp/wt", read_only=True)
    assert cmd[cmd.index("-s") + 1] == "read-only"
    assert "--approve-for-me" not in cmd
    assert "--sandbox" not in cmd
    assert "--json" in cmd
    assert cmd[cmd.index("-m") + 1] == "gpt-5.6-terra"
    assert cmd[cmd.index("-C") + 1] == "/tmp/wt"


# The task is a positional after ``--`` (review 2026-09-27): it used to sit
# right after ``exec``, so clap read a task that starts with "-" — a
# markdown bullet list — as an option ("error: unexpected argument '- '
# found", every time, in under a second), a task that is exactly "review",
# "resume", "fork" or "help" as that subcommand, and a task that is one
# flag-shaped token as that flag.

@pytest.mark.parametrize("task", [
    "- Update foo.py\n- run tests",
    "review",
    "resume",
    "--dangerously-bypass-approvals-and-sandbox",
    "-c sandbox_mode=danger-full-access",
])
@pytest.mark.parametrize("read_only", [False, True])
def test_the_task_is_only_ever_the_positional_after_a_double_dash(task, read_only):
    cmd = _build_codex_cmd(task, model="gpt-5.6-terra", cwd="/tmp/wt", read_only=read_only)
    assert cmd[:2] == ["codex", "exec"]
    assert cmd[-2:] == ["--", task]
    assert cmd.index("--") == len(cmd) - 2
    assert task not in cmd[:-1]


def test_every_flag_comes_before_the_double_dash():
    from services.codex_cli_service import argv_contract

    cmd = _build_codex_cmd("probe", model="m", instructions_path="/i", cwd="/c")
    head = cmd[2:cmd.index("--")]
    # ``--config`` repeats (approval_policy, model_instructions_file); the
    # contract lists each flag once.
    flags = list(dict.fromkeys(t for t in head if t.startswith("-")))
    assert flags == [t for t in argv_contract() if t in head]


# Instructions go through ``model_instructions_file`` (review 2026-09-27):
# the builder wrote ``experimental_instructions_file``, a key 0.153.2 does
# not know — its binary carries ``model_instructions_file`` and not the old
# name, and an unknown ``-c`` key is dropped without a word unless
# ``--strict-config``, so the preflight passed while the instructions were
# ignored.

def test_instructions_go_through_model_instructions_file():
    cmd = _build_codex_cmd("do a thing", instructions_path="/tmp/instr.md")
    configs = [cmd[i + 1] for i, t in enumerate(cmd) if t in ("-c", "--config")]
    assert "model_instructions_file=/tmp/instr.md" in configs
    assert not any("experimental_instructions_file" in token for token in cmd)


async def test_run_codex_passes_a_system_prompt_as_model_instructions(monkeypatch, tmp_path):
    from services import codex_cli_service

    spawned = []

    async def fake_exec(*cmd, **kwargs):
        spawned.append(list(cmd))
        return _Proc()

    async def no_db(**kwargs):
        return None

    monkeypatch.setenv("EDWARD_CODEX_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_cli_service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_cli_service, "_save_session_to_db", no_db)
    monkeypatch.setattr(codex_cli_service, "codex_preflight", lambda: {"ok": True, "version": "test"})
    [e async for e in codex_cli_service.run_codex("look", model="m", system_prompt="Be brief.")]
    cmd = spawned[0]
    configs = [cmd[i + 1] for i, t in enumerate(cmd) if t in ("-c", "--config")]
    assert any(value.startswith("model_instructions_file=") for value in configs)


# ------------------------------------------ read_only reaches the argv --

class _Proc:
    returncode = 0

    def __init__(self):
        self.stdout = _Stream()
        self.stderr = _Stream()

    async def wait(self):
        return 0

    def kill(self):
        pass


class _Stream:
    async def readline(self):
        return b""


async def test_run_codex_passes_read_only_to_the_argv(monkeypatch, tmp_path):
    from services import codex_cli_service

    spawned = []

    async def fake_exec(*cmd, **kwargs):
        spawned.append(list(cmd))
        return _Proc()

    async def no_db(**kwargs):
        return None

    monkeypatch.setenv("EDWARD_CODEX_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_cli_service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_cli_service, "_save_session_to_db", no_db)
    if hasattr(codex_cli_service, "codex_preflight"):
        monkeypatch.setattr(codex_cli_service, "codex_preflight", lambda: {"ok": True, "version": "test"})
    [e async for e in codex_cli_service.run_codex("look", model="m", read_only=True)]
    [e async for e in codex_cli_service.run_codex("change", model="m")]
    assert spawned[0][spawned[0].index("-s") + 1] == "read-only" and "--approve-for-me" not in spawned[0]
    assert spawned[1][spawned[1].index("-s") + 1] == "workspace-write" and "--approve-for-me" not in spawned[1]


async def test_the_cc_manager_passes_read_only_to_run_codex(monkeypatch):
    import asyncio
    from services import cc_manager_service, codex_cli_service

    seen = {}

    async def fake_run_codex(**kwargs):
        seen.update(kwargs)
        yield {"event_type": "cc_done", "status": "completed"}

    monkeypatch.setattr(codex_cli_service, "run_codex", fake_run_codex)
    await cc_manager_service._run_cc_session("t1", "conv", "look", None, None, 25, asyncio.Queue(), read_only=True)
    assert seen["read_only"] is True


async def test_spawn_cc_for_task_passes_read_only_on(monkeypatch):
    from services import cc_manager_service, conversation_service

    seen = {}

    async def fake_session(*args, **kwargs):
        seen.update(kwargs)
        return "ok"

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(cc_manager_service, "_run_cc_session", fake_session)
    monkeypatch.setattr(cc_manager_service, "_update_task_field", noop)
    monkeypatch.setattr(cc_manager_service, "_update_task_status", noop)
    monkeypatch.setattr(conversation_service, "create_conversation", noop)
    await cc_manager_service.spawn_cc_for_task(
        task_id="t1", parent_conversation_id="p", task_description="look", read_only=True)
    assert seen.get("read_only") is True


@pytest.mark.integration
async def test_spawn_cc_task_passes_read_only_to_the_cc_manager(clean_db, monkeypatch):
    from services import cc_manager_service, orchestrator_service

    seen = {}

    async def fake_spawn(**kwargs):
        seen.update(kwargs)

    async def enabled():
        return {"enabled": True}

    monkeypatch.setattr(orchestrator_service, "get_config", enabled)
    monkeypatch.setattr(cc_manager_service, "spawn_cc_for_task", fake_spawn)
    await orchestrator_service.spawn_cc_task(
        parent_conversation_id="p", task_description="look", read_only=True, wait=True)
    assert seen["read_only"] is True
