"""The engine tables and modules are gone — mind rebuild Phase 8c.

Batches B (dead), C (prompt-only) and D (folded / wake-pack) of the
plan's table audit: 80 tables, 58 modules. Batch A (the heartbeat
sub-engines) stays until the mind path replaces the old triage. The
memory graph is not touched. ``conftest.clean_db``'s truncate list is
pinned against the models so it cannot drift again.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]

DROPPED = (
    "impact_alert_records", "timeline_events", "workflow_templates", "runbook_records",
    "workflow_state_records", "commitment_records", "deadline_records", "assumption_tracking_records",
    "stakeholder_awareness_records", "preference_records", "preference_contract_records", "autonomy_audit_log",
    "relationship_maintenance_logs", "scenario_effects", "artifact_revision_snapshots", "artifact_change_events",
    "impact_candidates", "artifact_alignment_records", "artifact_authority_records", "artifact_fact_cards",
    "artifact_deltas", "playbook_templates", "active_playbook_runs", "dependency_edges",
    "planned_work_blocks", "capacity_pressure_events", "cadence_records", "evidence_gap_records",
    "scenario_cases", "episode_records", "precedent_cases", "commitment_patterns",
    "commitment_outcomes", "attention_items", "human_capability_map", "stakeholder_links",
    "stakeholder_contexts", "stakeholder_role_records", "stakeholder_position_records", "stakeholder_alignment_map",
    "preference_policy_rules", "constraint_ledger_records", "objective_records", "autonomy_policy",
    "autonomy_policy_records", "autonomy_actions", "meeting_briefs", "meeting_outcomes",
    "risk_register_records", "assumption_risk_register", "experiment_records", "fact_claims",
    "claim_records", "negotiation_states", "negotiation_items", "relationship_fact_graph",
    "preference_memory_graph", "decision_memories", "watchpoint_records", "workstreams",
    "workstream_items", "initiatives", "initiative_links", "commitments_ledger",
    "commitment_ledger", "outbound_commitment_ledger", "inbound_obligation_records", "unresolved_asks_ledger",
    "open_question_resolver", "outbound_ask_state", "outbound_request_records", "recurring_entity_dossiers",
    "open_relationship_threads", "push_entity_affinity", "relationship_maintenance_profiles", "proactive_outreach_candidates",
    "watch_target_registry", "external_signal_snapshots", "external_signal_events", "wakeup_ticks",
)

RETIRED_MODULES = (
    "analogical_precedent_engine", "artifact_authority_engine", "artifact_change_impact_engine",
    "artifact_drift_engine", "artifact_intelligence", "assumption_register_engine",
    "assumption_risk_register", "autonomy_policy_engine", "cadence_orchestration_engine",
    "claim_grounding_engine", "claim_verification_engine", "commitment_calibration_engine",
    "commitment_ledger", "commitments_ledger", "constraint_ledger_engine",
    "decision_memory_engine", "dependency_forecaster", "dependency_graph_engine",
    "episode_precedent_engine", "evidence_gap_planner", "experiment_learning_engine",
    "external_intelligence_watcher", "fact_consistency_graph", "fact_ledger_engine",
    "gap_resolution_engine", "human_capability_map", "inbound_obligation_engine",
    "initiative_tracker", "meeting_context_engine", "micro_autonomy_executor",
    "negotiation_intelligence_engine", "objective_alignment_engine", "objective_outcome_tracker",
    "objective_state_engine", "open_question_resolver", "open_relationship_thread_service",
    "outbound_ask_tracker", "outbound_commitment_ledger", "outbound_request_engine",
    "precedent_case_engine", "preference_memory_graph", "preference_policy_engine",
    "priority_attention_engine", "recurring_entity_dossier_service", "relationship_fact_graph",
    "relationship_maintenance_engine", "risk_register_engine", "scenario_planning_engine",
    "stakeholder_alignment_map", "stakeholder_position_engine", "stakeholder_role_graph_engine",
    "stakeholder_routing_engine", "unresolved_asks_ledger", "warm_intro_opportunity_engine",
    "watchpoint_engine", "workflow_playbook_engine", "workstream_tracker",
    "proactive.daily_execution_planner",
)

# Tables that exist but are deliberately not truncated between tests:
# configuration rows and seeded reference data the app expects to find.
# ``entities``, ``entity_mentions`` and ``graph_ingest_queue`` are not in
# it: they are memory-derived data with no foreign key to ``memories``, so
# the CASCADE never reached them and a test's rows reached the next test
# (2026-09-25, test_graph_dual_write_isolation.py).
KEEP_ACROSS_TESTS = {
    "care_events", "care_items", "claude_code_sessions", "communication_style_profiles",
    "coordination_context_state", "custom_mcp_servers", "display_config", "email_actions", "email_sender_prefs",
    "email_unsub_queue", "files", "gmail_credentials",
    "heartbeat_conversational_archetype_profiles", "heartbeat_engagement_profiles",
    "heartbeat_interaction_style_profiles", "heartbeat_reciprocity_style_profiles",
    "heartbeat_reply_expectation_profiles", "hub_list_items", "open_loops", "orchestrator_config",
    "orchestrator_tasks", "persistent_databases", "pets", "proactive_config", "screen_state", "usage_events",
}
# Tables created outside database.py that the truncate list still clears.
# ``pings`` and ``media_items`` are SQLAlchemy Core tables on their own
# MetaData (``services/pings/schema.py``, ``services/media/schema.py``,
# AREA 3, 2026-10-11); ``init_db`` creates them.
CREATED_ELSEWHERE = {"llm_rate_gate_state", "skills_audit", "pings", "media_items"}


def _model_tables() -> set[str]:
    tree = ast.parse((BACKEND / "services" / "database.py").read_text())
    out = set()
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            for stmt in node.body:
                if isinstance(stmt, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__tablename__" for t in stmt.targets):
                    if isinstance(stmt.value, ast.Constant):
                        out.add(stmt.value.value)
    return out


def _truncate_list() -> list[str]:
    src = (BACKEND / "tests" / "conftest.py").read_text()
    m = re.search(r'"TRUNCATE (.*?) RESTART IDENTITY CASCADE"', src, re.S)
    assert m, "clean_db's TRUNCATE literal moved"
    return [n for n in re.sub(r'["\n]', " ", m.group(1)).replace(",", " ").split() if n]


def test_engine_modules_are_gone():
    for name in RETIRED_MODULES:
        assert not (BACKEND / "services" / (name.replace(".", "/") + ".py")).exists(), name
    live = list((BACKEND / "services").rglob("*.py")) + list((BACKEND / "routers").rglob("*.py")) + [BACKEND / "main.py"]
    for path in live:
        text = path.read_text()
        hits = [n for n in RETIRED_MODULES if f"services.{n}" in text]
        assert not hits, f"{path} still imports {hits}"


def test_dropped_tables_have_no_model():
    tables = _model_tables()
    assert not set(DROPPED) & tables, sorted(set(DROPPED) & tables)
    assert "graph_ingest_queue" in tables, "the memory graph stays"
    assert "memories" in tables and "documents" in tables


def test_conftest_truncate_list_matches_models():
    tables = _model_tables()
    truncated = _truncate_list()
    assert len(truncated) == len(set(truncated)), "duplicates in the truncate list"
    missing = tables - set(truncated) - KEEP_ACROSS_TESTS
    assert not missing, f"model tables neither truncated nor listed as kept: {sorted(missing)}"
    stray = set(truncated) - tables - CREATED_ELSEWHERE
    assert not stray, f"truncate list names tables without a model: {sorted(stray)}"
    assert not set(truncated) & set(DROPPED)


@pytest.mark.integration
async def test_dropped_tables_are_gone(clean_db):
    from sqlalchemy import inspect

    from services import database

    async with database.engine.connect() as conn:
        names = set(await conn.run_sync(lambda c: inspect(c).get_table_names()))
    assert not names & set(DROPPED), sorted(names & set(DROPPED))
    assert "graph_ingest_queue" in names
