"""Entity extraction gets a budget it can finish in (2026-09-25 audit).

``ENTITY_TIMEOUT_SECONDS`` was 30, under the call's p90 latency: 48 % of
entity extractions timed out — each one a paid Codex request whose answer
was thrown away. The call runs off the critical path (a background task
since 2026-09-05), so it can wait: 90 s, ``effort="low"`` (it lists names,
it does not need to deliberate), and each message clipped to
``ENTITY_MESSAGE_MAX_CHARS`` so one pasted document cannot make the
prompt enormous.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.unit


class _Capture:
    def __init__(self):
        self.messages = None

    async def ainvoke(self, messages):
        self.messages = messages

        class R:
            content = '[{"name": "Parker", "entity_type": "person", "aliases": []}]'
        return R()


def test_the_budget_clears_the_p90():
    from services import entity_service

    assert entity_service.ENTITY_TIMEOUT_SECONDS == 90
    assert 3000 <= entity_service.ENTITY_MESSAGE_MAX_CHARS <= 5000


async def test_the_call_asks_for_low_effort(monkeypatch):
    from services import entity_service

    built = {}
    llm = _Capture()

    def _build(**kw):
        built.update(kw)
        return llm

    monkeypatch.setattr(entity_service, "build_background_llm", _build)
    out = await entity_service.extract_entities([{"role": "user", "content": "Parker called"}])
    assert built.get("effort") == "low"
    assert out and out[0]["name"] == "Parker"


async def test_each_message_is_clipped(monkeypatch):
    from services import entity_service

    llm = _Capture()
    monkeypatch.setattr(entity_service, "build_background_llm", lambda **kw: llm)
    huge = "Rowan " + ("x" * 20_000) + " TAIL-MARKER"
    await entity_service.extract_entities([
        {"role": "user", "content": huge},
        {"role": "assistant", "content": "Noted about Parker."},
    ])
    prompt = llm.messages[-1].content
    assert "Rowan" in prompt
    assert "TAIL-MARKER" not in prompt
    assert "Noted about Parker." in prompt, "short messages are untouched"
    assert len(prompt) < len(entity_service.ENTITY_EXTRACTION_PROMPT) + entity_service.ENTITY_MESSAGE_MAX_CHARS + 500
