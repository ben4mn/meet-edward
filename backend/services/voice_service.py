"""
Voice service for Edward — ElevenLabs end to end.

- Batch transcription (voice memos):   POST /v1/speech-to-text, scribe_v2
- Realtime transcription (voice mode): wss  /v1/speech-to-text/realtime, scribe_v2_realtime
- Streaming TTS (voice mode):          wss  /v1/text-to-speech/{voice}/stream-input, PCM 24 kHz
- One-shot TTS (read-aloud, HTTP):     POST /v1/text-to-speech/{voice}/stream, MP3

The wire formats live in ``services/voice/*_protocol.py`` and are unit
tested; this module only owns sockets and HTTP. Every network dependency
is injectable so the whole thing runs against fake local servers in tests.
"""

from __future__ import annotations

import asyncio
import io
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import AsyncGenerator, Awaitable, Callable, Optional

import httpx
from dotenv import load_dotenv

from services.voice.scribe_protocol import (
    SCRIBE_WS_URL,
    ScribeEvent,
    build_scribe_url,
    encode_audio_chunk,
    encode_commit,
    parse_scribe_message,
)
from services.voice.tts_protocol import (
    DEFAULT_TTS_MODEL,
    TTS_WS_BASE,
    build_tts_url,
    encode_close,
    encode_init,
    encode_text,
    parse_tts_message,
)

logger = logging.getLogger(__name__)

# Load .env so the keys are visible even when this module is imported first.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # Default: Rachel
ELEVENLABS_TTS_MODEL = os.getenv("ELEVENLABS_TTS_MODEL", DEFAULT_TTS_MODEL)

ELEVENLABS_HTTP_BASE = "https://api.elevenlabs.io"
SCRIBE_BATCH_MODEL = "scribe_v2"


class VoiceServiceError(Exception):
    """Raised when ElevenLabs refuses or the service is unconfigured."""


def is_configured() -> bool:
    return bool(ELEVENLABS_API_KEY)


def get_status() -> dict:
    if not ELEVENLABS_API_KEY:
        return {"status": "error", "status_message": "ELEVENLABS_API_KEY not set"}
    return {
        "status": "connected",
        "status_message": "ElevenLabs STT and TTS configured",
        "metadata": {"voice_id": ELEVENLABS_VOICE_ID, "tts_model": ELEVENLABS_TTS_MODEL},
    }


@dataclass
class TranscriptResult:
    text: str
    language: str = ""


async def _default_ws_connect(url: str, headers: dict):
    from websockets.asyncio.client import connect

    return await connect(url, additional_headers=headers, max_size=None, open_timeout=15)


class ScribeStream:
    """One realtime transcription socket."""

    def __init__(self, ws):
        self._ws = ws
        self._closed = False

    async def send_audio(self, pcm: bytes) -> None:
        if pcm and not self._closed:
            await self._ws.send(encode_audio_chunk(pcm))

    async def commit(self) -> None:
        if not self._closed:
            await self._ws.send(encode_commit())

    async def events(self) -> AsyncGenerator[ScribeEvent, None]:
        try:
            async for raw in self._ws:
                yield parse_scribe_message(raw)
        except Exception as e:  # connection closed / transport error
            if not self._closed:
                yield ScribeEvent(kind="error", code="connection_closed", text=str(e))

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close()
        except Exception:
            pass


class TTSStream:
    """One stream-input TTS socket: push text, pull PCM."""

    def __init__(self, ws):
        self._ws = ws
        self._closed = False
        self._finished = False

    async def send_text(self, text: str, *, flush: bool = False) -> None:
        if text and not self._closed and not self._finished:
            await self._ws.send(encode_text(text, flush=flush))

    async def finish(self) -> None:
        """No more text; ElevenLabs will flush and send isFinal."""
        if not self._closed and not self._finished:
            self._finished = True
            await self._ws.send(encode_close())

    async def audio(self) -> AsyncGenerator[bytes, None]:
        try:
            async for raw in self._ws:
                ev = parse_tts_message(raw)
                if ev.error:
                    logger.warning("TTS stream error: %s", ev.error)
                    return
                if ev.audio:
                    yield ev.audio
                if ev.is_final:
                    return
        except Exception as e:
            if not self._closed:
                logger.debug("TTS socket closed: %s", e)

    async def close(self) -> None:
        """Abort immediately (barge-in). Safe to call twice."""
        if self._closed:
            return
        self._closed = True
        try:
            await self._ws.close()
        except Exception:
            pass


class _StreamContext:
    def __init__(self, opener: Callable[[], Awaitable], wrapper):
        self._opener = opener
        self._wrapper = wrapper
        self._stream = None

    async def __aenter__(self):
        ws = await self._opener()
        self._stream = self._wrapper(ws)
        return self._stream

    async def __aexit__(self, *exc):
        if self._stream is not None:
            await self._stream.close()


