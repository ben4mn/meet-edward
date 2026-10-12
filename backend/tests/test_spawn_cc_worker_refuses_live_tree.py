"""``spawn_cc_worker`` never runs Codex in Edward's live code.

Why (Evolution v2 Phase 0, docs/research-2026-09-27/evolution-v2-design.md
§3.2): ``run_codex`` defaulted its cwd to the live ``backend/`` directory
and ran ``codex exec --approve-for-me`` there — write access to the code
production runs from, with no worktree and no gate. The Sep 7 YouTube-lookup
session did exactly that (``claude_code_sessions.cwd`` =
``/Users/me/edward/backend``).

Now a cwd that is, or is inside, a protected root — the repo this code runs
from, ``EDWARD_LIVE_REPO`` (default ``/Users/me/edward``) and the release
tree (``~/edward-releases``) — is refused at every level (the tool, the
orchestrator, ``run_codex``), after resolving symlinks and ``..``. No cwd
means a fresh scratch directory.

Review, the same day: ``cwd="~"`` passed — a parent of the live repo — and
workspace-write makes the whole cwd subtree writable. So a cwd that holds a
protected root is refused too, and the rest is an allowlist: a directory
inside the scratch root, or a registered git worktree on an ``evolve/*`` or
``edward/*`` branch inside a worktrees root (``EDWARD_CODEX_WORKTREE_ROOTS``,
default ``~/edward-evolve``), nothing else.

Pre-deploy review, the same day: the worktrees root was ``~/edward-worktrees``
— where Ellis's own Claude Code worktrees live (chunk1, integ-back, batch-*),
the branches that get merged and deployed — and any directory in it passed,
git worktree or not. An unattended turn could have Codex plant a conftest.py
there that the next pytest run executes unsandboxed. Now Ellis's worktrees
(``EDWARD_HUMAN_WORKTREE_ROOTS``, default ``~/edward-worktrees``) are refused
the way the live repo is, and a worktree must be Edward's own.

2026-10-11: the ``spawn_cc_worker`` tool was deleted (Edward codes in his own
clones with his shell); ``codex_cli_service`` and the orchestrator stay
dormant, so their containment cases stay here.
"""

from __future__ import annotations

import os
import subprocess
import unicodedata

import pytest

from services import codex_cli_service

pytestmark = pytest.mark.unit


@pytest.fixture
def roots(tmp_path, monkeypatch):
    """A fake live repo, a fake home (for ~/edward-releases), a scratch root
    and a worktrees root."""
    live = tmp_path / "live"
    (live / "backend").mkdir(parents=True)
    home = tmp_path / "home"
    home.mkdir()
    scratch = tmp_path / "scratch"
    monkeypatch.setenv("EDWARD_LIVE_REPO", str(live))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("EDWARD_RELEASES_DIR", raising=False)
    monkeypatch.setenv("EDWARD_CODEX_SCRATCH_ROOT", str(scratch))
    monkeypatch.setenv("EDWARD_CODEX_WORKTREE_ROOTS", str(tmp_path / "edward-evolve"))
    monkeypatch.delenv("EDWARD_HUMAN_WORKTREE_ROOTS", raising=False)
    return {"live": live, "home": home, "scratch": scratch, "tmp": tmp_path}


def _git(*args, cwd):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main", *args],
        cwd=str(cwd), check=True, capture_output=True, env=env,
    )


@pytest.fixture
def repo(tmp_path):
    """A repository whose linked worktrees the tests add where they need them."""
    r = tmp_path / "repo"
    r.mkdir()
    _git("init", "-q", cwd=r)
    _git("commit", "-q", "--allow-empty", "-m", "init", cwd=r)
    return r


