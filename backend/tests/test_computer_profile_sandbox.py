"""The rendered Seatbelt profile, exercised with real ``sandbox-exec``
against scratch paths and a throwaway TCP port. It never names :5432, a live
path, Google Chrome or osascript — the host-touching lsopen/AppleEvent
probes are opt-in (``EDWARD_TEST_HOST_SANDBOX=1``) and use nonexistent
bundles so nothing launches even if a deny is broken."""

from __future__ import annotations

import os
import shutil
import socket
import subprocess
import time

import pytest

sandbox_missing = not os.path.exists("/usr/bin/sandbox-exec")
pytestmark = pytest.mark.skipif(sandbox_missing, reason="sandbox-exec not present")


def _run_in_sandbox(profile_text, command, tmp_path, env=None):
    prof = tmp_path / "test.sb"
    prof.write_text(profile_text)
    full_env = {"PATH": "/usr/bin:/bin:/usr/sbin:/sbin"}
    if env:
        full_env.update(env)
    return subprocess.run(
        ["/usr/bin/sandbox-exec", "-f", str(prof), "/bin/bash", "--noprofile", "--norc", "-c", command],
        capture_output=True, text=True, env=full_env, timeout=30,
    )


def test_write_deny_last_match_wins(tmp_path):
    from services.computer import profile
    protected = tmp_path / "protected"
    allowed = tmp_path / "allowed"
    protected.mkdir()
    allowed.mkdir()
    text = profile.render_profile(write_deny=[str(protected)], pg_port=59999)
    r = _run_in_sandbox(text, f"touch '{protected}/x'", tmp_path)
    assert r.returncode != 0, "write into a denied subpath must fail"
    assert "not permitted" in (r.stderr + r.stdout).lower()
    r2 = _run_in_sandbox(text, f"touch '{allowed}/x'", tmp_path)
    assert r2.returncode == 0, "write outside the deny must succeed"


def test_tcp_deny_on_substituted_port(tmp_path):
    from services.computer import profile
    # Two real listeners: one on the "production" port we deny, one allowed.
    denied = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    allowed = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    denied.bind(("127.0.0.1", 0)); denied.listen(1)
    allowed.bind(("127.0.0.1", 0)); allowed.listen(1)
    dport = denied.getsockname()[1]
    aport = allowed.getsockname()[1]
    try:
        text = profile.render_profile(write_deny=[], pg_port=dport,
                                      pg_socket=str(tmp_path / "nosock"))
        script = (
            "python3 - <<'PY'\n"
            "import socket\n"
            f"print('denied', socket.socket().connect_ex(('127.0.0.1', {dport})))\n"
            f"print('allowed', socket.socket().connect_ex(('127.0.0.1', {aport})))\n"
            "PY"
        )
        r = _run_in_sandbox(text, script, tmp_path)
        out = r.stdout
        # EPERM (1) connecting to the denied port; 0 (connected) to the allowed.
        assert "denied 1" in out, out
        assert "allowed 0" in out, out
    finally:
        denied.close(); allowed.close()


