"""
Settings flag ``debug_streaming`` gates extra SSE visibility:

* When ON, the retrieve_memory phase emits one ``memory_match`` event
  per retrieved memory carrying the content preview and score, so the
  debug panel can show what was pulled from pgvector and how
  confident the match was.
* When OFF, none of those events fire — the existing user-facing
  events stay untouched.

The flag also lives on the ``Settings`` pydantic model so the frontend
can render a settings toggle.
"""

from __future__ import annotations

import pytest


def test_settings_has_debug_streaming_field_defaulting_off() -> None:
    """Default OFF so we don't change behavior for existing users."""
    from models.schemas import Settings, SettingsUpdate

    s = Settings()
    assert hasattr(s, "debug_streaming"), (
        "Settings must expose a debug_streaming flag for the toggle UI"
    )
    assert s.debug_streaming is False, (
        f"debug_streaming should default to False, got {s.debug_streaming!r}"
    )

    # And SettingsUpdate must be able to flip it.
    u = SettingsUpdate(debug_streaming=True)
    assert u.debug_streaming is True


@pytest.mark.asyncio
async def test_memory_match_events_emit_when_debug_on(monkeypatch) -> None:
    """When debug_streaming is on, the retrieve_memory phase yields one
    memory_match event per retrieved memory."""
    from services.graph import streaming as s

    class _M:
        def __init__(self, mid, content, score):
            self.id = mid
            self.content = content
            self.score = score
            self.memory_type = "fact"

    fake_mems = [
        _M("m1", "Ellis loves sushi.", 0.91),
        _M("m2", "Ellis works on Edward.", 0.87),
    ]

    async def _fake_retrieve(*args, **kwargs):
        return fake_mems

    monkeypatch.setattr("services.memory_service.retrieve_memories", _fake_retrieve)

    events = []
    async for ev in s._emit_memory_matches(fake_mems, conversation_id="c1", debug_streaming=True):
        events.append(ev)

    assert len(events) == 2, f"expected 2 memory_match events, got: {events}"
    for ev, m in zip(events, fake_mems):
        assert ev["type"] == "memory_match"
        assert ev["memory_id"] == m.id
        assert m.content[:30] in (ev.get("content_preview") or "")
        assert ev["score"] == m.score


@pytest.mark.asyncio
async def test_memory_match_events_silent_when_debug_off() -> None:
    from services.graph import streaming as s

    class _M:
        def __init__(self, mid, content, score):
            self.id = mid
            self.content = content
            self.score = score
            self.memory_type = "fact"

    fake_mems = [_M("m1", "x", 0.9)]
    events = [
        ev async for ev in s._emit_memory_matches(
            fake_mems, conversation_id="c1", debug_streaming=False
        )
    ]
    assert events == [], (
        f"expected no events when debug_streaming is off, got: {events}"
    )
