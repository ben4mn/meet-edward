"""Images and PDFs never stay in saved history (2026-10-11;
docs/architecture/media.md).

A photo in a turn is paid for once, in that turn. When the turn is written
to the checkpoint, every image and PDF block (in a ``HumanMessage``, the
trigger, or a ``ToolMessage``, a ``look`` result) becomes one text line with
what it showed and how to see it again:

    [image attachment:449321 — not kept in this history: "screenshot of a
    Ticketmaster order". look("attachment:449321") to see it again]

The ref comes from the label block before the image (``[Uploaded image:
attachment:449321 · …]``, ``| file_id: <id>`` for a PWA upload, or a
``look`` result's header) and the description from
``media.hooks.describe_ref``. Blocks are replaced in place, so
``additional_kwargs.attachments[].block_index`` still points at the right
block; that entry is marked ``"dehydrated": true`` and the conversation view
keeps showing a PWA upload as its photo chip (``file_id``). Only copies are
changed: the live turn keeps its pixels. ``EDWARD_MEDIA=off`` keeps history
as it was.

The line names the tool that can show it again. Without the ``look`` tool
(``services/toolset/look.py``), an iMessage attachment points at
``get_imessage_attachment`` and any other image (a PWA upload, an
unlabelled block) stays in history as it is, because nothing could bring it
back.

One pass: ``streaming._checkpoint_messages`` calls
``history_window.strip_media_blocks``, which calls this module — media is
stripped exactly once. The history window's rules ride here too: the label
before a run of images names every image in it; an image a person sent with
no ref is kept whole (nothing could open it again); a PDF says "PDF".
``EDWARD_MEDIA=off`` keeps a person's images as they were, while a ``look``
result (a ``ToolMessage``) is still stripped.
"""

from __future__ import annotations

import importlib.util
import re
from typing import Any, List, Optional

_MEDIA_TYPES = {"image", "image_url", "input_image", "document", "file"}
_REF_RE = re.compile(r"\b(attachment:\d+|media:\d+|file:[\w-]+)")
_FILE_ID_RE = re.compile(r"file_id:\s*([\w-]+)")


def _is_media(block: Any) -> bool:
    return isinstance(block, dict) and block.get("type") in _MEDIA_TYPES


def _kind(block: dict) -> str:
    if block.get("type") in ("document", "file"):
        mt = str((block.get("source") or {}).get("media_type") or block.get("mime_type") or "")
        return "PDF" if (mt == "application/pdf" or block.get("type") == "document") else "file"
    return "image"


def ref_from_label(text: Optional[str]) -> Optional[str]:
    """The media ref named in a label block, or None. Pure."""
    if not text:
        return None
    m = _REF_RE.search(text)
    if m:
        return m.group(1)
    m = _FILE_ID_RE.search(text)
    return f"file:{m.group(1)}" if m else None


def _look_available() -> bool:
    try:
        return importlib.util.find_spec("services.toolset.look") is not None
    except (ImportError, ValueError):
        return False


def see_again(ref: Optional[str]) -> Optional[str]:
    """The call that shows ``ref`` again, or None when nothing can."""
    if ref and _look_available():
        return f'look("{ref}")'
    m = re.fullmatch(r"attachment:(\d+)", ref or "")
    return f"get_imessage_attachment({m.group(1)})" if m else None


def replacement(kind: str, ref: Optional[str]) -> Optional[str]:
    """The line an image or PDF leaves in history, or None to keep the block
    (nothing could show it again). Pure but for the description cache."""
    if not ref:
        return f"[{kind} — not kept in this history]" if _look_available() else None
    again = see_again(ref)
    if again is None:
        return None
    try:
        from services.media.hooks import describe_ref
        desc = describe_ref(ref)
    except Exception:  # noqa: BLE001
        desc = None
    said = f': "{desc}"' if desc else ""
    return f"[{kind} {ref} — not kept in this history{said}. {again} to see it again]"


def _from_a_person(message: Any) -> bool:
    return getattr(message, "type", None) == "human"


def _strip_one(message: Any) -> Any:
    content = getattr(message, "content", None)
    if not isinstance(content, list) or not any(_is_media(b) for b in content):
        return message
    person = _from_a_person(message)
    new_content: List[Any] = []
    replaced = set()
    ref: Optional[str] = None
    for i, block in enumerate(content):
        if not _is_media(block):
            # The text block just before a run of images names the run.
            if isinstance(block, dict) and block.get("type") == "text":
                ref = ref_from_label(block.get("text"))
            new_content.append(block)
            continue
        if person and not ref:
            # Something a person sent that nothing could open again: kept.
            new_content.append(block)
            continue
        line = replacement(_kind(block), ref)
        if line is None:
            new_content.append(block)
            continue
        new_content.append({"type": "text", "text": line})
        replaced.add(i)
    if not replaced:
        return message
    update = {"content": new_content}
    ak = dict(getattr(message, "additional_kwargs", None) or {})
    if isinstance(ak.get("attachments"), list):
        ak["attachments"] = [
            ({**meta, "dehydrated": True} if isinstance(meta, dict) and meta.get("block_index") in replaced else meta)
            for meta in ak["attachments"]
        ]
        update["additional_kwargs"] = ak
    try:
        return message.model_copy(update=update)
    except AttributeError:  # pydantic v1 messages
        return message.copy(update=update)


def enabled() -> bool:
    try:
        from services.media.hooks import enabled as media_enabled
        return media_enabled()
    except Exception:  # noqa: BLE001
        return True


def strip_media_blocks(messages: List[Any]) -> List[Any]:
    """Copies of ``messages`` with every image/PDF block replaced in place by
    its history line; messages without media are returned as they are.
    With ``EDWARD_MEDIA=off`` only tool results (``look``) are stripped."""
    on = enabled()
    return [_strip_one(m) if (on or getattr(m, "type", None) == "tool") else m for m in messages]