def _worktree(repo, path, branch="evolve/x"):
    """A real ``git worktree add`` at ``path`` on ``branch``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _git("worktree", "add", "-q", "-b", branch, str(path), cwd=repo)
    return path


# ------------------------------------------------------------- the guard --

def test_the_live_repo_and_anything_inside_it_is_refused(roots):
    live = roots["live"]
    assert codex_cli_service.cwd_refusal(str(live))
    assert codex_cli_service.cwd_refusal(str(live / "backend"))
    assert codex_cli_service.cwd_refusal(str(live / "backend" / "services" / "not_yet_made"))


def test_the_default_live_repo_is_owners_checkout(monkeypatch):
    monkeypatch.delenv("EDWARD_LIVE_REPO", raising=False)
    assert codex_cli_service.cwd_refusal("/Users/me/edward/backend")
    # APFS is case-insensitive: the same directory spelled differently.
    assert codex_cli_service.cwd_refusal("/Users/me/EDWARD/backend")


def test_the_repo_this_code_runs_from_is_refused(roots):
    here = codex_cli_service.running_repo_root()
    assert os.path.isdir(os.path.join(here, "backend", "services"))
    assert codex_cli_service.cwd_refusal(here)
    assert codex_cli_service.cwd_refusal(os.path.join(here, "backend"))


def test_the_release_tree_is_refused(roots):
    releases = roots["home"] / "edward-releases" / "abc123"
    releases.mkdir(parents=True)
    assert codex_cli_service.cwd_refusal(str(releases))
    assert codex_cli_service.cwd_refusal("~/edward-releases/abc123/backend")


def test_a_symlink_or_dotdot_into_the_live_repo_is_refused(roots):
    link = roots["tmp"] / "innocent"
    link.symlink_to(roots["live"] / "backend")
    assert codex_cli_service.cwd_refusal(str(link))
    sneaky = roots["tmp"] / "elsewhere"
    sneaky.mkdir()
    assert codex_cli_service.cwd_refusal(f"{sneaky}/../live/backend")


def test_a_worktree_elsewhere_and_no_cwd_are_allowed(roots, repo):
    worktree = _worktree(repo, roots["tmp"] / "edward-evolve" / "x")
    assert codex_cli_service.cwd_refusal(str(worktree)) is None
    assert codex_cli_service.cwd_refusal(None) is None
    assert codex_cli_service.cwd_refusal("") is None


# A parent of the live repo (review 2026-09-27): workspace-write makes the
# whole cwd subtree writable, so ``cwd="~"`` handed Codex the live repo and
# the releases one level down. The check used to ask only whether the cwd
# sat inside a protected root, never whether a protected root sat inside
# the cwd.

def test_a_parent_of_a_protected_root_is_refused(roots):
    assert codex_cli_service.cwd_refusal("~")  # holds ~/edward-releases
    assert codex_cli_service.cwd_refusal(str(roots["tmp"]))  # holds the live repo
    assert codex_cli_service.cwd_refusal("/")
    assert codex_cli_service.cwd_refusal(f"{roots['live']}/backend/../..")
    error = codex_cli_service.cwd_refusal(str(roots["tmp"]))
    assert "live" in error.lower()


def test_the_parents_of_owners_checkout_are_refused(monkeypatch):
    monkeypatch.delenv("EDWARD_LIVE_REPO", raising=False)
    for cwd in ("/Users/me", "/Users", "/", "/Users/me/edward/backend/../.."):
        assert codex_cli_service.cwd_refusal(cwd), cwd


# The same directory under another name (review 2026-09-27): realpath
# follows symlinks but not APFS firmlinks — /System/Volumes/Data/Users is
# /Users — and APFS ignores Unicode normalization, so a string compare let
# a protected root through spelled differently. Identity (st_dev, st_ino)
# of the cwd and each of its ancestors decides.

def test_owners_checkout_through_the_data_volume_firmlink_is_refused(monkeypatch):
    monkeypatch.delenv("EDWARD_LIVE_REPO", raising=False)
    assert codex_cli_service.cwd_refusal("/System/Volumes/Data/Users/me/edward/backend")


def _firmlinked(path) -> str:
    alias = "/System/Volumes/Data" + os.path.realpath(str(path))
    if not (os.path.exists(alias) and os.path.samefile(alias, path)):
        pytest.skip("no /System/Volumes/Data firmlink on this machine")
    return alias


def test_a_protected_root_named_through_a_firmlink_is_refused(roots, monkeypatch):
    worktrees = roots["tmp"] / "edward-evolve"
    live = worktrees / "running"
    (live / "backend").mkdir(parents=True)
    monkeypatch.setenv("EDWARD_LIVE_REPO", _firmlinked(live))
    assert codex_cli_service.cwd_refusal(str(live / "backend"))


def test_a_cwd_named_through_a_firmlink_is_refused(roots, repo, monkeypatch):
    worktrees = roots["tmp"] / "edward-evolve"
    live = worktrees / "running"
    (live / "backend").mkdir(parents=True)
    _worktree(repo, worktrees / "other")
    monkeypatch.setenv("EDWARD_LIVE_REPO", str(live))
    monkeypatch.setenv("EDWARD_CODEX_WORKTREE_ROOTS", _firmlinked(worktrees))
    assert codex_cli_service.cwd_refusal(_firmlinked(live) + "/backend")
    assert codex_cli_service.cwd_refusal(_firmlinked(worktrees) + "/other") is None


def test_a_protected_root_in_another_unicode_form_is_refused(roots, monkeypatch):
    worktrees = roots["tmp"] / "edward-evolve"
    composed = worktrees / unicodedata.normalize("NFC", "café")
    (composed / "backend").mkdir(parents=True)
    decomposed = str(worktrees / unicodedata.normalize("NFD", "café"))
    if not os.path.samefile(decomposed, composed):
        pytest.skip("this filesystem tells the two spellings apart")
    monkeypatch.setenv("EDWARD_LIVE_REPO", str(composed))
    assert codex_cli_service.cwd_refusal(decomposed + "/backend")


# Only where Codex is meant to work (review 2026-09-27): a denylist of
# Edward's roots can never list everything production runs — apple-mcp in
# ~/apple-mcp, the LaunchAgents, the Homebrew python the live venv is built
# on. A cwd must be a directory inside the scratch root or an Edward
# worktree inside a worktrees root (EDWARD_CODEX_WORKTREE_ROOTS, default
# ~/edward-evolve); nothing else.

def test_only_a_scratch_directory_or_a_worktree_is_allowed(roots):
    home = roots["home"]
    for elsewhere in (home / "apple-mcp", home / "Library" / "LaunchAgents", roots["tmp"] / "somewhere"):
        elsewhere.mkdir(parents=True)
        error = codex_cli_service.cwd_refusal(str(elsewhere))
        assert error and "worktree" in error, elsewhere
    assert codex_cli_service.cwd_refusal("/opt/homebrew/opt/python@3.12")
    scratch = roots["scratch"] / "some-session"
    scratch.mkdir(parents=True)
    assert codex_cli_service.cwd_refusal(str(scratch)) is None


def test_a_worktrees_root_itself_is_not_a_worktree(roots, repo):
    root = roots["tmp"] / "edward-evolve"
    _worktree(repo, root / "x")
    assert codex_cli_service.cwd_refusal(str(root))
    assert codex_cli_service.cwd_refusal(str(roots["scratch"]))


def test_the_worktree_roots_default_to_edward_evolve_in_home(roots, repo, monkeypatch):
    monkeypatch.delenv("EDWARD_CODEX_WORKTREE_ROOTS")
    worktree = _worktree(repo, roots["home"] / "edward-evolve" / "e1", "evolve/e1")
    assert codex_cli_service.cwd_refusal(str(worktree)) is None
    assert codex_cli_service.cwd_refusal(str(_worktree(repo, roots["tmp"] / "edward-evolve" / "x")))


def test_several_worktree_roots_can_be_named(roots, repo, monkeypatch):
    other = roots["tmp"] / "edward-extensions"
    _worktree(repo, other / "weather", "edward/weather")
    monkeypatch.setenv("EDWARD_CODEX_WORKTREE_ROOTS",
                       os.pathsep.join([str(roots["tmp"] / "edward-evolve"), str(other)]))
    assert codex_cli_service.cwd_refusal(str(other / "weather")) is None


# Ellis's worktrees (pre-deploy review 2026-09-27): ~/edward-worktrees holds
# the Claude Code worktrees whose branches are merged and deployed. With it
# as the worktrees root every one of them — and any directory at all in it,
# git or not — was a writable target for an unattended turn.

def test_owners_worktrees_are_refused_by_default(roots, repo, monkeypatch):
    monkeypatch.delenv("EDWARD_CODEX_WORKTREE_ROOTS")
    for name, branch in (("integ-back", "chunk1/integ-back"), ("sneaky", "evolve/sneaky")):
        worktree = _worktree(repo, roots["home"] / "edward-worktrees" / name, branch)
        error = codex_cli_service.cwd_refusal(str(worktree))
        assert error and "Ellis's" in error, name
        assert codex_cli_service.cwd_refusal(str(worktree / "backend"))
    assert codex_cli_service.cwd_refusal(str(roots["home"] / "edward-worktrees" / "newdir"))


def test_owners_worktrees_stay_refused_when_named_as_a_worktrees_root(roots, repo, monkeypatch):
    owners = roots["home"] / "edward-worktrees"
    monkeypatch.setenv("EDWARD_CODEX_WORKTREE_ROOTS", str(owners))
    worktree = _worktree(repo, owners / "evolve-x", "evolve/x")
    error = codex_cli_service.cwd_refusal(str(worktree))
    assert error and "Ellis's" in error


def test_owners_worktrees_root_can_be_named(roots, repo, monkeypatch):
    mine = roots["tmp"] / "owner-trees"
    monkeypatch.setenv("EDWARD_HUMAN_WORKTREE_ROOTS", str(mine))
    monkeypatch.setenv("EDWARD_CODEX_WORKTREE_ROOTS", str(mine))
    assert codex_cli_service.cwd_refusal(str(_worktree(repo, mine / "chunk2", "evolve/chunk2")))


def test_a_directory_that_is_not_a_git_worktree_is_refused(roots):
    root = roots["tmp"] / "edward-evolve"
    (root / "plain").mkdir(parents=True)
    for cwd in (root / "plain", root / "newdir", root / "plain" / "deeper"):
        error = codex_cli_service.cwd_refusal(str(cwd))
        assert error and "worktree" in error, cwd


def test_a_main_checkout_is_not_a_worktree(roots):
    checkout = roots["tmp"] / "edward-evolve" / "clone"
    checkout.mkdir(parents=True)
    _git("init", "-q", "-b", "evolve/clone", cwd=checkout)
    assert codex_cli_service.cwd_refusal(str(checkout))


def test_a_worktree_on_another_branch_is_refused(roots, repo):
    root = roots["tmp"] / "edward-evolve"
    for branch in ("main-copy", "chunk1/stabilize", "evolved/x", "feature/evolve/x"):
        worktree = _worktree(repo, root / branch.replace("/", "-"), branch)
        error = codex_cli_service.cwd_refusal(str(worktree))
        assert error and "evolve/" in error, branch
    detached = root / "detached"
    _git("worktree", "add", "-q", "--detach", str(detached), cwd=repo)
    assert codex_cli_service.cwd_refusal(str(detached))


def test_inside_an_edward_worktree_is_allowed(roots, repo):
    worktree = _worktree(repo, roots["tmp"] / "edward-evolve" / "e2", "edward/e2")
    (worktree / "backend").mkdir()
    assert codex_cli_service.cwd_refusal(str(worktree / "backend")) is None
    assert codex_cli_service.cwd_refusal(str(worktree / "backend" / "not_yet_made")) is None


def test_a_dotgit_file_git_does_not_know_is_refused(roots, repo):
    """A ``.git`` file is only a pointer; the worktree is registered when
    the repository's own record points back at it."""
    real = _worktree(repo, roots["tmp"] / "edward-evolve" / "real", "evolve/real")
    forged = roots["tmp"] / "edward-evolve" / "forged"
    forged.mkdir()
    (forged / ".git").write_text((real / ".git").read_text())
    assert codex_cli_service.cwd_refusal(str(forged))
    loose = roots["tmp"] / "edward-evolve" / "loose"
    loose.mkdir()
    (loose / ".git").write_text(f"gitdir: {roots['tmp']}/nowhere\n")
    assert codex_cli_service.cwd_refusal(str(loose))


