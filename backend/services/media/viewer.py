"""``media.view(ref)``: what a piece of media looks like to Edward
(2026-10-11; docs/architecture/media.md). The ``look`` tool and the ping
turn's media both come through here.

Refs:
- ``attachment:<chat.db rowid>`` — an iMessage attachment, found through the
  listener's ``find_attachment`` (this process has Full Disk Access);
- ``file:<id>`` — a file in Edward's file storage (a PWA upload);
- a local path — resolved with ``realpath`` and opened only under the
  owner's home, the temp dirs, ``/Volumes`` or file storage, never inside a
  credential store (``.ssh``, ``Keychains``, ``.codex``, a ``.env``…). Edward
  has broad read access by design (2026-10-11); a viewer that only opens
  pictures, PDFs, videos and sounds needs no wider deny list than that.
- anything else (a URL; ``media:<id>`` until the store lands) ->
  ``MediaView(error="unsupported: …")``, and ``look`` uses its own path.

The file is opened with ``O_NOFOLLOW`` and read from that descriptor, and
its first bytes must be the media it claims to be: a ``.png`` that is really
a text file is refused, and its text is never returned. Images are converted
from a private copy, so a link swapped in after the check is never followed.
"""

from __future__ import annotations

import asyncio
import os
import re
import stat
import tempfile
from pathlib import Path
from typing import Optional, Tuple

from services.media import MediaView
from services.media import convert

IMAGE_MAX_BYTES = 25 * 1024 * 1024
PDF_MAX_BYTES = 15 * 1024 * 1024
PDF_MAX_PAGES = 100
HEAD_BYTES = 64

NOT_DOWNLOADED = "that attachment isn't on this Mac yet (Messages hasn't downloaded it)"
_PAGE_RE = re.compile(rb"/Type\s*/Page(?![s\w])")
_DENIED_PARTS = {".ssh", ".gnupg", ".codex", ".aws", ".kube", ".docker", "Keychains", ".password-store"}
_DENIED_NAMES = {".env", ".jwt_secret", ".setup_code", "auth.json", ".netrc", ".pgpass", "id_rsa", "id_ed25519"}


def _home() -> Path:
    return Path.home()


def _allowed_roots() -> Tuple[Path, ...]:
    roots = [_home(), Path("/tmp"), Path("/private/tmp"), Path("/private/var/folders"), Path("/Volumes")]
    try:
        from services.file_storage_service import FILE_STORAGE_ROOT
        roots.append(Path(FILE_STORAGE_ROOT))
    except Exception:  # noqa: BLE001
        pass
    return tuple(Path(os.path.realpath(r)) for r in roots)


def _denied(real: Path) -> Optional[str]:
    parts = set(real.parts)
    if parts & _DENIED_PARTS or real.name in _DENIED_NAMES or real.name.startswith(".env"):
        return f"{real.name} is in a place for keys and passwords — not something I open"
    return None


def _under(real: Path, roots) -> bool:
    return any(real == r or r in real.parents for r in roots)


def human_size(n: int) -> str:
    if n >= 1024 * 1024:
        return f"{n / (1024 * 1024):.1f} MB"
    if n >= 1024:
        return f"{n // 1024} KB"
    return f"{n} bytes"


def pdf_pages(data: bytes) -> int:
    """Page objects in a PDF (no pypdf here). Pure."""
    return len(_PAGE_RE.findall(data or b""))


# ----------------------------------------------------------- resolving ----

