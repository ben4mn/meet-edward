"""services/attachment_vision.py — turn an attachment into something the
model can see (a base64 JPEG block) or read (a one-paragraph description)."""

import base64
import json
import struct
import zlib
from pathlib import Path

import pytest

from services import attachment_vision as av

pytestmark = pytest.mark.unit


def _png(path: Path, w=4, h=4):
    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
    data = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")
    path.write_bytes(data)
    return path


class TestPrepare:
    def test_png_becomes_a_jpeg_block(self, tmp_path):
        p = _png(tmp_path / "a.png")
        block = av.prepare_image_for_model(p)
        assert block["mime_type"] == "image/jpeg" and block["filename"] == "a.png"
        assert base64.b64decode(block["data"])[:2] == b"\xff\xd8"  # JPEG magic

    def test_a_small_image_is_never_upscaled(self, tmp_path):
        """2026-10-11: `sips -Z 1600` blew a 12 KB sticker up to 159 KB; the
        media converter resizes only past the edge."""
        from services.media.convert import jpeg_size

        block = av.prepare_image_for_model(_png(tmp_path / "s.png", w=40, h=30))
        assert jpeg_size(base64.b64decode(block["data"])) == (40, 30)

    def test_missing_or_oversized_or_non_image_is_none(self, tmp_path):
        assert av.prepare_image_for_model(tmp_path / "nope.jpg") is None
        big = tmp_path / "big.jpg"; big.write_bytes(b"x" * (av.MAX_IMAGE_BYTES + 1))
        assert av.prepare_image_for_model(big) is None
        txt = tmp_path / "a.txt"; txt.write_text("hi")
        assert av.prepare_image_for_model(txt) is None

    def test_attachments_from_raw_data_only_includes_readable_images(self, tmp_path):
        p = _png(tmp_path / "a.png")
        raw = json.dumps({"attachments": [
            {"rowid": 1, "mime_type": "image/png", "path": str(p), "name": "a.png"},
            {"rowid": 2, "mime_type": "image/png", "path": str(tmp_path / "gone.png"), "name": "gone.png"},
            {"rowid": 3, "mime_type": "application/pdf", "path": str(p), "name": "x.pdf"},
        ]})
        blocks = av.attachments_from_raw_data(raw)
        # Labelled with the ref, so saved history can say how to see it again.
        assert [b["filename"] for b in blocks] == ["attachment:1 · a.png"]
        assert av.attachments_from_raw_data(None) == [] and av.attachments_from_raw_data("junk") == []


class TestDescribe:
    async def test_describe_image_uses_vision_llm_and_caches_by_path(self, tmp_path, monkeypatch):
        p = _png(tmp_path / "a.png")
        calls = []

        class FakeLLM:
            async def ainvoke(self, msgs):
                calls.append(msgs)
                class R: content = "A red square."
                return R()

        monkeypatch.setattr(av, "build_background_llm", lambda **kw: FakeLLM())
        av._description_cache.clear()
        out = await av.describe_image(p)
        assert out == "A red square."
        blocks = calls[0][0].content
        assert any(b.get("type") == "image_url" and b["image_url"]["url"].startswith("data:image/jpeg;base64,") for b in blocks)
        assert await av.describe_image(p) == "A red square." and len(calls) == 1

    def test_the_old_line_describer_is_gone(self):
        """The listener describes each image with describe_image and notes it
        on its ``attachment:<rowid>`` ref (media.hooks.note_description);
        the old '#rowid' line builder had no caller left (pings-media-spec §7.3)."""
        assert not hasattr(av, "describe_attachments")
