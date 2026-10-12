"""Where Codex may run, and what counts as Edward's live code.

Evolution v2 Phase 0 (2026-09-27). ``run_codex`` used to default its cwd to
the live ``backend/`` and run ``codex exec --approve-for-me`` there — write
access to the code production runs from; the Sep 7 YouTube-lookup session
did exactly that. Every level (the tool, the orchestrator, ``run_codex``)
now asks :func:`cwd_refusal` before anything is spawned; no cwd means a
fresh scratch directory per session.

It is an allowlist (review, the same day): the first version refused only a
cwd *inside* a protected root, so ``cwd="~"`` passed and workspace-write —
whose writable root is the whole cwd subtree — handed Codex the live repo
and the releases one level down. And a denylist can never name everything
production runs: apple-mcp in ``~/apple-mcp``, the LaunchAgents, the
Homebrew python the live venv is built on. So a cwd must be a directory
inside the scratch root or a worktrees root, and must neither sit in a
protected root nor hold one.

Only Edward's own worktrees (pre-deploy review, the same day): the
worktrees root was ``~/edward-worktrees``, which is where the owner's own
Claude Code worktrees live: the branches that get merged and deployed. Any directory in it passed, git or not, so an unattended turn
could have Codex write a conftest.py there that the next pytest run in that
worktree executes unsandboxed, or that the next commit carries to main.
Now the worktrees root is ``~/edward-evolve`` (which only Edward's evolve
flow creates), the owner's worktrees root is refused like a protected root, and a
cwd there must be inside a registered linked git worktree on an
``evolve/*`` or ``edward/*`` branch.

No imports beyond the standard library, ``services.host_config`` and
``services.owner`` (both standard-library only): ``sandbox_env`` reads the protected roots too, and
it is imported by ``tests/conftest.py`` before the test database is chosen.
"""

from __future__ import annotations

import os
from typing import List, Optional

from services.host_config import repo_root
from services.owner import owner_name

LIVE_REPO_ENV = "EDWARD_LIVE_REPO"
# The live checkout when EDWARD_LIVE_REPO is unset: the one this code runs
# from (``host_config.repo_root``, read per call in ``protected_roots``).
DEFAULT_LIVE_REPO = str(repo_root())
RELEASES_ENV = "EDWARD_RELEASES_DIR"
DEFAULT_RELEASES_DIR = "~/edward-releases"
SCRATCH_ROOT_ENV = "EDWARD_CODEX_SCRATCH_ROOT"
DEFAULT_SCRATCH_ROOT = "~/edward-scratch/codex"
# Where a git worktree Codex may build in lives: one or more directories,
# separated by ``os.pathsep``. A worktree is a directory *inside* one; the
# root itself holds every worktree and is not one. Only Edward's evolve flow
# makes worktrees here.
WORKTREE_ROOTS_ENV = "EDWARD_CODEX_WORKTREE_ROOTS"
DEFAULT_WORKTREE_ROOTS = "~/edward-evolve"
# The owner's own worktrees (Claude Code sessions, integration branches): merged
# and deployed, so never a place for Codex, even if named above.
HUMAN_WORKTREE_ROOTS_ENV = "EDWARD_HUMAN_WORKTREE_ROOTS"
DEFAULT_HUMAN_WORKTREE_ROOTS = "~/edward-worktrees"
# The branches an Edward worktree is on.
EDWARD_BRANCH_PREFIXES = ("evolve/", "edward/")


def _real(path: str) -> str:
    """``~``, symlinks and ``..`` resolved — what the path really is."""
    return os.path.realpath(os.path.expanduser(str(path)))


def running_repo_root() -> str:
    """The checkout this code runs from (``<repo>/backend/services/``)."""
    return _real(os.path.join(os.path.dirname(os.path.abspath(__file__)), os.pardir, os.pardir))


def _dedupe(paths: List[str]) -> List[str]:
    out: List[str] = []
    for p in paths:
        if p and p not in out:
            out.append(p)
    return out


def protected_roots() -> List[str]:
    """The repo this code runs from, the live checkout and the release tree."""
    live = str(repo_root())
    releases = (os.getenv(RELEASES_ENV) or "").strip() or DEFAULT_RELEASES_DIR
    return _dedupe([running_repo_root(), _real(live), _real(releases)])


def scratch_root() -> str:
    return _real((os.getenv(SCRATCH_ROOT_ENV) or "").strip() or DEFAULT_SCRATCH_ROOT)


def _roots_from(env: str, default: str) -> List[str]:
    raw = (os.getenv(env) or "").strip() or default
    return _dedupe([_real(p.strip()) for p in raw.split(os.pathsep) if p.strip()])


def worktree_roots() -> List[str]:
    return _roots_from(WORKTREE_ROOTS_ENV, DEFAULT_WORKTREE_ROOTS)


def human_worktree_roots() -> List[str]:
    """Where the owner's own worktrees live."""
    return _roots_from(HUMAN_WORKTREE_ROOTS_ENV, DEFAULT_HUMAN_WORKTREE_ROOTS)


