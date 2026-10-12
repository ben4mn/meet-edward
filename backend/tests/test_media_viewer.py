"""services/media/viewer.py — ``media.view(ref)``, what AREA 2's ``look`` and
the ping turn see (pings-media-spec §3.2). Refs: ``attachment:<rowid>``,
``file:<id>`` and local paths; anything else is ``unsupported:`` so ``look``
uses its own path. Paths are resolved, opened without following a link,
and must hold the media their bytes say they hold."""

import os
from pathlib import Path

import pytest

from services import media
from services.media import viewer
from tests.test_media_convert import HAS_SIPS, png_bytes

pytestmark = pytest.mark.asyncio


def pdf_bytes(pages=1, pad=0):
    body = b"".join(b"%d 0 obj << /Type /Page /Parent 1 0 R >> endobj\n" % (i + 2) for i in range(pages))
    head = b"%PDF-1.4\n1 0 obj << /Type /Pages /Count " + str(pages).encode() + b" >> endobj\n"
    return head + body + b"x" * pad + b"\n%%EOF\n"


@pytest.fixture
def home(tmp_path, monkeypatch):
    """A fake home: viewer roots and denies are computed from it."""
    h = tmp_path / "home"
    (h / "edward-home").mkdir(parents=True)
    monkeypatch.setattr(viewer, "_home", lambda: h)
    return h


@pytest.fixture
def chatdb(monkeypatch, tmp_path):
    """A fake find_attachment over files in tmp_path."""
    rows = {}

    def fake_find(rowid):
        return rows.get(int(rowid))

    from services.heartbeat import listener_imessage
    monkeypatch.setattr(listener_imessage, "find_attachment", fake_find)
    return rows


class TestAttachment:
    @pytest.mark.skipif(not HAS_SIPS, reason="needs macOS sips")
    async def test_an_image_attachment_becomes_a_jpeg_never_upscaled(self, chatdb, tmp_path):
        p = tmp_path / "IMG_1.png"
        p.write_bytes(png_bytes(40, 30))
        chatdb[449321] = {"rowid": 449321, "mime_type": "image/png", "path": str(p), "name": "IMG_1.png",
                          "chat_identifier": "chat-trivia"}
        v = await media.view("attachment:449321")
        assert v.error is None and v.kind == "image" and len(v.images) == 1
        from services.media.convert import jpeg_size
        assert jpeg_size(v.images[0]) == (40, 30)
        assert v.text.startswith("attachment:449321 · image · IMG_1.png")

    @pytest.mark.skipif(not HAS_SIPS, reason="needs macOS sips")
    async def test_the_description_rides_along(self, chatdb, tmp_path):
        from services.media import hooks
        p = tmp_path / "IMG_2.png"
        p.write_bytes(png_bytes())
        chatdb[5] = {"rowid": 5, "mime_type": "image/png", "path": str(p), "name": "IMG_2.png"}
        hooks.note_description("attachment:5", "a red square")
        v = await media.view("attachment:5")
        assert '"a red square"' in v.text

    async def test_a_missing_attachment_is_an_error_not_an_exception(self, chatdb, tmp_path):
        chatdb[6] = {"rowid": 6, "mime_type": "image/heic", "path": str(tmp_path / "gone.heic"), "name": "gone.heic"}
        v = await media.view("attachment:6")
        assert v.error and "isn't on this Mac yet" in v.error
        v = await media.view("attachment:999")
        assert v.error and "isn't on this Mac yet" in v.error

    async def test_a_file_that_lies_about_being_an_image(self, chatdb, tmp_path):
        p = tmp_path / "fake.png"
        p.write_text("DATABASE_URL=postgres://secret")
        chatdb[7] = {"rowid": 7, "mime_type": "image/png", "path": str(p), "name": "fake.png"}
        v = await media.view("attachment:7")
        assert v.error and "isn't" in v.error and not v.images and "secret" not in (v.text + v.error)

    async def test_a_pdf_comes_back_whole(self, chatdb, tmp_path):
        p = tmp_path / "menu.pdf"
        p.write_bytes(pdf_bytes(3))
        chatdb[8] = {"rowid": 8, "mime_type": "application/pdf", "path": str(p), "name": "menu.pdf"}
        v = await media.view("attachment:8")
        assert v.kind == "pdf" and v.pdf == p.read_bytes() and "3 pages" in v.text

    async def test_a_pdf_over_100_pages_is_text(self, chatdb, tmp_path):
        p = tmp_path / "book.pdf"
        p.write_bytes(pdf_bytes(101))
        chatdb[9] = {"rowid": 9, "mime_type": "application/pdf", "path": str(p), "name": "book.pdf"}
        v = await media.view("attachment:9")
        assert v.pdf is None and "101 pages" in v.text and "too big to read whole" in v.text

    async def test_a_pdf_over_15_mb_is_text(self, chatdb, tmp_path, monkeypatch):
        monkeypatch.setattr(viewer, "PDF_MAX_BYTES", 1000)
        p = tmp_path / "big.pdf"
        p.write_bytes(pdf_bytes(2, pad=2000))
        chatdb[10] = {"rowid": 10, "mime_type": "application/pdf", "path": str(p), "name": "big.pdf"}
        v = await media.view("attachment:10")
        assert v.pdf is None and "too big to read whole" in v.text

    async def test_another_file_is_named_not_read(self, chatdb, tmp_path):
        p = tmp_path / "notes.txt"
        p.write_text("the wifi password is hunter2")
        chatdb[11] = {"rowid": 11, "mime_type": "text/plain", "path": str(p), "name": "notes.txt"}
        v = await media.view("attachment:11")
        assert v.kind == "file" and "notes.txt" in v.text and "hunter2" not in v.text