async def _resolve(ref: str) -> Tuple[Optional[Path], str, Optional[str], Optional[str], bool]:
    """(path, name, declared mime, error, trusted_source). ``trusted_source``
    is a path that came from chat.db or file storage, not from the model."""
    m = re.fullmatch(r"attachment:(\d+)", ref)
    if m:
        from services.heartbeat import listener_imessage
        att = await asyncio.to_thread(listener_imessage.find_attachment, int(m.group(1)))
        if not att or not att.get("path"):
            return None, "", None, NOT_DOWNLOADED, True
        p = Path(att["path"]).expanduser()
        return p, att.get("name") or p.name, att.get("mime_type"), None, True
    m = re.fullmatch(r"file:([\w-]+)", ref)
    if m:
        from services import file_storage_service
        p = await file_storage_service.get_file_path(m.group(1))
        if p is None:
            return None, "", None, f"there's no file {m.group(1)} in my files", True
        return Path(p), Path(p).name, None, None, True
    if ref.startswith(("/", "~")):
        p = Path(ref).expanduser()
        return p, p.name, None, None, False
    return None, "", None, f"unsupported: {ref[:80] or 'an empty ref'}", False


def _open(path: Path, *, trusted: bool) -> Tuple[Optional[int], Optional[Path], Optional[str]]:
    """(fd, realpath, error): the realpath checked, opened without following a link."""
    try:
        real = Path(os.path.realpath(path))
    except OSError as exc:
        return None, None, f"can't resolve {path.name}: {type(exc).__name__}"
    denied = _denied(real)
    if denied:
        return None, real, denied
    if not trusted and not _under(real, _allowed_roots()):
        return None, real, f"{path} is outside the places I open files from (your home folder, temp, /Volumes)"
    try:
        fd = os.open(str(real), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None, real, NOT_DOWNLOADED if trusted else f"no such file: {path}"
    except OSError as exc:
        return None, real, f"can't open {path.name}: {type(exc).__name__}"
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        return None, real, f"{path.name} isn't a regular file"
    return fd, real, None


def _read_fd(fd: int, limit: int) -> bytes:
    chunks, total = [], 0
    while total < limit:
        chunk = os.read(fd, min(1 << 20, limit - total))
        if not chunk:
            break
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def _declared_kind(mime: Optional[str], name: str) -> Optional[str]:
    m = (mime or "").lower()
    if m.startswith(("image/", "video/", "audio/")):
        return m.split("/", 1)[0]
    if m == "application/pdf":
        return "pdf"
    ext = Path(name or "").suffix.lower()
    if ext in (".heic", ".heif", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"):
        return "image"
    if ext in (".mov", ".mp4", ".m4v", ".3gp"):
        return "video"
    if ext in (".caf", ".m4a", ".mp3", ".wav", ".aac", ".amr", ".aiff", ".opus"):
        return "audio"
    if ext == ".pdf":
        return "pdf"
    return None


# --------------------------------------------------------------- views ----

def _header(ref: str, kind: str, name: str, size: int) -> str:
    from services.media import hooks
    head = f"{ref} · {kind} · {name} · {human_size(size)}"
    desc = hooks.describe_ref(ref) if ref.startswith(("attachment:", "media:", "file:")) else None
    return f'{head} — "{desc}"' if desc else head


def _image_view(fd: int, size: int, ref: str, name: str, suffix: str, max_images: int) -> MediaView:
    if size > IMAGE_MAX_BYTES:
        return MediaView(kind="image", text=_header(ref, "image", name, size),
                         error=f"that image is {human_size(size)} — too big to open")
    data = _read_fd(fd, IMAGE_MAX_BYTES + 1)
    images = []
    try:
        from services.media import mediatool
        if suffix == ".gif" and max_images > 1 and mediatool.available():
            images = mediatool.frames_from_bytes(data, suffix=suffix, count=min(4, max_images))
    except ImportError:
        pass
    if not images:
        jpeg = convert.jpeg_bytes_to_jpeg(data, suffix=suffix)
        images = [jpeg] if jpeg else []
    if not images:
        return MediaView(kind="image", text=_header(ref, "image", name, size),
                         error="I couldn't convert that image")
    text = _header(ref, "image", name, size)
    if len(images) > 1:
        text += f" (an animation: {len(images)} frames)"
    return MediaView(kind="image", images=images[:max_images], text=text)


def _pdf_view(fd: int, size: int, ref: str, name: str, real: Path) -> MediaView:
    header = _header(ref, "pdf", name, size)
    data = _read_fd(fd, PDF_MAX_BYTES + 1) if size <= PDF_MAX_BYTES else b""
    pages = pdf_pages(data) if data else None
    if data and len(data) <= PDF_MAX_BYTES and (pages or 0) <= PDF_MAX_PAGES:
        return MediaView(kind="pdf", pdf=data, text=f"{header} · {pages or 'unknown'} pages")
    why = f"{pages} pages" if pages else human_size(size)
    text = (f"{header} — this PDF is {why}, too big to read whole; extract the text with the shell "
            "(e.g. `pip install pypdf` in your home venv)")
    try:
        from services.media import mediatool
        if mediatool.available():
            extracted = mediatool.pdf_text(real)
            if extracted:
                text += "\nIts text, as far as I could read it:\n" + extracted
    except ImportError:
        pass
    return MediaView(kind="pdf", text=text)


def _video_view(real: Path, size: int, ref: str, name: str, max_images: int) -> MediaView:
    header = _header(ref, "video", name, size)
    try:
        from services.media import av
        return av.video_view(real, header=header, max_images=max_images, ref=ref)
    except ImportError:
        pass
    poster = convert.qlmanage_poster(real)
    return MediaView(kind="video", images=[poster] if poster else [],
                     text=f"{header} — a {human_size(size)} video; this is its poster frame")


def _audio_view(real: Path, size: int, ref: str, name: str) -> MediaView:
    header = _header(ref, "audio", name, size)
    try:
        from services.media import av
        return av.audio_view(real, header=header, ref=ref)
    except ImportError:
        return MediaView(kind="audio", text=f"{header} — a recording; I can't listen to it yet")


def _view_sync(path: Path, name: str, mime: Optional[str], ref: str, trusted: bool,
               max_images: int) -> MediaView:
    fd, real, error = _open(path, trusted=trusted)
    if error:
        return MediaView(kind=_declared_kind(mime, name) or "file", error=error)
    try:
        size = os.fstat(fd).st_size
        head = os.pread(fd, HEAD_BYTES, 0)
        sniffed = convert.sniff(head)
        declared = _declared_kind(mime, name)
        if sniffed is None:
            if declared in ("image", "pdf", "video", "audio"):
                return MediaView(kind=declared, error=f"{name} isn't the {declared} it says it is")
            return MediaView(kind="file", text=f"{ref} · file · {name} · {human_size(size)} — not a picture, "
                                               "PDF, video or sound; read it with the shell if you need it")
        kind, sniffed_mime = sniffed
        if kind == "image":
            suffix = ".gif" if sniffed_mime == "image/gif" else (Path(name).suffix.lower() or ".img")
            return _image_view(fd, size, ref, name, suffix, max_images)
        if kind == "pdf":
            return _pdf_view(fd, size, ref, name, real)
        if kind == "video":
            return _video_view(real, size, ref, name, max_images)
        return _audio_view(real, size, ref, name)
    finally:
        os.close(fd)


async def view(ref: str, *, max_images: int = 6) -> MediaView:
    """What ``ref`` looks like. Never raises: a failure is ``MediaView.error``."""
    from services.media import hooks

    ref = str(ref or "").strip()
    if not hooks.enabled():
        return MediaView(kind="file", error="unsupported: media is switched off (EDWARD_MEDIA=off)")
    try:
        path, name, mime, error, trusted = await _resolve(ref)
        if error:
            kind = "link" if error.startswith("unsupported:") else (_declared_kind(mime, name) or "file")
            return MediaView(kind=kind, error=error)
        label = ref if not ref.startswith(("/", "~")) else str(path)
        return await asyncio.to_thread(_view_sync, path, name, mime, label, trusted, max(1, int(max_images)))
    except Exception as exc:  # noqa: BLE001 - a broken file must never break a turn
        return MediaView(kind="file", error=f"I couldn't open that ({type(exc).__name__})")