async def test_run_codex_refuses_home_and_spawns_nothing(roots, spawned):
    events = [e async for e in codex_cli_service.run_codex("tidy my home dir", cwd="~", model="gpt-6-astra")]
    assert spawned == []
    assert events[-1]["event_type"] == "cc_done" and events[-1]["status"] == "failed"


class _Proc:
    def __init__(self):
        self.stdout = _Stream()
        self.stderr = _Stream()
        self.returncode = 0

    async def wait(self):
        return 0

    def kill(self):
        pass


class _Stream:
    async def readline(self):
        return b""


@pytest.fixture
def spawned(monkeypatch):
    """Record every subprocess run_codex would start; never start one."""
    calls = []

    async def fake_exec(*cmd, **kwargs):
        calls.append({"cmd": list(cmd), **kwargs})
        return _Proc()

    async def no_db(**kwargs):
        return None

    monkeypatch.setattr(codex_cli_service.asyncio, "create_subprocess_exec", fake_exec)
    monkeypatch.setattr(codex_cli_service, "_save_session_to_db", no_db)
    if hasattr(codex_cli_service, "codex_preflight"):
        monkeypatch.setattr(codex_cli_service, "codex_preflight", lambda: {"ok": True, "version": "test"})
    return calls


async def test_run_codex_refuses_a_live_cwd_and_spawns_nothing(roots, spawned):
    events = [e async for e in codex_cli_service.run_codex(
        "tidy up", cwd=str(roots["live"] / "backend"), model="gpt-6-astra")]
    assert spawned == []
    kinds = [e["event_type"] for e in events]
    assert "cc_error" in kinds and "cc_started" not in kinds
    error = next(e for e in events if e["event_type"] == "cc_error")["error"]
    assert "live" in error.lower()
    assert events[-1]["event_type"] == "cc_done" and events[-1]["status"] == "failed"


