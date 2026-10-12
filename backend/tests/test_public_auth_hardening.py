"""The front door: who a request is from, the login limiter, the first
password, and an install that has no password yet.

* ``services/client_ip.py`` keys the limiter on the real client: the peer,
  or behind a loopback proxy ``CF-Connecting-IP``, else the right-most
  ``X-Forwarded-For`` entry that is not loopback — never the left-most one,
  which the client writes itself.
* The limiter (``routers/auth.py``, shared with the photo door) has a
  ceiling across every client and a cap on how many it tracks.
* ``POST /api/auth/setup`` takes the first password only with the
  one-time code in ``backend/.setup_code`` (services/setup_code.py), only
  as JSON, only from a page on this Mac — a header a LAN device can forge
  (``Host: localhost:3000``) no longer decides it.
* Every attempt is reserved before the first await, so a parallel burst
  cannot pass the limits; bcrypt runs off the event loop; /change-password
  is limited per session, never by the login's global ceiling.
* Before a password is set the API answers only the public routes
  (``middleware/auth.py``); it used to be wide open.
"""

import asyncio
import os
import stat
import threading

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from starlette.datastructures import Headers

pytestmark = pytest.mark.unit


def _conn(host="127.0.0.1", port=50123, **headers):
    raw = []
    for k, v in headers.items():
        for value in (v if isinstance(v, list) else [v]):
            raw.append((k.replace("_", "-").lower().encode(), str(value).encode()))
    return SimpleNamespace(client=SimpleNamespace(host=host, port=port), headers=Headers(raw=raw))


def _clear_limiters():
    from routers import auth as auth_router

    auth_router._login_attempts.clear()
    auth_router._change_password_attempts.clear()
    auth_router._setup_code_failures.clear()


@pytest.fixture(autouse=True)
def _fresh_limiter():
    _clear_limiters()
    yield
    _clear_limiters()


@pytest.fixture(autouse=True)
def code_file(tmp_path, monkeypatch):
    """The setup code lives in a scratch file, never the checkout's."""
    from services import setup_code

    path = tmp_path / "backend" / ".setup_code"
    path.parent.mkdir()
    monkeypatch.setattr(setup_code, "SETUP_CODE_FILE", path)
    return path


# --------------------------------------------------------------- client_ip --

@pytest.mark.parametrize("host,expected", [
    ("127.0.0.1", True), ("127.0.0.2", True), ("::1", True), ("::ffff:127.0.0.1", True),
    ("localhost", True), ("localhost:3000", True), ("[::1]:3000", True), ("127.0.0.1:3000", True),
    ("", False), ("testclient", False), ("192.168.1.20", False), ("my-mac.local", False),
    ("my-mac.local:3000", False), ("203.0.113.5", False), ("::ffff:192.168.1.2", False), (None, False),
])
def test_is_loopback(host, expected):
    from services.client_ip import is_loopback

    assert is_loopback(host) is expected


def test_a_lan_peer_is_itself_whatever_it_claims():
    from services.client_ip import client_ip

    conn = _conn("192.168.1.9", x_forwarded_for="198.51.100.1", cf_connecting_ip="198.51.100.2")
    assert client_ip(conn) == "192.168.1.9"


def test_behind_cloudflare_the_cf_header_wins():
    from services.client_ip import client_ip

    conn = _conn(cf_connecting_ip="198.51.100.7", x_forwarded_for="1.2.3.4, 198.51.100.7")
    assert client_ip(conn) == "198.51.100.7"


@pytest.mark.parametrize("xff,expected", [
    ("spoofed-1, 198.51.100.7", "198.51.100.7"),       # the client wrote the left-most entry
    ("198.51.100.7, 127.0.0.1", "198.51.100.7"),
    ("198.51.100.7", "198.51.100.7"),
    ("127.0.0.1, ::1", "127.0.0.1"),                  # all loopback: the peer
])
def test_behind_a_loopback_proxy_the_right_most_outside_entry_counts(xff, expected):
    from services.client_ip import client_ip

    assert client_ip(_conn(x_forwarded_for=xff)) == expected


def test_several_xff_headers_read_as_one_list():
    from services.client_ip import client_ip

    assert client_ip(_conn(x_forwarded_for=["9.9.9.9", "198.51.100.7"])) == "198.51.100.7"


