"""The Swift media helper, from Python (2026-10-11; docs/architecture/media.md).

``edward-mediatool`` is one small binary over Apple's own frameworks
(AVFoundation, ImageIO, PDFKit): evenly spaced frames of a movie or an
animated image, a probe (kind, duration, sound), and a PDF's text — no
ffmpeg, no poppler. The source is ``backend/tools/media/edward_mediatool.swift``;
the deploy builds it (``backend/scripts/build_mediatool.sh``) into
``~/edward-media/bin/edward-mediatool``, or ``EDWARD_MEDIATOOL`` names it.
The runtime only locates it: when it isn't there, callers fall back
(``qlmanage`` for a poster frame, a GIF's first frame through ``sips``).

Every call is bounded and never raises; a failure is empty.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.media import convert

# ADAPT: media leans on macOS tools and a Swift helper built on this Mac.
#   How it works here: build_mediatool.sh compiles edward_mediatool.swift with the
#   Xcode command-line tools' swiftc (for this Mac's architecture, arm64 on Apple
#   silicon); sips, qlmanage, afconvert and avconvert ship with macOS.
#   To adapt: run `bash backend/scripts/build_mediatool.sh` (needs
#   `xcode-select --install`), or set EDWARD_MEDIATOOL to the binary; without it
#   videos get a poster frame only and a GIF its first frame.
DEFAULT_PATH = "~/edward-media/bin/edward-mediatool"
PROBE_TIMEOUT_S = 20
FRAMES_TIMEOUT_S = 60
PDF_TIMEOUT_S = 30
PDF_TEXT_MAX = 20_000


def binary_path() -> Path:
    return Path(os.path.expanduser(os.getenv("EDWARD_MEDIATOOL") or DEFAULT_PATH))


def available() -> bool:
    p = binary_path()
    return p.is_file() and os.access(p, os.X_OK)


def _call(args: List[str], timeout_s: float) -> Optional[Dict[str, Any]]:
    if not available():
        return None
    try:
        r = convert.run([str(binary_path()), *args], timeout_s)
    except (OSError, convert.subprocess.SubprocessError):
        return None
    try:
        out = json.loads(r.stdout.decode("utf-8", "replace").strip().splitlines()[-1])
    except (ValueError, IndexError):
        return None
    return out if r.returncode == 0 and isinstance(out, dict) and out.get("ok") else None


def probe(src: Path | str) -> Optional[Dict[str, Any]]:
    """{"kind": video|audio|image|pdf, "duration", "has_audio", "width", "height", …} or None."""
    return _call(["probe", str(src)], PROBE_TIMEOUT_S)


def frames(src: Path | str, *, count: int, max_edge: int = 1024) -> List[bytes]:
    """Up to ``count`` evenly spaced JPEG frames of a movie or an animated image."""
    with tempfile.TemporaryDirectory(prefix="edward-frames-") as td:
        out = _call(["frames", str(src), td, str(max(1, int(count))), str(int(max_edge))], FRAMES_TIMEOUT_S)
        if not out:
            return []
        images = []
        for frame in out.get("frames") or []:
            p = Path(str(frame.get("path") or ""))
            try:
                if p.parent == Path(td) and p.is_file():
                    data = p.read_bytes()
                    if data[:3] == b"\xff\xd8\xff":
                        images.append(data)
            except OSError:
                continue
        return images


def frames_from_bytes(data: bytes, *, suffix: str, count: int, max_edge: int = convert.MAX_EDGE) -> List[bytes]:
    """``frames`` for an image already read into memory (a private temp copy)."""
    with tempfile.TemporaryDirectory(prefix="edward-media-") as td:
        src = Path(td) / f"in{suffix}"
        src.write_bytes(data)
        return frames(src, count=count, max_edge=max_edge)


def pdf_text(src: Path | str, max_chars: int = PDF_TEXT_MAX) -> Optional[str]:
    """A PDF's text through PDFKit, or None."""
    out = _call(["pdf", str(src), str(int(max_chars))], PDF_TIMEOUT_S)
    text = (out or {}).get("text")
    return str(text).strip() or None if text else None
