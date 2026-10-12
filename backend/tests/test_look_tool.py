"""``look`` (AREA 2, 2026-10-11; tools-spec §3.7): Edward sees images,
PDFs and screens as image/document blocks, not a second model's words.

Targets: a path, an http(s) URL, attachment:<rowid>, media:<id>, screen,
chrome, browser. AREA 3's ``media.view`` resolves what it knows; its
``unsupported:`` (or its absence) sends look down its own path. Images are
JPEG/PNG/GIF/WebP ≤ 1600 px on the long edge, never upscaled (sips). Under
Codex the answer is today's described text. A turn carries at most 40
images. No test captures the display or Ellis's Chrome (conftest), reaches
the network, or calls a model."""

import base64
import shutil
import struct
import subprocess
import sys
import types
import zlib
from pathlib import Path

import httpx
import pytest

from services.toolset import look as lk
from services.toolset._compat import ToolFailure

pytestmark = pytest.mark.unit

needs_sips = pytest.mark.skipif(shutil.which("sips") is None, reason="needs macOS sips")


def png_bytes(w: int, h: int) -> bytes:
    def chunk(kind, data):
        c = kind + data
        return struct.pack(">I", len(data)) + c + struct.pack(">I", zlib.crc32(c) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\x80\x40\x20" * w for _ in range(h))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b""))


def jpeg_of(tmp_path, w, h) -> bytes:
    src = tmp_path / f"src-{w}x{h}.png"
    src.write_bytes(png_bytes(w, h))
    out = tmp_path / f"src-{w}x{h}.jpg"
    subprocess.run(["sips", "-s", "format", "jpeg", str(src), "--out", str(out)], capture_output=True, check=True)
    return out.read_bytes()


def size_of(data: bytes, tmp_path) -> tuple:
    p = tmp_path / "probe.img"
    p.write_bytes(data)
    out = subprocess.run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(p)], capture_output=True, text=True).stdout
    w = int(out.split("pixelWidth:")[1].split()[0])
    h = int(out.split("pixelHeight:")[1].split()[0])
    return w, h


def images(blocks):
    return [b for b in blocks if b.get("type") == "image"]


def texts(blocks):
    return "\n".join(b["text"] for b in blocks if b.get("type") == "text")


async def call(**args):
    return await lk.look.ainvoke(args)


@pytest.fixture(autouse=True)
def _claude_and_no_media(monkeypatch):
    monkeypatch.setattr(lk, "_on_claude", lambda: True)
    monkeypatch.setattr(lk, "_media_view", None)      # AREA 3's module: absent unless a test installs it
    lk._turn_counts.clear()
    monkeypatch.setattr(lk, "_turn_key", lambda: "turn-1")

    from services import headless_browser as hb
    hb._dns_cache.clear()

    async def resolve(host):
        return ["93.184.216.34"]
    monkeypatch.setattr(hb, "_resolve", resolve)


@needs_sips
class TestFiles:
    async def test_a_small_png_comes_back_as_an_image_block_unscaled(self, tmp_path):
        p = tmp_path / "chart.png"
        p.write_bytes(png_bytes(40, 30))
        out = await call(target=str(p))
        imgs = images(out)
        assert len(imgs) == 1
        data = base64.b64decode(imgs[0]["source"]["data"])
        assert size_of(data, tmp_path) == (40, 30)          # never upscaled
        assert "chart.png" in texts(out)

    async def test_a_big_image_is_bounded_to_1600(self, tmp_path):
        p = tmp_path / "big.png"
        p.write_bytes(png_bytes(2400, 1200))
        out = await call(target=str(p))
        img = images(out)[0]
        assert img["source"]["media_type"] == "image/jpeg"
        assert size_of(base64.b64decode(img["source"]["data"]), tmp_path) == (1600, 800)

    async def test_heic_is_converted(self, tmp_path):
        src = tmp_path / "s.png"
        src.write_bytes(png_bytes(64, 48))
        heic = tmp_path / "IMG_1.heic"
        r = subprocess.run(["sips", "-s", "format", "heic", str(src), "--out", str(heic)], capture_output=True)
        if r.returncode != 0 or not heic.exists():
            pytest.skip("this Mac's sips can't write HEIC")
        out = await call(target=str(heic))
        img = images(out)[0]
        assert img["source"]["media_type"] == "image/jpeg"
        assert base64.b64decode(img["source"]["data"])[:2] == b"\xff\xd8"

    async def test_a_pdf_is_a_document_block(self, tmp_path):
        p = tmp_path / "menu.pdf"
        p.write_bytes(b"%PDF-1.4\n1 0 obj<</Type/Page>>endobj\n%%EOF")
        out = await call(target=str(p), question="what's the price of the burger?")
        docs = [b for b in out if b.get("type") == "document"]
        assert docs and docs[0]["source"]["media_type"] == "application/pdf"
        assert "burger" in texts(out)

    async def test_text_and_missing_files_fail_honestly(self, tmp_path):
        t = tmp_path / "notes.txt"
        t.write_text("hello")
        with pytest.raises(ToolFailure, match="not an image"):
            await call(target=str(t))
        with pytest.raises(ToolFailure, match="no file"):
            await call(target=str(tmp_path / "gone.jpg"))

    async def test_a_path_the_rails_deny_is_refused(self, tmp_path, monkeypatch):
        p = tmp_path / "x.png"
        p.write_bytes(png_bytes(4, 4))
        monkeypatch.setattr(lk, "_can_read", lambda path: False)
        with pytest.raises(ToolFailure, match="can't read"):
            await call(target=str(p))