class TestFileRef:
    @pytest.mark.skipif(not HAS_SIPS, reason="needs macOS sips")
    async def test_an_uploaded_file(self, monkeypatch, tmp_path):
        p = tmp_path / "upload.png"
        p.write_bytes(png_bytes())

        async def fake_path(file_id):
            return p if file_id == "abc" else None

        from services import file_storage_service
        monkeypatch.setattr(file_storage_service, "get_file_path", fake_path)
        v = await media.view("file:abc")
        assert v.kind == "image" and v.images and v.text.startswith("file:abc")
        v = await media.view("file:nope")
        assert v.error and "no file" in v.error


class TestLocalPaths:
    @pytest.mark.skipif(not HAS_SIPS, reason="needs macOS sips")
    async def test_a_path_in_edwards_home(self, home):
        p = home / "edward-home" / "shot.png"
        p.write_bytes(png_bytes())
        v = await media.view(str(p))
        assert v.kind == "image" and v.images

    async def test_credential_stores_are_refused(self, home):
        for rel in (".env", ".ssh/id_rsa.png", "Library/Keychains/x.png", ".codex/auth.png"):
            p = home / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(png_bytes())
            v = await media.view(str(p))
            assert v.error and "not something I open" in v.error, rel

    async def test_a_symlink_to_a_denied_file_is_refused(self, home):
        secret = home / ".ssh" / "key.png"
        secret.parent.mkdir(parents=True)
        secret.write_bytes(png_bytes())
        link = home / "edward-home" / "innocent.png"
        os.symlink(secret, link)
        v = await media.view(str(link))
        assert v.error and not v.images

    async def test_outside_the_allowed_roots(self, home, tmp_path):
        v = await media.view("/etc/hosts")
        assert v.error

    async def test_a_missing_path(self, home):
        v = await media.view(str(home / "edward-home" / "nope.png"))
        assert v.error and "no such file" in v.error


class TestUnsupported:
    @pytest.mark.parametrize("ref", ["https://example.com/a.png", "media:12", "screen", "chrome", ""])
    async def test_look_uses_its_own_path(self, ref):
        v = await media.view(ref)
        assert v.error and v.error.startswith("unsupported:")

    async def test_media_off(self, monkeypatch, chatdb):
        monkeypatch.setenv("EDWARD_MEDIA", "off")
        v = await media.view("attachment:1")
        assert v.error and v.error.startswith("unsupported:")