def _build_tiny_binary(path):
    """Compile a fresh ad-hoc-signed binary at ``path`` (a copy of a platform
    binary would be AMFI-killed, masking the sandbox result)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(["cc", "-x", "c", "-o", str(path), "-"],
                       input="int main(){return 0;}", text=True,
                       capture_output=True, timeout=30)
    return r.returncode == 0 and path.exists()


def test_chrome_exec_deny_with_scratch_binary(tmp_path):
    """The Chrome exec-deny is a path regex. Prove it with a harmless binary
    freshly built at a '<glob>.app/Contents/MacOS/' path — never real Chrome."""
    from services.computer import profile
    if not shutil.which("cc"):
        pytest.skip("cc not available to build a scratch binary")
    glob = str(tmp_path / "Google Chrome")
    decoy = tmp_path / "Google Chrome.app" / "Contents" / "MacOS" / "decoy"
    ok = tmp_path / "other" / "decoy"
    if not _build_tiny_binary(decoy) or not _build_tiny_binary(ok):
        pytest.skip("could not build scratch binaries")

    text = profile.render_profile(write_deny=[], pg_port=59999, chrome_glob=glob)
    r = _run_in_sandbox(text, f"'{decoy}'; echo rc=$?", tmp_path)
    assert "rc=0" not in r.stdout, "a binary under the Chrome glob must be exec-denied"
    r2 = _run_in_sandbox(text, f"'{ok}'; echo rc=$?", tmp_path)
    assert "rc=0" in r2.stdout, "an ordinary binary outside the glob must run"


def _decoy_bundle(root, app="Google Chrome.app", exe="Google Chrome"):
    path = root / app / "Contents" / "MacOS" / exe
    return path if _build_tiny_binary(path) else None


def test_a_copied_or_moved_chrome_bundle_cannot_run(tmp_path):
    """The rail is the bundle, not its path (review 2026-10-11): reads are
    allowed everywhere and ~/edward-home is writable, so a copy of the
    bundle elsewhere ran. Its data can't be read (no copy), it can't be
    moved or linked, and its executable is refused wherever it sits. Only
    harmless decoys built here — never real Chrome."""
    from services.computer import profile
    if not shutil.which("cc"):
        pytest.skip("cc not available to build a scratch binary")
    apps, home = tmp_path / "Applications", tmp_path / "home"
    home.mkdir()
    exe = _decoy_bundle(apps)
    if exe is None:
        pytest.skip("could not build scratch binaries")
    bundle = apps / "Google Chrome.app"
    text = profile.render_profile(write_deny=[], pg_port=59999, chrome_glob=str(apps / "Google Chrome"))
    r = _run_in_sandbox(text, (f"cp -R '{bundle}' '{home}/copy.app'; ditto '{bundle}' '{home}/d.app'; "
                               f"cat '{exe}' > '{home}/cat-copy' && chmod +x '{home}/cat-copy'; "
                               f"ln '{exe}' '{home}/hard'; ln -s '{bundle}' '{home}/soft.app'; "
                               f"mv '{bundle}' '{home}/moved.app'; "
                               f"for x in '{home}/copy.app/Contents/MacOS/Google Chrome' "
                               f"'{home}/d.app/Contents/MacOS/Google Chrome' '{home}/cat-copy' '{home}/hard' "
                               f"'{home}/soft.app/Contents/MacOS/Google Chrome' "
                               f"'{home}/moved.app/Contents/MacOS/Google Chrome'; do \"$x\"; echo \"rc=$?\"; done"),
                        tmp_path)
    assert "rc=0" not in r.stdout, r.stdout + r.stderr
    assert exe.exists(), "the bundle can't be moved away"


def test_a_chrome_bundle_from_anywhere_cannot_run(tmp_path):
    """A downloaded or unpacked Chrome outside /Applications — by its
    bundle name or its executable's — is refused; Chrome for Testing and
    ordinary programs still run."""
    from services.computer import profile
    if not shutil.which("cc"):
        pytest.skip("cc not available to build a scratch binary")
    dl = tmp_path / "Downloads"
    named = _decoy_bundle(dl, exe="whatever")
    renamed = _decoy_bundle(dl, app="x.app")
    testing = _decoy_bundle(dl, app="Google Chrome for Testing.app", exe="Google Chrome for Testing")
    plain = dl / "tool"
    if None in (named, renamed, testing) or not _build_tiny_binary(plain):
        pytest.skip("could not build scratch binaries")
    text = profile.render_profile(write_deny=[], pg_port=59999,
                                  chrome_glob=str(tmp_path / "Applications" / "Google Chrome"))
    for denied in (named, renamed):
        r = _run_in_sandbox(text, f"'{denied}'; echo rc=$?", tmp_path)
        assert "rc=0" not in r.stdout, denied
    for allowed in (testing, plain):
        r = _run_in_sandbox(text, f"'{allowed}'; echo rc=$?", tmp_path)
        assert "rc=0" in r.stdout, (allowed, r.stdout, r.stderr)


def test_signal_deny_to_unrelated_process(tmp_path):
    from services.computer import profile
    victim = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        text = profile.render_profile(write_deny=[], pg_port=59999)
        r = _run_in_sandbox(text, f"kill {victim.pid}", tmp_path)
        assert r.returncode != 0, "signalling a process he didn't start must fail"
        assert victim.poll() is None, "the unrelated process must survive"
    finally:
        victim.kill(); victim.wait()


def test_reads_are_allowed(tmp_path):
    """Ellis's ruling: the shell may read anywhere."""
    from services.computer import profile
    f = tmp_path / "readme"
    f.write_text("hello")
    text = profile.render_profile(write_deny=[str(tmp_path / "protected")], pg_port=59999)
    r = _run_in_sandbox(text, f"cat '{f}'", tmp_path)
    assert r.returncode == 0 and "hello" in r.stdout


