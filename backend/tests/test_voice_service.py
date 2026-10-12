"""ElevenLabs voice service: batch STT over HTTP, realtime STT and TTS over
WebSockets. Sockets are exercised against fake local servers built with
the same ``websockets`` package the service uses; HTTP goes through
``httpx.MockTransport``. No network.
"""

import asyncio
import base64
import json

import httpx
import pytest
from websockets.asyncio.server import serve

from services.voice_service import VoiceService, VoiceServiceError


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

class _FakeServer:
    """Context manager around a local websockets server on an ephemeral port."""

    def __init__(self, handler):
        self.handler = handler
        self.server = None
        self.seen_headers = []
        self.seen_paths = []

    async def __aenter__(self):
        async def _wrapped(ws):
            self.seen_headers.append(dict(ws.request.headers))
            self.seen_paths.append(ws.request.path)
            await self.handler(ws)

        self.server = await serve(_wrapped, "127.0.0.1", 0).__aenter__()
        port = self.server.sockets[0].getsockname()[1]
        self.base = f"ws://127.0.0.1:{port}"
        return self

    async def __aexit__(self, *exc):
        self.server.close()
        await self.server.wait_closed()


def _svc(**over) -> VoiceService:
    kwargs = dict(api_key="test-key", voice_id="VOICE1")
    kwargs.update(over)
    return VoiceService(**kwargs)


# --------------------------------------------------------------------------
# status
# --------------------------------------------------------------------------

def test_status_needs_only_the_elevenlabs_key(monkeypatch):
    from services import voice_service as vs

    monkeypatch.setattr(vs, "ELEVENLABS_API_KEY", "k")
    assert vs.is_configured() is True
    assert vs.get_status()["status"] == "connected"
    monkeypatch.setattr(vs, "ELEVENLABS_API_KEY", None)
    assert vs.is_configured() is False
    assert vs.get_status()["status"] == "error"
    assert "ELEVENLABS" in vs.get_status()["status_message"]


# --------------------------------------------------------------------------
# batch transcription (voice memos)
# --------------------------------------------------------------------------

