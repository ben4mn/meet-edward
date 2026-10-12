"""
Policy test: the `orchestrator_tasks.model` column default must not be a
Claude model string. Per the LLM policy in
`feedback_llm_policy.md`, runtime LLM calls go through `build_chat_llm` /
`build_background_llm` which route to GPT via Codex OAuth; Anthropic is
disabled backup.

A DB-level default of `claude-haiku-4-5-20251001` would silently persist
a Claude model id on every new task, defeating that routing.
"""

import pytest

from services.database import OrchestratorTaskModel


def test_orchestrator_task_model_default_is_not_claude():
    column = OrchestratorTaskModel.__table__.columns["model"]
    default = getattr(column.default, "arg", None) if column.default else None
    assert default is None or not str(default).startswith("claude-"), (
        f"orchestrator_tasks.model has a Claude default: {default!r}. "
        "Per policy, background/chat LLMs route through build_*_llm() — "
        "the DB column should default to a GPT model id or None, not a "
        "claude-* string."
    )
