"""``look``: Edward sees for himself (2026-10-11).

A ping once failed because the agent answering could not see the photo it
was asked about; ``look_at_screen`` and ``get_imessage_attachment`` handed
the model a second model's description. ``look`` returns the picture itself
as content blocks: up to six image blocks (JPEG/PNG/GIF/WebP, long edge
≤ 1600 px, never upscaled), a PDF document block, and a short text block
that says what it is.

Targets:

* ``attachment:<chat.db rowid>``, ``media:<id>``, ``file:<id>``, media paths —
  ``services.media.view`` first (docs/architecture/media.md). When media is
  not installed, or answers ``unsupported: …``, look uses its own path:
  Messages' attachment table for ``attachment:``, Edward's file storage for
  ``file:`` (a PWA upload's ``file_id``), sips for files.
* an http(s) URL — fetched with the SSRF guard (every hop), ≤ 25 MB.
* ``screen`` — this Mac's display (``screencapture``).
* ``chrome`` — Edward's working tab in the owner's Chrome, over CDP without
  activating it (a wake must not switch the owner's tab); needs
  ``EDWARD_CHROME_HANDS=on``.
* ``browser`` — his own headless page.

A turn carries at most ``TURN_MAX_IMAGES`` images and ``TURN_MAX_BYTES`` of
media (the API rejects bodies over 32 MB), counted here because only look
sees every target; a ping turn's count starts from what its trigger
carried (``pings.hooks.current_ping()["media"]``).

Under the Codex provider the Responses converter drops images in tool
output, so look answers with ``attachment_vision.describe_image``'s words.
Tests never capture the display or the owner's Chrome (conftest).
"""

from __future__ import annotations

import asyncio
import base64
import importlib
import os
import re
import shutil
import tempfile
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import httpx
from langchain_core.tools import tool

from services.owner import owner_doc, owner_name
from services.toolset import _compat
from services.toolset._compat import ToolFailure

LOOK_TIMEOUT_S = 60
MAX_EDGE = 1600
MAX_IMAGES_PER_LOOK = 6
TURN_MAX_IMAGES = 40
TURN_MAX_BYTES = 15 * 1024 * 1024
PDF_MAX_BYTES = 15 * 1024 * 1024
URL_MAX_BYTES = 25 * 1024 * 1024
PASSTHROUGH_MAX_BYTES = 3_500_000
SIPS_TIMEOUT_S = 30
NATIVE_IMAGE_TYPES = {"jpeg": "image/jpeg", "png": "image/png", "gif": "image/gif", "webp": "image/webp"}
SCREEN_TARGETS = ("screen", "chrome", "browser")


# ---- seams (tests patch these) --------------------------------------------------------------

def _on_claude() -> bool:
    try:
        from services.llm_utils import anthropic_enabled

        return anthropic_enabled()
    except Exception:  # noqa: BLE001
        return False


async def _area3_view(ref: str, *, max_images: int = MAX_IMAGES_PER_LOOK) -> Any:
    """The media package's ``media.view``; None when media is not installed."""
    try:
        media = importlib.import_module("services.media")
    except ImportError:
        return None
    view = getattr(media, "view", None)
    return await view(ref, max_images=max_images) if view is not None else None


_media_view: Any = _area3_view


def _can_read(path: str) -> bool:
    """The computer's read rail (``services.computer.rails.can_read``: only
    Edward's own credentials are refused, however the path is spelled) when
    it is merged; until then every existing file may be looked at (look
    returns only pictures and PDFs, never a file's text)."""
    try:
        from services.computer.rails import can_read  # type: ignore
    except Exception:  # noqa: BLE001
        return True
    try:
        return bool(can_read(path))
    except Exception:  # noqa: BLE001
        return False


def _read_local(real: str, raw: str) -> Tuple[bytes, str]:
    """``(bytes, name)`` of a local file, read through the computer's rails
    (``rails.read_checked``: the file opened without following a link and
    its inode and canonical path checked again after the open, so a link
    swapped in after ``_can_read`` never reaches a credential)."""
    try:
        from services.computer import rails  # type: ignore
    except Exception:  # noqa: BLE001
        rails = None  # type: ignore
    if rails is None or not hasattr(rails, "read_checked"):
        p = Path(real)
        if not p.is_file():
            raise ToolFailure(f"there's no file at {raw}")
        if p.stat().st_size > URL_MAX_BYTES:
            raise ToolFailure(f"{raw} is {_kb(p.stat().st_size)} — too big to look at")
        return p.read_bytes(), p.name
    try:
        data, canon = rails.read_checked(real, limit=URL_MAX_BYTES)
    except rails.RailRefused:
        raise ToolFailure(f"can't read {raw} — that path is behind a rail") from None
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        raise ToolFailure(f"there's no file at {raw}") from None
    except OSError as exc:
        raise ToolFailure(f"can't read {raw}: {exc}") from None
    if len(data) > URL_MAX_BYTES:
        raise ToolFailure(f"{raw} is over {_kb(URL_MAX_BYTES)} — too big to look at")
    return data, os.path.basename(canon)


