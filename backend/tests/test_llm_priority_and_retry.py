"""User-facing LLM calls run at a priority the gate never defers, and a
retry never changes the model behind Ellis's back.

From the log (2026-09-10/11): "Could not read image 'IMG_0584.png': Codex
rate gate: background LLM call deferred" — Edward looking at a photo Ellis
just asked about was a *background* call, so the pacer refused it. Same for
the greeting ("Greeting stream failed: ... deferred") and the chat error
explainer. And a failed scheduled event retried on a Claude id that
build_chat_llm silently rewrote to astra — the one model that was out of
weekly quota — regardless of settings.model.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit


class _FakeLLM:
    def __init__(self, text="A red square."):
        self.text = text

    async def ainvoke(self, msgs):
        class R:
            content = self.text
        return R()

    async def astream(self, msgs):
        class C:
            content = self.text
        yield C()


class TestVisionPriority:
    async def test_describe_image_is_background_by_default_and_asks_for_a_seeing_model(self, tmp_path, monkeypatch):
        from services import attachment_vision as av
        calls = []
        monkeypatch.setattr(av, "build_background_llm", lambda **kw: calls.append(kw) or _FakeLLM())
        monkeypatch.setattr(av, "prepare_image_for_model", lambda p, **k: {"data": "AAAA", "filename": "a.jpg", "mime_type": "image/jpeg", "size": 3})
        av._description_cache.clear()
        await av.describe_image(tmp_path / "a.jpg")
        assert calls[0]["priority"] == "background" and calls[0]["vision"] is True

    async def test_describe_image_can_run_interactively(self, tmp_path, monkeypatch):
        from services import attachment_vision as av
        calls = []
        monkeypatch.setattr(av, "build_background_llm", lambda **kw: calls.append(kw) or _FakeLLM())
        monkeypatch.setattr(av, "prepare_image_for_model", lambda p, **k: {"data": "AAAA", "filename": "b.jpg", "mime_type": "image/jpeg", "size": 3})
        av._description_cache.clear()
        await av.describe_image(tmp_path / "b.jpg", priority="interactive")
        assert calls[0]["priority"] == "interactive"

    async def test_the_attachment_tool_looks_interactively(self, monkeypatch, tmp_path):
        from services.graph import tools
        from services import attachment_vision as av
        from services.heartbeat import listener_imessage as listener

        p = tmp_path / "p.jpeg"; p.write_bytes(b"\xff\xd8\xff")
        monkeypatch.setattr(listener, "find_attachment", lambda rowid: {"rowid": rowid, "mime_type": "image/jpeg", "path": str(p), "name": "p.jpeg"})
        seen = []

        async def fake_desc(path, **kw):
            seen.append(kw)
            return "Biscuit asleep."

        async def fake_store(path, **kw):
            return None

        monkeypatch.setattr(av, "describe_image", fake_desc)
        monkeypatch.setattr(av, "store_attachment_copy", fake_store)
        out = await tools.get_imessage_attachment.ainvoke({"attachment_rowid": 7})
        assert "Biscuit asleep" in out
        assert seen == [{"priority": "interactive"}]

    def test_look_at_screen_describes_interactively(self):
        from services import browser_hands
        body = inspect.getsource(browser_hands.look)
        assert 'describe_image(path, priority="interactive")' in body


class TestUserFacingPriority:
    async def test_greeting_runs_at_triage_priority(self, monkeypatch):
        from routers import chat
        from services import llm_utils
        calls = []

        async def ctx():
            return ""

        monkeypatch.setattr(chat, "_build_greeting_context", ctx)
        monkeypatch.setattr(llm_utils, "build_background_llm", lambda **kw: calls.append(kw) or _FakeLLM("Hey."))
        pieces = [p async for p in chat._astream_fresh_greeting()]
        assert pieces and calls[0]["priority"] == "triage"

    def test_error_explainer_runs_at_triage_priority(self):
        from routers import chat
        body = inspect.getsource(chat._format_chat_error_explanation)
        assert 'priority="triage"' in body


class TestSchedulerRetry:
    def test_retry_keeps_the_configured_model(self):
        src = (Path(__file__).resolve().parents[1] / "services/scheduler_service.py").read_text()
        body = src[src.index("async def _execute_event("):]
        assert "claude-" not in body and '"anthropic"' not in body, (
            "a retry must not switch models — the Claude id was rewritten to astra behind settings.model"
        )
        assert body.count("model=settings.model,") >= 2
