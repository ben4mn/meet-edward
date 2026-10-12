"""Sound and moving pictures: voice messages and videos (2026-10-11;
docs/architecture/media.md).

- **A voice message** (iMessage records Opus in a ``.caf``) is converted with
  ``afconvert -f m4af -d aac`` and transcribed by ElevenLabs Scribe
  (``voice_service.VoiceService().transcribe``, which takes bytes).
- **A video** gives evenly spaced frames through the Swift media helper
  (``mediatool``; ``qlmanage``'s poster frame without it), and its sound
  track through ``avconvert -p PresetAppleM4A`` (exit 205: no sound), then
  Scribe.

Transcripts are cached by file (path, size, mtime), so a recording is paid
for once, and noted as the ref's description (``media.hooks``), so the
thread's placeholder and saved history carry the words. Errors say only
their class: an HTTP error can carry the API key in its URL.

These run inside ``media.view``'s worker thread; Scribe is async, so it runs
in a short-lived event loop of its own there (``_transcribe_bytes``).
"""

from __future__ import annotations

import asyncio
import tempfile
import threading
from collections import OrderedDict
from pathlib import Path
from typing import Optional, Tuple

from services.media import MediaView, convert, mediatool

AFCONVERT_TIMEOUT_S = 60
AVCONVERT_TIMEOUT_S = 120
SCRIBE_TIMEOUT_S = 90
AUDIO_MAX_BYTES = 50 * 1024 * 1024
VIDEO_FRAMES = 6
VIDEO_FRAME_EDGE = 1024
AVCONVERT_NO_AUDIO = 205

_transcripts: "OrderedDict[Tuple[str, int, int], str]" = OrderedDict()
_lock = threading.Lock()


def _scribe_configured() -> bool:
    try:
        from services import voice_service
        return voice_service.is_configured()
    except Exception:  # noqa: BLE001
        return False


def _transcribe_bytes(data: bytes, *, filename: str, mime_type: str) -> str:
    """Scribe, from a worker thread. Raises on failure."""
    from services.voice_service import VoiceService

    async def go() -> str:
        svc = VoiceService()
        try:
            result = await asyncio.wait_for(svc.transcribe(data, filename=filename, mime_type=mime_type),
                                            SCRIBE_TIMEOUT_S)
            return result.text
        finally:
            await svc.close()

    return asyncio.run(go())


def _key(src: Path) -> Optional[Tuple[str, int, int]]:
    try:
        st = src.stat()
    except OSError:
        return None
    return (str(src), st.st_size, int(st.st_mtime))


def _to_m4a(src: Path) -> Optional[bytes]:
    with tempfile.TemporaryDirectory(prefix="edward-audio-") as td:
        out = Path(td) / "m.m4a"
        try:
            r = convert.run(["afconvert", "-f", "m4af", "-d", "aac", str(src), str(out)], AFCONVERT_TIMEOUT_S)
        except (OSError, convert.subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not out.exists():
            return None
        return out.read_bytes()


def _extract_audio(src: Path) -> Optional[bytes]:
    """A video's sound track as M4A, or None (no sound, or it failed)."""
    with tempfile.TemporaryDirectory(prefix="edward-audio-") as td:
        out = Path(td) / "a.m4a"
        try:
            r = convert.run(["avconvert", "-s", str(src), "-p", "PresetAppleM4A", "-o", str(out)],
                            AVCONVERT_TIMEOUT_S)
        except (OSError, convert.subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not out.exists():
            return None
        return out.read_bytes()


def _words(audio: bytes, key: Optional[Tuple[str, int, int]]) -> Tuple[Optional[str], Optional[str]]:
    """(transcript, why not)."""
    if key is not None:
        with _lock:
            if key in _transcripts:
                return _transcripts[key], None
    if not _scribe_configured():
        return None, "I can't listen to recordings yet (ElevenLabs isn't configured)"
    try:
        text = " ".join((_transcribe_bytes(audio, filename="m.m4a", mime_type="audio/mp4") or "").split())
    except Exception as exc:  # noqa: BLE001 - only the class: the URL can carry the key
        return None, f"I couldn't transcribe it ({type(exc).__name__})"
    if key is not None:
        with _lock:
            _transcripts[key] = text
            while len(_transcripts) > 500:
                _transcripts.popitem(last=False)
    return text, None


def _note(ref: Optional[str], line: str) -> None:
    if not ref:
        return
    try:
        from services.media.hooks import note_description
        note_description(ref, line)
    except Exception:  # noqa: BLE001
        pass


def audio_view(src: Path, *, header: str, ref: Optional[str] = None) -> MediaView:
    """A recording, with its words."""
    try:
        size = src.stat().st_size
    except OSError:
        return MediaView(kind="audio", error="that recording isn't on this Mac yet")
    if size > AUDIO_MAX_BYTES:
        return MediaView(kind="audio", text=f"{header} — a long recording ({size // (1024 * 1024)} MB); "
                                            "too big for me to listen to here")
    if not _scribe_configured():
        return MediaView(kind="audio", text=f"{header} — a recording; I can't listen to it yet "
                                            "(ElevenLabs isn't configured)")
    key = _key(src)
    m4a = _to_m4a(src)
    if m4a is None:
        try:
            m4a = src.read_bytes()
        except OSError:
            return MediaView(kind="audio", error="I couldn't read that recording")
    text, why = _words(m4a, key)
    if text is None:
        return MediaView(kind="audio", text=f"{header} — a recording; {why}")
    if not text:
        return MediaView(kind="audio", text=f"{header} — a recording with no words I could make out")
    _note(ref, f'voice message: "{text}"')
    return MediaView(kind="audio", text=f'{header} — it says: "{text}"')


def video_view(src: Path, *, header: str, max_images: int = VIDEO_FRAMES, ref: Optional[str] = None) -> MediaView:
    """A video: evenly spaced frames, and what is said in it."""
    count = max(1, min(VIDEO_FRAMES, int(max_images)))
    info = mediatool.probe(src) if mediatool.available() else None
    images = mediatool.frames(src, count=count, max_edge=VIDEO_FRAME_EDGE) if mediatool.available() else []
    parts = []
    duration = (info or {}).get("duration")
    if images:
        length = f"a {duration:.0f} s video" if isinstance(duration, (int, float)) else "a video"
        parts.append(f"{length}; {len(images)} frames from start to end below")
    else:
        poster = convert.qlmanage_poster(src)
        images = [poster] if poster else []
        parts.append("a video; this is its poster frame" if poster else "a video I couldn't open")
    has_audio = (info or {}).get("has_audio")
    if has_audio is False:
        parts.append("no sound")
    else:
        audio = _extract_audio(src)
        if audio is None:
            if has_audio:
                parts.append("I couldn't get its sound out")
        else:
            text, why = _words(audio, _key(src))
            if text:
                parts.append(f'it says: "{text}"')
                _note(ref, f'video; it says: "{text[:200]}"')
            elif why:
                parts.append(why)
            else:
                parts.append("no words I could make out")
    return MediaView(kind="video", images=images, text=f"{header} — " + "; ".join(parts))
