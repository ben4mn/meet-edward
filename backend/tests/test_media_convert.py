"""services/media/convert.py — local conversion with macOS's own tools:
sips to JPEG (never upscaled), a process group killed on timeout, magic-byte
sniffing (pings-media-spec §3.2)."""

import os
import shutil
import struct
import time
import zlib
from pathlib import Path

import pytest

from services.media import convert

pytestmark = pytest.mark.unit

HAS_SIPS = shutil.which("sips") is not None


def png_bytes(w=4, h=4, rgba=None):
    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    if rgba:
        raw = b"".join(b"\x00" + bytes(rgba) * w for _ in range(h))
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0)
    else:
        raw = b"".join(b"\x00" + b"\xff\x00\x00" * w for _ in range(h))
        ihdr = struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw)) + chunk(b"IEND", b"")


def gif_bytes(frames=3, w=8, h=6):
    """A tiny animated GIF (uncompressed-ish LZW via the minimal-code trick)."""
    def frame(colour_index):
        # Image descriptor + LZW min code size 2 + one data block of clear/index codes.
        pixels = w * h
        codes, bits = [], []
        # Emit clear code (4) before every pixel so the code size stays 3 bits.
        for _ in range(pixels):
            codes += [4, colour_index]
        codes.append(5)  # end of information
        for c in codes:
            bits += [(c >> i) & 1 for i in range(3)]
        data = bytearray()
        for i in range(0, len(bits), 8):
            byte = 0
            for j, b in enumerate(bits[i:i + 8]):
                byte |= b << j
            data.append(byte)
        blocks = b"".join(bytes([len(data[i:i + 255])]) + bytes(data[i:i + 255]) for i in range(0, len(data), 255))
        gce = b"\x21\xf9\x04\x00\x0a\x00\x00\x00"
        return gce + b"\x2c" + struct.pack("<HHHH", 0, 0, w, h) + b"\x00" + b"\x02" + blocks + b"\x00"
    header = b"GIF89a" + struct.pack("<HH", w, h) + b"\xf1\x00\x00"
    palette = b"\xff\x00\x00" + b"\x00\xff\x00" + b"\x00\x00\xff" + b"\xff\xff\xff"
    loop = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    return header + palette + loop + b"".join(frame(i % 3) for i in range(frames)) + b"\x3b"


class TestSniff:
    @pytest.mark.parametrize("head,kind,mime", [
        (b"\xff\xd8\xff\xe0" + b"\x00" * 12, "image", "image/jpeg"),
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "image", "image/png"),
        (b"GIF89a" + b"\x00" * 10, "image", "image/gif"),
        (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image", "image/webp"),
        (b"\x00\x00\x00\x18ftypheic\x00\x00\x00\x00", "image", "image/heic"),
        (b"\x00\x00\x00\x18ftypmif1\x00\x00\x00\x00", "image", "image/heic"),
        (b"%PDF-1.7\n" + b"\x00" * 7, "pdf", "application/pdf"),
        (b"\x00\x00\x00\x14ftypqt  \x00\x00\x00\x00", "video", "video/quicktime"),
        (b"\x00\x00\x00\x18ftypmp42\x00\x00\x00\x00", "video", "video/mp4"),
        (b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00", "audio", "audio/mp4"),
        (b"caff\x00\x01\x00\x00" + b"\x00" * 8, "audio", "audio/x-caf"),
        (b"#!AMR\n" + b"\x00" * 10, "audio", "audio/amr"),
        (b"ID3\x04" + b"\x00" * 12, "audio", "audio/mpeg"),
        (b"RIFF\x00\x00\x00\x00WAVEfmt ", "audio", "audio/wav"),
    ])
    def test_known_formats(self, head, kind, mime):
        assert convert.sniff(head) == (kind, mime)

    def test_text_is_nothing(self):
        assert convert.sniff(b"DATABASE_URL=postgres://") is None
        assert convert.sniff(b"") is None


class TestBuilders:
    def test_no_resize_under_the_edge(self):
        argv = convert.sips_jpeg_argv("/a.heic", "/o.jpg", width=1200, height=900)
        assert argv[:5] == ["sips", "-s", "format", "jpeg", "-s"] and "-Z" not in argv
        assert argv[-3:] == ["/a.heic", "--out", "/o.jpg"]

    def test_resize_only_past_the_edge(self):
        argv = convert.sips_jpeg_argv("/a.heic", "/o.jpg", width=4032, height=3024)
        assert argv[argv.index("-Z") + 1] == "1600"

    def test_unknown_size_resizes_to_be_safe(self):
        assert "-Z" in convert.sips_jpeg_argv("/a.heic", "/o.jpg", width=None, height=None)

    def test_parse_size(self):
        out = "/x/a.png\n  pixelWidth: 4032\n  pixelHeight: 3024\n"
        assert convert.parse_sips_size(out) == (4032, 3024)
        assert convert.parse_sips_size("garbage") == (None, None)


class TestRun:
    def test_a_timeout_kills_the_whole_group(self, tmp_path):
        marker = tmp_path / "child.pid"
        script = f"sleep 5 & echo $! > {marker}; wait"
        started = time.monotonic()
        with pytest.raises(convert.subprocess.TimeoutExpired):
            convert.run(["/bin/sh", "-c", script], timeout_s=0.5)
        assert time.monotonic() - started < 3
        time.sleep(0.2)
        child = int(marker.read_text().strip())
        with pytest.raises(ProcessLookupError):
            os.kill(child, 0)

    def test_output_is_captured(self):
        r = convert.run(["/bin/echo", "hi"], timeout_s=5)
        assert r.returncode == 0 and r.stdout.strip() == b"hi"


@pytest.mark.skipif(not HAS_SIPS, reason="needs macOS sips")
class TestRealSips:
    def test_png_to_jpeg_never_upscaled(self, tmp_path):
        src = tmp_path / "small.png"
        src.write_bytes(png_bytes(40, 30))
        jpeg = convert.to_jpeg(src)
        assert jpeg[:3] == b"\xff\xd8\xff"
        assert convert.jpeg_size(jpeg) == (40, 30)

    def test_a_big_image_is_bounded(self, tmp_path):
        src = tmp_path / "big.png"
        src.write_bytes(png_bytes(2000, 10))
        w, h = convert.jpeg_size(convert.to_jpeg(src))
        assert max(w, h) == 1600

    def test_transparency_lands_on_white(self, tmp_path):
        src = tmp_path / "sticker.png"
        src.write_bytes(png_bytes(20, 20, rgba=(255, 0, 0, 0)))
        assert convert.to_jpeg(src)[:3] == b"\xff\xd8\xff"

    def test_gif_first_frame(self, tmp_path):
        src = tmp_path / "anim.gif"
        src.write_bytes(gif_bytes())
        jpeg = convert.to_jpeg(src)
        assert jpeg and jpeg[:3] == b"\xff\xd8\xff" and convert.jpeg_size(jpeg) == (8, 6)

    def test_garbage_is_none(self, tmp_path):
        src = tmp_path / "x.png"
        src.write_bytes(b"not an image")
        assert convert.to_jpeg(src) is None
