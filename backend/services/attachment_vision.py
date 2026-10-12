"""Turn an iMessage/MMS attachment into something Edward can see or read.

Why (2026-09-03): photos and files sent over iMessage arrived as a
U+FFFC glyph, or were dropped. The model side already accepts images
(Codex `input_image`); this module is the bridge from a path on disk (or
bytes from Twilio) to (a) a base64 JPEG block for a chat turn, and (b) a
one-paragraph description for triage text, thread views and tools.

HEIC (every iPhone photo) is converted with macOS `sips`, which also
downsizes to ``MAX_EDGE`` so a 12MP shot does not cost 12MP of tokens.
"""

from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional

from services.llm_utils import build_background_llm

MAX_IMAGE_BYTES = 25 * 1024 * 1024   # refuse anything larger on disk
MAX_EDGE = 1600
IMAGE_EXTENSIONS = {".heic", ".heif", ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tif", ".tiff", ".bmp"}

_description_cache: Dict[str, str] = {}


def _is_image(path: Path, mime_type: Optional[str] = None) -> bool:
    if mime_type and mime_type.startswith("image/"):
        return True
    return path.suffix.lower() in IMAGE_EXTENSIONS


def _to_jpeg(path: Path) -> Optional[bytes]:
    """Any image on disk → JPEG bytes bounded to MAX_EDGE and never upscaled,
    via the media converter's sips builder (2026-10-11: ``-Z`` on every
    image blew a 12 KB sticker up to 159 KB)."""
    if shutil.which("sips") is None:
        return path.read_bytes() if path.suffix.lower() in (".jpg", ".jpeg") else None
    from services.media.convert import to_jpeg

    return to_jpeg(path, max_edge=MAX_EDGE)


def prepare_image_for_model(path: Path | str, *, mime_type: Optional[str] = None, name: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """A chat attachment dict (filename, mime_type, data=base64) or None."""
    p = Path(path).expanduser()
    if not p.is_file() or not _is_image(p, mime_type):
        return None
    try:
        if p.stat().st_size > MAX_IMAGE_BYTES:
            return None
    except OSError:
        return None
    jpeg = _to_jpeg(p)
    if not jpeg:
        return None
    return {"filename": name or p.name, "mime_type": "image/jpeg",
            "data": base64.b64encode(jpeg).decode("ascii"), "size": len(jpeg)}


def attachments_from_raw_data(raw_data: Optional[str]) -> List[Dict[str, Any]]:
    """Image blocks for every readable image attachment on a heartbeat event."""
    if not raw_data:
        return []
    try:
        atts = (json.loads(raw_data) or {}).get("attachments") or []
    except (ValueError, TypeError):
        return []
    blocks: List[Dict[str, Any]] = []
    for a in atts:
        if not isinstance(a, dict) or not a.get("path"):
            continue
        if not str(a.get("mime_type") or "").startswith("image/"):
            continue
        # The ref in the name: ``[Uploaded image: attachment:<rowid> · …]`` is
        # where saved history finds how to see it again (media.dehydrate).
        name = a.get("name")
        if a.get("rowid") is not None:
            name = f"attachment:{a['rowid']} · {name or 'image'}"
        block = prepare_image_for_model(a["path"], mime_type=a.get("mime_type"), name=name)
        if block:
            blocks.append(block)
    return blocks[:4]


async def describe_image(path: Path | str, *, priority: str = "background") -> str:
    """One short paragraph about the image, from a vision-capable LLM.

    ``priority`` is the rate-gate tier. The listener describing a photo that
    just arrived is background work; Edward looking at a photo the owner
    asked about (``get_imessage_attachment``, ``look_at_screen``) is interactive
    and must never be deferred behind enrichment traffic — it was, until
    2026-09-11 ("Could not read image ... background LLM call deferred").
    """
    key = str(Path(path).expanduser())
    if key in _description_cache:
        return _description_cache[key]
    block = prepare_image_for_model(key)
    if not block:
        return "(image could not be read)"
    from langchain_core.messages import HumanMessage
    llm = build_background_llm(max_tokens=200, priority=priority, vision=True)
    msg = HumanMessage(content=[
        {"type": "text", "text": (
            "Describe this photo in one or two plain sentences for someone who cannot see it: "
            "what it shows, any readable text (quote it), and anything that looks like it needs "
            "a response (a question, a screenshot of a problem, a form, a receipt total)."
        )},
        {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{block['data']}"}},
    ])
    resp = await llm.ainvoke([msg])
    text = resp.content if isinstance(resp.content, str) else str(resp.content)
    text = " ".join(text.split())[:600]
    if text:
        _description_cache[key] = text
    return text or "(no description)"


async def store_attachment_copy(path: Path | str, *, description: str = "", source: str = "imessage") -> Optional[Dict[str, Any]]:
    """Keep a JPEG copy in file storage so the web UI can show it."""
    block = prepare_image_for_model(path)
    if not block:
        return None
    from services.file_storage_service import store_file
    stem = Path(block["filename"]).stem
    record = await store_file(
        file_data=base64.b64decode(block["data"]), filename=f"{stem}.jpg", mime_type="image/jpeg",
        category="attachment", source=source, description=description[:500] or None, tags="imessage,attachment",
    )
    fid = record.get("id") if isinstance(record, dict) else getattr(record, "id", None)
    return {"id": fid, "url": f"/api/files/{fid}/download"} if fid else None
