"""The per-turn context is memory, documents, loops and briefing — mind
rebuild Phase 8b. The engine fan-out (twenty-six "reply drafting
context" reads, eight post-turn learners, the send-time commitment
ledgers, the preference/decision/capability prompt blocks, the chat
fact cards, the experiment block in the graph node) is unplugged.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

# Module names that must not appear in the live turn path any more.
RETIRED = (
    "workstream_tracker", "initiative_tracker", "outbound_commitment_ledger", "commitment_calibration_engine",
    "relationship_maintenance_engine", "group_coordination_tracker", "workflow_playbook_engine",
    "negotiation_intelligence_engine", "fact_consistency_graph", "dependency_graph_engine",
    "daily_execution_planner", "objective_alignment_engine", "objective_outcome_tracker",
    "micro_autonomy_executor", "priority_attention_engine", "artifact_change_impact_engine",
    "artifact_drift_engine", "artifact_authority_engine", "cadence_orchestration_engine",
    "scenario_planning_engine", "analogical_precedent_engine", "assumption_register_engine",
    "risk_register_engine", "constraint_ledger_engine", "evidence_gap_planner", "gap_resolution_engine",
    "stakeholder_routing_engine", "claim_verification_engine", "claim_grounding_engine",
    "stakeholder_position_engine", "preference_policy_engine", "preference_memory_graph",
    "decision_memory_engine", "human_capability_map", "experiment_learning_engine",
    "commitment_ledger", "commitments_ledger", "outbound_ask_tracker", "outbound_request_engine",
    "unresolved_asks_ledger", "artifact_intelligence", "relationship_fact_graph",
    "communication_preference_engine",
)
LIVE_PATH = (
    "services/graph/streaming.py", "services/graph/nodes.py", "routers/chat.py",
    "services/memory_service.py", "services/memory_context_service.py",
)


@pytest.mark.parametrize("rel", LIVE_PATH)
def test_streaming_context_has_no_engine_sections(rel):
    text = (BACKEND / rel).read_text()
    hits = [name for name in RETIRED if f"services.{name}" in text]
    assert not hits, f"{rel} still imports {hits}"
    assert "Recent Awareness\\nWhile you were away, you noticed" not in text or rel == "services/graph/streaming.py"


def test_recent_awareness_is_briefing_and_ambient_notes_only():
    src = (BACKEND / "services/graph/streaming.py").read_text()
    for gone in ("Relevant workstreams", "Relevant initiatives", "Outbound commitments", "Workflow playbook suggestion",
                 "## Experiment Memory", "build_policy_context", "build_preference_context", "build_decision_context",
                 "build_capability_context", "record_legacy_commitment"):
        assert gone not in src, gone


def test_collect_llm_context_returns_only_the_seven_reads():
    from services.graph import streaming

    src = inspect.getsource(streaming._collect_llm_context)
    fetchers = sorted(set(line.strip()[len("async def "):].split("(")[0]
                          for line in src.splitlines() if line.strip().startswith("async def _fetch")))
    assert fetchers == ["_fetch_briefing", "_fetch_documents", "_fetch_enrichments", "_fetch_entity_memories",
                        "_fetch_open_loops", "_fetch_session_briefing", "_fetch_tools"]


def test_compose_system_prompt_has_no_engine_blocks():
    from services.graph.streaming import compose_system_prompt

    params = set(inspect.signature(compose_system_prompt).parameters)
    # ``tool_manual`` (2026-09-25): the manual moved out of the memory
    # context to sit right after ``base``, so the prompt cache covers it.
    # ``dynamic_split`` (2026-10-10): the seam a Claude turn's prompt cache
    # splits on (``claude_chat_model.STATIC_DYNAMIC_SPLIT``), not a block.
    # ``v2`` (2026-10-11): the toolset's manual carries the directives.
    assert params == {"base", "memory_context", "briefing_context", "session_briefing_context", "time_context",
                      "state_documents", "orchestrator_context", "tool_manual", "dynamic_split", "v2"}


def test_chat_message_is_not_prefixed_with_fact_cards():
    src = (BACKEND / "routers/chat.py").read_text()
    assert "Artifact facts:" not in src and "artifact_intelligence" not in src


def _section_sizes(prompt: str) -> str:
    """`## Heading: chars` per section, biggest first — the failure message."""
    import re

    pieces = [p for p in re.split(r"(?=\n## )", prompt) if p.strip()]
    sized = sorted(((len(p), p.strip().splitlines()[0][:60]) for p in pieces), reverse=True)
    return f"{len(prompt)} chars: " + "; ".join(f"{head} {n}" for n, head in sized)


@pytest.mark.integration
@pytest.mark.parametrize("skills_on", [True, False], ids=["all_skills_on", "all_skills_off"])
async def test_per_turn_prompt_floor_under_20k_chars(clean_db, monkeypatch, skills_on):
    """The static prompt for the toolset: base, the ~1K-token manual and
    the state-document directive. Since the toolset (2026-10-11) the
    manual is the same on every surface whatever is bound (it was a
    family-gated ~18K manual, 21,139 chars on the mini), so the floor is far
    under the old cap; this pins it so it cannot creep back."""
    from services import tool_registry
    from services.graph.streaming import build_memory_context, compose_system_prompt

    async def skill_states(force_refresh: bool = False):
        return {skill_id: skills_on for skill_id in tool_registry._SKILL_IDS}

    monkeypatch.delenv("EDWARD_TOOLSET", raising=False)
    monkeypatch.setattr(tool_registry, "_get_skill_states", skill_states)
    monkeypatch.setattr(tool_registry, "_get_custom_mcp_tools", lambda: [])

    tools = await tool_registry.get_available_tools()
    assert "search_inbox" not in {t.name for t in tools}, "Mail.app's tools were deleted with the toolset"
    mem = build_memory_context([], documents=[], enriched_memories=[], state_documents=[], open_loops=[])
    prompt = compose_system_prompt(base="You are Edward.", memory_context=mem, state_documents=[],
                                   tool_manual=tool_registry.get_tool_descriptions(tools), v2=True)
    assert "## Your computer and your tools" in prompt
    assert len(prompt) < 8_000, _section_sizes(prompt)


@pytest.mark.integration
async def test_memories_context_endpoint_reads_memories(clean_db):
    from services.memory_context_service import context_for_chat
    from services.memory_service import Memory, store_memory

    await store_memory(Memory(id=None, content="Rowan lands Friday at 6pm", memory_type="fact",
                              importance=0.6, origin="read", about="contact"))
    await store_memory(Memory(id=None, content="Ellis prefers dark mode", memory_type="preference", importance=0.5))
    await store_memory(Memory(id=None, content="Rowan likes the window seat", memory_type="preference",
                              importance=0.5, origin="heard", about="contact"))
    out = await context_for_chat("Rowan")
    everything = [m["content"] for m in out["memories"]] + out["relationship_facts"]
    assert "Rowan lands Friday at 6pm" in everything and "Rowan likes the window seat" in everything
    assert "Ellis prefers dark mode" not in out["relationship_facts"], "only what is about the contact"
    assert len(set(everything)) == len(everything), "a fact is shown once, not in both lists"
