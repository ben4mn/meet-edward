"""Voice memos through the ordinary chat path.

A memo is an audio file in the multipart upload. The backend stores it,
transcribes it with Scribe, and the transcript becomes the message text.
The audio survives as an attachment on the user bubble.
"""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from langchain_core.messages import HumanMessage


# --------------------------------------------------------------------------
# storage allowlist
# --------------------------------------------------------------------------

def test_file_storage_accepts_recorder_audio_types():
    from services.file_storage_service import ALLOWED_MIME_TYPES

    for m in ("audio/webm", "audio/mp4", "audio/wav", "audio/mpeg", "audio/ogg"):
        assert m in ALLOWED_MIME_TYPES


# --------------------------------------------------------------------------
# request parsing
# --------------------------------------------------------------------------

@pytest.fixture
def parse_app(monkeypatch):
    from routers import chat as chat_router

    stored = []

    async def fake_store_file(file_data, filename, mime_type, **kw):
        stored.append(dict(filename=filename, mime_type=mime_type, size=len(file_data), **kw))
        return SimpleNamespace(id=f"file-{len(stored)}")

    class FakeVoice:
        def __init__(self):
            self.calls = []

        async def transcribe(self, audio_bytes, filename="", mime_type=None, language="en"):
            self.calls.append((filename, mime_type, len(audio_bytes)))
            return SimpleNamespace(text="Pick up the dry cleaning.", language="eng")

    voice = FakeVoice()
    import services.file_storage_service as fss
    monkeypatch.setattr(fss, "store_file", fake_store_file)
    monkeypatch.setattr(chat_router, "get_voice_service", lambda: voice)

    app = FastAPI()

    @app.post("/parse")
    async def parse(request: Request):
        message, conversation_id, attachments = await chat_router._parse_chat_request(request)
        return {"message": message, "conversation_id": conversation_id, "attachments": attachments}

    return SimpleNamespace(client=TestClient(app), stored=stored, voice=voice)


def test_audio_upload_is_transcribed_into_the_message(parse_app):
    r = parse_app.client.post(
        "/parse",
        data={"message": ""},
        files=[("files", ("memo.m4a", b"\x00\x01\x02", "audio/x-m4a"))],
    )
    body = r.json()
    assert body["message"] == "Pick up the dry cleaning."
    att = body["attachments"][0]
    assert att["kind"] == "voice_memo"
    assert att["mime_type"] == "audio/mp4"
    assert att["file_id"] == "file-1"
    assert att["transcript"] == "Pick up the dry cleaning."
    assert "data" not in att or not att["data"]  # audio bytes never go to the LLM
    assert parse_app.stored[0]["mime_type"] == "audio/mp4"
    assert parse_app.voice.calls[0][1] == "audio/mp4"


def test_typed_text_plus_memo_keeps_both(parse_app):
    r = parse_app.client.post(
        "/parse",
        data={"message": "For tomorrow:"},
        files=[("files", ("memo.webm", b"\x00", "audio/webm;codecs=opus"))],
    )
    body = r.json()
    assert body["message"].startswith("For tomorrow:")
    assert body["message"].endswith("Pick up the dry cleaning.")
    assert body["attachments"][0]["mime_type"] == "audio/webm"


def test_transcription_failure_does_not_lose_the_memo(parse_app):
    async def boom(*a, **k):
        raise RuntimeError("scribe down")

    parse_app.voice.transcribe = boom
    r = parse_app.client.post(
        "/parse",
        data={"message": ""},
        files=[("files", ("memo.wav", b"\x00", "audio/wav"))],
    )
    body = r.json()
    assert "voice memo" in body["message"].lower()
    assert body["attachments"][0]["kind"] == "voice_memo"
    assert body["attachments"][0]["transcript"] == ""


def test_images_still_parse_the_old_way(parse_app):
    r = parse_app.client.post(
        "/parse",
        data={"message": "look"},
        files=[("files", ("pic.png", b"\x89PNG", "image/png"))],
    )
    body = r.json()
    assert body["message"] == "look"
    assert body["attachments"][0].get("kind") is None
    assert body["attachments"][0]["data"]
    assert parse_app.voice.calls == []


# --------------------------------------------------------------------------
# LLM-facing message and the round trip back to the UI
# --------------------------------------------------------------------------

def _memo_attachment():
    return {
        "kind": "voice_memo",
        "filename": "memo.m4a",
        "mime_type": "audio/mp4",
        "size": 3,
        "file_id": "file-9",
        "transcript": "Pick up the dry cleaning.",
    }


def test_human_message_marks_the_memo_without_audio_bytes():
    from services.graph.streaming import _build_human_message

    msg = _build_human_message("Pick up the dry cleaning.", [_memo_attachment()])
    assert isinstance(msg, HumanMessage)
    assert isinstance(msg.content, list)
    assert msg.content[0] == {"type": "text", "text": "Pick up the dry cleaning."}
    marker = msg.content[1]
    assert marker["type"] == "text"
    assert "Voice memo" in marker["text"] and "file-9" in marker["text"]
    meta = msg.additional_kwargs["attachments"][0]
    assert meta["block_index"] == 1
    assert meta["mime_type"] == "audio/mp4"
    assert meta["kind"] == "voice_memo"


def test_serialized_history_shows_transcript_and_audio_attachment():
    from services.graph.streaming import _build_human_message
    from routers.conversations import serialize_checkpoint_message

    msg = _build_human_message("Pick up the dry cleaning.", [_memo_attachment()])
    out = serialize_checkpoint_message(msg)
    assert out is not None
    assert out.role == "user"
    assert out.content == "Pick up the dry cleaning."
    assert out.attachments and out.attachments[0].mime_type == "audio/mp4"
    assert out.attachments[0].file_id == "file-9"
    assert out.attachments[0].filename == "memo.m4a"


