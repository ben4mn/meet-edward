"""ElevenLabs Scribe v2 Realtime — wire protocol, no sockets.

Shapes verified against the live API on 2026-09-05.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field
from typing import Optional, Any, Dict, Union
from urllib.parse import urlencode

SCRIBE_WS_URL = "wss://api.elevenlabs.io/v1/speech-to-text/realtime"
SCRIBE_MODEL = "scribe_v2_realtime"
SCRIBE_SAMPLE_RATE = 16000

# Server message_type values that mean "this session is broken".
_ERROR_TYPES = {
    "auth_error",
    "quota_exceeded",
    "rate_limited",
    "resource_exhausted",
    "session_time_limit_exceeded",
    "input_error",
    "chunk_size_exceeded",
    "insufficient_audio_activity",
    "error",
}

# Errors where opening a fresh Scribe socket and carrying on is the
# right move. Auth and quota are not going to fix themselves.
_RECOVERABLE = {
    "session_time_limit_exceeded",
    "rate_limited",
    "resource_exhausted",
    "insufficient_audio_activity",
}

# 10 ms of 16-bit mono silence at 16 kHz; a manual commit needs *some*
# audio attached to the commit frame.
_COMMIT_SILENCE = b"\x00" * 320


def build_scribe_url(
    *,
    commit_strategy: str = "vad",
    sample_rate: int = SCRIBE_SAMPLE_RATE,
    language: str = "en",
    vad_silence_secs: float = 0.7,
    base_url: str = SCRIBE_WS_URL,
    vad_threshold: Optional[float] = None,
    min_speech_duration_ms: Optional[int] = None,
) -> str:
    params: Dict[str, Any] = {
        "model_id": SCRIBE_MODEL,
        "audio_format": f"pcm_{sample_rate}",
        "language_code": language,
        "commit_strategy": commit_strategy,
        "include_timestamps": "false",
    }
    if commit_strategy == "vad":
        params["vad_silence_threshold_secs"] = f"{vad_silence_secs:g}"
    if vad_threshold is not None:
        params["vad_threshold"] = f"{vad_threshold:g}"
    if min_speech_duration_ms is not None:
        params["min_speech_duration_ms"] = str(int(min_speech_duration_ms))
    return f"{base_url}?{urlencode(params)}"


# Mic sensitivity, chosen by the person in the voice view:
#   high   — hears everything (a quiet room; the pre-2026-09-06 behaviour)
#   normal — the client-side noise gate is on, server defaults untouched
#   low    — a noisy room: the gate opens wider *and* Scribe itself needs a
#            stronger, longer voice before it will commit a transcript
SENSITIVITIES = ("high", "normal", "low")
NOISY_ROOM_TUNING = {"vad_threshold": 0.65, "min_speech_duration_ms": 250}


def normalize_sensitivity(value) -> str:
    return value if value in SENSITIVITIES else "normal"


def scribe_tuning(sensitivity) -> Dict[str, Any]:
    """Extra Scribe query parameters for a sensitivity level."""
    if normalize_sensitivity(sensitivity) == "low":
        return dict(NOISY_ROOM_TUNING)
    return {}


def encode_audio_chunk(
    pcm: bytes, *, commit: bool = False, sample_rate: int = SCRIBE_SAMPLE_RATE
) -> str:
    return json.dumps(
        {
            "message_type": "input_audio_chunk",
            "audio_base_64": base64.b64encode(pcm).decode("ascii"),
            "commit": bool(commit),
            "sample_rate": sample_rate,
        }
    )


def encode_commit(sample_rate: int = SCRIBE_SAMPLE_RATE) -> str:
    return encode_audio_chunk(_COMMIT_SILENCE, commit=True, sample_rate=sample_rate)


@dataclass
class ScribeEvent:
    kind: str  # session_started | partial | committed | error | other
    text: str = ""
    code: str = ""
    raw: Dict[str, Any] = field(default_factory=dict)


def parse_scribe_message(raw: Union[str, bytes, Dict[str, Any]]) -> ScribeEvent:
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return ScribeEvent(kind="error", code="bad_message", text="unparseable message")
    else:
        data = raw
    if not isinstance(data, dict):
        return ScribeEvent(kind="error", code="bad_message", text="unexpected payload")

    mtype = str(data.get("message_type", ""))
    text = str(data.get("text") or "")
    if mtype == "session_started":
        return ScribeEvent(kind="session_started", raw=data)
    if mtype == "partial_transcript":
        return ScribeEvent(kind="partial", text=text, raw=data)
    if mtype == "committed_transcript":
        return ScribeEvent(kind="committed", text=text, raw=data)
    if mtype in _ERROR_TYPES:
        detail = data.get("error") or data.get("message") or data.get("detail") or ""
        return ScribeEvent(kind="error", code=mtype, text=str(detail), raw=data)
    return ScribeEvent(kind="other", text=text, raw=data)


def is_recoverable_error(code: str) -> bool:
    return code in _RECOVERABLE