def test_a_client_uvicorn_already_rewrote_still_counts_as_proxied(monkeypatch):
    """Uvicorn's proxy headers (trusting 127.0.0.1) replace the client with
    the right-most XFF entry and leave port 0."""
    from services.client_ip import client_ip, is_local_client, peer_is_loopback

    monkeypatch.delenv("FORWARDED_ALLOW_IPS", raising=False)

    conn = _conn("198.51.100.7", port=0, x_forwarded_for="1.1.1.1, 198.51.100.7", cf_connecting_ip="198.51.100.8")
    assert peer_is_loopback(conn)
    assert client_ip(conn) == "198.51.100.8"
    assert not is_local_client(conn)


def test_a_rewritten_client_proves_nothing_when_uvicorn_trusts_more_than_loopback(monkeypatch):
    from services.client_ip import client_ip, is_local_client, peer_is_loopback

    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    conn = _conn("127.0.0.1", port=0, x_forwarded_for="127.0.0.1")   # a LAN device's forged header
    assert not peer_is_loopback(conn) and not is_local_client(conn)
    assert client_ip(conn) == "127.0.0.1"
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "127.0.0.1, ::1")
    assert peer_is_loopback(conn)


def test_a_long_header_is_cut_to_a_short_key():
    from services.client_ip import MAX_KEY_CHARS, client_ip

    assert len(client_ip(_conn(cf_connecting_ip="x" * 5000))) == MAX_KEY_CHARS


@pytest.mark.parametrize("peer,headers,local", [
    ("127.0.0.1", {}, True),                                                  # curl on this Mac
    ("::1", {}, True),
    ("127.0.0.1", {"x_forwarded_host": "localhost:3000"}, True),              # a browser here, via Next
    ("127.0.0.1", {"x_forwarded_host": "127.0.0.1:3000"}, True),
    ("127.0.0.1", {"x_forwarded_host": "[::1]:3000"}, True),
    ("127.0.0.1", {"x_forwarded_host": "my-mac.local:3000"}, False),          # a phone on the LAN, via Next
    ("127.0.0.1", {"x_forwarded_host": "edward.example.com"}, False),
    ("127.0.0.1", {"x_forwarded_host": ""}, False),
    ("127.0.0.1", {"cf_connecting_ip": "198.51.100.7"}, False),               # the tunnel
    ("127.0.0.1", {"x_forwarded_for": "127.0.0.1, 203.0.113.5"}, False),
    ("127.0.0.1", {"x_forwarded_for": "127.0.0.1"}, True),
    ("127.0.0.1", {"x_real_ip": "203.0.113.5"}, False),
    ("127.0.0.1", {"forwarded": "for=203.0.113.5;proto=https"}, False),
    ("127.0.0.1", {"forwarded": 'for="[::1]:4711"'}, True),
    ("127.0.0.1", {"forwarded": "proto=https"}, False),
    ("192.168.1.20", {}, False),                                              # straight to :8000 from the LAN
])
def test_is_local_client(peer, headers, local):
    from services.client_ip import is_local_client

    assert is_local_client(_conn(peer, **headers)) is local


def test_is_direct_loopback_refuses_any_proxy_header():
    from services.client_ip import PROXY_HEADERS, is_direct_loopback

    assert is_direct_loopback(_conn())
    assert not is_direct_loopback(_conn("192.168.1.20"))
    for name in PROXY_HEADERS:
        assert not is_direct_loopback(_conn(**{name.replace("-", "_"): "127.0.0.1"})), name


# --------------------------------------------------------------- the limiter --

def test_the_ceiling_across_every_client_holds_when_keys_rotate():
    from routers import auth as auth_router

    for i in range(auth_router._RATE_LIMIT_GLOBAL_MAX_FAILURES):
        auth_router._record_failed_attempt(f"10.0.{i // 250}.{i % 250}")
    allowed, retry_after = auth_router._check_rate_limit("198.51.100.200")
    assert not allowed and 0 < retry_after <= auth_router._RATE_LIMIT_WINDOW + 1


def test_the_tracked_clients_are_capped(monkeypatch):
    from routers import auth as auth_router

    monkeypatch.setattr(auth_router, "_RATE_LIMIT_MAX_CLIENTS", 50)
    for i in range(120):
        auth_router._record_failed_attempt(f"client-{i}")
    assert len(auth_router._login_attempts) <= 50
    assert "client-119" in auth_router._login_attempts and "client-0" not in auth_router._login_attempts


