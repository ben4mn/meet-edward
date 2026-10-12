"""Rails: can_read / can_write, the precheck, the rail hint, and parity
between the in-process checks and the rendered Seatbelt profile."""

from __future__ import annotations

import os

import pytest


@pytest.fixture
def home_env(tmp_path, monkeypatch):
    """Point Edward's home, computer dir, live repo and releases at scratch
    paths so no test ever touches the real ones."""
    ehome = tmp_path / "edward-home"
    cdir = tmp_path / ".edward-computer"
    repo = tmp_path / "edward"          # stands in for the live repo
    releases = tmp_path / "edward-releases"
    evolve = tmp_path / "edward-evolve"
    for p in (ehome, cdir, repo, releases, evolve):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("EDWARD_HOME", str(ehome))
    monkeypatch.setenv("EDWARD_COMPUTER_DIR", str(cdir))
    monkeypatch.setenv("EDWARD_LIVE_REPO", str(repo))
    monkeypatch.setenv("EDWARD_RELEASES_DIR", str(releases))
    monkeypatch.setenv("EDWARD_COMPUTER_WORKTREE_ROOTS", str(evolve))
    return {"home": ehome, "cdir": cdir, "repo": repo, "releases": releases, "evolve": evolve}


def test_reads_allowed_everywhere_but_his_own_credentials(home_env):
    """Ellis's ruling: reads are allowed everywhere (output is redacted) —
    except the files that would let code in his shell act as the owner
    (2026-10-12 review): the session key, the setup code and .env."""
    from services.computer import rails
    assert rails.can_read("/Users/me/.ssh/id_rsa") is True
    assert rails.can_read(home_env["home"] / "notes.md") is True
    assert rails.can_read(home_env["repo"] / ".env.example") is True
    assert rails.can_read(home_env["repo"] / "backend" / "main.py") is True
    for rel in (".env", "backend/.env", "backend/.jwt_secret", "backend/.setup_code"):
        assert rails.can_read(home_env["repo"] / rel) is False, rel


def test_the_running_checkouts_credentials_are_refused_too(home_env):
    """The backend reads its key next to its own code, which is the live
    repo in production; both are covered when they differ."""
    from pathlib import Path

    from services.computer import rails
    checkout = Path(rails.__file__).resolve().parents[3]
    assert rails.can_read(checkout / "backend" / ".jwt_secret") is False
    assert rails.can_read(checkout / ".env") is False
    assert str((checkout / "backend" / ".setup_code").resolve()) in rails.read_deny_files()


def test_a_symlink_or_a_relative_path_cannot_reach_a_credential(home_env):
    from services.computer import rails
    secret = home_env["repo"] / "backend" / ".jwt_secret"
    secret.parent.mkdir(parents=True, exist_ok=True)
    secret.write_text("k")
    os.symlink(secret, home_env["home"] / "key")
    assert rails.can_read(home_env["home"] / "key") is False
    assert rails.can_read("key") is False
    assert rails.can_read(home_env["repo"] / "backend" / ".." / "backend" / ".jwt_secret") is False


def test_the_credentials_cannot_be_written_either(home_env):
    from services.computer import rails
    for rel in (".env", "backend/.jwt_secret", "backend/.setup_code"):
        assert rails.can_write(home_env["repo"] / rel) is False, rel
        assert "credentials" in rails.write_refusal(home_env["repo"] / rel)


def test_write_denies_live_repo_releases_and_own_config(home_env):
    from services.computer import rails
    assert rails.can_write(home_env["repo"] / "backend" / "main.py") is False
    assert rails.can_write(home_env["releases"] / "r1" / "x") is False
    assert rails.can_write(home_env["cdir"] / "profile.sb") is False


def test_write_allowed_in_his_home_and_siblings(home_env):
    from services.computer import rails
    assert rails.can_write(home_env["home"] / "projects" / "a.py") is True
    assert rails.can_write(home_env["evolve"] / "edward" / "x.py") is True
    # A sibling that merely shares the repo's prefix is not the repo.
    sibling = home_env["repo"].parent / "edward-home" / "x"
    assert rails.can_write(sibling) is True


