"""The household photo door: /photos (2026-10-11).

A small PWA with its own password for someone else in the household
(Rowan, here): they see the photos the hub's screensaver shows, add more,
edit them (rotate, delete) and set the screensaver. Nothing else. The
rules worth pinning:

1. The door's password opens only the door. Its session is a different
   cookie with a different subject, so it can never read Ellis's chat,
   memory or anything else under /api — and Ellis's password does not log
   in through the door.
2. Ellis's own session may use the door's routes (the owner is the house).
3. The door's routes act on the same album and the same settings row the
   hub reads; its edits are recorded as the door's.
4. The door is off unless PHOTO_DOOR_PASSWORD_HASH holds a bcrypt hash:
   no built-in password, a refused login, and no rooms behind it.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import bcrypt
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

# Made up here, hashed here: the door has no password of its own.
DOOR_PASSWORD = "plum-teapot-42"
_DOOR_HASH = bcrypt.hashpw(DOOR_PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode()


@pytest.fixture(autouse=True)
def door_open(monkeypatch):
    monkeypatch.setenv("PHOTO_DOOR_PASSWORD_HASH", _DOOR_HASH)


@pytest.fixture
def door_closed(monkeypatch):
    monkeypatch.delenv("PHOTO_DOOR_PASSWORD_HASH", raising=False)


@pytest.fixture
def album(tmp_path, monkeypatch):
    from services import hub_photos_service

    monkeypatch.setattr(hub_photos_service, "PHOTOS_DIR", tmp_path)
    monkeypatch.setattr(hub_photos_service, "THUMBS_DIR", tmp_path.parent / f"{tmp_path.name}-thumbs")
    return tmp_path


@pytest.fixture
def client(monkeypatch, album):
    from middleware.auth import AuthMiddleware
    from routers import auth as auth_router
    from routers import photo_door
    from services import auth_service

    monkeypatch.setattr(auth_service, "is_password_configured_cached", lambda: True)
    auth_router._login_attempts.clear()
    app = FastAPI()
    app.add_middleware(AuthMiddleware)
    app.include_router(photo_door.router, prefix="/api")

    @app.get("/api/memories")
    async def owners_memories():
        return {"secret": "Ellis's"}

    return TestClient(app)


# ---------------------------------------------------------------------------
# The password and the session
# ---------------------------------------------------------------------------


def test_the_door_password_is_the_configured_one_and_nothing_else():
    from services import photo_door_auth

    assert photo_door_auth.check_password(DOOR_PASSWORD) is True
    assert photo_door_auth.check_password(DOOR_PASSWORD.upper()) is False
    assert photo_door_auth.check_password("") is False
    assert photo_door_auth.check_password(DOOR_PASSWORD + " ") is False


def test_a_door_token_is_never_the_owners():
    from services import auth_service, photo_door_auth

    door = photo_door_auth.create_token()
    assert photo_door_auth.verify_token(door) is True
    assert auth_service.verify_token(door) is False, "a door token must not open Ellis's app"
    assert photo_door_auth.verify_token(auth_service.create_access_token()) is False


def test_login_sets_the_doors_own_cookie(client):
    r = client.post("/api/photo-door/auth/login", json={"password": DOOR_PASSWORD})
    assert r.status_code == 200
    set_cookie = r.headers.get("set-cookie", "")
    assert "photo_door_token=" in set_cookie and "HttpOnly" in set_cookie and "Secure" in set_cookie
    assert "edward_token=" not in set_cookie


def test_a_wrong_password_is_refused(client):
    r = client.post("/api/photo-door/auth/login", json={"password": "edward"})
    assert r.status_code == 401
    assert "photo_door_token=" not in r.headers.get("set-cookie", "")


def test_status_says_whether_the_door_is_signed_in(client):
    from services import photo_door_auth

    assert client.get("/api/photo-door/auth/status").json() == {"authenticated": False, "enabled": True}
    client.cookies.set("photo_door_token", photo_door_auth.create_token())
    assert client.get("/api/photo-door/auth/status").json() == {"authenticated": True, "enabled": True}


def test_the_door_routes_need_a_session(client, album):
    assert client.get("/api/photo-door/photos").status_code == 401
    assert client.get("/api/photo-door/settings").status_code == 401


def test_a_door_session_opens_only_the_door(client, album):
    from services import photo_door_auth

    client.cookies.set("photo_door_token", photo_door_auth.create_token())
    assert client.get("/api/photo-door/photos").status_code == 200
    assert client.get("/api/memories").status_code == 401, "a door session must never reach Ellis's data"


def test_a_door_token_in_the_owners_cookie_opens_nothing(client):
    from services import photo_door_auth

    client.cookies.set("edward_token", photo_door_auth.create_token())
    assert client.get("/api/memories").status_code == 401


def test_the_owners_session_may_use_the_door(client, album):
    from services import auth_service

    client.cookies.set("edward_token", auth_service.create_access_token())
    assert client.get("/api/photo-door/photos").status_code == 200


def test_a_door_session_is_renewed_past_half_life(client, album):
    from services import photo_door_auth

    old = photo_door_auth.create_token(expires_delta=timedelta(days=10))
    client.cookies.set("photo_door_token", old)
    r = client.get("/api/photo-door/photos")
    assert r.status_code == 200
    renewed = r.cookies.get("photo_door_token")
    assert renewed and renewed != old and photo_door_auth.verify_token(renewed)


def test_logout_clears_the_door_cookie(client):
    from services import photo_door_auth

    client.cookies.set("photo_door_token", photo_door_auth.create_token())
    r = client.post("/api/photo-door/auth/logout")
    assert r.status_code == 200
    assert 'photo_door_token=""' in r.headers.get("set-cookie", "") or "Max-Age=0" in r.headers.get("set-cookie", "")


# ---------------------------------------------------------------------------
# The album
# ---------------------------------------------------------------------------


@pytest.fixture
def signed_in(client):
    from services import photo_door_auth

    client.cookies.set("photo_door_token", photo_door_auth.create_token())
    return client


def _jpeg(path: Path, w: int = 40, h: int = 20) -> None:
    """A real JPEG via sips, so rotate and thumbnails run for real."""
    import shutil
    import subprocess

    if shutil.which("sips") is None:
        pytest.skip("needs macOS sips")
    bmp = path.with_suffix(".bmp")
    row = b"\x00\x00\xff" * w + b"\x00" * ((4 - (w * 3) % 4) % 4)
    pixels = row * h
    header = (
        b"BM" + (54 + len(pixels)).to_bytes(4, "little") + b"\x00\x00\x00\x00" + (54).to_bytes(4, "little")
        + (40).to_bytes(4, "little") + w.to_bytes(4, "little") + h.to_bytes(4, "little")
        + (1).to_bytes(2, "little") + (24).to_bytes(2, "little") + b"\x00" * 24
    )
    bmp.write_bytes(header + pixels)
    subprocess.run(["sips", "-s", "format", "jpeg", str(bmp), "--out", str(path)], capture_output=True, check=True)
    bmp.unlink()


def _size(path: Path) -> tuple[int, int]:
    import subprocess

    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)], capture_output=True, text=True).stdout
    vals = {line.split(":")[0].strip(): int(line.split(":")[1]) for line in out.splitlines()[1:]}
    return vals["pixelWidth"], vals["pixelHeight"]


def test_the_door_sees_the_album_without_the_hosts_paths(signed_in, album):
    (album / "a.jpg").write_bytes(b"x")
    (album / "b.png").write_bytes(b"x")
    body = signed_in.get("/api/photo-door/photos").json()
    assert sorted(body["photos"]) == ["a.jpg", "b.png"]
    assert "dir" not in body


def test_the_door_can_upload_and_delete(signed_in, album):
    r = signed_in.post("/api/photo-door/photos", files=[("files", ("new.jpg", b"jpegbytes", "image/jpeg"))])
    assert r.status_code == 200 and r.json()["saved_count"] == 1
    assert (album / "new.jpg").exists()
    assert signed_in.delete("/api/photo-door/photos/new.jpg").status_code == 200
    assert not (album / "new.jpg").exists()
    assert signed_in.delete("/api/photo-door/photos/new.jpg").status_code == 404


def test_the_door_cannot_reach_outside_the_album(signed_in, album):
    secret = album.parent / "secret.jpg"
    secret.write_bytes(b"x")
    assert signed_in.get("/api/photo-door/photos/..%2Fsecret.jpg").status_code == 404
    assert signed_in.delete("/api/photo-door/photos/..%2Fsecret.jpg").status_code == 404
    assert secret.exists()


def test_rotate_writes_a_new_name_and_never_touches_the_old_file_in_place(album):
    """Hub rule 4: a photo in a visible slideshow slot is never mutated."""
    from services import hub_photos_service as svc

    _jpeg(album / "cat.jpg", 40, 20)
    new = svc.rotate_photo("cat.jpg", "right")
    assert new and new != "cat.jpg"
    assert not (album / "cat.jpg").exists()
    assert _size(album / new) == (20, 40)
    again = svc.rotate_photo(new, "left")
    assert again and _size(album / again) == (40, 20)
    assert "rotated-rotated" not in again


def test_rotate_refuses_what_it_cannot_rotate(album):
    from services import hub_photos_service as svc

    (album / "anim.gif").write_bytes(b"GIF89a")
    assert svc.rotate_photo("anim.gif", "right") is None
    assert svc.rotate_photo("missing.jpg", "right") is None
    assert svc.rotate_photo("../etc.jpg", "right") is None
    with pytest.raises(ValueError):
        svc.rotate_photo("anim.gif", "sideways")


def test_rotate_route(signed_in, album):
    _jpeg(album / "dog.jpg", 40, 20)
    r = signed_in.post("/api/photo-door/photos/dog.jpg/rotate", json={"direction": "left"})
    assert r.status_code == 200
    assert (album / r.json()["name"]).exists()
    assert signed_in.post("/api/photo-door/photos/dog.jpg/rotate", json={"direction": "left"}).status_code == 404
    assert signed_in.post(f"/api/photo-door/photos/{r.json()['name']}/rotate", json={"direction": "up"}).status_code == 422


def test_thumbnails_are_small_and_cached_outside_the_album(signed_in, album):
    from services import hub_photos_service as svc

    _jpeg(album / "big.jpg", 1200, 600)
    r = signed_in.get("/api/photo-door/photos/big.jpg/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert sorted(p.name for p in album.iterdir()) == ["big.jpg"], "thumbs never land in the album"
    thumb = next(Path(svc.THUMBS_DIR).iterdir())
    assert max(_size(thumb)) <= svc.THUMB_MAX_EDGE
    assert signed_in.get("/api/photo-door/photos/nope.jpg/thumb").status_code == 404


# ---------------------------------------------------------------------------
# The screensaver settings
# ---------------------------------------------------------------------------


def test_the_door_reads_and_sets_the_hubs_screensaver(signed_in, monkeypatch):
    from services import hub_settings_service

    stored: dict = {}

    async def get_settings():
        return hub_settings_service.normalize(stored.get("data"))

    async def update_settings(patch, updated_by="user"):
        stored["data"] = hub_settings_service.normalize({**(stored.get("data") or {}), **patch})
        stored["by"] = updated_by
        return stored["data"]

    monkeypatch.setattr(hub_settings_service, "get_settings", get_settings)
    monkeypatch.setattr(hub_settings_service, "update_settings", update_settings)

    assert signed_in.get("/api/photo-door/settings").json()["slide_seconds"] == hub_settings_service.DEFAULT_SLIDE_SECONDS
    r = signed_in.patch("/api/photo-door/settings", json={"slide_seconds": 12, "transition": "crossfade"})
    assert r.status_code == 200
    assert r.json()["slide_seconds"] == 12 and r.json()["transition"] == "crossfade"
    assert stored["by"] == "photo_door"


# ---------------------------------------------------------------------------
# A door with no password configured is no door
# ---------------------------------------------------------------------------


def test_there_is_no_built_in_password(door_closed):
    from services import photo_door_auth

    assert photo_door_auth.enabled() is False
    for guess in ("", "edward", "password", DOOR_PASSWORD):
        assert photo_door_auth.check_password(guess) is False


def test_a_closed_door_refuses_a_login_and_says_why(door_closed, client):
    r = client.post("/api/photo-door/auth/login", json={"password": DOOR_PASSWORD})
    assert r.status_code == 403
    assert "PHOTO_DOOR_PASSWORD_HASH" in r.json()["detail"]
    assert "photo_door_token=" not in r.headers.get("set-cookie", "")
    assert client.get("/api/photo-door/auth/status").json() == {"authenticated": False, "enabled": False}


def test_a_closed_door_has_no_rooms_behind_it(door_closed, client, album):
    from services import auth_service, photo_door_auth

    client.cookies.set("photo_door_token", photo_door_auth.create_token())
    assert photo_door_auth.verify_token(client.cookies.get("photo_door_token")) is False
    assert client.get("/api/photo-door/photos").status_code == 404
    assert client.get("/api/photo-door/settings").status_code == 404
    client.cookies.set("edward_token", auth_service.create_access_token())
    assert client.get("/api/photo-door/photos").status_code == 404, "the owner's session finds it closed too"


def test_a_hash_mangled_by_the_shell_keeps_the_door_closed(monkeypatch):
    """start.sh sources .env: an unquoted $2b$12$... loses its $ parts."""
    from services import photo_door_auth

    monkeypatch.setenv("PHOTO_DOOR_PASSWORD_HASH", _DOOR_HASH.replace("$2b$12$", "").replace("$2b$04$", ""))
    assert photo_door_auth.enabled() is False
    assert "single quotes" in photo_door_auth.disabled_message()


def test_the_routes_hold_without_the_middleware(door_closed, album):
    from routers import photo_door

    app = FastAPI()
    app.include_router(photo_door.router, prefix="/api")
    bare = TestClient(app)
    assert bare.get("/api/photo-door/photos").status_code == 404
    assert bare.get("/api/photo-door/settings").status_code == 404