def test_a_client_that_fails_again_is_kept_over_older_ones(monkeypatch):
    from routers import auth as auth_router

    monkeypatch.setattr(auth_router, "_RATE_LIMIT_MAX_CLIENTS", 3)
    for ip in ("a", "b", "c"):
        auth_router._record_failed_attempt(ip)
    auth_router._record_failed_attempt("a")      # a is now the most recent
    auth_router._record_failed_attempt("d")
    assert set(auth_router._login_attempts) == {"c", "a", "d"}


def _peer_app(router, prefix="/api", peer=("127.0.0.1", 50123)):
    app = FastAPI()
    app.include_router(router, prefix=prefix)
    return AsyncClient(transport=ASGITransport(app=app, client=peer), base_url="http://localhost:8000")


@pytest.fixture
def failing_login(monkeypatch):
    from routers import auth as auth_router
    from services import auth_service

    async def wrong(session, password):
        return None

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(auth_service, "authenticate", wrong)
    monkeypatch.setattr(auth_router, "async_session", lambda: _Session())
    return auth_router


async def test_rotating_x_forwarded_for_through_the_tunnel_does_not_reset_the_limit(failing_login):
    codes = []
    async with _peer_app(failing_login.router) as c:
        for i in range(7):
            r = await c.post("/api/auth/login", json={"password": "guess"},
                             headers={"x-forwarded-for": f"10.9.9.{i}, 198.51.100.7"})
            codes.append(r.status_code)
    assert codes[:5] == [401] * 5 and codes[5:] == [429, 429]


async def test_rotating_x_forwarded_for_behind_cloudflare_does_not_reset_the_limit(failing_login):
    codes = []
    async with _peer_app(failing_login.router) as c:
        for i in range(6):
            r = await c.post("/api/auth/login", json={"password": "guess"},
                             headers={"cf-connecting-ip": "198.51.100.7", "x-forwarded-for": f"10.9.9.{i}"})
            codes.append(r.status_code)
    assert codes == [401] * 5 + [429]


async def test_the_photo_door_is_keyed_the_same_way(monkeypatch):
    from routers import photo_door
    from services import photo_door_auth

    monkeypatch.setattr(photo_door_auth, "enabled", lambda: True)
    monkeypatch.setattr(photo_door_auth, "check_password", lambda p: False)
    codes = []
    async with _peer_app(photo_door.router) as c:
        for i in range(6):
            r = await c.post("/api/photo-door/auth/login", json={"password": "guess"},
                             headers={"x-forwarded-for": f"10.8.8.{i}, 198.51.100.9"})
            codes.append(r.status_code)
    assert codes == [401] * 5 + [429]


@pytest.fixture
def slow_login(monkeypatch):
    """authenticate awaits like the real one (the database, then bcrypt)."""
    from routers import auth as auth_router
    from services import auth_service

    reached = []

    async def wrong(session, password):
        reached.append(password)
        await asyncio.sleep(0.05)
        return None

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(auth_service, "authenticate", wrong)
    monkeypatch.setattr(auth_router, "async_session", lambda: _Session())
    return auth_router, reached


async def test_a_parallel_burst_of_logins_cannot_pass_the_per_client_limit(slow_login):
    """2026-10-12 review: the check and the record were separated by awaits,
    so 300 parallel guesses from one client all reached the password check."""
    mod, reached = slow_login
    async with _peer_app(mod.router) as c:
        rs = await asyncio.gather(*[c.post("/api/auth/login", json={"password": f"guess-{i}"}) for i in range(20)])
    assert len(reached) <= mod._RATE_LIMIT_MAX_ATTEMPTS
    assert sorted(r.status_code for r in rs) == [401] * len(reached) + [429] * (20 - len(reached))
    assert all("retry-after" in r.headers for r in rs if r.status_code == 429)


async def test_a_parallel_burst_from_rotating_keys_cannot_pass_the_ceiling(slow_login, monkeypatch):
    mod, reached = slow_login
    monkeypatch.setattr(mod, "_RATE_LIMIT_GLOBAL_MAX_FAILURES", 12)
    async with _peer_app(mod.router) as c:
        await asyncio.gather(*[
            c.post("/api/auth/login", json={"password": "guess"},
                   headers={"cf-connecting-ip": f"198.51.100.{i}"})
            for i in range(40)
        ])
    assert len(reached) <= 12


