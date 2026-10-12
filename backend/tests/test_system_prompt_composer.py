"""
T5: trim the system prompt by skipping context blocks that aren't
relevant to the current turn.

Today every turn pays for STATE_DOCUMENT_DIRECTIVE (~700 chars) even
when the user has zero state documents — a directive telling the LLM
"maintain structured state docs" with no context to anchor it. Same
shape applies to other large blocks.

The seam: a pure compose_system_prompt(...) function. This test pins
the gate behavior so future tweaks have something to point at.
"""

from __future__ import annotations


def test_compose_drops_state_directive_when_no_state_docs():
    from services.graph.streaming import compose_system_prompt, STATE_DOCUMENT_DIRECTIVE

    out = compose_system_prompt(
        base="You are Edward.",
        memory_context="",
        briefing_context="",
        session_briefing_context="",
        time_context="\nCurrent date: 2026-05-05",
        state_documents=[],
    )
    assert STATE_DOCUMENT_DIRECTIVE not in out, (
        "with no state docs, the state-doc directive is bloat and should be dropped"
    )


def test_compose_includes_state_directive_when_state_docs_exist():
    from services.graph.streaming import compose_system_prompt, STATE_DOCUMENT_DIRECTIVE

    out = compose_system_prompt(
        base="You are Edward.",
        memory_context="",
        briefing_context="",
        session_briefing_context="",
        time_context="\nCurrent date: 2026-05-05",
        state_documents=[{"id": "1", "title": "Project: foo"}],
    )
    assert STATE_DOCUMENT_DIRECTIVE in out, (
        "user has state docs already — the directive helps the LLM update them"
    )


def test_compose_drops_briefing_when_empty():
    from services.graph.streaming import compose_system_prompt

    out = compose_system_prompt(
        base="You are Edward.",
        memory_context="",
        briefing_context="",
        session_briefing_context="",
        time_context="",
        state_documents=[],
    )
    # No "Recent Awareness" / briefing markers leaked into the prompt
    assert "Recent Awareness" not in out
    assert "While you were away" not in out


def test_compose_includes_base_and_time_always():
    from services.graph.streaming import compose_system_prompt

    out = compose_system_prompt(
        base="You are Edward.",
        memory_context="",
        briefing_context="",
        session_briefing_context="",
        time_context="\nCurrent date: 2026-05-05",
        state_documents=[],
    )
    assert "You are Edward." in out
    assert "Current date: 2026-05-05" in out


def test_compose_has_no_engine_blocks():
    """Phase 8: the policy, preference and relationship blocks were engine
    output; the composer no longer takes them."""
    import inspect

    from services.graph.streaming import compose_system_prompt

    params = set(inspect.signature(compose_system_prompt).parameters)
    assert not params & {"policy_context", "preference_context", "relationship_context"}, params
    out = compose_system_prompt(base="You are Edward.", state_documents=[])
    assert "Recent Awareness" not in out and "Preference Policies" not in out


def test_compose_includes_credential_directive_with_use_then_dont_persist_policy():
    """N6 (revised): Ellis is the sole user — Edward should USE credentials
    he's given for the task, just not persist them. Pin both halves of
    that policy so a future refactor can't accidentally swing it back
    to refusal-by-default."""
    from services.graph.streaming import compose_system_prompt

    out = compose_system_prompt(
        base="You are Edward.",
        memory_context="",
        briefing_context="",
        session_briefing_context="",
        time_context="",
        state_documents=[],
    ).lower()

    # Must mention credentials, USE policy, and persistence ban.
    assert "credentials" in out
    assert "passwords" in out
    # USE policy: explicit "use" / "do the task" language somewhere
    assert "use them" in out, "directive must instruct Edward to USE creds, not refuse"
    # Persistence ban
    assert "persistent" in out or "persist" in out or "memory" in out
    # No flat refusal language — was the bug we just fixed
    assert "never ask for, accept, store, or use" not in out, (
        "old refusal-by-default phrasing must not return"
    )