@needs_sips
class TestMedia:
    async def test_media_view_answers_first(self, tmp_path, monkeypatch):
        jpeg = jpeg_of(tmp_path, 20, 10)
        mv = types.SimpleNamespace(kind="image", images=[jpeg], text="attachment:449321 · image from Parker, 7:20 pm",
                                   pdf=None, error=None)

        async def view(ref, *, max_images=6):
            assert ref == "attachment:449321" and max_images == 6
            return mv
        monkeypatch.setattr(lk, "_media_view", view)
        out = await call(target="attachment:449321")
        assert len(images(out)) == 1 and "image from Parker" in texts(out)

    async def test_media_errors_are_failures(self, monkeypatch):
        async def view(ref, *, max_images=6):
            return types.SimpleNamespace(kind="image", images=[], text="", pdf=None,
                                         error="that attachment isn't on this Mac yet (Messages hasn't downloaded it)")
        monkeypatch.setattr(lk, "_media_view", view)
        with pytest.raises(ToolFailure, match="isn't on this Mac yet"):
            await call(target="attachment:5")

    async def test_unsupported_falls_back_to_looks_own_path(self, tmp_path, monkeypatch):
        jpeg = jpeg_of(tmp_path, 30, 20)

        async def view(ref, *, max_images=6):
            return types.SimpleNamespace(kind="link", images=[], text="", pdf=None, error="unsupported: a URL")
        monkeypatch.setattr(lk, "_media_view", view)

        def handler(request):
            return httpx.Response(200, content=jpeg, headers={"content-type": "image/jpeg"})
        monkeypatch.setattr(lk, "_http_client", lambda **kw: httpx.AsyncClient(transport=httpx.MockTransport(handler), **kw))
        out = await call(target="https://example.com/photo.jpg")
        assert len(images(out)) == 1 and "example.com" in texts(out)

    async def test_without_media_an_attachment_is_found_in_messages(self, tmp_path, monkeypatch):
        p = tmp_path / "IMG_3533.png"
        p.write_bytes(png_bytes(12, 12))
        import services.heartbeat.listener_imessage as li
        monkeypatch.setattr(li, "find_attachment", lambda rowid: {"path": str(p), "name": "IMG_3533.png",
                                                                   "mime_type": "image/png"})
        out = await call(target="attachment:77")
        assert len(images(out)) == 1 and "IMG_3533.png" in texts(out)

    async def test_urls_inside_are_refused(self):
        with pytest.raises(ToolFailure):
            await call(target="http://127.0.0.1:8000/api/screen/look")


@needs_sips
class TestScreens:
    async def test_screen_is_the_displays_picture(self, tmp_path, monkeypatch):
        from services import browser_hands as bh
        jpeg = jpeg_of(tmp_path, 3200, 1800)

        async def display():
            return jpeg, "this Mac's display"
        monkeypatch.setattr(bh, "screenshot_display", display)
        out = await call(target="screen")
        img = images(out)[0]
        assert size_of(base64.b64decode(img["source"]["data"]), tmp_path) == (1600, 900)
        assert "display" in texts(out)

    async def test_the_suite_never_captures_the_display(self):
        """conftest's guard: look('screen') in a test fails instead of
        running screencapture on this Mac (the critique's rule)."""
        with pytest.raises(ToolFailure, match="tests never capture"):
            await call(target="screen")

    async def test_chrome_needs_the_extension_and_never_activates_a_tab(self, tmp_path, monkeypatch):
        from services import browser_ext_service as bx
        from services import browser_hands as bh
        monkeypatch.delenv("EDWARD_CHROME_HANDS", raising=False)      # off by default
        with pytest.raises(ToolFailure, match="Chrome hands are off"):
            await call(target="chrome")
        monkeypatch.setenv("EDWARD_CHROME_HANDS", "on")
        monkeypatch.setattr(bx, "connected", lambda: False)
        with pytest.raises(ToolFailure, match="extension"):
            await call(target="chrome")
        monkeypatch.setattr(bx, "connected", lambda: True)
        monkeypatch.setattr(bh, "working_tab", lambda: 3)
        jpeg = jpeg_of(tmp_path, 100, 80)

        async def shot(target=None, **kw):
            return jpeg, "https://www.youtube.com/watch?v=x"
        monkeypatch.setattr(bh, "screenshot_target", shot)
        out = await call(target="chrome")
        assert images(out) and "youtube.com" in texts(out)

    async def test_browser_is_his_own_page(self, tmp_path, monkeypatch):
        from services import headless_browser as hb
        jpeg = jpeg_of(tmp_path, 100, 80)

        async def shot(conv):
            return jpeg
        monkeypatch.setattr(hb, "screenshot", shot)
        monkeypatch.setattr(hb, "has_page", lambda conv: True)
        out = await call(target="browser")
        assert images(out)