async def test_a_successful_login_clears_its_own_reservation(monkeypatch):
    from routers import auth as auth_router
    from services import auth_service

    async def right(session, password):
        await asyncio.sleep(0)
        return auth_service.create_access_token()

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(auth_service, "authenticate", right)
    monkeypatch.setattr(auth_router, "async_session", lambda: _Session())
    auth_router._record_failed_attempt("127.0.0.1")
    async with _peer_app(auth_router.router) as c:
        r = await c.post("/api/auth/login", json={"password": "the right one"})
    assert r.status_code == 200
    assert "127.0.0.1" not in auth_router._login_attempts


async def test_a_parallel_burst_on_the_photo_door_cannot_pass_the_limit(monkeypatch):
    from routers import auth as auth_router
    from routers import photo_door
    from services import photo_door_auth

    reached = []

    def slow_wrong(password):
        reached.append(password)
        import time
        time.sleep(0.05)
        return False

    monkeypatch.setattr(photo_door_auth, "enabled", lambda: True)
    monkeypatch.setattr(photo_door_auth, "check_password", slow_wrong)
    async with _peer_app(photo_door.router) as c:
        rs = await asyncio.gather(*[c.post("/api/photo-door/auth/login", json={"password": "guess"})
                                    for _ in range(15)])
    assert len(reached) <= auth_router._RATE_LIMIT_MAX_ATTEMPTS
    assert sorted(r.status_code for r in rs) == [401] * len(reached) + [429] * (15 - len(reached))


async def test_bcrypt_runs_off_the_event_loop(monkeypatch):
    """A cost-12 check takes about a quarter of a second; on the loop, a
    burst froze every turn, the scheduler and every stream."""
    from services import auth_service

    loop_thread = threading.get_ident()
    seen = []

    def verify(plain, hashed):
        seen.append(threading.get_ident())
        return False

    def hashpw(password):
        seen.append(threading.get_ident())
        return "$2b$12$" + "x" * 53

    class _Result:
        def __init__(self, row):
            self._row = row

        def first(self):
            return self._row

        def scalar_one_or_none(self):
            return SimpleNamespace(password_hash="$2b$12$" + "y" * 53)

    class _Session:
        async def execute(self, *_a, **_k):
            return _Result(("$2b$12$" + "y" * 53,))

        async def commit(self):
            return None

    monkeypatch.setattr(auth_service, "verify_password", verify)
    monkeypatch.setattr(auth_service, "hash_password", hashpw)
    assert await auth_service.authenticate(_Session(), "guess") is None
    assert await auth_service.change_password(_Session(), "old", "new password") is False
    assert seen and all(t != loop_thread for t in seen)


@pytest.fixture
def change_password_route(monkeypatch):
    from routers import auth as auth_router
    from services import auth_service

    calls = []

    async def change(session, current, new):
        calls.append(current)
        await asyncio.sleep(0.02)
        return current == "the right one"

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(auth_service, "change_password", change)
    monkeypatch.setattr(auth_router, "async_session", lambda: _Session())
    return auth_router, calls


def _signed_in(router, token=None):
    from services.auth_service import create_access_token

    app = FastAPI()
    app.include_router(router, prefix="/api")
    return AsyncClient(transport=ASGITransport(app=app, client=("127.0.0.1", 50123)),
                       base_url="http://localhost:8000", cookies={"edward_token": token or create_access_token()})


async def test_change_password_ignores_the_login_ceiling(change_password_route):
    """The owner can rotate the password during the very guessing attack
    that calls for it."""
    mod, calls = change_password_route
    for i in range(mod._RATE_LIMIT_GLOBAL_MAX_FAILURES + 5):
        mod._record_failed_attempt(f"10.2.{i // 250}.{i % 250}")
    async with _signed_in(mod.router) as c:
        r = await c.post("/api/auth/change-password",
                         json={"current_password": "the right one", "new_password": "a new long one"})
    assert r.status_code == 200, r.text