async def test_transcribe_posts_multipart_to_scribe_v2():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("xi-api-key")
        body = request.content
        captured["has_model"] = b'name="model_id"' in body and b"scribe_v2" in body
        captured["has_file"] = b'filename="memo.m4a"' in body
        captured["has_lang"] = b'name="language_code"' in body
        return httpx.Response(200, json={"text": "Pick up the dry cleaning.", "language_code": "eng"})

    svc = _svc(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    result = await svc.transcribe(b"\x00\x01", filename="memo.m4a", mime_type="audio/mp4")

    assert result.text == "Pick up the dry cleaning."
    assert result.language == "eng"
    assert captured["url"].endswith("/v1/speech-to-text")
    assert captured["key"] == "test-key"
    assert captured["has_model"] and captured["has_file"] and captured["has_lang"]


async def test_transcribe_failure_raises_voice_error_with_status():
    def handler(request):
        return httpx.Response(401, json={"detail": "bad key"})

    svc = _svc(http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with pytest.raises(VoiceServiceError) as exc:
        await svc.transcribe(b"\x00", filename="a.wav", mime_type="audio/wav")
    assert "401" in str(exc.value)


async def test_transcribe_without_key_raises_before_any_request():
    svc = _svc(api_key=None)
    with pytest.raises(VoiceServiceError):
        await svc.transcribe(b"\x00", filename="a.wav", mime_type="audio/wav")


# --------------------------------------------------------------------------
# realtime STT stream
# --------------------------------------------------------------------------

async def test_stt_stream_relays_audio_and_yields_parsed_events():
    received = []

    async def scribe(ws):
        await ws.send(json.dumps({"message_type": "session_started", "session_id": "s1"}))
        async for msg in ws:
            data = json.loads(msg)
            received.append(data)
            if data.get("commit"):
                await ws.send(json.dumps({"message_type": "committed_transcript", "text": "hello edward"}))
                break
            await ws.send(json.dumps({"message_type": "partial_transcript", "text": "hel"}))
        await ws.close()

    async with _FakeServer(scribe) as fake:
        svc = _svc(scribe_base_url=fake.base + "/v1/speech-to-text/realtime")
        events = []
        async with svc.open_stt_stream(commit_strategy="manual") as stt:
            await stt.send_audio(b"\x01\x02\x03\x04")
            await stt.commit()
            async for ev in stt.events():
                events.append(ev)
                if ev.kind == "committed":
                    break

    kinds = [e.kind for e in events]
    assert kinds[0] == "session_started"
    assert "partial" in kinds and kinds[-1] == "committed"
    assert events[-1].text == "hello edward"
    # Audio reached the server intact, and the commit frame was flagged.
    assert base64.b64decode(received[0]["audio_base_64"]) == b"\x01\x02\x03\x04"
    assert received[0]["commit"] is False and received[-1]["commit"] is True
    # Auth went in the header, not the query string.
    assert fake.seen_headers[0].get("xi-api-key") == "test-key"
    assert "commit_strategy=manual" in fake.seen_paths[0]
    assert "model_id=scribe_v2_realtime" in fake.seen_paths[0]


async def test_stt_stream_surfaces_server_errors_as_events_not_exceptions():
    async def scribe(ws):
        await ws.send(json.dumps({"message_type": "quota_exceeded", "error": "out of credits"}))
        await ws.close()

    async with _FakeServer(scribe) as fake:
        svc = _svc(scribe_base_url=fake.base)
        async with svc.open_stt_stream() as stt:
            events = [ev async for ev in stt.events()]
    assert events[-1].kind == "error"
    assert events[-1].code == "quota_exceeded"


# --------------------------------------------------------------------------
# streaming TTS
# --------------------------------------------------------------------------

async def test_tts_stream_sends_init_text_close_and_yields_pcm():
    got = []

    async def tts(ws):
        async for msg in ws:
            data = json.loads(msg)
            got.append(data)
            if data.get("text") == "":
                await ws.send(json.dumps({"audio": None, "isFinal": True}))
                break
            if data.get("text", " ").strip():
                await ws.send(json.dumps({"audio": base64.b64encode(b"PCM" + data["text"].encode()).decode(), "isFinal": None}))
        await ws.close()

    async with _FakeServer(tts) as fake:
        svc = _svc(tts_base_url=fake.base + "/v1/text-to-speech")
        chunks = []
        async with svc.open_tts_stream() as stream:
            await stream.send_text("Hello. ")
            await stream.send_text("Bye.", flush=True)
            await stream.finish()
            async for chunk in stream.audio():
                chunks.append(chunk)

    assert got[0]["text"] == " " and "voice_settings" in got[0]
    assert got[1]["text"] == "Hello. "
    assert got[2]["text"] == "Bye." and got[2]["flush"] is True
    assert got[3]["text"] == ""
    assert chunks == [b"PCMHello. ", b"PCMBye."]
    assert "/VOICE1/stream-input" in fake.seen_paths[0]
    assert "output_format=pcm_24000" in fake.seen_paths[0]
    assert fake.seen_headers[0].get("xi-api-key") == "test-key"


async def test_tts_stream_close_mid_generation_does_not_raise():
    async def tts(ws):
        try:
            async for _ in ws:
                await ws.send(json.dumps({"audio": base64.b64encode(b"x").decode(), "isFinal": None}))
                await asyncio.sleep(0.05)
        except Exception:
            pass

    async with _FakeServer(tts) as fake:
        svc = _svc(tts_base_url=fake.base)
        async with svc.open_tts_stream() as stream:
            await stream.send_text("long text")
            first = await stream.audio().__anext__()
            assert first == b"x"
            await stream.close()  # barge-in


async def test_tts_stream_model_override_hits_url():
    async def tts(ws):
        async for msg in ws:
            if json.loads(msg).get("text") == "":
                await ws.send(json.dumps({"isFinal": True}))
                break

    async with _FakeServer(tts) as fake:
        svc = _svc(tts_base_url=fake.base, tts_model="eleven_v3_conversational")
        async with svc.open_tts_stream() as stream:
            await stream.finish()
            async for _ in stream.audio():
                pass
    assert "model_id=eleven_v3_conversational" in fake.seen_paths[0]
