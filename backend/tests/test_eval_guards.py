"""``eval_guards`` — the process guards every eval runs under (review of
AREA 1, 2026-10-11).

``notice_eval --speak`` once ran the speaker with no process guard, and its
``lookup_contact`` runs ``open -ga Contacts`` and AppleScript on the live
Mac. The judgement check, the ping eval and the noticer eval now all run
under these guards (``install_for_eval``), after taking production's model
settings from ``.env`` (``productions_model_env``).

Nothing here can start a process: every install puts a fake spawner
*beneath* the guard, so a guard bug can never run osascript from a test.
"""

from __future__ import annotations

import socket
import subprocess

import pytest

from services import eval_guards as eg

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("argv", [
    ["osascript", "-e", 'tell application "Contacts" to count people'],
    ["/usr/bin/open", "-ga", "Contacts"],
    ["env", "FOO=1", "osascript", "-e", "x"],
    ["/bin/sh", "-c", "echo hi && open -a Safari"],
    "screencapture -x /tmp/a.png",
    ["nohup", "pkill", "-f", "next-server"],
    ["/bin/zsh", "-lc", "launchctl kickstart -k gui/501/com.meet-edward.backend"],
    ["python3", "-c", "print('Google Chrome')"],
])
def test_what_an_eval_may_never_run(argv):
    assert eg.refusal_reason(argv) is not None


@pytest.mark.parametrize("argv", [
    ["ls", "-la"], ["/bin/sh", "-c", "grep -n open notes.txt | head"], ["git", "status"],
    ["python3", "-c", "print(1)"], ["sips", "-g", "pixelWidth", "a.png"],
])
def test_what_an_eval_may_run(argv):
    assert eg.refusal_reason(argv) is None


def test_the_live_local_services_are_off_limits():
    assert eg.connect_refusal(socket.AF_INET, ("127.0.0.1", 8000))
    assert eg.connect_refusal(socket.AF_INET, ("localhost", 8080))      # the WhatsApp bridge sends
    assert eg.connect_refusal(socket.AF_INET, ("127.0.0.1", 5433)) is None
    assert eg.connect_refusal(socket.AF_INET, ("api.anthropic.com", 443)) is None


class _FakeSpawner(eg.Spawner):
    def __init__(self):
        self.calls = []

        def execute_child(popen, args, executable, *rest, **kw):
            self.calls.append(list(args) if not isinstance(args, (str, bytes)) else [args])
            popen.pid = 999_999
            popen.returncode = 0

        def system(cmd):
            self.calls.append([cmd])
            return 0

        super().__init__(execute_child=execute_child, system=system)


def _real_popen():
    """``subprocess.Popen`` itself. conftest's ``_never_run_osascript``
    (AREA 2) swaps in a subclass that refuses osascript in ``__init__`` —
    before the guard under test is ever reached (met at the AREA 1 + 2
    merge). The fake spawner beneath the guard starts nothing either way."""
    return next(c for c in subprocess.Popen.__mro__
                if c.__module__ == "subprocess" and c.__name__ == "Popen")


@pytest.fixture
def guarded(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "_INSTALLED", None)
    monkeypatch.setattr(subprocess, "Popen", _real_popen())
    spawner = _FakeSpawner()
    env = {}
    g = eg.install(run="t", spawner=spawner, scratch_root=tmp_path, environ=env, chrome=False)
    try:
        yield g, spawner, env
    finally:
        g.uninstall()


def test_installed_guards_refuse_osascript_and_open(guarded):
    g, spawner, env = guarded
    with pytest.raises(eg.EvalGuardRefused):
        subprocess.run(["osascript", "-e", "beep"])
    with pytest.raises(eg.EvalGuardRefused):
        subprocess.run("open -ga Contacts", shell=True)
    import os

    with pytest.raises(eg.EvalGuardRefused):
        os.system("osascript -e beep")
    subprocess.run(["echo", "fine"])
    assert spawner.calls == [["echo", "fine"]], "only the harmless one reached the (fake) spawner"
    assert len(g.refused) == 3
    assert env["EDWARD_HOME"].startswith(str(g.home)) and env["EDWARD_COMPUTER_DIR"] == str(g.state_dir)


def test_installed_guards_refuse_the_live_backend(guarded):
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        with pytest.raises(eg.EvalGuardRefused):
            s.connect(("127.0.0.1", 8000))
    finally:
        s.close()