async def test_change_password_is_limited_per_session_even_in_a_burst(change_password_route):
    mod, calls = change_password_route
    async with _signed_in(mod.router) as c:
        rs = await asyncio.gather(*[
            c.post("/api/auth/change-password", json={"current_password": f"guess-{i}", "new_password": "a new long one"})
            for i in range(12)
        ])
    assert len(calls) <= mod._RATE_LIMIT_MAX_ATTEMPTS
    assert sorted(r.status_code for r in rs) == [400] * len(calls) + [429] * (12 - len(calls))
    assert mod._login_attempts == {}, "a signed-in session's typos never feed the login ceiling"


async def test_change_password_needs_a_session(change_password_route):
    mod, calls = change_password_route
    async with _peer_app(mod.router) as c:
        r = await c.post("/api/auth/change-password",
                         json={"current_password": "x", "new_password": "a new long one"})
    assert r.status_code == 401 and calls == []


# --------------------------------------------------------------- the setup code --

def test_a_code_is_three_groups_from_an_unambiguous_alphabet():
    import re

    from services import setup_code

    for _ in range(200):
        code = setup_code.generate()
        assert re.fullmatch(r"[A-Z2-9]{4}-[A-Z2-9]{4}-[A-Z2-9]{4}", code), code
        assert not set(code) & set("01OIL")
    assert len({setup_code.generate() for _ in range(50)}) == 50


def test_the_code_file_is_private_and_kept_once_made(code_file):
    from services import setup_code

    code = setup_code.ensure()
    assert code and code_file.read_text().strip() == code
    assert stat.S_IMODE(os.stat(code_file).st_mode) == 0o600
    assert setup_code.ensure() == code, "an existing private code is kept"
    assert setup_code.matches(code) and setup_code.matches(code.lower().replace("-", " "))
    assert not setup_code.matches("") and not setup_code.matches(None)
    setup_code.discard()
    assert not code_file.exists()
    setup_code.discard()  # already gone: fine


@pytest.mark.parametrize("plant", ["world-readable", "symlink", "garbage"])
def test_a_planted_code_file_is_never_trusted(code_file, tmp_path, plant):
    """Someone who could write a code there would know it; a file that is
    not a private regular file holding a code is replaced."""
    from services import setup_code

    if plant == "world-readable":
        code_file.write_text("AAAA-BBBB-CCCC\n")
        os.chmod(code_file, 0o644)
    elif plant == "symlink":
        target = tmp_path / "elsewhere"
        target.write_text("AAAA-BBBB-CCCC\n")
        os.chmod(target, 0o600)
        os.symlink(target, code_file)
    else:
        code_file.write_text("hello\n")
        os.chmod(code_file, 0o600)
    code = setup_code.ensure()
    assert code and code != "AAAA-BBBB-CCCC"
    assert not code_file.is_symlink()
    assert stat.S_IMODE(os.stat(code_file).st_mode) == 0o600
    assert not setup_code.matches("AAAA-BBBB-CCCC")


def test_the_log_names_the_file_never_the_code(code_file, caplog):
    import logging

    from services import setup_code

    with caplog.at_level(logging.INFO):
        code = setup_code.ensure()
    text = caplog.text
    assert "cat backend/.setup_code" in text
    assert code not in text and code.replace("-", "") not in text


async def test_startup_makes_a_code_before_setup_and_drops_it_after(code_file, monkeypatch, caplog):
    import logging

    from services import auth_service, setup_code

    configured = {"now": False}

    async def is_configured(session):
        return configured["now"]

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    import services.database as database

    monkeypatch.setattr(auth_service, "is_password_configured", is_configured)
    monkeypatch.setattr(database, "async_session", lambda: _Session())
    with caplog.at_level(logging.INFO):
        await setup_code.ensure_for_startup()
    assert code_file.exists()
    assert "cat backend/.setup_code" in caplog.text
    assert code_file.read_text().strip() not in caplog.text
    configured["now"] = True
    await setup_code.ensure_for_startup()
    assert not code_file.exists()


# --------------------------------------------------------------- the first password --

@pytest.fixture
def setup_route(monkeypatch):
    from routers import auth as auth_router
    from services import auth_service, setup_code

    calls = []
    configured = {"now": False}

    async def setup(session, password):
        # Like auth_service.setup_password: check, hash (an await), write.
        if configured["now"]:
            return False
        await asyncio.sleep(0.01)
        calls.append(password)
        configured["now"] = True
        return True

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(auth_service, "setup_password", setup)
    monkeypatch.setattr(auth_service, "is_password_configured_cached", lambda: configured["now"])
    monkeypatch.setattr(auth_router, "async_session", lambda: _Session())
    code = setup_code.ensure()
    assert code
    return SimpleNamespace(router=auth_router.router, mod=auth_router, calls=calls, code=code,
                           configured=configured)


