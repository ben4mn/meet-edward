"""Voice memos posted into ordinary chat."""

from __future__ import annotations

import os

AUDIO_MIME_TYPES = {
    "audio/webm",
    "audio/mp4",
    "audio/mpeg",
    "audio/wav",
    "audio/ogg",
    "audio/flac",
    "audio/aac",
}

_ALIASES = {
    "audio/x-m4a": "audio/mp4",
    "audio/m4a": "audio/mp4",
    "audio/x-wav": "audio/wav",
    "audio/wave": "audio/wav",
    "audio/mp3": "audio/mpeg",
    "audio/x-mpeg": "audio/mpeg",
    "audio/opus": "audio/ogg",
    "audio/x-flac": "audio/flac",
}

_EXT_TO_MIME = {
    ".m4a": "audio/mp4",
    ".mp4": "audio/mp4",
    ".webm": "audio/webm",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".ogg": "audio/ogg",
    ".oga": "audio/ogg",
    ".opus": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
}


def is_audio_mime(mime: str) -> bool:
    return (mime or "").split(";")[0].strip().lower() in AUDIO_MIME_TYPES


def normalize_audio_mime(mime: str, filename: str = "") -> str:
    base = (mime or "").split(";")[0].strip().lower()
    base = _ALIASES.get(base, base)
    if base in AUDIO_MIME_TYPES:
        return base
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in _EXT_TO_MIME:
        return _EXT_TO_MIME[ext]
    return base or "application/octet-stream"


def memo_marker(filename: str, file_id: str) -> str:
    return f"[Voice memo: {filename} | file_id: {file_id}]"


def compose_memo_message(typed_text: str, transcript: str) -> str:
    typed = (typed_text or "").strip()
    said = (transcript or "").strip()
    if not said:
        said = "(Voice memo attached, but no speech could be transcribed.)"
    if typed:
        return f"{typed}\n\n{said}"
    return said


def memo_title(transcript: str) -> str:
    said = (transcript or "").strip()
    if not said:
        return "Voice memo"
    if len(said) <= 50:
        return said
    return said[:50].rstrip() + "..."
