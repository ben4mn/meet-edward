"""ElevenLabs stream-input TTS wire protocol — pure encode/parse.

Verified live 2026-09-05: audio frames are {"audio": "<b64>", "isFinal": null, ...};
the terminal frame is {"audio": null, "isFinal": true}.
"""

import base64
import json
from urllib.parse import parse_qs, urlparse

from services.voice.tts_protocol import (
    DEFAULT_TTS_MODEL,
    TTS_OUTPUT_FORMAT,
    TTS_SAMPLE_RATE,
    build_tts_url,
    encode_close,
    encode_init,
    encode_text,
    parse_tts_message,
)


def test_url_targets_voice_with_pcm_and_auto_mode():
    url = build_tts_url("VOICE123")
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.path.endswith("/v1/text-to-speech/VOICE123/stream-input")
    assert q["model_id"] == [DEFAULT_TTS_MODEL]
    assert q["output_format"] == [TTS_OUTPUT_FORMAT]
    assert q["auto_mode"] == ["true"]
    assert int(q["inactivity_timeout"][0]) >= 60


def test_pcm_output_is_24k():
    assert TTS_OUTPUT_FORMAT == "pcm_24000"
    assert TTS_SAMPLE_RATE == 24000


def test_model_override():
    q = parse_qs(urlparse(build_tts_url("v", model_id="eleven_v3_conversational")).query)
    assert q["model_id"] == ["eleven_v3_conversational"]


def test_init_message_is_single_space_with_voice_settings():
    msg = json.loads(encode_init())
    assert msg["text"] == " "
    assert 0 <= msg["voice_settings"]["stability"] <= 1
    assert 0 <= msg["voice_settings"]["similarity_boost"] <= 1


def test_text_message_and_flush():
    plain = json.loads(encode_text("Hello there. "))
    assert plain["text"] == "Hello there. "
    assert plain.get("flush", False) is False
    flushed = json.loads(encode_text("Bye.", flush=True))
    assert flushed["flush"] is True


def test_close_is_empty_text():
    assert json.loads(encode_close()) == {"text": ""}


def test_parse_audio_frame():
    raw = base64.b64encode(b"\x00\x01" * 10).decode()
    ev = parse_tts_message(json.dumps({"audio": raw, "isFinal": None, "alignment": {}}))
    assert ev.audio == b"\x00\x01" * 10
    assert ev.is_final is False


def test_parse_final_frame_has_no_audio():
    ev = parse_tts_message({"audio": None, "isFinal": True})
    assert ev.audio == b""
    assert ev.is_final is True


def test_parse_error_frame():
    ev = parse_tts_message({"error": "quota", "code": 1008})
    assert ev.is_final is True
    assert ev.error == "quota"