def test_his_own_credentials_cannot_be_read_copied_linked_or_moved(tmp_path):
    """2026-10-12 review: the shell read backend/.jwt_secret and signed an
    owner session inside one command, where redaction never sees the key.
    Scratch files only: a stand-in key next to an ordinary file."""
    from services.computer import profile
    repo, home = tmp_path / "repo", tmp_path / "home"
    (repo / "backend").mkdir(parents=True)
    home.mkdir()
    key = repo / "backend" / ".jwt_secret"
    key.write_text("SCRATCH-KEY-123")
    other = repo / "backend" / "main.py"
    other.write_text("print('ok')")
    text = profile.render_profile(write_deny=[], read_deny=[os.path.realpath(key)], pg_port=59999,
                                  app_ports=[])
    script = (
        f"cat '{key}'; "
        f"cd '{repo}/backend' && cat ./.jwt_secret; cat ../backend/.jwt_secret; "
        f"ln -s '{key}' '{home}/soft' && cat '{home}/soft'; "
        f"ln '{key}' '{home}/hard'; cat '{home}/hard'; "
        f"cp -c '{key}' '{home}/clone'; cat '{home}/clone'; "
        f"cp '{key}' '{home}/copy'; cat '{home}/copy'; "
        f"mv '{key}' '{home}/moved'; cat '{home}/moved'; "
        f"python3 -c 'print(open(\"{key}\").read())'; "
        f"echo replaced > '{key}'; "
        f"cat '{other}'"
    )
    r = _run_in_sandbox(text, script, tmp_path)
    assert "SCRATCH-KEY-123" not in r.stdout, r.stdout + r.stderr
    assert "print('ok')" in r.stdout, "the rest of the checkout stays readable"
    assert key.read_text() == "SCRATCH-KEY-123", "the key can't be replaced or moved away"


def test_his_own_api_ports_are_refused_on_every_address(tmp_path):
    """No TCP from the shell to the backend's or the frontend's port, IPv4
    or IPv6; any other port still connects. Throwaway listeners only."""
    from services.computer import profile
    listeners = []

    def listen(family, host):
        s = socket.socket(family, socket.SOCK_STREAM)
        if family == socket.AF_INET6:
            s.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        s.bind((host, 0)); s.listen(5)
        listeners.append(s)
        return s.getsockname()[1]

    try:
        app = listen(socket.AF_INET, "127.0.0.1")
        try:
            app6 = listen(socket.AF_INET6, "::1")
        except OSError:
            app6 = None
        free = listen(socket.AF_INET, "127.0.0.1")
        ports = [app] + ([app6] if app6 else [])
        text = profile.render_profile(write_deny=[], read_deny=[], pg_port=59999,
                                      pg_socket=str(tmp_path / "nosock"), app_ports=ports)
        probe = [f"print('app', socket.socket().connect_ex(('127.0.0.1', {app})))",
                 f"print('free', socket.socket().connect_ex(('127.0.0.1', {free})))"]
        if app6:
            probe.append(f"print('app6', socket.socket(socket.AF_INET6).connect_ex(('::1', {app6})))")
        script = "python3 - <<'PY'\nimport socket\n" + "\n".join(probe) + "\nPY"
        out = _run_in_sandbox(text, script, tmp_path).stdout
        assert "app 1" in out, out
        assert "free 0" in out, out
        if app6:
            assert "app6 1" in out, out
    finally:
        for s in listeners:
            s.close()


