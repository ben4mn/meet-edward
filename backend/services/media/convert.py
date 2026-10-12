"""Local media conversion with the Mac's own tools (2026-10-11;
docs/architecture/media.md).

No ffmpeg, no Pillow: ``sips`` turns any image macOS can read (HEIC, PNG,
WebP, a GIF's first frame, a sticker) into a JPEG, bounded to 1,600 px on the
long edge and **never upscaled** (the old ``sips -Z 1600`` turned a 12 KB
sticker into 159 KB). Transparency lands on white. ``qlmanage`` gives a
video's poster frame. ``afconvert``/``avconvert`` and the Swift media helper
are in ``av.py`` and ``mediatool.py``.

Every child runs in its own process group; a timeout kills the whole group,
so nothing is left orphaned (``run``). Work happens in a private temp dir
that is deleted after.
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

MAX_EDGE = 1600
JPEG_QUALITY = 82
SIPS_TIMEOUT_S = 30
QLMANAGE_TIMEOUT_S = 15

_HEIF_BRANDS = {b"heic", b"heix", b"heim", b"heis", b"hevc", b"hevx", b"mif1", b"msf1", b"avif"}
_AUDIO_BRANDS = {b"M4A ", b"M4B ", b"M4P "}
_VIDEO_BRANDS = {b"qt  ": "video/quicktime"}


def sniff(head: bytes) -> Optional[Tuple[str, str]]:
    """(kind, mime) from a file's first bytes, or None for anything that is
    not a picture, a PDF, a video or a sound. Pure."""
    h = bytes(head or b"")
    if h.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg"
    if h.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png"
    if h.startswith((b"GIF87a", b"GIF89a")):
        return "image", "image/gif"
    if h[:4] == b"RIFF" and h[8:12] == b"WEBP":
        return "image", "image/webp"
    if h[:4] == b"RIFF" and h[8:12] == b"WAVE":
        return "audio", "audio/wav"
    if h.startswith((b"II*\x00", b"MM\x00*")):
        return "image", "image/tiff"
    if h.startswith(b"BM") and len(h) >= 14:
        return "image", "image/bmp"
    if h.startswith(b"%PDF-"):
        return "pdf", "application/pdf"
    if h[4:8] == b"ftyp":
        brand = h[8:12]
        if brand in _HEIF_BRANDS:
            return "image", "image/heic"
        if brand in _AUDIO_BRANDS:
            return "audio", "audio/mp4"
        if brand in _VIDEO_BRANDS:
            return "video", _VIDEO_BRANDS[brand]
        return "video", "video/mp4"
    if h.startswith(b"caff"):
        return "audio", "audio/x-caf"
    if h.startswith(b"#!AMR"):
        return "audio", "audio/amr"
    if h.startswith(b"ID3") or (len(h) > 1 and h[0] == 0xFF and (h[1] & 0xE0) == 0xE0):
        return "audio", "audio/mpeg"
    if h[:4] == b"FORM" and h[8:12] in (b"AIFF", b"AIFC"):
        return "audio", "audio/aiff"
    return None


def run(argv: Sequence[str], timeout_s: float, *, cwd: Optional[str] = None) -> subprocess.CompletedProcess:
    """Run ``argv`` in its own process group, output captured. On timeout the
    whole group is killed and ``subprocess.TimeoutExpired`` raised."""
    proc = subprocess.Popen(list(argv), stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                            start_new_session=True, cwd=cwd)
    try:
        out, err = proc.communicate(timeout=timeout_s)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        proc.communicate()
        raise
    return subprocess.CompletedProcess(list(argv), proc.returncode, out, err)


def sips_size_argv(src: str) -> List[str]:
    return ["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(src)]


def parse_sips_size(stdout: str) -> Tuple[Optional[int], Optional[int]]:
    w = re.search(r"pixelWidth:\s*(\d+)", stdout or "")
    h = re.search(r"pixelHeight:\s*(\d+)", stdout or "")
    return (int(w.group(1)) if w else None, int(h.group(1)) if h else None)


def sips_jpeg_argv(src: str, dst: str, *, width: Optional[int], height: Optional[int],
                   max_edge: int = MAX_EDGE, quality: int = JPEG_QUALITY) -> List[str]:
    """sips to JPEG; ``-Z`` only when the long edge is over ``max_edge`` (or unknown)."""
    argv = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality)]
    if width is None or height is None or max(width, height) > max_edge:
        argv += ["-Z", str(max_edge)]
    return argv + [str(src), "--out", str(dst)]


def image_size(src: Path | str) -> Tuple[Optional[int], Optional[int]]:
    try:
        r = run(sips_size_argv(str(src)), SIPS_TIMEOUT_S)
    except (OSError, subprocess.SubprocessError):
        return None, None
    return parse_sips_size(r.stdout.decode("utf-8", "replace"))


def to_jpeg(src: Path | str, *, max_edge: int = MAX_EDGE) -> Optional[bytes]:
    """Any image macOS can read -> JPEG bytes, long edge <= ``max_edge``,
    never upscaled. None when it can't be read."""
    width, height = image_size(src)
    with tempfile.TemporaryDirectory(prefix="edward-media-") as td:
        out = Path(td) / "out.jpg"
        try:
            r = run(sips_jpeg_argv(str(src), str(out), width=width, height=height, max_edge=max_edge),
                    SIPS_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not out.exists():
            return None
        data = out.read_bytes()
    return data if data[:3] == b"\xff\xd8\xff" else None


def jpeg_bytes_to_jpeg(data: bytes, *, suffix: str = ".img", max_edge: int = MAX_EDGE) -> Optional[bytes]:
    """``to_jpeg`` for bytes already in memory (written to a private temp file)."""
    with tempfile.TemporaryDirectory(prefix="edward-media-") as td:
        src = Path(td) / f"in{suffix}"
        src.write_bytes(data)
        return to_jpeg(src, max_edge=max_edge)


def qlmanage_poster(src: Path | str, *, size: int = 1024) -> Optional[bytes]:
    """A video's (or any previewable file's) Quick Look poster frame as JPEG."""
    with tempfile.TemporaryDirectory(prefix="edward-media-") as td:
        try:
            run(["qlmanage", "-t", "-s", str(size), "-o", td, str(src)], QLMANAGE_TIMEOUT_S)
        except (OSError, subprocess.SubprocessError):
            return None
        pngs = sorted(Path(td).glob("*.png"))
        if not pngs:
            return None
        return to_jpeg(pngs[0], max_edge=min(size, MAX_EDGE))


def jpeg_size(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    """(width, height) from a JPEG's SOF marker. Pure."""
    d = bytes(data or b"")
    i = 2
    while i + 9 < len(d):
        if d[i] != 0xFF:
            i += 1
            continue
        marker = d[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        length = int.from_bytes(d[i + 2:i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            height = int.from_bytes(d[i + 5:i + 7], "big")
            width = int.from_bytes(d[i + 7:i + 9], "big")
            return width, height
        i += 2 + length
    return None, None
