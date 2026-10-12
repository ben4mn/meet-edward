"""Voice messages, videos and animations (pings-media-spec §3.6 P1a):
afconvert/avconvert to M4A, ElevenLabs Scribe for the words (always faked
here), the Swift media helper (Appendix A7) for frames, probes and PDF text,
qlmanage when the helper isn't built."""

import math
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from services.media import MediaView, av, hooks, mediatool
from tests.test_media_convert import gif_bytes

HAS_SWIFTC = shutil.which("swiftc") is not None
HAS_AFCONVERT = shutil.which("afconvert") is not None
SWIFT_SRC = Path(__file__).resolve().parents[1] / "tools" / "media" / "edward_mediatool.swift"
JPEG = b"\xff\xd8\xff\xe0fake"


def wav_file(path: Path, seconds=1.0):
    w = wave.open(str(path), "wb")
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(16000)
    w.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(i / 10))) for i in range(int(16000 * seconds))))
    w.close()
    return path


@pytest.fixture(scope="session")
def built_tool(tmp_path_factory):
    if not HAS_SWIFTC:
        pytest.skip("needs swiftc")
    out = tmp_path_factory.mktemp("mediatool") / "edward-mediatool"
    r = subprocess.run(["swiftc", "-O", "-swift-version", "5", "-parse-as-library", "-o", str(out), str(SWIFT_SRC)],
                       capture_output=True, timeout=240)
    if r.returncode != 0:
        pytest.fail(r.stderr.decode()[-2000:])
    selftest = tmp_path_factory.mktemp("selftest")
    subprocess.run([str(out), "selftest", str(selftest)], capture_output=True, timeout=60, check=True)
    return out, selftest


@pytest.fixture
def scribe(monkeypatch):
    calls = []

    def fake(data, *, filename, mime_type):
        calls.append((len(data), filename, mime_type))
        return "see you at seven"

    monkeypatch.setattr(av, "_transcribe_bytes", fake)
    monkeypatch.setattr(av, "_scribe_configured", lambda: True)
    av._transcripts.clear()
    return calls


class TestMediatool:
    def test_located_by_env_or_home(self, monkeypatch, tmp_path):
        tool = tmp_path / "edward-mediatool"
        tool.write_text("#!/bin/sh\n")
        tool.chmod(0o755)
        monkeypatch.setenv("EDWARD_MEDIATOOL", str(tool))
        assert mediatool.available() and mediatool.binary_path() == tool
        monkeypatch.setenv("EDWARD_MEDIATOOL", str(tmp_path / "missing"))
        assert not mediatool.available()

    def test_the_real_helper(self, built_tool, monkeypatch):
        tool, selftest = built_tool
        monkeypatch.setenv("EDWARD_MEDIATOOL", str(tool))
        probe = mediatool.probe(selftest / "selftest.mov")
        assert probe["kind"] == "video" and probe["has_audio"] is False and probe["duration"] == pytest.approx(2, abs=0.2)
        frames = mediatool.frames(selftest / "selftest.mov", count=3, max_edge=512)
        assert len(frames) == 3 and all(f[:3] == b"\xff\xd8\xff" for f in frames)
        gif_frames = mediatool.frames_from_bytes(gif_bytes(frames=3), suffix=".gif", count=4)
        assert len(gif_frames) == 3

    def test_a_failure_is_empty(self, monkeypatch, tmp_path):
        tool = tmp_path / "edward-mediatool"
        tool.write_text("#!/bin/sh\necho '{\"ok\": false, \"error\": \"nope\"}'\nexit 3\n")
        tool.chmod(0o755)
        monkeypatch.setenv("EDWARD_MEDIATOOL", str(tool))
        assert mediatool.frames(tmp_path / "x.mov", count=3, max_edge=512) == []
        assert mediatool.probe(tmp_path / "x.mov") is None
        assert mediatool.pdf_text(tmp_path / "x.pdf") is None


