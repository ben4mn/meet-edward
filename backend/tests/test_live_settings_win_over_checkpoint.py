"""Live settings must win over what a thread checkpointed on an earlier turn.

Incident (2026-09-05): settings.model had been gpt-6-astra for weeks, but
both chat paths did ``model = existing.values.get("model", model)`` — the
checkpoint's stale ``gpt-5.4`` beat the live value on every long-lived
thread (724 of 1806, including every heartbeat contact thread and
``edward-surfaces``). When Codex retired gpt-5.4 those threads all started
failing with HTTP 400, so Ellis's @edward ping went unanswered and the
expression pulse fell back to the composed widget. Same bug class hits the
system prompt: prompt edits never reached an existing thread either.
"""

from __future__ import annotations

from pathlib import Path


def test_resolve_turn_settings_prefers_live_values():
    from services.graph.streaming import _resolve_turn_settings

    checkpointed = {"system_prompt": "old prompt", "model": "gpt-5.4", "temperature": 0.1}
    system_prompt, model, temperature = _resolve_turn_settings(
        checkpointed, system_prompt="new prompt", model="gpt-6-astra", temperature=0.7
    )
    assert (system_prompt, model, temperature) == ("new prompt", "gpt-6-astra", 0.7)


def test_resolve_turn_settings_handles_fresh_thread():
    from services.graph.streaming import _resolve_turn_settings

    assert _resolve_turn_settings({}, "p", "gpt-6-astra", 0.5) == ("p", "gpt-6-astra", 0.5)
    assert _resolve_turn_settings(None, "p", "gpt-6-astra", 0.5) == ("p", "gpt-6-astra", 0.5)


def test_streaming_no_longer_reads_model_from_checkpoint():
    """Both call sites (stream + chat_with_memory) used the same override."""
    text = (Path(__file__).resolve().parents[1] / "services/graph/streaming.py").read_text()
    assert 'existing.values.get("model"' not in text
    assert 'existing.values.get("system_prompt"' not in text
    assert 'existing.values.get("temperature"' not in text
