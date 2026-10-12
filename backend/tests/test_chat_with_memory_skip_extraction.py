"""The expression pulse must never extract memories.

The pulse turn in ``edward-surfaces`` consumes a signal pack (recent
messages, presence, the pet board, calendar), not a conversation. Extracting
from it produced rows like "no medications were scheduled at the time
shown", which then raised contradiction flags against Biscuit's real steroid
schedule (2026-09-05). 22 rows in a week came from that thread.

Retrieval is untouched — the pulse still benefits from memory.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def test_post_turn_extraction_enabled_helper():
    from services.graph.streaming import post_turn_extraction_enabled

    assert post_turn_extraction_enabled(skip_memory=False, skip_extraction=False) is True
    assert post_turn_extraction_enabled(skip_memory=False, skip_extraction=True) is False
    # skip_memory (workers) gates retrieval only; workers keep extracting today
    # and changing that is a separate decision, so the new flag is the only gate.
    assert post_turn_extraction_enabled(skip_memory=True, skip_extraction=False) is True


def test_chat_with_memory_guards_extraction_with_the_helper():
    """The extraction block in chat_with_memory sits behind the helper, and
    the flag is part of the signature (not a hidden module global)."""
    src = (Path(__file__).resolve().parents[1] / "services/graph/streaming.py").read_text()
    sig_start = src.index("async def chat_with_memory(")
    sig = src[sig_start: src.index(") -> str:", sig_start)]
    assert "skip_extraction: bool = False" in sig
    block = src.index("# ===== MEMORY EXTRACTION =====", sig_start)
    guard = src[block: block + 400]
    assert "post_turn_extraction_enabled(" in guard


class _S:
    model = "gpt-6-astra"
    temperature = 0.4
    system_prompt = "be edward"
    provider = "openai"


async def test_pulse_turn_never_extracts(monkeypatch):
    import services.expression_service as es

    captured = {}

    async def fake_chat(**kwargs):
        captured.update(kwargs)
        return "left both surfaces alone"

    async def _const_signals(**kw):
        from services.expression_service import PulseSignals
        return PulseSignals(now=es.datetime.now(es.timezone.utc)) if hasattr(es, "timezone") else None

    async def _none(*a, **k):
        return None

    async def _settings():
        return _S()

    async def _graph():
        return object()

    # Since Phase 10 the pulse's turn is a surfaces wake: the chat call,
    # settings and graph live in mind_service.
    import services.mind_service as ms
    monkeypatch.setattr(ms, "chat_with_memory", fake_chat)
    monkeypatch.setattr(ms, "_settings", _settings)
    monkeypatch.setattr(ms, "_graph", _graph)
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(es, "_ensure_conversation", _none)
    # Reuse the same signal stub the existing pulse tests use.
    from tests.test_expression_service import _signals  # noqa: WPS433
    async def _sig(**kw):
        return _signals()
    monkeypatch.setattr(es, "collect_signals", _sig)

    await es.run_expression_pulse("test")

    assert captured.get("skip_extraction") is True