@pytest.mark.skipif(not HAS_AFCONVERT, reason="needs macOS afconvert")
class TestVoiceMessages:
    def test_a_voice_message_is_heard(self, scribe, tmp_path):
        caf = tmp_path / "Audio Message.caf"
        subprocess.run(["afconvert", "-f", "caff", "-d", "opus", str(wav_file(tmp_path / "v.wav")), str(caf)],
                       check=True, capture_output=True)
        v = av.audio_view(caf, header="attachment:9 · audio · Audio Message.caf · 6 KB", ref="attachment:9")
        assert v.kind == "audio" and '"see you at seven"' in v.text and v.text.startswith("attachment:9 · audio")
        assert scribe[0][1:] == ("m.m4a", "audio/mp4")
        assert hooks.describe_ref("attachment:9") == 'voice message: "see you at seven"'

    def test_heard_once(self, scribe, tmp_path):
        p = wav_file(tmp_path / "a.wav")
        av.audio_view(p, header="h", ref=None)
        av.audio_view(p, header="h", ref=None)
        assert len(scribe) == 1

    def test_no_scribe_key(self, monkeypatch, tmp_path):
        monkeypatch.setattr(av, "_scribe_configured", lambda: False)
        v = av.audio_view(wav_file(tmp_path / "a.wav"), header="h", ref=None)
        assert "can't listen" in v.text and "ElevenLabs" in v.text

    def test_a_failing_transcription_names_its_class(self, monkeypatch, tmp_path):
        def boom(data, **kw):
            raise ConnectionError("https://api.elevenlabs.io?xi-api-key=sk_live_123")

        monkeypatch.setattr(av, "_transcribe_bytes", boom)
        monkeypatch.setattr(av, "_scribe_configured", lambda: True)
        av._transcripts.clear()
        v = av.audio_view(wav_file(tmp_path / "a.wav"), header="h", ref=None)
        assert "ConnectionError" in v.text and "sk_live" not in v.text


class TestVideo:
    def test_frames_and_words_through_the_helper(self, scribe, monkeypatch, tmp_path):
        monkeypatch.setattr(mediatool, "available", lambda: True)
        monkeypatch.setattr(mediatool, "probe", lambda p: {"kind": "video", "duration": 12.4, "has_audio": True})
        monkeypatch.setattr(mediatool, "frames", lambda p, count, max_edge: [JPEG] * count)
        monkeypatch.setattr(av, "_extract_audio", lambda src: b"m4a-bytes")
        v = av.video_view(tmp_path / "clip.mov", header="attachment:3 · video · clip.mov · 4.0 MB", max_images=6,
                          ref="attachment:3")
        assert v.kind == "video" and len(v.images) == 6
        assert "12 s video" in v.text and '"see you at seven"' in v.text

    def test_a_silent_video(self, scribe, monkeypatch, tmp_path):
        monkeypatch.setattr(mediatool, "available", lambda: True)
        monkeypatch.setattr(mediatool, "probe", lambda p: {"kind": "video", "duration": 3, "has_audio": False})
        monkeypatch.setattr(mediatool, "frames", lambda p, count, max_edge: [JPEG] * count)
        v = av.video_view(tmp_path / "clip.mov", header="h", max_images=2, ref=None)
        assert len(v.images) == 2 and "no sound" in v.text and scribe == []

    def test_without_the_helper_a_poster_frame(self, scribe, monkeypatch, tmp_path):
        monkeypatch.setattr(mediatool, "available", lambda: False)
        from services.media import convert
        monkeypatch.setattr(convert, "qlmanage_poster", lambda p, size=1024: JPEG)
        monkeypatch.setattr(av, "_extract_audio", lambda src: None)
        v = av.video_view(tmp_path / "clip.mov", header="h", max_images=6, ref=None)
        assert v.images == [JPEG] and "poster frame" in v.text

    def test_the_real_helper_on_a_real_movie(self, built_tool, scribe, monkeypatch):
        tool, selftest = built_tool
        monkeypatch.setenv("EDWARD_MEDIATOOL", str(tool))
        v = av.video_view(selftest / "selftest.mov", header="h", max_images=6, ref=None)
        assert len(v.images) == 6 and "no sound" in v.text


@pytest.mark.asyncio
class TestThroughTheViewer:
    async def test_an_audio_attachment(self, scribe, monkeypatch, tmp_path):
        if not HAS_AFCONVERT:
            pytest.skip("needs afconvert")
        p = wav_file(tmp_path / "memo.wav")
        from services.heartbeat import listener_imessage
        monkeypatch.setattr(listener_imessage, "find_attachment",
                            lambda rowid: {"rowid": rowid, "mime_type": "audio/wav", "path": str(p), "name": "memo.wav"})
        from services import media
        v = await media.view("attachment:21")
        assert isinstance(v, MediaView) and v.kind == "audio" and "see you at seven" in v.text