def _local_path(raw: str) -> Path:
    path = Path(os.path.expanduser(raw))
    if not path.is_absolute():
        try:
            from services.headless_browser import edward_home

            path = edward_home() / path
        except Exception:  # noqa: BLE001
            path = Path.home() / path
    return path


def _http_client(**kw: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(**kw)


def _turn_key() -> Optional[str]:
    try:
        from services.turn_ledger import current_turn

        rec = current_turn()
    except Exception:  # noqa: BLE001
        return None
    if rec is None:
        return None
    return str(getattr(rec, "id", None) or id(rec))


# ---- the per-turn count --------------------------------------------------------------------

_turn_counts: "OrderedDict[str, List[int]]" = OrderedDict()


def _ping_seed() -> Tuple[int, int]:
    try:
        hooks = importlib.import_module("services.pings.hooks")
        ping = hooks.current_ping() or {}
    except Exception:  # noqa: BLE001
        return 0, 0
    media = ping.get("media") if isinstance(ping, dict) else None
    if not isinstance(media, dict):
        return 0, 0
    imgs = media.get("images", 0)
    raw = media.get("bytes", 0)
    n = len(imgs) if isinstance(imgs, (list, tuple)) else int(imgs or 0)
    b = sum(len(x) for x in raw) if isinstance(raw, (list, tuple)) else int(raw or 0)
    return n, b


def _counts() -> Optional[List[int]]:
    key = _turn_key()
    if key is None:
        return None
    held = _turn_counts.get(key)
    if held is None:
        held = list(_ping_seed())
        _turn_counts[key] = held
        while len(_turn_counts) > 200:
            _turn_counts.popitem(last=False)
    return held


def _admit(n_images: int, n_bytes: int) -> None:
    held = _counts()
    if held is None:
        return
    if held[0] + n_images > TURN_MAX_IMAGES or held[1] + n_bytes > TURN_MAX_BYTES:
        raise ToolFailure("that's as many images as one request can carry "
                          f"({TURN_MAX_IMAGES} images / {TURN_MAX_BYTES // (1024 * 1024)} MB a turn) — "
                          "say what you've seen so far; ask again in a new message to see more")
    held[0] += n_images
    held[1] += n_bytes


# ---- pure: what a file is ----------------------------------------------------------------------

def sniff(head: bytes) -> str:
    """jpeg|png|gif|webp|heic|tiff|bmp|pdf|video|other, from magic bytes."""
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"%PDF":
        return "pdf"
    if head[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if head[:2] == b"BM":
        return "bmp"
    if head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"heis", b"mif1", b"msf1", b"avif"):
            return "heic"
        return "video"
    return "other"


def _pdf_pages(data: bytes) -> int:
    return len(re.findall(rb"/Type\s*/Page(?!s)", data))


async def _run(argv: List[str], timeout: float = SIPS_TIMEOUT_S) -> Tuple[int, str]:
    proc = await asyncio.create_subprocess_exec(*argv, stdout=asyncio.subprocess.PIPE,
                                                stderr=asyncio.subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        raise RuntimeError(f"{argv[0]} took longer than {timeout:g}s") from None
    return proc.returncode or 0, (out or b"").decode("utf-8", "replace")


async def _pixel_size(path: Path) -> Tuple[int, int]:
    rc, out = await _run(["sips", "-g", "pixelWidth", "-g", "pixelHeight", str(path)])
    m_w = re.search(r"pixelWidth:\s*(\d+)", out)
    m_h = re.search(r"pixelHeight:\s*(\d+)", out)
    if rc != 0 or not m_w or not m_h:
        raise RuntimeError(f"sips can't read that image ({out.strip()[:120]})")
    return int(m_w.group(1)), int(m_h.group(1))


async def model_image(data: bytes) -> Tuple[bytes, str]:
    """(bytes, media type) the model reads: as given when it is already a
    type the API takes, ≤ 1600 px and small; else JPEG via sips, scaled
    down only when larger than 1600 px (never up: ``-Z`` alone upscales)."""
    kind = sniff(data[:32])
    if shutil.which("sips") is None:
        if kind in NATIVE_IMAGE_TYPES and len(data) <= PASSTHROUGH_MAX_BYTES:
            return data, NATIVE_IMAGE_TYPES[kind]
        raise RuntimeError("can't convert images on this machine (no sips)")
    with tempfile.TemporaryDirectory(prefix="edward-look-") as td:
        src = Path(td) / f"src.{kind if kind != 'other' else 'img'}"
        src.write_bytes(data)
        w, h = await _pixel_size(src)
        if kind in NATIVE_IMAGE_TYPES and max(w, h) <= MAX_EDGE and len(data) <= PASSTHROUGH_MAX_BYTES:
            return data, NATIVE_IMAGE_TYPES[kind]
        out = Path(td) / "out.jpg"
        argv = ["sips", "-s", "format", "jpeg", "-s", "formatOptions", "82"]
        if max(w, h) > MAX_EDGE:
            argv += ["--resampleHeightWidthMax", str(MAX_EDGE)]
        rc, msg = await _run(argv + [str(src), "--out", str(out)])
        if rc != 0 or not out.exists():
            raise RuntimeError(f"couldn't convert the image ({msg.strip()[:120]})")
        return out.read_bytes(), "image/jpeg"


# ---- blocks -------------------------------------------------------------------------------------

def _image_block(data: bytes, media_type: str) -> Dict[str, Any]:
    return {"type": "image", "source": {"type": "base64", "media_type": media_type,
                                        "data": base64.b64encode(data).decode("ascii")}}


def _pdf_block(data: bytes) -> Dict[str, Any]:
    return {"type": "document", "source": {"type": "base64", "media_type": "application/pdf",
                                           "data": base64.b64encode(data).decode("ascii")}}


def _text(label: str, question: Optional[str]) -> Dict[str, Any]:
    text = label
    if question:
        text += f"\nWhat to look for: {question.strip()[:300]}"
    return {"type": "text", "text": text}


def _kb(n: int) -> str:
    return f"{max(1, round(n / 1024))} KB" if n < 1024 * 1024 else f"{n / (1024 * 1024):.1f} MB"


class _Seen:
    """What a target resolved to, before the blocks are built."""

    def __init__(self, label: str, images: Optional[List[bytes]] = None, pdf: Optional[bytes] = None,
                 note: str = ""):
        self.label = label
        self.images = images or []
        self.pdf = pdf
        self.note = note


async def _from_bytes(data: bytes, label: str) -> _Seen:
    kind = sniff(data[:32])
    if kind == "pdf":
        if len(data) > PDF_MAX_BYTES:
            raise ToolFailure(f"{label} is a {_kb(len(data))} PDF — too big to read whole; "
                              "extract its text with bash (pip install pypdf in your venv)")
        pages = _pdf_pages(data)
        return _Seen(f"{label} — a PDF, {pages or '?'} page(s), {_kb(len(data))}", pdf=data)
    if kind in ("jpeg", "png", "gif", "webp", "heic", "tiff", "bmp"):
        return _Seen(f"{label} — an image, {_kb(len(data))}", images=[data])
    if kind == "video":
        raise ToolFailure(f"{label} is a video — frames need the media helper; tell {owner_name()} if you need it")
    raise ToolFailure(f"{label} is not an image or a PDF — read text files with the editor or bash")


async def _from_path(raw: str) -> _Seen:
    real = os.path.realpath(_local_path(raw))
    if not _can_read(real):
        raise ToolFailure(f"can't read {raw} — that path is behind a rail")
    data, name = _read_local(real, raw)
    return await _from_bytes(data, name)


async def _from_url(url: str) -> _Seen:
    from services.headless_browser import BrowserRefused, check_url, url_refusal

    try:
        checked = check_url(url)
    except BrowserRefused as exc:
        raise ToolFailure(f"not looking at that: {exc}") from exc
    why = await url_refusal(checked)
    if why:
        raise ToolFailure(f"not looking at that: {why}")

    async def guard(request: httpx.Request) -> None:
        bad = await url_refusal(str(request.url))
        if bad:
            raise PermissionError(f"not fetching {request.url.host}: {bad}")

    async with _http_client(timeout=20.0, follow_redirects=True, max_redirects=8,
                            event_hooks={"request": [guard]}) as client:
        async with client.stream("GET", checked, headers={"Accept-Encoding": "gzip, deflate",
                                                         "User-Agent": "Mozilla/5.0 (Macintosh) Edward"}) as resp:
            if resp.status_code >= 400:
                raise ToolFailure(f"{checked} answered HTTP {resp.status_code}")
            ctype = (resp.headers.get("content-type") or "").split(";", 1)[0].lower()
            if "html" in ctype:
                raise ToolFailure(f"{checked} is a web page — web_fetch reads it; to see it, "
                                  "browser(open) then look('browser')")
            chunks: List[bytes] = []
            total = 0
            async for chunk in resp.aiter_bytes():
                total += len(chunk)
                if total > URL_MAX_BYTES:
                    raise ToolFailure(f"{checked} is over {_kb(URL_MAX_BYTES)} — too big to look at")
                chunks.append(chunk)
    host = httpx.URL(checked).host
    return await _from_bytes(b"".join(chunks), f"{host}{httpx.URL(checked).path}")


async def _from_attachment_fallback(rowid: str) -> _Seen:
    from services.heartbeat.listener_imessage import find_attachment

    try:
        att = find_attachment(int(rowid))
    except (TypeError, ValueError):
        raise ToolFailure(f"attachment:{rowid} is not an attachment id") from None
    if not att:
        raise ToolFailure(f"attachment:{rowid} isn't in Messages")
    path = att.get("path")
    if not path or not Path(path).is_file():
        raise ToolFailure(f"{att.get('name') or 'that attachment'} isn't on this Mac yet (Messages hasn't downloaded it)")
    seen = await _from_bytes(Path(path).read_bytes(), str(att.get("name") or Path(path).name))
    seen.label = f"attachment:{rowid} · {seen.label}"
    return seen


async def _from_upload(file_id: str) -> _Seen:
    """``file:<id>``: a file in Edward's storage — a photo or PDF the owner
    uploaded in the app (its label carries the ``file_id``)."""
    from services import file_storage_service

    fid = file_id.strip()
    if not fid:
        raise ToolFailure("file: needs an id (file:<file_id>)")
    try:
        path = await file_storage_service.get_file_path(fid)
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure(f"couldn't look up file:{fid} ({type(exc).__name__}: {exc})") from exc
    if path is None or not Path(path).is_file():
        raise ToolFailure(f"there's no uploaded file file:{fid} (it may have been deleted)")
    size = Path(path).stat().st_size
    if size > URL_MAX_BYTES:
        raise ToolFailure(f"file:{fid} is {_kb(size)} — too big to look at")
    seen = await _from_bytes(Path(path).read_bytes(), Path(path).name)
    seen.label = f"file:{fid} · {seen.label}"
    return seen


async def _from_screen(which: str) -> _Seen:
    from services import browser_ext_service as bx
    from services import browser_hands as bh

    try:
        if which == "screen":
            data, label = await bh.screenshot_display()
            return _Seen(str(label or "this Mac's display"), images=[data])
        if which == "chrome":
            if not bh.enabled():
                raise ToolFailure(bx.HANDS_OFF)
            if not bx.connected():
                raise ToolFailure("your hands in Chrome are down — the Edward extension is not connected")
            if bh.working_tab() is None:
                raise ToolFailure(f"you are not holding a tab in {owner_name()}'s Chrome — "
                                  "chrome(open) or chrome(use) first")
            data, url = await bh.screenshot_target()
            return _Seen(f"your tab in {owner_name()}'s Chrome ({url})", images=[data])
        from services import headless_browser as hb

        conv = _compat.conversation_id()
        if not hb.has_page(conv):
            raise ToolFailure("your browser has no page open — browser(open, url) first")
        return _Seen("your own browser's page", images=[await hb.screenshot(conv)])
    except ToolFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        _compat.record_failure("browser", f"look:{which}", exc)
        raise ToolFailure(f"couldn't capture the {which}: {exc}") from exc


async def _resolve(target: str) -> _Seen:
    t = target.strip()
    low = t.lower()
    if low in SCREEN_TARGETS:
        return await _from_screen(low)
    is_url = bool(re.match(r"^https?://", low))
    is_ref = bool(re.match(r"^(attachment|media|file):", low))
    if t.startswith(("/", "~")) and not _can_read(os.path.realpath(_local_path(t))):
        # Before the media viewer too: the computer's read rail is the one
        # that knows Edward's own credentials, whatever their spelling.
        raise ToolFailure(f"can't read {t} — that path is behind a rail")
    if _media_view is not None and (is_ref or is_url or t.startswith(("/", "~"))):
        mv = await _media_view(t, max_images=MAX_IMAGES_PER_LOOK)
        if mv is not None:
            err = getattr(mv, "error", None)
            if not err:
                return _Seen(str(getattr(mv, "text", "") or t), images=list(getattr(mv, "images", None) or []),
                             pdf=getattr(mv, "pdf", None))
            if not str(err).startswith("unsupported:"):
                raise ToolFailure(str(err))
    if low.startswith("attachment:"):
        return await _from_attachment_fallback(t.split(":", 1)[1].strip().lstrip("#"))
    if low.startswith("file:"):
        return await _from_upload(t.split(":", 1)[1])
    if is_ref:
        raise ToolFailure(f"{t.split(':', 1)[0]}: refs need the media module, which isn't installed yet")
    if is_url:
        return await _from_url(t)
    if re.match(r"^[a-z][a-z0-9+.-]*:", low) and not re.match(r"^[a-z]:\\", low):
        raise ToolFailure(f"{t.split(':', 1)[0]}: is not something look opens — a path, an http(s) URL, "
                          "attachment:<id>, media:<id>, file:<id>, screen, chrome or browser")
    return await _from_path(t)


async def _describe(seen: _Seen) -> str:
    """Codex: today's words for the first image (no images in tool output)."""
    from services import attachment_vision

    if seen.pdf is not None and not seen.images:
        return (f"{seen.label}. On this model I can't read a PDF as a document — extract its text with bash "
                "(pip install pypdf in your venv).")
    if not seen.images:
        return seen.label
    data, _ = await model_image(seen.images[0])
    fd, tmp = tempfile.mkstemp(prefix="edward-look-", suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        words = await attachment_vision.describe_image(tmp, priority="interactive")
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass
    more = f" ({len(seen.images) - 1} more image(s) not described)" if len(seen.images) > 1 else ""
    return f"Looking at {seen.label}: {words}{more}"


@tool
@owner_doc
async def look(target: str, question: Optional[str] = None) -> Union[str, List[Dict[str, Any]]]:
    """See something for yourself, as a picture: a photo or file someone
    sent (attachment:<id>, media:<id>; file:<id> for one {OWNER} uploaded), an
    image or PDF by path or http(s) URL, this Mac's display (screen),
    your tab in {OWNER}'s Chrome (chrome) or your own browser's page (browser). Use it to check what a person
    sent, what a page or screen really shows, or after changing something
    on a screen. question (optional) says what you're looking for."""
    t = str(target or "").strip()
    if not t:
        raise ToolFailure("look needs a target: a path, a URL, attachment:<id>, media:<id>, file:<id>, screen, chrome "
                          "or browser")
    try:
        seen = await _resolve(t)
    except ToolFailure:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure(f"couldn't look at {t}: {type(exc).__name__}: {exc}") from exc

    if not _on_claude():
        try:
            return await _describe(seen)
        except Exception as exc:  # noqa: BLE001
            raise ToolFailure(f"couldn't describe {t}: {exc}") from exc

    blocks: List[Dict[str, Any]] = [_text(seen.label + (f"\n{seen.note}" if seen.note else ""), question)]
    converted: List[Tuple[bytes, str]] = []
    try:
        for raw in seen.images[:MAX_IMAGES_PER_LOOK]:
            converted.append(await model_image(raw))
    except Exception as exc:  # noqa: BLE001
        raise ToolFailure(f"couldn't prepare the image from {t}: {exc}") from exc
    pdf = seen.pdf if seen.pdf is not None and len(seen.pdf) <= PDF_MAX_BYTES else None
    _admit(len(converted), sum(len(d) for d, _ in converted) + (len(pdf) if pdf else 0))
    for data, media_type in converted:
        blocks.append(_image_block(data, media_type))
    if pdf is not None:
        blocks.append(_pdf_block(pdf))
    if len(seen.images) > MAX_IMAGES_PER_LOOK:
        blocks[0]["text"] += f"\n(showing {MAX_IMAGES_PER_LOOK} of {len(seen.images)} images)"
    if len(blocks) == 1:
        raise ToolFailure(f"{t} gave nothing to see")
    return blocks


look.metadata = {"timeout_s": LOOK_TIMEOUT_S, "parallel_safe": True}

LOOK_TOOLS = [look]
