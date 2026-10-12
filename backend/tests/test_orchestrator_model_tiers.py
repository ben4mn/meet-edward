"""Orchestrator aliases map to the current Codex tiers, not the retiring 5.4."""


def test_aliases_map_to_current_tiers():
    from services.orchestrator_service import MODEL_SHORTHAND, _resolve_model

    assert MODEL_SHORTHAND["mini"] == "gpt-5.6-luna"
    assert MODEL_SHORTHAND["fast"] == "gpt-5.6-luna"
    assert MODEL_SHORTHAND["pro"] == "gpt-5.6-terra"
    assert MODEL_SHORTHAND["deep"] == "gpt-6-astra"
    assert _resolve_model("deep", default="gpt-5.6-terra") == "gpt-6-astra"


def test_worker_default_is_terra():
    from services.orchestrator_models import OrchestratorConfigResponse, OrchestratorTaskResponse

    assert OrchestratorConfigResponse().default_worker_model == "gpt-5.6-terra"
    task = OrchestratorTaskResponse(id="t", parent_conversation_id="c", task_description="x")
    assert task.model == "gpt-5.6-terra"