def test_uninstall_restores_the_process(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "_INSTALLED", None)
    before = (subprocess.Popen._execute_child, socket.socket.connect)
    g = eg.install(run="u", spawner=_FakeSpawner(), scratch_root=tmp_path, environ={}, chrome=False)
    assert subprocess.Popen._execute_child is not before[0]
    g.uninstall()
    assert (subprocess.Popen._execute_child, socket.socket.connect) == before
    assert eg.installed() is None


def test_the_sandbox_may_not_be_off(tmp_path, monkeypatch):
    monkeypatch.setattr(eg, "_INSTALLED", None)
    with pytest.raises(eg.EvalGuardRefused):
        eg.install(run="v", spawner=_FakeSpawner(), scratch_root=tmp_path,
                   environ={"EDWARD_COMPUTER_SANDBOX": "off"}, chrome=False)


async def test_the_chrome_helpers_are_faked(tmp_path, monkeypatch):
    from services import screen_window_service as sw

    monkeypatch.setattr(eg, "_INSTALLED", None)
    before = sw.ensure_screen_tab
    g = eg.install(run="w", spawner=_FakeSpawner(), scratch_root=tmp_path, environ={}, network=False)
    try:
        assert sw.ensure_screen_tab is not before
        assert (await sw.ensure_screen_tab(url="http://x"))["faked"] is True
        assert (await sw.recover_hands())["action"] == "none"
    finally:
        g.uninstall()


def _env_file(tmp_path, name, text):
    path = tmp_path / name
    path.write_text(text)
    return path


def test_the_gate_runs_on_productions_provider(monkeypatch, tmp_path):
    """2026-10-11: a gate run from a bare shell once scored every scenario
    on the code default while production ran another provider. A gate run
    takes production's ``EDWARD_LLM_PROVIDER`` (and the Anthropic key) from
    ``.env`` — this checkout's first, then the live one's — when the shell
    did not choose them, and nothing else from there."""
    here = _env_file(tmp_path, "worktree.env", "SOMETHING_ELSE=1\n")
    live = _env_file(tmp_path, "live.env", "ANTHROPIC_API_KEY=sk-ant-x\nEDWARD_LLM_PROVIDER=codex\n"
                                           "DATABASE_URL=postgresql+asyncpg://e:e@localhost:5432/edward\n")
    env = {}
    assert eg.productions_model_env([here, live], env) == "codex"
    assert env == {"EDWARD_LLM_PROVIDER": "codex", "ANTHROPIC_API_KEY": "sk-ant-x"}


def test_a_provider_the_shell_chose_wins(monkeypatch, tmp_path):
    """The shell's choice (an empty value too) is never overwritten."""
    live = _env_file(tmp_path, "live.env", "EDWARD_LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-ant-x\n")
    env = {"EDWARD_LLM_PROVIDER": "codex", "ANTHROPIC_API_KEY": ""}
    assert eg.productions_model_env([live], env) == "codex"
    assert env == {"EDWARD_LLM_PROVIDER": "codex", "ANTHROPIC_API_KEY": ""}


def test_no_provider_in_env_leaves_the_code_default(monkeypatch, tmp_path):
    """No provider anywhere: none is set, and the code default — Anthropic —
    is what the run uses."""
    from services import claude_routing

    live = _env_file(tmp_path, "live.env", "ANTHROPIC_API_KEY=sk-ant-x\n")
    env = {}
    assert eg.productions_model_env([live, tmp_path / "missing.env"], env) is None
    assert env == {"ANTHROPIC_API_KEY": "sk-ant-x"}
    monkeypatch.delenv("EDWARD_LLM_PROVIDER", raising=False)
    assert claude_routing.llm_provider() == "anthropic"


def test_install_for_eval_takes_productions_model_then_installs_the_guards(monkeypatch):
    calls = []
    monkeypatch.setattr(eg, "productions_model_env", lambda *a, **kw: calls.append("model"))
    monkeypatch.setattr(eg, "install", lambda **kw: calls.append(kw))
    assert eg.install_for_eval("notice") == "in-repo"
    assert calls[0] == "model", "the model settings are read before the guards go on"
    assert len(calls) == 2 and calls[1]["run"].startswith("notice-")
