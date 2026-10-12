"""ElevenLabs stream-input TTS — wire protocol, no sockets.

Verified live 2026-09-05: audio frames are ``{"audio": "<b64>", "isFinal": null}``;
the terminal frame is ``{"audio": null, "isFinal": true}``.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional, Union
from urllib.parse import urlencode

TTS_WS_BASE = "wss://api.elevenlabs.io/v1/text-to-speech"
DEFAULT_TTS_MODEL = "eleven_flash_v2_5"
TTS_OUTPUT_FORMAT = "pcm_24000"
TTS_SAMPLE_RATE = 24000

DEFAULT_VOICE_SETTINGS: Dict[str, Any] = {
    "stability": 0.5,
    "similarity_boost": 0.75,
    "use_speaker_boost": True,
}


def build_tts_url(
    voice_id: str,
    *,
    model_id: str = DEFAULT_TTS_MODEL,
    output_format: str = TTS_OUTPUT_FORMAT,
    auto_mode: bool = True,
    inactivity_timeout: int = 60,
    base_url: str = TTS_WS_BASE,
) -> str:
    params = {
        "model_id": model_id,
        "output_format": output_format,
        "auto_mode": "true" if auto_mode else "false",
        "inactivity_timeout": str(inactivity_timeout),
    }
    return f"{base_url}/{voice_id}/stream-input?{urlencode(params)}"


def encode_init(voice_settings: Optional[Dict[str, Any]] = None) -> str:
    return json.dumps(
        {"text": " ", "voice_settings": dict(voice_settings or DEFAULT_VOICE_SETTINGS)}
    )


def encode_text(text: str, *, flush: bool = False) -> str:
    msg: Dict[str, Any] = {"text": text}
    if flush:
        msg["flush"] = True
    return json.dumps(msg)


def encode_close() -> str:
    return json.dumps({"text": ""})


@dataclass
class TTSEvent:
    audio: bytes = b""
    is_final: bool = False
    error: str = ""


def parse_tts_message(raw: Union[str, bytes, Dict[str, Any]]) -> TTSEvent:
    if isinstance(raw, (str, bytes)):
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            return TTSEvent(is_final=True, error="unparseable message")
    else:
        data = raw
    if not isinstance(data, dict):
        return TTSEvent(is_final=True, error="unexpected payload")

    if data.get("error"):
        return TTSEvent(is_final=True, error=str(data.get("error")))

    audio_b64 = data.get("audio")
    audio = base64.b64decode(audio_b64) if audio_b64 else b""
    return TTSEvent(audio=audio, is_final=bool(data.get("isFinal")))
