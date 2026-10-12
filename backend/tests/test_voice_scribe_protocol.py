"""Scribe v2 Realtime wire protocol — pure encode/parse, no sockets.

Shapes verified against the live API on 2026-09-05:
  server: {"message_type": "session_started"|"partial_transcript"|"committed_transcript", "text": ...}
  client: {"message_type": "input_audio_chunk", "audio_base_64": ..., "commit": bool, "sample_rate": int}
"""

import base64
import json
from urllib.parse import parse_qs, urlparse

import pytest

from services.voice.scribe_protocol import (
    SCRIBE_MODEL,
    build_scribe_url,
    encode_audio_chunk,
    encode_commit,
    is_recoverable_error,
    parse_scribe_message,
)


def test_url_defaults_to_vad_commit_pcm16k_english():
    url = build_scribe_url()
    parsed = urlparse(url)
    q = parse_qs(parsed.query)
    assert parsed.scheme == "wss"
    assert parsed.path.endswith("/v1/speech-to-text/realtime")
    assert q["model_id"] == [SCRIBE_MODEL]
    assert q["audio_format"] == ["pcm_16000"]
    assert q["commit_strategy"] == ["vad"]
    assert q["language_code"] == ["en"]
    assert float(q["vad_silence_threshold_secs"][0]) == pytest.approx(0.7)


def test_url_manual_commit_omits_vad_tuning():
    q = parse_qs(urlparse(build_scribe_url(commit_strategy="manual")).query)
    assert q["commit_strategy"] == ["manual"]
    assert "vad_silence_threshold_secs" not in q


def test_audio_chunk_is_base64_with_commit_false():
    pcm = b"\x01\x02\x03\x04"
    msg = json.loads(encode_audio_chunk(pcm))
    assert msg["message_type"] == "input_audio_chunk"
    assert base64.b64decode(msg["audio_base_64"]) == pcm
    assert msg["commit"] is False
    assert msg["sample_rate"] == 16000


def test_commit_message_carries_commit_true_and_some_silence():
    msg = json.loads(encode_commit())
    assert msg["message_type"] == "input_audio_chunk"
    assert msg["commit"] is True
    silence = base64.b64decode(msg["audio_base_64"])
    assert len(silence) >= 320 and set(silence) == {0}


def test_parse_partial_and_committed():
    p = parse_scribe_message('{"message_type": "partial_transcript", "text": "Hey, Ed"}')
    assert p.kind == "partial" and p.text == "Hey, Ed"
    c = parse_scribe_message({"message_type": "committed_transcript", "text": "Hey, Edward."})
    assert c.kind == "committed" and c.text == "Hey, Edward."


def test_parse_session_started_and_unknown():
    s = parse_scribe_message({"message_type": "session_started", "session_id": "x"})
    assert s.kind == "session_started"
    o = parse_scribe_message({"message_type": "committed_transcript_with_timestamps", "text": "a"})
    assert o.kind == "other"


def test_parse_errors_carry_code():
    e = parse_scribe_message({"message_type": "quota_exceeded", "error": "no credits"})
    assert e.kind == "error"
    assert e.code == "quota_exceeded"
    assert "no credits" in e.text


def test_parse_garbage_is_error_not_exception():
    e = parse_scribe_message("not json")
    assert e.kind == "error"
    assert e.code == "bad_message"


def test_session_time_limit_is_recoverable_auth_is_not():
    assert is_recoverable_error("session_time_limit_exceeded") is True
    assert is_recoverable_error("auth_error") is False
    assert is_recoverable_error("quota_exceeded") is False


# --- sensitivity tuning -----------------------------------------------------
#
# Scribe's VAD hears everything. A noisy room needs a higher voice-activity
# threshold and a minimum speech length so a clatter of dishes doesn't
# commit a transcript. "normal" and "high" leave the server defaults alone;
# the client-side gate does the rest.

from services.voice.scribe_protocol import scribe_tuning


def test_url_carries_vad_tuning_when_given():
    q = parse_qs(urlparse(build_scribe_url(vad_threshold=0.65, min_speech_duration_ms=250)).query)
    assert float(q["vad_threshold"][0]) == pytest.approx(0.65)
    assert q["min_speech_duration_ms"] == ["250"]


def test_url_omits_vad_tuning_by_default():
    q = parse_qs(urlparse(build_scribe_url()).query)
    assert "vad_threshold" not in q
    assert "min_speech_duration_ms" not in q


def test_noisy_room_tuning_raises_threshold_and_min_speech():
    t = scribe_tuning("low")
    assert t["vad_threshold"] > 0.5
    assert t["min_speech_duration_ms"] >= 200


def test_other_sensitivities_keep_server_defaults():
    assert scribe_tuning("normal") == {}
    assert scribe_tuning("high") == {}
    assert scribe_tuning("garbage") == {}
    assert scribe_tuning(None) == {}