@needs_sips
class TestLimitsAndProviders:
    async def test_a_turn_carries_at_most_40_images(self, tmp_path, monkeypatch):
        p = tmp_path / "a.png"
        p.write_bytes(png_bytes(8, 8))
        lk._turn_counts["turn-1"] = [lk.TURN_MAX_IMAGES, 0]
        with pytest.raises(ToolFailure, match="as many images"):
            await call(target=str(p))

    async def test_the_count_is_seeded_from_the_ping(self, tmp_path, monkeypatch):
        p = tmp_path / "a.png"
        p.write_bytes(png_bytes(8, 8))
        fake_hooks = types.SimpleNamespace(current_ping=lambda: {"media": {"images": lk.TURN_MAX_IMAGES, "bytes": 0}})
        monkeypatch.setitem(sys.modules, "services.pings.hooks", fake_hooks)
        lk._turn_counts.clear()
        with pytest.raises(ToolFailure, match="as many images"):
            await call(target=str(p))

    async def test_codex_gets_words(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lk, "_on_claude", lambda: False)
        p = tmp_path / "a.png"
        p.write_bytes(png_bytes(8, 8))

        async def describe(path, *, priority="background"):
            assert priority == "interactive"
            return "a tiny orange square"
        import services.attachment_vision as av
        monkeypatch.setattr(av, "describe_image", describe)
        out = await call(target=str(p))
        assert isinstance(out, str) and "tiny orange square" in out


class TestTheReadRail:
    """Review 2026-10-12: look read through ``rails.can_read``, which a case
    variant got past; now it compares identities, and the file is opened
    without following a link and checked again after the open."""

    @pytest.fixture
    def repo(self, tmp_path, monkeypatch):
        repo = tmp_path / "edward"
        (repo / "backend").mkdir(parents=True)
        monkeypatch.setenv("EDWARD_LIVE_REPO", str(repo))
        monkeypatch.setenv("EDWARD_HOME", str(tmp_path / "edward-home"))
        (tmp_path / "edward-home").mkdir()
        # The real rail (the autouse fixture leaves _can_read alone).
        return repo

    async def test_a_case_variant_of_a_credential_is_refused(self, repo, tmp_path):
        try:
            if __import__("os").pathconf(str(tmp_path), 11) != 0:
                pytest.skip("this volume compares names case-sensitively")
        except (OSError, ValueError):
            pytest.skip("can't tell how this volume compares names")
        key = repo / "backend" / ".jwt_secret"
        key.write_bytes(png_bytes(2, 2))        # would come back as a picture if read
        for spelling in (str(key).upper(), str(repo) + "/BACKEND/.Jwt_Secret"):
            with pytest.raises(ToolFailure, match="can't read"):
                await call(target=spelling)

    async def test_the_media_viewer_is_not_asked_about_a_credential(self, repo, monkeypatch):
        key = repo / "backend" / ".setup_code"
        key.write_bytes(png_bytes(2, 2))
        asked = []

        async def view(ref, *, max_images=6):
            asked.append(ref)
            return None
        monkeypatch.setattr(lk, "_media_view", view)
        with pytest.raises(ToolFailure, match="can't read"):
            await call(target=str(key))
        assert asked == []

    async def test_a_link_to_a_credential_is_refused(self, repo, tmp_path):
        import os
        key = repo / "backend" / ".jwt_secret"
        key.write_bytes(png_bytes(2, 2))
        link = tmp_path / "edward-home" / "pic.png"
        os.symlink(key, link)
        with pytest.raises(ToolFailure, match="can't read"):
            await call(target=str(link))