async def test_run_codex_without_a_cwd_runs_in_a_fresh_scratch_directory(roots, spawned):
    events = [e async for e in codex_cli_service.run_codex("look around", model="gpt-6-astra")]
    assert events[-1]["status"] == "completed"
    assert len(spawned) == 1
    cwd = spawned[0]["cwd"]
    assert cwd.startswith(os.path.realpath(str(roots["scratch"])) + os.sep)
    assert os.path.isdir(cwd)
    cmd = spawned[0]["cmd"]
    assert cmd[cmd.index("-C") + 1] == cwd
    assert codex_cli_service.cwd_refusal(cwd) is None
    # Each session gets its own directory.
    [e async for e in codex_cli_service.run_codex("look again", model="gpt-6-astra")]
    assert spawned[1]["cwd"] != cwd


async def test_run_codex_runs_in_the_resolved_worktree(roots, repo, spawned):
    worktree = _worktree(repo, roots["tmp"] / "edward-evolve" / "x")
    link = roots["tmp"] / "wt-link"
    link.symlink_to(worktree)
    [e async for e in codex_cli_service.run_codex("fix it", cwd=str(link), model="gpt-6-astra")]
    assert spawned[0]["cwd"] == os.path.realpath(str(worktree))


async def test_a_scratch_root_inside_the_live_repo_is_refused_too(roots, spawned, monkeypatch):
    monkeypatch.setenv("EDWARD_CODEX_SCRATCH_ROOT", str(roots["live"] / "scratch"))
    events = [e async for e in codex_cli_service.run_codex("look around", model="gpt-6-astra")]
    assert spawned == []
    assert events[-1]["status"] == "failed"
    assert not (roots["live"] / "scratch").exists()


# ------------------------------------------------ the orchestrator --
# (The spawn_cc_worker tool and its inline streaming path were deleted with
# the toolset, 2026-10-11; the orchestrator service keeps its refusal.)

async def test_spawn_cc_task_refuses_before_making_a_task(roots, monkeypatch):
    from services import orchestrator_service

    async def no_config():
        raise AssertionError("the refusal comes before any config read or task row")

    monkeypatch.setattr(orchestrator_service, "get_config", no_config)
    result = await orchestrator_service.spawn_cc_task(
        parent_conversation_id="c1", task_description="edit main.py", cwd=str(roots["live"] / "backend"))
    assert "error" in result and "live" in result["error"].lower()
