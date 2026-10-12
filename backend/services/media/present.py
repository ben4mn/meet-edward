"""The ping trigger's media (2026-10-11; docs/architecture/media.md).

``turn_attachments(atts, max_images=6)`` turns the listener's attachment
dicts (``{rowid, path, mime_type, name, label}``; this message's first, then
recent thread images, newest first) into ``chat_with_memory`` attachment
dicts:

- at most ``max_images`` JPEGs (an animation's frames count one each), each
  named by its label, which carries the ``attachment:<rowid>`` ref:
  ``_build_human_message`` puts ``[Uploaded image: <label>]`` before the
  block, and the history dehydrator finds the ref there;
- at most one PDF of at most 8 MB, as a document block;
- everything else as a short text note (a transcript, a video's words, "too
  big — look(…)", "not downloaded yet"), which the human message shows as an
  attached text file.

Images past the budget are never converted, only listed. The whole request
stays far under the API's 32 MB body: 6 JPEGs of <= 1,600 px and one 8 MB
PDF (base64 ~11 MB).
"""

from __future__ import annotations

import base64
import json
import re
from typing import Any, Dict, List, Optional, Sequence

from services.media import view

TRIGGER_PDF_MAX_BYTES = 8 * 1024 * 1024


def _ref(att: Dict[str, Any]) -> Optional[str]:
    if att.get("ref"):
        return str(att["ref"])
    if att.get("rowid") is not None:
        return f"attachment:{int(att['rowid'])}"
    return str(att["path"]) if att.get("path") else None


def _label(att: Dict[str, Any], ref: str) -> str:
    if att.get("label"):
        return str(att["label"])
    name = att.get("name")
    return f"{ref} · {name}" if name else ref