def test_write_denies_production_kegs_and_pgdata(home_env):
    from services.computer import rails
    assert rails.can_write("/opt/homebrew/var/postgresql@16/base") is False
    assert rails.can_write("/opt/homebrew/Cellar/python@3.12/3.12.13/bin/x") is False
    assert rails.can_write("/opt/homebrew/opt/postgresql@16/bin/psql") is False
    # Ordinary Homebrew prefix stays writable (installs need it).
    assert rails.can_write("/opt/homebrew/lib/node_modules/x") is True


def test_symlink_cannot_escape_write_deny(home_env):
    """A symlink in his home pointing at the live repo is still a repo write."""
    from services.computer import rails
    link = home_env["home"] / "sneaky"
    os.symlink(home_env["repo"], link)
    assert rails.can_write(link / "main.py") is False


def test_tilde_and_relative_resolve_against_home(home_env):
    from services.computer import rails
    # A relative path resolves under ~/edward-home → writable.
    assert rails.can_write("projects/a.py") is True


def test_precheck_brew_upgrade_without_formula(home_env):
    from services.computer import rails
    assert rails.precheck("brew upgrade") is not None
    assert "python@3.12" in rails.precheck("brew upgrade")


def test_precheck_protected_formulae(home_env):
    from services.computer import rails
    assert rails.precheck("brew upgrade postgresql@16") is not None
    assert rails.precheck("brew reinstall python@3.12") is not None
    assert rails.precheck("brew uninstall postgresql@16") is not None
    # A different formula is fine.
    assert rails.precheck("brew install ffmpeg") is None
    assert rails.precheck("brew upgrade ffmpeg") is None


def test_precheck_protected_formulae_every_spelling(home_env):
    """``rm`` is brew's alias of uninstall, and ``unlink``/``link``/``pin``
    move the keg's links the backups and the FDA python run through (area 2
    integration, 2026-10-11; tools-spec §2.2)."""
    from services.computer import rails
    for cmd in ("brew rm postgresql@16", "brew unlink python@3.12", "brew link --overwrite python@3.12",
                "brew unpin postgresql@16", "brew upgrade python@3.12 node"):
        assert rails.precheck(cmd) is not None, cmd
    assert rails.precheck("brew unlink node") is None


def test_precheck_rm_rf_homebrew_core(home_env):
    from services.computer import rails
    assert rails.precheck("rm -rf /opt/homebrew/Cellar") is not None
    assert rails.precheck("rm -rf /opt/homebrew/bin/*") is not None
    # rm inside his home is fine.
    assert rails.precheck("rm -rf ~/edward-home/tmp/x") is None


def test_precheck_disk_rail(home_env, monkeypatch):
    from services.computer import rails

    class _Usage:
        free = 1 * 1024 ** 3  # 1 GB free

    monkeypatch.setattr(rails.shutil, "disk_usage", lambda _p: _Usage())
    assert rails.precheck("pip install torch") is not None
    # Cleanup commands still run.
    assert rails.precheck("rm -rf ~/edward-home/.out") is None
    assert rails.precheck("du -sh ~/edward-home") is None


def test_rail_hint_matches_the_right_rail(home_env):
    from services.computer import rails
    h = rails.rail_hint("psql: Operation not permitted connecting to :5432")
    assert h and "5432" in h
    h2 = rails.rail_hint("open -a 'Google Chrome': Operation not permitted")
    assert h2 and "chrome" in h2.lower()
    # Ordinary output never gets a hint.
    assert rails.rail_hint("hello world\n") is None


def test_rail_hint_names_the_credentials_and_the_api_rails(home_env):
    from services.computer import rails
    h = rails.rail_hint("cat: /x/edward/backend/.jwt_secret: Operation not permitted")
    assert h and "credentials" in h
    h = rails.rail_hint("PermissionError: [Errno 1] Operation not permitted: '/x/edward/.env'")
    assert h and "credentials" in h
    # curl does not say EPERM for a refused connect.
    h = rails.rail_hint("curl: (7) Failed to connect to localhost port 8000 after 0 ms: Couldn't connect to server")
    assert h and "8000" in h
    h = rails.rail_hint("curl: (7) Failed to connect to 192.168.1.20 port 3000 after 1 ms: Couldn't connect to server")
    assert h and "API" in h
    assert rails.rail_hint("curl: (7) Failed to connect to example.com port 443 after 3 ms: Timeout") is None


