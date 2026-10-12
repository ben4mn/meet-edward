"""Before a Codex session starts: is the CLI there, and does it know every flag?

Why (Evolution v2 Phase 0, evolution-v2-design.md §4.4, 2026-09-27): an
argv the installed CLI does not accept kills every session in under a
second, silently — 0.153 dropping ``--full-auto`` (2026-09-05) and 0.153.2
refusing ``--sandbox`` beside ``--approve-for-me`` (2026-09-07) each did.
``codex_preflight`` runs ``codex --version`` and ``codex exec --help`` and
checks the *argv contract*: every flag ``_build_codex_cmd`` can emit must be
listed. ``run_codex`` refuses to spawn when it fails (an error event), and
says so in the log once, not on every attempt. The parsing and the
comparison are pure; the runner is injected.
"""

from __future__ import annotations

import shutil
import subprocess

import pytest

from services import codex_cli_service
from services.codex_cli_service import argv_contract, codex_preflight, contract_missing, help_flags

pytestmark = pytest.mark.unit

# ``codex exec --help`` from codex-cli 0.153.2, trimmed (every option line kept).
HELP_0153_2 = """Run Codex non-interactively

Usage: codex exec [OPTIONS] [PROMPT]
       codex exec [OPTIONS] <COMMAND> [ARGS]

Commands:
  resume  Resume a previous session by id or pick the most recent with --last
  review  Run a code review against the current repository

Arguments:
  [PROMPT]
          Initial instructions for the agent. If not provided as an argument (or if `-` is used),
          instructions are read from stdin.

Options:
  -c, --config <key=value>
          Override a configuration value that would otherwise be loaded from `~/.codex/config.toml`.
          Examples: - `-c model="o3"` - `-c 'sandbox_permissions=["disk-full-read-access"]'` - `-c
          shell_environment_policy.inherit=all`

      --enable <FEATURE>
      --disable <FEATURE>
      --strict-config
  -i, --image <FILE>...
  -m, --model <MODEL>
          Model the agent should use
      --oss
      --local-provider <OSS_PROVIDER>
  -p, --profile <CONFIG_PROFILE_V2>
  -s, --sandbox <SANDBOX_MODE>
          [possible values: read-only, workspace-write, danger-full-access]
      --approve-for-me
          Route approval requests through automatic review using the workspace-write sandbox
      --dangerously-bypass-approvals-and-sandbox
      --dangerously-bypass-hook-trust
  -C, --cd <DIR>
      --add-dir <DIR>
      --thread-source <SOURCE>
      --skip-git-repo-check
          Allow running Codex outside a Git repository
      --ephemeral
          Run without persisting session files to disk
      --ignore-user-config
          Do not load `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME`
      --ignore-rules
      --output-schema <FILE>
      --color <COLOR>
      --json
          Print events to stdout as JSONL
  -o, --output-last-message <FILE>
  -h, --help
  -V, --version
"""


def _without(help_text: str, flag: str) -> str:
    return "\n".join(line for line in help_text.splitlines() if flag not in line.split())


# ------------------------------------------------------------------ pure --

def test_help_flags_reads_every_option_line():
    flags = help_flags(HELP_0153_2)
    for flag in ("-c", "--config", "-m", "--model", "-s", "--sandbox", "-C", "--cd", "--json",
                 "--approve-for-me", "--ephemeral", "--ignore-user-config", "--skip-git-repo-check",
                 "--output-schema", "-o", "--output-last-message"):
        assert flag in flags, flag
    assert "--full-auto" not in flags
    # Prose and examples are not options.
    assert "--last" not in flags and "-" not in flags