def test_the_live_profile_compiles(tmp_path):
    """The rendered default profile (the real credential paths and ports)
    is valid SBPL: sandbox-exec runs /usr/bin/true under it."""
    from services.computer import profile
    r = _run_in_sandbox(profile.render_profile(), "/usr/bin/true && echo compiled", tmp_path)
    assert "compiled" in r.stdout, r.stderr


@pytest.mark.skipif(os.getenv("EDWARD_TEST_HOST_SANDBOX") != "1",
                    reason="opt-in: touches LaunchServices/AppleEvents on the host")
def test_lsopen_and_appleevent_deny(tmp_path):
    from services.computer import profile
    text = profile.render_profile(write_deny=[], pg_port=59999)
    # A nonexistent bundle: nothing launches even if the deny were broken.
    r = _run_in_sandbox(text, "open -g -j -a /nonexistent/Bogus.app 2>&1; echo rc=$?", tmp_path)
    assert "rc=0" not in r.stdout


# ---------------------------------------------------- review 2026-10-12 --

def test_a_parent_of_the_repo_cannot_be_renamed_out_from_under_the_rules(tmp_path):
    """Seatbelt matches a path at the time of each operation: ``mv code
    code2`` carried the repo (and its key) out from under the write-deny
    and the read-deny. Every directory above them is pinned; files inside
    those directories are still his to create, rename and remove."""
    from services.computer import profile
    code = tmp_path / "code"
    repo = code / "repo"
    (repo / "backend").mkdir(parents=True)
    key = repo / "backend" / ".jwt_secret"
    key.write_text("SCRATCH-KEY-123")
    (repo / "backend" / "app.py").write_text("print('ok')\n")
    text = profile.render_profile(write_deny=[os.path.realpath(repo)], read_deny=[os.path.realpath(key)],
                                  pg_port=59999, app_ports=[])
    script = (
        f"mv '{code}' '{tmp_path}/code2'; echo mv-grandparent=$?; "
        f"mv '{tmp_path}' '{tmp_path}-moved'; echo mv-root=$?; "
        f"cat '{tmp_path}/code2/repo/backend/.jwt_secret'; "
        f"echo PWNED >> '{tmp_path}/code2/repo/backend/app.py'; "
        f"touch '{code}/sibling' && mv '{code}/sibling' '{code}/sibling2' && mkdir '{code}/d' "
        f"&& rm '{code}/sibling2' && rmdir '{code}/d' && echo siblings-ok"
    )
    r = _run_in_sandbox(text, script, tmp_path)
    out = r.stdout + r.stderr
    assert "mv-grandparent=0" not in out and "mv-root=0" not in out, out
    assert "SCRATCH-KEY-123" not in r.stdout, out
    assert code.is_dir() and (repo / "backend" / "app.py").read_text() == "print('ok')\n"
    assert "siblings-ok" in r.stdout, out