def test_profile_write_deny_parity(home_env):
    """Every subpath the profile denies writes on is denied by can_write, and
    his own home is writable — the two views agree."""
    from services.computer import rails, profile
    text = profile.render_profile()
    assert "(allow default)" in text
    for root in rails.write_deny_roots():
        assert rails.can_write(os.path.join(root, "x")) is False
        assert f'(subpath "{root}")' in text
    assert rails.can_write(home_env["home"] / "ok.txt") is True


def test_profile_read_deny_parity(home_env):
    """Every credential the profile refuses is refused by can_read and
    can_write, and every one can_read refuses is in the profile."""
    import re

    from services.computer import profile, rails
    text = profile.render_profile()
    line = next(l for l in text.splitlines() if l.startswith("(deny file-read-data file-write* file-link (literal"))
    literals = re.findall(r'\(literal "([^"]+)"\)', line)
    assert sorted(literals) == sorted(rails.read_deny_files())
    for path in literals:
        assert rails.can_read(path) is False and rails.can_write(path) is False


def test_profile_refuses_the_app_ports_on_any_address(home_env):
    from services.computer import profile, rails
    text = profile.render_profile()
    assert rails.APP_PORTS == (8000, 3000)
    for port in rails.APP_PORTS:
        assert f'(deny network-outbound (remote tcp "*:{port}"))' in text


# ---------------------------------------------------- review 2026-10-12 --
# macOS volumes usually compare names case-insensitively and realpath keeps
# the case the caller typed, so a string comparison let
# <repo>/BACKEND/.JWT_SECRET through to the real key. The rails compare
# what is on disk.

def _folds_case(path) -> bool:
    try:
        return os.pathconf(str(path), 11) == 0  # _PC_CASE_SENSITIVE on macOS
    except (OSError, ValueError):
        return False


case_insensitive_only = pytest.mark.skipif(
    not _folds_case(os.path.realpath(os.getenv("TMPDIR", "/tmp"))),
    reason="the temp volume compares names case-sensitively")


def _upper(path) -> str:
    """The same path with every letter upper-cased (a case variant)."""
    return str(path).upper()


@case_insensitive_only
@pytest.mark.parametrize("rel", ["backend/.jwt_secret", "backend/.setup_code", ".env", "backend/.env"])
def test_a_case_variant_of_a_credential_is_still_his_own_credential(home_env, rel):
    from services.computer import rails
    target = home_env["repo"] / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("not-for-edward")
    for spelling in (_upper(target), str(home_env["repo"]) + "/" + rel.upper(),
                     str(home_env["repo"]) + "/" + rel.replace("backend", "Backend")):
        assert rails.can_read(spelling) is False, spelling
        assert rails.can_write(spelling) is False, spelling
        assert "credentials" in rails.write_refusal(spelling)


@case_insensitive_only
def test_a_case_variant_of_a_missing_setup_code_is_refused_too(home_env):
    """``.setup_code`` exists only until the first password is set: a
    spelling of the file that doesn't exist yet is still the file."""
    from services.computer import rails
    (home_env["repo"] / "backend").mkdir(parents=True, exist_ok=True)
    assert not (home_env["repo"] / "backend" / ".setup_code").exists()
    for spelling in (_upper(home_env["repo"] / "backend" / ".setup_code"),
                     str(home_env["repo"]) + "/backend/.Setup_Code"):
        assert rails.can_read(spelling) is False, spelling
        assert rails.can_write(spelling) is False, spelling


@case_insensitive_only
def test_a_case_variant_of_the_live_repo_is_the_live_repo(home_env):
    from services.computer import rails
    (home_env["repo"] / "backend").mkdir(parents=True, exist_ok=True)
    (home_env["repo"] / "backend" / "main.py").write_text("print('live')")
    for spelling in (_upper(home_env["repo"] / "backend" / "main.py"),
                     _upper(home_env["repo"]) + "/backend/new_file.py",
                     _upper(home_env["releases"]) + "/r1/x"):
        assert rails.can_write(spelling) is False, spelling
    assert "live code" in rails.write_refusal(_upper(home_env["repo"] / "backend" / "main.py"))
    # His home stays his, in any spelling.
    assert rails.can_write(_upper(home_env["home"]) + "/projects/a.py") is True


