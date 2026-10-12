"""
Orchestrator model aliases: no claude-* strings (Track G3).

The shorthand map in ``orchestrator_service.MODEL_SHORTHAND`` carried
historical ``"haiku" → "claude-haiku-4-5-20251001"`` and similar
``sonnet`` / ``opus`` entries. Edward's LLM policy is GPT/OpenAI-only
(Anthropic is backup, disabled). These aliases would silently route
workers to the wrong provider.

Replace with GPT-only aliases (current tiers live in
``test_orchestrator_model_tiers.py``). Also
guard against ``claude-*`` string literals in the orchestrator module.
"""

from __future__ import annotations

from pathlib import Path


def test_model_shorthand_has_no_claude_entries():
    from services.orchestrator_service import MODEL_SHORTHAND

    for key, value in MODEL_SHORTHAND.items():
        assert "claude" not in value.lower(), (
            f"MODEL_SHORTHAND['{key}'] points at a Claude model ({value!r}); "
            "Edward's policy is GPT-only."
        )


def test_orchestrator_module_has_no_claude_string_literals():
    path = Path(__file__).resolve().parent.parent / "services" / "orchestrator_service.py"
    source = path.read_text()
    assert "claude-haiku" not in source, (
        "orchestrator_service.py must not contain 'claude-haiku' — route via gpt-5.4."
    )
    assert "claude-sonnet" not in source, (
        "orchestrator_service.py must not contain 'claude-sonnet' — route via gpt-5.4."
    )
    assert "claude-opus" not in source, (
        "orchestrator_service.py must not contain 'claude-opus' — route via gpt-5.4."
    )


def test_fast_alias_resolves_to_gpt():
    from services.orchestrator_service import _resolve_model

    assert _resolve_model("fast", default="gpt-5.6-terra").startswith("gpt-")


def test_unknown_alias_returns_model_unchanged():
    """The resolver passes through unknown strings so callers can still use
    explicit model IDs when they want."""
    from services.orchestrator_service import _resolve_model

    assert _resolve_model("gpt-5.4", default="gpt-5.4") == "gpt-5.4"