def _setup_body(code, password="a long enough one"):
    return {"password": password, "setup_code": code}


@pytest.mark.parametrize("peer,headers", [
    (("127.0.0.1", 50123), {}),
    (("127.0.0.1", 50123), {"x-forwarded-host": "localhost:3000"}),
    (("::1", 50123), {"x-forwarded-host": "[::1]:3000"}),
    (("127.0.0.1", 50123), {"x-forwarded-host": "localhost:3000", "origin": "http://localhost:3000"}),
    (("127.0.0.1", 50123), {"origin": "http://127.0.0.1:3000"}),
    (("127.0.0.1", 50123), {"origin": "https://[::1]"}),
])
async def test_the_first_password_is_taken_on_this_mac_with_the_code(setup_route, code_file, peer, headers):
    async with _peer_app(setup_route.router, peer=peer) as c:
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code), headers=headers)
    assert r.status_code == 200, r.text
    assert setup_route.calls == ["a long enough one"]
    assert "edward_token=" in r.headers.get("set-cookie", "")
    assert not code_file.exists(), "the code is deleted once a password is set"


@pytest.mark.parametrize("typed", ["{lower}", "{spaced}", "  {bare}  "])
async def test_the_code_is_read_however_it_was_typed(setup_route, typed):
    code = setup_route.code
    offered = typed.format(lower=code.lower(), spaced=code.replace("-", " "), bare=code.replace("-", ""))
    async with _peer_app(setup_route.router) as c:
        r = await c.post("/api/auth/setup", json=_setup_body(offered))
    assert r.status_code == 200, r.text


@pytest.mark.parametrize("offered", [None, "", "   ", "AAAA-BBBB-CCCC", "not the code"])
async def test_without_the_code_the_first_password_is_refused(setup_route, offered):
    """The LAN forging Host: localhost:3000 passes every header check; the
    code is what it cannot get."""
    from routers.auth import SETUP_CODE_WRONG

    body = {"password": "a long enough one"}
    if offered is not None:
        body["setup_code"] = offered
    async with _peer_app(setup_route.router) as c:
        r = await c.post("/api/auth/setup", json=body, headers={"x-forwarded-host": "localhost:3000"})
    assert r.status_code == 403
    assert r.json()["detail"] == SETUP_CODE_WRONG
    assert "cat backend/.setup_code" in SETUP_CODE_WRONG
    assert setup_route.calls == []


@pytest.mark.parametrize("content_type,body", [
    (None, b'{"password": "a long enough one"}'),            # a typeless Blob: no preflight
    ("text/plain", b'{"password": "a long enough one"}'),
    ("application/x-www-form-urlencoded", b"password=a+long+enough+one"),
    ("multipart/form-data; boundary=x", b"--x--"),
])
async def test_the_first_password_is_json_only(setup_route, content_type, body):
    """A cross-site page can send a simple request with no preflight; JSON
    forces one."""
    headers = {} if content_type is None else {"content-type": content_type}
    async with _peer_app(setup_route.router) as c:
        r = await c.post("/api/auth/setup", content=body, headers=headers)
    assert r.status_code == 415
    assert setup_route.calls == []


@pytest.mark.parametrize("origin", ["https://evil.example.com", "null", "http://my-mac.local:3000",
                                    "http://localhost.evil.example", "http://127.0.0.1.example.com",
                                    "file://", "http://localhost:notaport"])
async def test_a_page_that_is_not_this_macs_localhost_is_refused(setup_route, origin):
    from routers.auth import SETUP_LOCAL_ONLY

    async with _peer_app(setup_route.router) as c:
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code), headers={"origin": origin})
    assert r.status_code == 403
    assert r.json()["detail"] == SETUP_LOCAL_ONLY
    assert setup_route.calls == []