@case_insensitive_only
def test_the_running_checkout_is_write_protected_in_any_spelling(home_env):
    """The checkout this code runs from is protected even when it is not
    EDWARD_LIVE_REPO (its backend/ was renamable, review 2026-10-12)."""
    from services.computer import rails
    checkout = rails._CODE_CHECKOUT
    assert str(checkout) in rails.write_deny_roots()
    assert rails.can_write(checkout / "backend" / "main.py") is False
    assert rails.can_write(_upper(checkout / "backend" / "main.py")) is False
    assert rails.can_read(_upper(checkout / "backend" / ".jwt_secret")) is False


def test_a_hard_link_to_a_credential_is_the_credential(home_env):
    from services.computer import rails
    key = home_env["repo"] / "backend" / ".jwt_secret"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("k")
    os.link(key, home_env["home"] / "linked")
    assert rails.can_read(home_env["home"] / "linked") is False
    assert rails.can_write(home_env["home"] / "linked") is False


def test_the_directories_above_what_is_protected_cannot_be_replaced(home_env):
    """Seatbelt matches paths at the time of each operation: renaming a
    parent carried the repo and its key out from under every rule. The
    ancestors of every protected root and credential are pinned; what is
    inside them is not."""
    from services.computer import profile, rails
    ancestors = rails.protected_ancestors()
    repo_parent = str(home_env["repo"].parent)
    assert repo_parent in ancestors and "/" in ancestors
    assert rails.can_write(repo_parent) is False
    assert "can't be moved" in rails.write_refusal(repo_parent)
    # Siblings inside an ancestor stay his.
    assert rails.can_write(home_env["repo"].parent / "notes.txt") is True
    text = profile.render_profile()
    line = next(l for l in text.splitlines() if l.startswith("(deny file-write* (literal"))
    import re
    pinned = re.findall(r'\(literal "([^"]+)"\)', line)
    assert sorted(pinned) == sorted(ancestors)
    for path in pinned:
        assert rails.can_write(path) is False, path


def test_a_linked_root_is_protected_as_written_and_as_resolved(home_env, tmp_path, monkeypatch):
    """``opt/postgresql@16`` is a link: the link itself must not be
    repointed (launchd's PATH runs postgresql@16's tools through it), and
    what it resolves to must not be written."""
    from services.computer import rails
    keg = tmp_path / "Cellar" / "pg" / "16.1"
    (keg / "bin").mkdir(parents=True)
    link = tmp_path / "opt-pg"
    os.symlink(keg, link)
    monkeypatch.setenv("EDWARD_PG_BIN", str(link / "bin"))
    roots = rails.write_deny_roots()
    assert str(link / "bin") in roots and str(keg / "bin") in roots
    assert rails.can_write(link / "bin" / "psql") is False
    assert rails.can_write(keg / "bin" / "psql") is False


def test_the_test_clusters_files_are_not_his_to_write(home_env, tmp_path, monkeypatch):
    """Its postmaster runs outside the sandbox: a planted postgresql.conf
    would run at its next start."""
    from services.computer import rails
    pgdata = tmp_path / "test-pg"
    pgdata.mkdir()
    monkeypatch.setenv("EDWARD_TEST_PGDATA", str(pgdata))
    assert str(pgdata) in rails.write_deny_roots()
    assert rails.can_write(pgdata / "postgresql.conf") is False
    assert "test database" in rails.write_refusal(pgdata / "postgresql.conf")


def test_the_shell_reaches_neither_database_nor_the_whatsapp_bridge(home_env):
    from services.computer import profile, rails
    from services.mcp_client import WHATSAPP_BRIDGE_ADDR
    text = profile.render_profile()
    for port in (5432, 5433, WHATSAPP_BRIDGE_ADDR[1], 8000, 3000):
        assert f'(deny network-outbound (remote tcp "*:{port}"))' in text, port
    for sock in ("/private/tmp/.s.PGSQL.5432", "/private/tmp/.s.PGSQL.5433", "/tmp/.s.PGSQL.5433"):
        assert f'(path-literal "{sock}")' in text, sock
    assert WHATSAPP_BRIDGE_ADDR[1] in rails.denied_ports()
    assert rails.APP_PORTS == (8000, 3000)