def test_a_case_variant_does_not_get_past_the_sandbox(tmp_path):
    """Seatbelt resolves a path's case itself (the in-process rails now
    compare identities to match)."""
    from services.computer import profile
    try:
        if os.pathconf(str(tmp_path), 11) != 0:
            pytest.skip("this volume compares names case-sensitively")
    except (OSError, ValueError):
        pytest.skip("can't tell how this volume compares names")
    repo = tmp_path / "repo"
    (repo / "backend").mkdir(parents=True)
    key = repo / "backend" / ".jwt_secret"
    key.write_text("SCRATCH-KEY-123")
    text = profile.render_profile(write_deny=[os.path.realpath(repo)], read_deny=[os.path.realpath(key)],
                                  pg_port=59999, app_ports=[])
    upper = str(repo).upper()
    r = _run_in_sandbox(text, f"cat '{upper}/BACKEND/.JWT_SECRET'; touch '{upper}/BACKEND/NEW'; "
                              f"echo x > '{upper}/BACKEND/.Jwt_Secret'", tmp_path)
    assert "SCRATCH-KEY-123" not in r.stdout
    assert not (repo / "backend" / "NEW").exists()
    assert key.read_text() == "SCRATCH-KEY-123"


def test_a_protected_link_cannot_be_repointed(tmp_path):
    """``opt/postgresql@16`` is a link; launchd's jobs run its tools. The
    link's own path is denied as well as where it resolves."""
    from services.computer import profile
    keg = tmp_path / "Cellar" / "pg"
    keg.mkdir(parents=True)
    other = tmp_path / "evil"
    other.mkdir()
    link = tmp_path / "opt-pg"
    os.symlink(keg, link)
    text = profile.render_profile(write_deny=[str(link), os.path.realpath(keg)], read_deny=[],
                                  pg_port=59999, app_ports=[])
    r = _run_in_sandbox(text, f"rm '{link}'; ln -sfn '{other}' '{link}'; touch '{link}/x'; mv '{link}' '{link}2'",
                        tmp_path)
    assert os.readlink(link) == str(keg), r.stderr
    assert not (keg / "x").exists()


def _listen_tcp():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(5)
    return s


def test_the_test_cluster_is_refused_like_production(tmp_path):
    """The test cluster's OS-user superuser trusts loopback; from the
    shell, its server-side file reads and COPY … TO PROGRAM would run
    outside the sandbox. Its TCP port and its socket are refused (a
    throwaway port and socket stand in for :5433)."""
    import tempfile

    from services.computer import profile
    test_pg = _listen_tcp()
    free = _listen_tcp()
    sock_dir = tempfile.mkdtemp(prefix="edw", dir="/tmp")
    sock_path = os.path.join(sock_dir, ".s.PGSQL.1")
    unix = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        unix.bind(sock_path)
        unix.listen(1)
        text = profile.render_profile(write_deny=[], read_deny=[], pg_port=59999,
                                      pg_socket=str(tmp_path / "nosock"),
                                      test_pg_port=test_pg.getsockname()[1],
                                      test_pg_socket=os.path.realpath(sock_path), app_ports=[])
        script = ("python3 - <<'PY'\nimport socket\n"
                  f"print('test_pg', socket.socket().connect_ex(('127.0.0.1', {test_pg.getsockname()[1]})))\n"
                  f"print('free', socket.socket().connect_ex(('127.0.0.1', {free.getsockname()[1]})))\n"
                  f"print('unix', socket.socket(socket.AF_UNIX).connect_ex({sock_path!r}))\n"
                  "PY")
        out = _run_in_sandbox(text, script, tmp_path).stdout
        assert "test_pg 1" in out, out
        assert "free 0" in out, out
        assert "unix 0" not in out, out
    finally:
        test_pg.close(); free.close(); unix.close()
        shutil.rmtree(sock_dir, ignore_errors=True)


def test_the_live_profile_denies_both_databases_and_the_bridge():
    from services.computer import profile
    from services.mcp_client import WHATSAPP_BRIDGE_ADDR
    text = profile.render_profile()
    for port in (5432, 5433, WHATSAPP_BRIDGE_ADDR[1]):
        assert f'(deny network-outbound (remote tcp "*:{port}"))' in text
