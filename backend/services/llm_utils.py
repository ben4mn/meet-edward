"""
Shared LLM helpers for background services.

Provides a single place to configure the cheap/fast model used by
memory extraction, triage, reflection, deep retrieval, consolidation,
and search tag generation.
"""

from langchain_core.messages import SystemMessage


def build_background_llm(
    temperature: float = 0,
    max_tokens: int = 512,
):
    """Build an LLM for background tasks (memory, triage, etc.).

    Uses Claude Haiku 4.5 — the fastest/cheapest Anthropic model.
    Change the model here to update all background services at once.
    """
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model="claude-haiku-4-5-20251001",
        temperature=temperature,
        max_tokens=max_tokens,
    )


def make_background_system_message(content: str) -> SystemMessage:
    """Build a SystemMessage for background tasks."""
    return SystemMessage(content=content)