def test_rail_hint_names_the_test_cluster_and_the_bridge(home_env):
    from services.computer import rails
    h = rails.rail_hint('psql: error: connection to server at "localhost" (::1), port 5433 failed: '
                        'Operation not permitted')
    assert h and "5433" in h
    h = rails.rail_hint("curl: (7) Failed to connect to 127.0.0.1 port 8080 after 0 ms: Couldn't connect to server")
    assert h and "bridge" in h


def test_open_checked_never_follows_a_link_swapped_in_after_the_check(home_env, monkeypatch):
    """The check resolves the path; a link that appears at the last
    component before the open is refused (O_NOFOLLOW), never followed."""
    from services.computer import rails
    key = home_env["repo"] / "backend" / ".jwt_secret"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("not-for-edward")
    link = home_env["home"] / "swapped.txt"
    os.symlink(key, link)
    # As if the check had seen a plain file here: the path is not resolved.
    monkeypatch.setattr(rails, "_real", lambda p: os.path.abspath(os.path.expanduser(str(p))))
    monkeypatch.setattr(rails, "can_read", lambda p: True)
    with pytest.raises(rails.RailRefused):
        rails.open_checked(link)
    with pytest.raises(rails.RailRefused):
        rails.open_checked(link, write=True)
    assert key.read_text() == "not-for-edward"


def test_open_checked_checks_the_file_it_opened(home_env, monkeypatch):
    """Even when every check before the open is fooled, the opened file's
    own inode is compared with the credentials before a byte is read."""
    from services.computer import rails
    key = home_env["repo"] / "backend" / ".jwt_secret"
    key.parent.mkdir(parents=True, exist_ok=True)
    key.write_text("not-for-edward")
    real_can_read = rails.can_read
    calls = {"n": 0}

    def fooled_twice(p):
        calls["n"] += 1
        return True if calls["n"] <= 2 else real_can_read(p)
    monkeypatch.setattr(rails, "can_read", fooled_twice)
    with pytest.raises(rails.RailRefused):
        rails.open_checked(key)


def test_open_checked_reads_and_writes_his_own_files(home_env):
    from services.computer import rails
    fd, canon = rails.open_checked(home_env["home"] / "new.txt", write=True, create=True)
    try:
        rails.write_fd(fd, b"hello")
    finally:
        os.close(fd)
    assert canon.endswith("/new.txt")
    data, _ = rails.read_checked(home_env["home"] / "new.txt")
    assert data == b"hello"
    with pytest.raises(FileExistsError):
        rails.open_checked(home_env["home"] / "new.txt", write=True, create=True, exclusive=True)


def test_the_shell_environment_names_no_database(home_env, monkeypatch):
    """Whatever the backend's own environment holds, his shell gets no
    DATABASE_URL and none of libpq's PG* (review 2026-10-12)."""
    from services.computer import home
    for name, value in (("DATABASE_URL", "postgresql://edward:pw@localhost:5432/edward"),
                        ("TEST_DATABASE_URL", "postgresql+asyncpg://x@localhost:5433/edward_test"),
                        ("PGPORT", "5433"), ("PGPASSWORD", "pw"), ("PGHOST", "localhost")):
        monkeypatch.setenv(name, value)
    env = home.shell_env()
    assert not {k for k in env if k in ("DATABASE_URL", "TEST_DATABASE_URL") or k.startswith("PG")}, env


def test_ensure_home_never_writes_through_a_planted_link(home_env):
    """The backend lays out ~/edward-home outside the sandbox: a link the
    shell left where README.md goes is never followed."""
    from services.computer import home
    victim = home_env["repo"] / "victim.txt"
    victim.write_text("live\n")
    os.symlink(victim, home_env["home"] / "README.md")
    os.symlink(home_env["repo"] / "missing.txt", home_env["home"] / "SKILLS.md")
    home.ensure_home()
    assert victim.read_text() == "live\n"
    assert not (home_env["repo"] / "missing.txt").exists()