def _note(label: str, text: str) -> Dict[str, Any]:
    return {"filename": label, "mime_type": "text/plain",
            "data": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _is_image(att: Dict[str, Any]) -> bool:
    return str(att.get("mime_type") or "").lower().startswith("image/")


async def turn_attachments(attachments: Sequence[Dict[str, Any]], *, max_images: int = 6) -> List[Dict[str, Any]]:
    """Attachment dicts for the ping turn's trigger (see the module doc)."""
    out: List[Dict[str, Any]] = []
    images_left = max(0, int(max_images))
    pdf_done = False
    seen = set()
    for att in attachments or ():
        ref = _ref(att)
        if not ref or ref in seen:
            continue
        seen.add(ref)
        label = _label(att, ref)
        if _is_image(att) and images_left <= 0:
            out.append(_note(label, f'an image I didn\'t attach (one message carries {max_images}) — '
                                    f'look("{ref}") to see it'))
            continue
        v = await view(ref, max_images=max(1, images_left))
        if v.error:
            out.append(_note(label, v.error))
            continue
        frames = list(v.images[:images_left])
        for i, jpeg in enumerate(frames):
            name = label if len(frames) == 1 else f"{label} · frame {i + 1} of {len(frames)}"
            out.append({"filename": name, "mime_type": "image/jpeg", "data": _b64(jpeg)})
        images_left -= len(frames)
        if v.pdf is not None:
            if not pdf_done and len(v.pdf) <= TRIGGER_PDF_MAX_BYTES:
                out.append({"filename": label, "mime_type": "application/pdf", "data": _b64(v.pdf)})
                pdf_done = True
            else:
                why = "too big to attach here" if len(v.pdf) > TRIGGER_PDF_MAX_BYTES else "one PDF per message"
                out.append(_note(label, f'a PDF ({why}) — look("{ref}") to read it'))
        elif v.images and not frames:
            out.append(_note(label, f'an image I didn\'t attach (one message carries {max_images}) — '
                                    f'look("{ref}") to see it'))
            continue
        elif v.images and len(v.images) > len(frames):
            out.append(_note(label, f'more frames than one message carries — look("{ref}") for the rest'))
        if v.kind in ("audio", "video", "file", "link") or (v.text and not frames and v.pdf is None):
            if v.text:
                out.append(_note(label, v.text))
    return out


def media_counts(attachments: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """{"images", "bytes"} (raw) of what ``turn_attachments`` attached, for
    the turn's media budget (``CURRENT_PING["media"]``, the ``look`` tool's)."""
    images = 0
    raw = 0
    for a in attachments or ():
        mime = str(a.get("mime_type") or "")
        if mime == "image/jpeg" or mime == "application/pdf":
            raw += len(base64.b64decode(a.get("data") or ""))
            images += mime == "image/jpeg"
    return {"images": images, "bytes": raw}


def describe_ref(ref: str) -> Optional[str]:
    """``media.hooks.describe_ref``'s fallback past its in-memory cache. This
    build keeps every description there (the describer and the transcripts
    note theirs); a persistent media store (``media_items``) would answer
    here."""
    return None


# ``event_text`` (2026-10-11): a placeholder still without its
# description ends in its ref (``[image: IMG_0042.heic attachment:449321]``);
# once described it reads ``[… attachment:449321 - "<description>"]``.
_BARE_PLACEHOLDER = re.compile(r"\[([^\[\]]*?\b(attachment:\d+))\]")
# The describer's stored line (``listener_imessage._describe_event_attachments``):
# ``[<kind> <name> attachment:<rowid>]: <description>``.
_DESCRIBED_LINE = re.compile(r"^\[[^\]]*?\b(attachment:\d+)\]:\s*(.+)$", re.S)
DESCRIPTION_CHARS = 300


def _raw_of(row: Any) -> Dict[str, Any]:
    raw = getattr(row, "raw_data", None)
    if isinstance(raw, dict):
        return raw
    try:
        data = json.loads(raw) if raw else {}
    except (TypeError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _clean_description(text: Any) -> str:
    # Inside a bracketed, quoted placeholder: never a quote or bracket of its own.
    flat = " ".join(str(text or "").split())[:DESCRIPTION_CHARS]
    return flat.replace('"', "'").replace("[", "(").replace("]", ")")


def _stored_descriptions(raw: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for line in raw.get("attachment_descriptions") or []:
        m = _DESCRIBED_LINE.match(str(line or "").strip())
        if m and m.group(2).strip():
            out.setdefault(m.group(1), _clean_description(m.group(2)))
    return out


def _cached_description(ref: str) -> Optional[str]:
    try:
        from services.media import hooks

        found = hooks.describe_ref(ref)
    except Exception:  # noqa: BLE001
        return None
    return _clean_description(found) if found else None


def event_text(row: Any) -> str:
    """A heartbeat event's message with what its attachments show: the
    listener's ``raw_data.full_text`` with each bare ``attachment:<rowid>``
    placeholder filled from the describer's stored lines
    (``raw_data.attachment_descriptions``), else from the describer's
    in-memory cache (``media.hooks.describe_ref``: described, not yet
    written back); a description whose placeholder is not in the text is
    added after it. "" when the event has no text, so the callers
    (``mind_service.event_text``, ``noticing_service.event_text``) fall
    back to the summary. Pure but for the cache; never raises."""
    try:
        raw = _raw_of(row)
        text = str(raw.get("full_text") or "")
        stored = _stored_descriptions(raw)
        if not text and not stored:
            return ""
        seen: set = set()

        def fill(m: "re.Match[str]") -> str:
            ref = m.group(2)
            seen.add(ref)
            desc = stored.get(ref) or _cached_description(ref)
            return f'[{m.group(1)} - "{desc}"]' if desc else m.group(0)

        out = _BARE_PLACEHOLDER.sub(fill, text)
        extra = [f'[{ref} - "{desc}"]' for ref, desc in stored.items()
                 if ref not in seen and not re.search(rf"\b{re.escape(ref)}\b", text)]
        return "\n".join([out] + extra).strip() if extra else out
    except Exception:  # noqa: BLE001
        return ""
