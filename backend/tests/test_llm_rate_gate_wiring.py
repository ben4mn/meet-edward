"""The meter has to reach the gate, and the gate's state has to reach disk.

* ``main.py`` restores the persisted meter before any background loop runs,
  installs the persistence hook, and gives the gate a way to refresh a stale
  meter from ``wham/usage``.
* ``codex_chat_model`` hands every response's ``x-codex-*`` headers to the
  gate and reports a 200 as success.
"""

from __future__ import annotations

import inspect

import pytest

pytestmark = pytest.mark.unit


def test_startup_restores_the_gate_and_wires_the_hooks():
    src = inspect.getsource(__import__("main"))
    assert "restore_gate_from_db" in src
    assert "on_state_change" in src, "state changes must be persisted"
    assert "refresh_meter" in src, "a stale meter must be refreshable from wham/usage"


def test_no_hook_is_harmless():
    from services import llm_rate_gate as m

    gate = m.LLMRateGate()
    gate.note_429('{"error": {"resets_in_seconds": 5}}')  # must not raise


def test_the_codex_stream_reports_headers_and_success_to_the_gate():
    from services import codex_chat_model

    src = inspect.getsource(codex_chat_model)
    assert "note_success" in src
    assert "note_usage_headers" in src, (
        "Every Codex response carries the usage meter in x-codex-* headers; "
        "the streaming path is the one place that sees them."
    )


def test_the_old_budget_pacer_is_gone():
    """A guessed budget must not come back beside the real meter."""
    from services import llm_rate_gate as m

    for name in ("DEFAULT_BUDGET", "maybe_grow_budget", "on_budget_change"):
        assert not hasattr(m.LLMRateGate, name), name