def allowed_roots() -> List[str]:
    """Codex runs only in a directory inside one of these."""
    return _dedupe([scratch_root(), *worktree_roots()])


def _within(path: str, root: str) -> bool:
    # APFS is case-insensitive: ~/EDWARD is ~/edward too.
    # Folding case can only refuse more, never less.
    p, r = path.casefold(), root.rstrip(os.sep).casefold()
    return p == r or p.startswith(r + os.sep)


def _identity(path: str) -> Optional[tuple]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _lineage(path: str):
    """``path``, then each of its parents up to ``/``."""
    while True:
        yield path
        parent = os.path.dirname(path)
        if parent == path:
            return
        path = parent


def is_under(path: str, root: str) -> bool:
    """``path`` is ``root`` or inside it — by name, or by identity: the same
    directory can have several names that realpath leaves alone (review
    2026-09-27): APFS firmlinks (``/System/Volumes/Data/Users`` is
    ``/Users``) and Unicode normalization (APFS ignores it). A path that does
    not exist yet is judged by its nearest existing parent."""
    if _within(path, root):
        return True
    target = _identity(root)
    if target is None:
        return False
    return any(_identity(p) == target for p in _lineage(path))


def is_inside(path: str, root: str) -> bool:
    """``path`` is strictly inside ``root``."""
    return is_under(path, root) and not is_under(root, path)


def _first_line(path: str) -> Optional[str]:
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.readline().strip()
    except (OSError, UnicodeDecodeError):
        return None


def edward_worktree_branch(path: str, root: str) -> Optional[str]:
    """The branch of the registered linked git worktree that holds ``path``
    below ``root``, or None: no ``.git`` between them, a ``.git`` directory
    (a main checkout, whose hooks and config would sit inside the writable
    tree), a ``.git`` file the repository does not point back at (not
    registered), or a detached HEAD. Read from the files git keeps, never by
    running git in a directory Codex may have written to."""
    for top in _lineage(path):
        if not is_inside(top, root):
            return None
        dotgit = os.path.join(top, ".git")
        if not os.path.lexists(dotgit):
            continue
        if os.path.islink(dotgit) or not os.path.isfile(dotgit):
            return None
        pointer = _first_line(dotgit) or ""
        if not pointer.startswith("gitdir:"):
            return None
        admin = pointer[len("gitdir:"):].strip()
        admin = _real(admin if os.path.isabs(admin) else os.path.join(top, admin))
        # A linked worktree's admin directory is <repo>/.git/worktrees/<name>,
        # and its ``gitdir`` file names this worktree's .git back.
        if os.path.basename(os.path.dirname(admin)) != "worktrees":
            return None
        back = _first_line(os.path.join(admin, "gitdir"))
        if not back or _identity(_real(back)) != _identity(dotgit):
            return None
        head = _first_line(os.path.join(admin, "HEAD")) or ""
        prefix = "ref: refs/heads/"
        return head[len(prefix):] if head.startswith(prefix) else None
    return None


def cwd_refusal(cwd: Optional[str]) -> Optional[str]:
    """Why Codex may not run in ``cwd``, or ``None``. No cwd is fine (a
    scratch directory is made for it)."""
    if cwd is None or not str(cwd).strip():
        return None
    real = _real(cwd)
    for root in protected_roots():
        if is_under(real, root):
            return (f"Codex never runs in Edward's live code or a release: {cwd} is inside {root}. "
                    "Leave cwd empty for a scratch directory.")
        if is_under(root, real):
            return (f"Codex never runs in a directory that holds Edward's live code or a release: "
                    f"{cwd} contains {root}. Leave cwd empty for a scratch directory.")
    for root in human_worktree_roots():
        if is_under(real, root) or is_under(root, real):
            return (f"Codex never runs in {owner_name()}'s own worktrees ({root}): their branches are merged "
                    f"and deployed. Leave cwd empty for a scratch directory.")
    if is_inside(real, scratch_root()):
        return None
    prefixes = " or ".join(f"{p}*" for p in EDWARD_BRANCH_PREFIXES)
    where = " or ".join(worktree_roots()) or DEFAULT_WORKTREE_ROOTS
    for root in worktree_roots():
        if not is_inside(real, root):
            continue
        branch = edward_worktree_branch(real, root)
        if branch and branch.startswith(EDWARD_BRANCH_PREFIXES):
            return None
        return (f"Codex runs in {where} only inside a registered git worktree on an {prefixes} "
                f"branch; {cwd} is not one ({'branch ' + branch if branch else 'no such worktree'}). "
                "Leave cwd empty for a scratch directory.")
    return (f"Codex runs only in a scratch directory or a git worktree on an {prefixes} branch "
            f"inside {where}; {cwd} is neither. Leave cwd empty for a scratch directory.")


def scratch_dir_for(session_id: str) -> str:
    """A session's own scratch directory (not created here)."""
    return os.path.join(scratch_root(), session_id)