async def test_wrong_codes_are_limited_per_client_and_never_touch_the_login_limiter(setup_route):
    mod = setup_route.mod
    codes = []
    async with _peer_app(setup_route.router) as c:
        for _ in range(mod._SETUP_CODE_MAX_FAILURES + 1):
            r = await c.post("/api/auth/setup", json=_setup_body("WRNG-WRNG-WRNG"))
            codes.append(r.status_code)
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code))
    assert codes == [403] * mod._SETUP_CODE_MAX_FAILURES + [429]
    assert r.status_code == 429 and "retry-after" in r.headers
    assert mod._login_attempts == {}
    assert setup_route.calls == []


async def test_concurrent_wrong_codes_cannot_pass_the_limit(setup_route, monkeypatch):
    from services import setup_code

    mod = setup_route.mod
    checked = []
    real = setup_code.matches

    def counting(offered):
        checked.append(offered)
        return real(offered)

    monkeypatch.setattr(setup_code, "matches", counting)
    async with _peer_app(setup_route.router) as c:
        rs = await asyncio.gather(*[c.post("/api/auth/setup", json=_setup_body("WRNG-WRNG-WRNG"))
                                    for _ in range(30)])
    assert len(checked) <= mod._SETUP_CODE_MAX_FAILURES
    assert sorted(r.status_code for r in rs) == [403] * mod._SETUP_CODE_MAX_FAILURES + [429] * (30 - mod._SETUP_CODE_MAX_FAILURES)


async def test_the_login_ceiling_never_blocks_the_first_password(setup_route):
    mod = setup_route.mod
    for i in range(mod._RATE_LIMIT_GLOBAL_MAX_FAILURES):
        mod._record_failed_attempt(f"10.1.{i // 250}.{i % 250}")
    mod._record_failed_attempt("127.0.0.1")
    async with _peer_app(setup_route.router) as c:
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code))
    assert r.status_code == 200, r.text


async def test_a_second_setup_says_a_password_is_set(setup_route):
    from routers.auth import SETUP_ALREADY_DONE

    async with _peer_app(setup_route.router) as c:
        assert (await c.post("/api/auth/setup", json=_setup_body(setup_route.code))).status_code == 200
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code))
    assert r.status_code == 400 and r.json()["detail"] == SETUP_ALREADY_DONE
    assert setup_route.calls == ["a long enough one"]


async def test_a_double_submit_sets_the_password_once(setup_route):
    async with _peer_app(setup_route.router) as c:
        rs = await asyncio.gather(*[c.post("/api/auth/setup", json=_setup_body(setup_route.code, f"password {i}"))
                                    for i in range(3)])
    assert sorted(r.status_code for r in rs) == [200, 400, 400]
    assert len(setup_route.calls) == 1


async def test_a_short_password_with_the_right_code_is_refused_and_the_code_kept(setup_route, code_file):
    async with _peer_app(setup_route.router) as c:
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code, password="short"))
    assert r.status_code == 400 and "8 characters" in r.json()["detail"]
    assert code_file.exists() and setup_route.calls == []


async def test_setup_remakes_a_missing_code_and_still_refuses_a_guess(setup_route, code_file):
    code_file.unlink()
    async with _peer_app(setup_route.router) as c:
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code))
    assert r.status_code == 403
    assert code_file.exists(), "the route makes a fresh code when the file is gone"
    assert setup_route.calls == []


@pytest.fixture
def status_route(monkeypatch):
    from routers import auth as auth_router
    from services import auth_service

    state = {"configured": False}

    class _Session:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    async def is_configured(session):
        return state["configured"]

    monkeypatch.setattr(auth_service, "is_password_configured", is_configured)
    monkeypatch.setattr(auth_router, "async_session", lambda: _Session())
    return auth_router, state


async def test_status_says_the_setup_form_needs_the_code(status_route, code_file):
    router_mod, state = status_route
    async with _peer_app(router_mod.router) as c:
        body = (await c.get("/api/auth/status")).json()
        assert body["configured"] is False and body["setup_code_required"] is True
        assert code_file.exists(), "the code the page asks for is on disk"
        state["configured"] = True
        body = (await c.get("/api/auth/status")).json()
        assert body["configured"] is True and body["setup_code_required"] is False


