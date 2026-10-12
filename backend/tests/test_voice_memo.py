"""Voice memos in ordinary chat: MIME handling and message composition."""

from services.voice.memo import (
    AUDIO_MIME_TYPES,
    compose_memo_message,
    is_audio_mime,
    memo_marker,
    memo_title,
    normalize_audio_mime,
)


def test_common_browser_recorder_types_are_audio():
    for m in ("audio/webm", "audio/mp4", "audio/mpeg", "audio/wav", "audio/ogg"):
        assert m in AUDIO_MIME_TYPES
        assert is_audio_mime(m)
    assert not is_audio_mime("image/png")


def test_codec_suffix_and_vendor_aliases_normalize():
    assert normalize_audio_mime("audio/webm;codecs=opus", "memo.webm") == "audio/webm"
    assert normalize_audio_mime("audio/x-m4a", "memo.m4a") == "audio/mp4"
    assert normalize_audio_mime("", "memo.m4a") == "audio/mp4"
    assert normalize_audio_mime("application/octet-stream", "memo.wav") == "audio/wav"
    assert normalize_audio_mime("application/octet-stream", "notes.txt") == "application/octet-stream"


def test_marker_names_file_and_id():
    assert memo_marker("memo.m4a", "abc-123") == "[Voice memo: memo.m4a | file_id: abc-123]"


def test_compose_uses_transcript_when_nothing_typed():
    assert compose_memo_message("", "Pick up the dry cleaning.") == "Pick up the dry cleaning."
    assert compose_memo_message("  ", "Pick up the dry cleaning.") == "Pick up the dry cleaning."


def test_compose_keeps_typed_text_first():
    out = compose_memo_message("Reminder for tomorrow:", "Pick up the dry cleaning.")
    assert out.startswith("Reminder for tomorrow:")
    assert out.endswith("Pick up the dry cleaning.")


def test_compose_with_empty_transcript_flags_it():
    out = compose_memo_message("", "")
    assert "voice memo" in out.lower()
    assert "couldn't" in out.lower() or "no speech" in out.lower()


def test_title_is_short_and_trimmed():
    t = memo_title("This is a fairly long transcript that keeps going well past fifty characters, yes.")
    assert len(t) <= 53
    assert t.endswith("...")
    assert memo_title("") == "Voice memo"