def test_serializer_still_handles_plain_and_image_messages():
    from services.graph.streaming import _build_human_message
    from routers.conversations import serialize_checkpoint_message

    plain = serialize_checkpoint_message(HumanMessage(content="hi"))
    assert plain.content == "hi" and plain.attachments is None

    img = _build_human_message("look", [{"filename": "p.png", "mime_type": "image/png", "data": "AAAA", "file_id": "f1"}])
    out = serialize_checkpoint_message(img)
    assert out.content == "look[Uploaded image: p.png | file_id: f1]"
    assert out.attachments[0].mime_type == "image/png"

    assert serialize_checkpoint_message(SimpleNamespace(type="tool", content="x")) is None


# --------------------------------------------------------------------------
# HTTP transcribe endpoint (insert-as-text path)
# --------------------------------------------------------------------------

def test_transcribe_endpoint_returns_text_and_language(monkeypatch):
    from routers import voice as voice_router

    class FakeVoice:
        async def transcribe(self, audio_bytes, filename="", mime_type=None, language="en"):
            assert filename == "clip.webm"
            return SimpleNamespace(text="hello there", language="eng")

    async def _enabled(name):
        return True

    monkeypatch.setattr(voice_router, "get_voice_service", lambda: FakeVoice())
    monkeypatch.setattr(voice_router, "is_skill_enabled", _enabled)
    app = FastAPI()
    app.include_router(voice_router.router, prefix="/api")
    r = TestClient(app).post("/api/voice/transcribe", files={"audio": ("clip.webm", b"\x00", "audio/webm")})
    assert r.status_code == 200
    assert r.json() == {"text": "hello there", "language": "eng"}


# --------------------------------------------------------------------------
# streaming chat: the UI learns the transcript before Edward answers
# --------------------------------------------------------------------------

def test_memo_chat_stream_leads_with_user_message(monkeypatch):
    from routers import chat as chat_router

    created = []

    async def fake_store_file(file_data, filename, mime_type, **kw):
        return SimpleNamespace(id="file-1")

    class FakeVoice:
        async def transcribe(self, audio_bytes, filename="", mime_type=None, language="en"):
            return SimpleNamespace(text="Add oat milk to the list.", language="eng")

    async def _settings():
        return SimpleNamespace(system_prompt="x", model="m", temperature=0.5, provider="openai", debug_streaming=False)

    async def _graph():
        return object()

    async def _create(cid, title, **kw):
        created.append((cid, title))

    async def _exists(cid):
        return False

    async def _artifact(**kw):
        return kw["message"]

    async def fake_wrap(**kw):
        yield {"type": "content", "content": "Done."}
        yield {"type": "done"}

    import services.file_storage_service as fss
    monkeypatch.setattr(fss, "store_file", fake_store_file)
    monkeypatch.setattr(chat_router, "get_voice_service", lambda: FakeVoice())
    monkeypatch.setattr(chat_router, "get_settings", _settings)
    monkeypatch.setattr(chat_router, "get_graph", _graph)
    monkeypatch.setattr(chat_router, "create_conversation", _create)
    monkeypatch.setattr(chat_router, "conversation_exists", _exists)
    monkeypatch.setattr(chat_router, "_message_with_artifact_context", _artifact)
    monkeypatch.setattr(chat_router, "_wrap_chat_stream", fake_wrap)

    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api")
    r = TestClient(app).post(
        "/api/chat",
        data={"message": ""},
        files=[("files", ("memo.m4a", b"\x00\x01", "audio/mp4"))],
    )
    assert r.status_code == 200
    import json as _json
    events = [_json.loads(line[6:]) for line in r.text.splitlines() if line.startswith("data: ")]
    assert events[0]["type"] == "user_message"
    assert events[0]["content"] == "Add oat milk to the list."
    assert events[0]["attachments"][0]["file_id"] == "file-1"
    assert events[0]["attachments"][0]["mime_type"] == "audio/mp4"
    assert [e["type"] for e in events[1:]] == ["content", "done"]
    assert created[0][1] == "Add oat milk to the list."


def test_chat_with_unknown_conversation_id_creates_the_row(monkeypatch):
    # Voice mode hands the browser a conversation id before any row exists
    # (the row is created on the first spoken turn). If the user says
    # nothing, exits, and types instead, the id reaches /api/chat with no
    # row behind it — that message must not vanish from the sidebar.
    from routers import chat as chat_router

    created = []

    async def _settings():
        return SimpleNamespace(system_prompt="x", model="m", temperature=0.5, provider="openai", debug_streaming=False)

    async def _graph():
        return object()

    async def _create(cid, title, **kw):
        created.append((cid, title))

    async def _exists(cid):
        return False

    async def _artifact(**kw):
        return kw["message"]

    async def fake_wrap(**kw):
        yield {"type": "done"}

    monkeypatch.setattr(chat_router, "get_settings", _settings)
    monkeypatch.setattr(chat_router, "get_graph", _graph)
    monkeypatch.setattr(chat_router, "create_conversation", _create)
    monkeypatch.setattr(chat_router, "conversation_exists", _exists)
    monkeypatch.setattr(chat_router, "_message_with_artifact_context", _artifact)
    monkeypatch.setattr(chat_router, "_wrap_chat_stream", fake_wrap)

    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api")
    r = TestClient(app).post("/api/chat", json={"message": "hello there", "conversation_id": "ghost-id"})
    assert r.status_code == 200
    assert created == [("ghost-id", "hello there")]