@pytest.mark.parametrize("peer,headers", [
    (("192.168.1.20", 50123), {}),                                         # LAN straight to :8000
    (("127.0.0.1", 50123), {"x-forwarded-host": "my-mac.local:3000"}),     # LAN through Next
    (("127.0.0.1", 50123), {"cf-connecting-ip": "198.51.100.7",
                            "x-forwarded-for": "198.51.100.7"}),           # the tunnel
    (("127.0.0.1", 50123), {"x-forwarded-for": "127.0.0.1, 203.0.113.5"}),
    (("203.0.113.5", 0), {"x-forwarded-for": "203.0.113.5"}),              # uvicorn rewrote it
])
async def test_the_first_password_is_refused_from_anywhere_else_even_with_the_code(setup_route, peer, headers):
    async with _peer_app(setup_route.router, peer=peer) as c:
        r = await c.post("/api/auth/setup", json=_setup_body(setup_route.code), headers=headers)
    assert r.status_code == 403
    assert "localhost:3000" in r.json()["detail"]
    assert setup_route.calls == []


# --------------------------------------------------------------- an install with no password --

def _guarded_app():
    from middleware.auth import AuthMiddleware

    app = FastAPI()
    app.add_middleware(AuthMiddleware)

    @app.get("/api/memories")
    async def memories():
        return {"private": True}

    @app.post("/api/skills/shell")
    async def flip():
        return {"flipped": True}

    @app.get("/api/auth/status")
    async def status():
        return {"configured": False}

    @app.post("/api/auth/setup")
    async def setup():
        return {"ok": True}

    @app.get("/api/push/vapid-key")
    async def vapid():
        return {"key": "public"}

    @app.get("/health")
    async def health():
        return {"ok": True}

    @app.get("/")
    async def root():
        return {"ok": True}

    return app


@pytest.fixture
def no_password(monkeypatch):
    from services import auth_service

    monkeypatch.setattr(auth_service, "is_password_configured_cached", lambda: False)


async def test_before_setup_the_private_api_is_closed(no_password):
    from middleware.auth import NOT_SET_UP

    async with AsyncClient(transport=ASGITransport(app=_guarded_app()), base_url="http://t") as c:
        r = await c.get("/api/memories")
        assert r.status_code == 401 and r.json()["detail"] == NOT_SET_UP
        assert (await c.post("/api/skills/shell")).status_code == 401


async def test_before_setup_the_first_run_routes_answer(no_password):
    async with AsyncClient(transport=ASGITransport(app=_guarded_app()), base_url="http://t") as c:
        for method, path in (("get", "/health"), ("get", "/"), ("get", "/api/auth/status"),
                             ("post", "/api/auth/setup"), ("get", "/api/push/vapid-key")):
            r = await getattr(c, method)(path)
            assert r.status_code == 200, (path, r.text)


async def test_before_setup_a_valid_session_still_counts(no_password):
    from services.auth_service import create_access_token

    async with AsyncClient(transport=ASGITransport(app=_guarded_app()), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        assert (await c.get("/api/memories")).status_code == 200
    async with AsyncClient(transport=ASGITransport(app=_guarded_app()), base_url="http://t",
                           cookies={"edward_token": "forged"}) as c:
        assert (await c.get("/api/memories")).status_code == 401


async def test_after_setup_the_messages_are_the_usual_ones(monkeypatch):
    from services import auth_service

    monkeypatch.setattr(auth_service, "is_password_configured_cached", lambda: True)
    async with AsyncClient(transport=ASGITransport(app=_guarded_app()), base_url="http://t") as c:
        r = await c.get("/api/memories")
        assert r.status_code == 401 and r.json()["detail"] == "Authentication required"
    async with AsyncClient(transport=ASGITransport(app=_guarded_app()), base_url="http://t",
                           cookies={"edward_token": "forged"}) as c:
        r = await c.get("/api/memories")
        assert r.status_code == 401 and r.json()["detail"] == "Invalid or expired token"


def test_the_public_list_has_not_grown():
    """Every public route checks its own token, a signature, or serves
    nothing private — a new one belongs in this list deliberately."""
    from middleware.auth import PUBLIC_ROUTES

    assert PUBLIC_ROUTES == {
        "/api/auth/status", "/api/auth/login", "/api/auth/setup",
        "/api/webhook/twilio", "/api/webhook/twilio/whatsapp",
        "/api/push/vapid-key", "/api/push/status",
        "/api/widget", "/api/widget/chat",
        "/api/presence/beacon", "/api/presence/stream",
        "/api/alexa", "/api/gmail/oauth/callback", "/api/health/email",
    }
