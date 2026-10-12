"""Background LLM traffic must not ride a retiring model.

Codex's catalog marks gpt-5.4 as retiring (upgrade pointer → gpt-5.6-terra).
``build_background_llm`` hardcoded it for memory extraction, triage L2,
reflection, consolidation and search tags. Decision (2026-09-04): background
work moves to Terra — the like-for-like successor, so triage quality holds —
while chat runs on Astra. Luna stays the cheaper lever via the env override.
"""

import importlib


def _capture_build(monkeypatch, module):
    calls = []

    def _fake(model, temperature, max_tokens, priority="interactive"):
        calls.append({"model": model, "priority": priority})
        return object()

    monkeypatch.setattr(module, "_build_gpt_llm", _fake)
    return calls


def test_background_llm_uses_terra_by_default(monkeypatch):
    monkeypatch.delenv("EDWARD_BACKGROUND_MODEL", raising=False)
    import services.llm_utils as llm_utils
    importlib.reload(llm_utils)
    calls = _capture_build(monkeypatch, llm_utils)
    llm_utils.build_background_llm()
    assert calls[0]["model"] == "gpt-5.6-terra"
    assert calls[0]["priority"] == "background"


def test_background_model_env_override(monkeypatch):
    monkeypatch.setenv("EDWARD_BACKGROUND_MODEL", "gpt-5.6-luna")
    import services.llm_utils as llm_utils
    importlib.reload(llm_utils)
    calls = _capture_build(monkeypatch, llm_utils)
    llm_utils.build_background_llm()
    assert calls[0]["model"] == "gpt-5.6-luna"
    monkeypatch.delenv("EDWARD_BACKGROUND_MODEL")
    importlib.reload(llm_utils)


def test_chat_fallback_for_stale_claude_model_is_astra(monkeypatch):
    """Old checkpoints carry claude-* model strings; they must land on the
    current chat default, not the retiring 5.4."""
    import services.llm_utils as llm_utils
    calls = _capture_build(monkeypatch, llm_utils)
    llm_utils.build_chat_llm(model="claude-sonnet-4-5-20250929")
    # DEFAULT_CHAT_MODEL is the Claude chat route now; Codex's chat default
    # is CODEX_CHAT_MODEL (conftest runs the suite on the Codex fallback).
    assert calls[0]["model"] == llm_utils.CODEX_CHAT_MODEL == "gpt-6-astra"


def test_no_retiring_model_literal_in_runtime_defaults():
    """gpt-5.4 may only survive where it is explicitly labelled as retiring
    (the settings picker) or in historical comments — never as a default."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    offenders = []
    for rel in (
        "services/llm_utils.py",
        "services/orchestrator_service.py",
        "services/orchestrator_models.py",
        "services/database.py",
        "services/codex_chat_model.py",
    ):
        for n, line in enumerate((root / rel).read_text().splitlines(), 1):
            if "gpt-5.4" in line and ("default" in line or "=" in line) and not line.strip().startswith("#"):
                offenders.append(f"{rel}:{n}: {line.strip()}")
    assert not offenders, "\n".join(offenders)


def test_chat_fallback_for_retired_gpt54_is_astra(monkeypatch):
    """Codex now rejects gpt-5.4 outright (HTTP 400, 2026-09-04). Any caller
    still naming it — stale checkpoints above all — must land on Astra."""
    import services.llm_utils as llm_utils
    calls = _capture_build(monkeypatch, llm_utils)
    llm_utils.build_chat_llm(model="gpt-5.4")
    # DEFAULT_CHAT_MODEL is the Claude chat route now; Codex's chat default
    # is CODEX_CHAT_MODEL (conftest runs the suite on the Codex fallback).
    assert calls[0]["model"] == llm_utils.CODEX_CHAT_MODEL == "gpt-6-astra"


def test_background_llm_accepts_model_override(monkeypatch):
    """Entity extraction rides Luna (Phase 4, 2026-09-05); the default stays Terra."""
    monkeypatch.delenv("EDWARD_BACKGROUND_MODEL", raising=False)
    import services.llm_utils as llm_utils
    importlib.reload(llm_utils)
    calls = _capture_build(monkeypatch, llm_utils)
    llm_utils.build_background_llm(model="gpt-5.6-luna")
    llm_utils.build_background_llm()
    assert [c["model"] for c in calls] == ["gpt-5.6-luna", "gpt-5.6-terra"]
