"""Tests for services/hub_canvas_service.py — the pet-station screen as
Edward's universal expression surface.

Why (2026-09-02, Ellis): Biscuit is out of recovery. The 7" screen that was
built for her doses becomes a generic canvas Edward uses at will: show a
movie, render an image, put up an alert, write a note, or run a small
HTML scene. Pure validation lives here; the DB row and the tool are thin.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.hub_canvas_service import (
    KINDS,
    MAX_HTML_CHARS,
    CanvasContent,
    is_expired,
    make_content,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 2, 23, 30, tzinfo=timezone.utc)


class TestMakeContent:
    def test_image_needs_https_or_local_url(self):
        c = make_content(kind="image", source="https://example.com/a.jpg", title="Sunset", seconds=120, now=NOW)
        assert c.kind == "image" and c.source.startswith("https://")
        with pytest.raises(ValueError):
            make_content(kind="image", source="javascript:alert(1)", title="x", seconds=10, now=NOW)

    def test_local_photo_path_is_allowed(self):
        c = make_content(kind="image", source="/api/hub/photos/biscuit.jpg", title=None, seconds=60, now=NOW)
        assert c.source == "/api/hub/photos/biscuit.jpg"

    def test_video_accepts_youtube_and_direct_files(self):
        yt = make_content(kind="video", source="https://www.youtube.com/watch?v=dQw4w9WgXcQ", title="movie", seconds=0, now=NOW)
        assert yt.embed_url == "https://www.youtube.com/embed/dQw4w9WgXcQ?autoplay=1&mute=0"
        mp4 = make_content(kind="video", source="https://example.com/clip.mp4", title=None, seconds=0, now=NOW)
        assert mp4.embed_url is None and mp4.source.endswith(".mp4")

    def test_alert_and_text_carry_body(self):
        a = make_content(kind="alert", body="Storm in 20 minutes. Bring Biscuit in.", title="Weather", seconds=300, now=NOW)
        assert a.body.startswith("Storm") and a.kind == "alert"
        with pytest.raises(ValueError):
            make_content(kind="alert", body="", title=None, seconds=10, now=NOW)

    def test_html_is_size_capped(self):
        make_content(kind="html", body="<h1>hi</h1>", title=None, seconds=60, now=NOW)
        with pytest.raises(ValueError):
            make_content(kind="html", body="x" * (MAX_HTML_CHARS + 1), title=None, seconds=60, now=NOW)

    def test_unknown_kind_rejected(self):
        with pytest.raises(ValueError):
            make_content(kind="hologram", body="x", title=None, seconds=1, now=NOW)
        assert set(KINDS) == {"image", "video", "alert", "text", "html"}

    def test_seconds_zero_means_until_cleared_and_is_clamped(self):
        forever = make_content(kind="text", body="hi", title=None, seconds=0, now=NOW)
        assert forever.expires_at is None
        long = make_content(kind="text", body="hi", title=None, seconds=10**9, now=NOW)
        assert long.expires_at == NOW + timedelta(hours=12)


class TestExpiry:
    def test_is_expired(self):
        c = make_content(kind="text", body="hi", title=None, seconds=60, now=NOW)
        assert is_expired(c, NOW + timedelta(seconds=59)) is False
        assert is_expired(c, NOW + timedelta(seconds=61)) is True
        assert is_expired(make_content(kind="text", body="hi", title=None, seconds=0, now=NOW), NOW + timedelta(days=9)) is False

    def test_as_dict_round_trip(self):
        c = make_content(kind="alert", body="b", title="t", seconds=30, now=NOW)
        d = c.as_dict()
        assert d["kind"] == "alert" and d["expires_at"].startswith("2026-09-02T23:30:30")
        assert CanvasContent.from_dict(d) == c


@pytest.mark.integration
async def test_canvas_round_trip_via_api(clean_db):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token
    from services import hub_canvas_service

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as client:
        r = await client.get("/api/hub/canvas")
        assert r.status_code == 200 and r.json()["content"] is None
        await hub_canvas_service.show(kind="alert", body="Dinner!", title="Edward", seconds=120)
        r = await client.get("/api/hub/canvas")
        assert r.json()["content"]["body"] == "Dinner!"
        r = await client.delete("/api/hub/canvas")
        assert r.status_code == 200
        r = await client.get("/api/hub/canvas")
        assert r.json()["content"] is None
