"""The four sandbox skills after the computer (AREA 2, 2026-10-11): the
Python, JavaScript and SQL sandboxes are gone (Edward has a real shell),
so their rows report "retired — use bash" without importing anything; the
Shell/Bash row reports Edward's computer (``services.computer.status``)
when it is there."""

import sys
import types

import pytest

from services import skills_service as sk

pytestmark = pytest.mark.unit


def _block(monkeypatch, *names):
    import services

    for n in names:
        monkeypatch.setitem(sys.modules, n, None)   # import raises ImportError
        # ``from services import computer`` reads the package attribute
        # first, so a module another test already imported must go too.
        if n.count(".") == 1 and n.startswith("services."):
            monkeypatch.delattr(services, n.split(".", 1)[1], raising=False)


def _fake_computer(monkeypatch, status):
    import services

    fake = types.ModuleType("services.computer")
    fake.status = status
    monkeypatch.setitem(sys.modules, "services.computer", fake)
    monkeypatch.setattr(services, "computer", fake, raising=False)


@pytest.mark.parametrize("getter", ["_get_code_interpreter_status", "_get_javascript_interpreter_status",
                                    "_get_sql_interpreter_status"])
def test_retired_sandboxes_say_so_without_their_modules(monkeypatch, getter):
    _block(monkeypatch, "services.execution", "services.execution.javascript_execution",
           "services.execution.sql_execution", "services.code_execution_service")
    st = getattr(sk, getter)()
    assert st["status"] == "connected"
    assert "retired" in st["status_message"].lower() and "bash" in st["status_message"]


def test_shell_reports_the_computer(monkeypatch):
    _fake_computer(monkeypatch, lambda: {"sessions": 2, "jobs": 1, "home": "/Users/me/edward-home"})
    st = sk._get_shell_interpreter_status()
    assert st["status"] == "connected" and "2 shell" in st["status_message"]


def test_shell_passes_a_computer_error_through(monkeypatch):
    _fake_computer(monkeypatch, lambda: {"status": "error", "status_message": "sandbox failed: sandbox-exec missing"})
    st = sk._get_shell_interpreter_status()
    assert st == {"status": "error", "status_message": "sandbox failed: sandbox-exec missing"}


def test_shell_without_the_computer_module(monkeypatch):
    _block(monkeypatch, "services.computer", "services.execution", "services.execution.shell_execution")
    st = sk._get_shell_interpreter_status()
    assert st["status"] == "connected" and "bash" in st["status_message"]


# ── The real computer (area 2 integration, 2026-10-11) ────────────────────
# The fakes above guessed the shape of ``computer.status()``; these pin the
# real module's answer to what the Settings row reads.

def test_shell_reports_the_real_computers_sessions(monkeypatch):
    from services.computer import shell

    monkeypatch.setattr(shell, "session_snapshot", lambda: [
        {"conversation_id": "a", "alive": True, "pgid": 1, "cwd": "/x", "idle_s": 3},
        {"conversation_id": "b", "alive": True, "pgid": 2, "cwd": "/y", "idle_s": 9},
    ])
    st = sk._get_shell_interpreter_status()
    assert st["status"] == "connected" and "2 shell sessions open" in st["status_message"]


def test_shell_says_when_the_sandbox_cannot_start(monkeypatch):
    from services.computer import shell

    monkeypatch.delenv("EDWARD_COMPUTER_SANDBOX", raising=False)
    monkeypatch.setattr(shell, "_SANDBOX_EXEC", "/nonexistent/sandbox-exec")
    st = sk._get_shell_interpreter_status()
    assert st["status"] == "error" and "sandbox-exec" in st["status_message"]


def test_shell_says_when_it_runs_unsandboxed(monkeypatch):
    monkeypatch.setenv("EDWARD_COMPUTER_SANDBOX", "off")
    st = sk._get_shell_interpreter_status()
    assert "unsandboxed" in st["status_message"].lower()