class VoiceService:
    def __init__(
        self,
        api_key: Optional[str] = None,
        voice_id: Optional[str] = None,
        tts_model: Optional[str] = None,
        http_client: Optional[httpx.AsyncClient] = None,
        ws_connect: Optional[Callable[[str, dict], Awaitable]] = None,
        http_base_url: str = ELEVENLABS_HTTP_BASE,
        scribe_base_url: str = SCRIBE_WS_URL,
        tts_base_url: str = TTS_WS_BASE,
    ):
        self.api_key = api_key if api_key is not None else ELEVENLABS_API_KEY
        self.voice_id = voice_id or ELEVENLABS_VOICE_ID
        self.tts_model = tts_model or ELEVENLABS_TTS_MODEL
        self._http_client = http_client
        self._ws_connect = ws_connect or _default_ws_connect
        self.http_base_url = http_base_url.rstrip("/")
        self.scribe_base_url = scribe_base_url
        self.tts_base_url = tts_base_url.rstrip("/")

    # ---- plumbing ---------------------------------------------------------

    def _headers(self) -> dict:
        return {"xi-api-key": self.api_key or ""}

    def _require_key(self) -> None:
        if not self.api_key:
            raise VoiceServiceError("ElevenLabs API key not configured")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    async def close(self) -> None:
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    # ---- batch STT --------------------------------------------------------

    async def transcribe(
        self,
        audio_bytes: bytes,
        filename: str = "audio.webm",
        mime_type: Optional[str] = None,
        language: str = "en",
    ) -> TranscriptResult:
        self._require_key()
        client = await self._get_client()
        files = {"file": (filename, io.BytesIO(audio_bytes), mime_type or "application/octet-stream")}
        data = {"model_id": SCRIBE_BATCH_MODEL, "language_code": language, "tag_audio_events": "false"}
        response = await client.post(
            f"{self.http_base_url}/v1/speech-to-text",
            headers=self._headers(),
            files=files,
            data=data,
        )
        if response.status_code != 200:
            raise VoiceServiceError(
                f"ElevenLabs transcription failed: {response.status_code} - {response.text[:300]}"
            )
        body = response.json()
        return TranscriptResult(text=(body.get("text") or "").strip(), language=body.get("language_code") or "")

    # ---- one-shot TTS over HTTP (read-aloud, HTTP fallback) ---------------

    def _tts_http_payload(self, text: str) -> dict:
        return {
            "text": text,
            "model_id": self.tts_model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75},
        }

    async def synthesize(self, text: str) -> bytes:
        self._require_key()
        client = await self._get_client()
        response = await client.post(
            f"{self.http_base_url}/v1/text-to-speech/{self.voice_id}",
            headers={**self._headers(), "Content-Type": "application/json"},
            json=self._tts_http_payload(text),
        )
        if response.status_code != 200:
            raise VoiceServiceError(f"ElevenLabs TTS failed: {response.status_code} - {response.text[:300]}")
        return response.content

    async def synthesize_stream(self, text: str) -> AsyncGenerator[bytes, None]:
        self._require_key()
        client = await self._get_client()
        async with client.stream(
            "POST",
            f"{self.http_base_url}/v1/text-to-speech/{self.voice_id}/stream",
            headers={**self._headers(), "Content-Type": "application/json"},
            json={**self._tts_http_payload(text), "output_format": "mp3_44100_128"},
        ) as response:
            if response.status_code != 200:
                error_bytes = await response.aread()
                raise VoiceServiceError(
                    f"ElevenLabs TTS stream failed: {response.status_code} - {error_bytes[:300]!r}"
                )
            async for chunk in response.aiter_bytes(chunk_size=4096):
                if chunk:
                    yield chunk

    # ---- realtime sockets -------------------------------------------------

    def open_stt_stream(self, commit_strategy: str = "vad", language: str = "en", **tuning) -> _StreamContext:
        self._require_key()
        url = build_scribe_url(commit_strategy=commit_strategy, language=language, base_url=self.scribe_base_url, **tuning)

        async def _open():
            return await self._ws_connect(url, self._headers())

        return _StreamContext(_open, ScribeStream)

    def open_tts_stream(self, model_id: Optional[str] = None) -> _StreamContext:
        self._require_key()
        url = build_tts_url(self.voice_id, model_id=model_id or self.tts_model, base_url=self.tts_base_url)

        async def _open():
            ws = await self._ws_connect(url, self._headers())
            await ws.send(encode_init())
            return ws

        return _StreamContext(_open, TTSStream)


# Singleton
_voice_service: Optional[VoiceService] = None


def get_voice_service() -> VoiceService:
    global _voice_service
    if _voice_service is None:
        _voice_service = VoiceService()
    return _voice_service


async def shutdown_voice_service():
    global _voice_service
    if _voice_service:
        await _voice_service.close()
        _voice_service = None