def test_the_contract_is_every_flag_the_builder_can_emit():
    contract = argv_contract()
    assert set(contract) == {"--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                             "--skip-git-repo-check", "-s", "-m", "--config", "-C"}


def test_the_real_0153_2_help_satisfies_the_contract():
    assert contract_missing(HELP_0153_2) == []


def test_a_cli_without_a_flag_breaks_the_contract():
    assert contract_missing(_without(HELP_0153_2, "--ephemeral")) == ["--ephemeral"]
    old = _without(_without(HELP_0153_2, "--ignore-user-config"), "--ignore-rules")
    assert set(contract_missing(old)) == {"--ignore-user-config", "--ignore-rules"}


# ------------------------------------------------------------ preflight --

class _Runner:
    def __init__(self, version="codex-cli 0.153.2", help_text=HELP_0153_2, missing_binary=False, rc=0):
        self.calls = []
        self.version, self.help_text, self.missing_binary, self.rc = version, help_text, missing_binary, rc

    def __call__(self, argv, **kwargs):
        self.calls.append(list(argv))
        if self.missing_binary:
            raise FileNotFoundError(2, "No such file or directory", "codex")
        out = self.version if argv[1:] == ["--version"] else self.help_text
        return subprocess.CompletedProcess(argv, self.rc, stdout=out + "\n", stderr="")


@pytest.fixture(autouse=True)
def _fresh_cache():
    codex_cli_service.reset_preflight()
    yield
    codex_cli_service.reset_preflight()


def test_preflight_passes_on_0153_2():
    runner = _Runner()
    result = codex_preflight(run=runner)
    assert result["ok"] is True and result["version"] == "codex-cli 0.153.2" and result["missing"] == []
    assert ["codex", "--version"] in runner.calls and ["codex", "exec", "--help"] in runner.calls


def test_preflight_fails_when_a_flag_is_missing():
    result = codex_preflight(run=_Runner(help_text=_without(HELP_0153_2, "--skip-git-repo-check")))
    assert result["ok"] is False and result["missing"] == ["--skip-git-repo-check"]
    assert "--skip-git-repo-check" in result["error"]


def test_preflight_fails_when_codex_is_not_installed():
    result = codex_preflight(run=_Runner(missing_binary=True))
    assert result["ok"] is False and "not installed" in result["error"]


def test_preflight_fails_when_the_cli_errors():
    result = codex_preflight(run=_Runner(rc=1))
    assert result["ok"] is False


def test_preflight_is_cached_until_forced():
    runner = _Runner()
    codex_preflight(run=runner)
    codex_preflight(run=runner)
    assert len(runner.calls) == 2
    codex_preflight(run=runner, force=True)
    assert len(runner.calls) == 4


# A failure is not kept for the full five minutes (review 2026-09-27): one
# timeout under load, an EAGAIN, or the moment `brew upgrade --cask codex`
# swaps the Caskroom symlink refused every Codex session for 300 s though
# the CLI answered again within seconds. A success is kept 300 s, a failure
# PREFLIGHT_FAILURE_TTL_SECONDS.

class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_a_failed_preflight_is_probed_again_soon(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(codex_cli_service.time, "monotonic", clock)
    assert codex_preflight(run=_Runner(missing_binary=True))["ok"] is False
    healthy = _Runner()
    clock.now += 5
    assert codex_preflight(run=healthy)["ok"] is False  # still the failure: no probe storm
    assert healthy.calls == []
    clock.now += codex_cli_service.PREFLIGHT_FAILURE_TTL_SECONDS
    assert codex_preflight(run=healthy)["ok"] is True
    assert codex_cli_service.PREFLIGHT_FAILURE_TTL_SECONDS < codex_cli_service.PREFLIGHT_TTL_SECONDS


def test_a_success_is_still_kept_for_the_full_ttl(monkeypatch):
    clock = _Clock()
    monkeypatch.setattr(codex_cli_service.time, "monotonic", clock)
    healthy = _Runner()
    codex_preflight(run=healthy)
    clock.now += codex_cli_service.PREFLIGHT_TTL_SECONDS - 1
    assert codex_preflight(run=_Runner(missing_binary=True))["ok"] is True
    assert len(healthy.calls) == 2


def test_the_same_failure_after_a_recovery_is_logged_again(capsys):
    broken = _Runner(missing_binary=True)
    codex_cli_service._log_preflight_failure(codex_preflight(run=broken, force=True))
    codex_cli_service._log_preflight_failure(codex_preflight(run=broken, force=True))
    assert codex_preflight(run=_Runner(), force=True)["ok"] is True
    codex_cli_service._log_preflight_failure(codex_preflight(run=broken, force=True))
    assert capsys.readouterr().out.count("preflight failed") == 2


@pytest.mark.skipif(shutil.which("codex") is None, reason="codex CLI not installed here")
def test_the_installed_cli_satisfies_the_contract():
    """Reads ``codex --version`` / ``codex exec --help`` only; never runs a task."""
    result = codex_preflight(force=True)
    assert result["ok"], result


# ------------------------------------------------------------ run_codex --

async def test_run_codex_refuses_to_spawn_when_the_preflight_fails_and_logs_once(monkeypatch, tmp_path, capsys):
    spawned = []

    async def fake_exec(*cmd, **kwargs):
        spawned.append(cmd)
        raise AssertionError("nothing is spawned when the preflight fails")

    async def no_db(**kwargs):
        return None

    monkeypatch.setenv("EDWARD_CODEX_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_cli_service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_cli_service, "_save_session_to_db", no_db)
    monkeypatch.setattr(codex_cli_service, "codex_preflight", lambda: {
        "ok": False, "version": "codex-cli 0.160.0", "missing": ["--ephemeral"],
        "error": "codex exec no longer accepts --ephemeral"})
    for _ in range(2):
        events = [e async for e in codex_cli_service.run_codex("look", model="m")]
        assert spawned == []
        error = next(e for e in events if e["event_type"] == "cc_error")["error"]
        assert "--ephemeral" in error
        assert events[-1]["event_type"] == "cc_done" and events[-1]["status"] == "failed"
    out = capsys.readouterr().out
    assert out.count("preflight failed") == 1
