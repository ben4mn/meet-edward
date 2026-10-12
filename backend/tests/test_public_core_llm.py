"""The public build's defaults in the turn and LLM core.

* The provider defaults to the Claude API when ``EDWARD_LLM_PROVIDER`` is
  unset; ``codex`` is the opt-in fallback.
* Prepaid credits are tracked only when both credits variables are set.
* The default persona names the owner with ``{OWNER}``, rendered when the
  system prompt is composed, never stored.
* The settings picker lists the Claude routes under the Claude provider.
* A held send's prefix matches between the approvals service and the turn
  ledger.
* Public PR #5: a task dict's always-present ``error`` key is not a lookup
  failure.
"""

from __future__ import annotations

import asyncio

import pytest


# ---------------------------------------------------------------- provider --

def test_the_provider_defaults_to_the_claude_api(monkeypatch):
    from services import claude_routing, llm_spend

    monkeypatch.delenv("EDWARD_LLM_PROVIDER", raising=False)
    assert claude_routing.llm_provider() == "anthropic"
    assert llm_spend.provider() == "anthropic"


@pytest.mark.parametrize("value", ["codex", "openai", "gpt", "CODEX"])
def test_codex_is_the_opt_in_fallback(monkeypatch, value):
    from services import claude_routing, llm_spend

    monkeypatch.setenv("EDWARD_LLM_PROVIDER", value)
    assert claude_routing.llm_provider() == "codex"
    assert llm_spend.provider() == "codex"


def test_an_unknown_provider_stays_on_the_claude_api(monkeypatch):
    from services import claude_routing

    monkeypatch.setenv("EDWARD_LLM_PROVIDER", "bogus")
    assert claude_routing.llm_provider() == "anthropic"


def test_the_default_models_are_the_claude_routes():
    from models.schemas import DEFAULT_CHAT_MODEL, Settings
    from services import llm_utils
    from services.claude_routing import DEFAULT_ROUTES
    from services.database import SettingsModel

    assert llm_utils.DEFAULT_CHAT_MODEL == DEFAULT_ROUTES["chat"].model
    assert llm_utils.DEFAULT_BACKGROUND_MODEL == DEFAULT_ROUTES["background"].model
    assert not llm_utils.DEFAULT_CHAT_MODEL.startswith("gpt-")
    assert DEFAULT_CHAT_MODEL == DEFAULT_ROUTES["chat"].model
    assert Settings().model == DEFAULT_CHAT_MODEL and Settings().provider == "anthropic"
    assert SettingsModel.__table__.c.model.default.arg == DEFAULT_CHAT_MODEL
    assert SettingsModel.__table__.c.provider.default.arg == "anthropic"


def test_the_heartbeat_defaults_are_mind_on_and_imessage_opt_in():
    from services.database import HeartbeatConfigModel

    cols = HeartbeatConfigModel.__table__.c
    assert cols.mind_enabled.default.arg is True
    assert cols.imessage_enabled.default.arg is False


# ----------------------------------------------------------------- credits --

def test_credits_are_untracked_without_both_variables(monkeypatch):
    from services import llm_spend

    monkeypatch.delenv("EDWARD_CREDITS_START_USD", raising=False)
    monkeypatch.delenv("EDWARD_CREDITS_START_DATE", raising=False)
    assert not llm_spend.credits_tracked()
    out = asyncio.run(llm_spend.credits_status())
    assert out["source"] == "untracked" and "remaining_usd" not in out
    assert "EDWARD_CREDITS_START_USD" in out["note"]

    monkeypatch.setenv("EDWARD_CREDITS_START_USD", "100")
    assert not llm_spend.credits_tracked()      # the date is still missing
    monkeypatch.setenv("EDWARD_CREDITS_START_DATE", "2026-10-11")
    assert llm_spend.credits_tracked()


def test_an_untracked_block_reads_as_no_credits_in_the_headline():
    from services import llm_spend

    block = llm_spend.compose_claude_block(
        spent=1.0, limits=llm_spend.SpendLimits(), day=__import__("datetime").date(2026, 10, 12),
        tz_name="UTC", groups=[], credits={"source": "untracked", "note": llm_spend.CREDITS_UNTRACKED_NOTE},
        active=True)
    head = llm_spend.headline(block)
    assert head["credits_remaining_usd"] is None and head["credits_days_left"] is None


def test_the_cap_sentence_names_the_owner():
    from services import llm_spend
    from services.owner import owner_name

    d = llm_spend.spend_decision("interactive", 6.0, llm_spend.SpendLimits(), autonomous=True)
    assert not d.allowed and f"{owner_name()}'s messages" in d.reason


# ----------------------------------------------------------------- persona --

def test_the_default_persona_carries_the_owner_token_and_compose_renders_it():
    from models.schemas import DEFAULT_SYSTEM_PROMPT
    from services.graph.streaming import compose_system_prompt
    from services.owner import owner_name

    assert "{OWNER}" in DEFAULT_SYSTEM_PROMPT
    out = compose_system_prompt(base=DEFAULT_SYSTEM_PROMPT, v2=True)
    assert "{OWNER}" not in out and owner_name() in out


def test_compose_renders_a_name_change_without_a_database_edit(monkeypatch):
    from services.graph.streaming import compose_system_prompt

    monkeypatch.setenv("EDWARD_OWNER_NAME", "Robin")
    assert "Robin's Mac" in compose_system_prompt(base="You run on {OWNER}'s Mac.", v2=True)


def test_the_credential_directive_names_the_owner():
    from services.graph.streaming import CREDENTIAL_SAFETY_DIRECTIVE
    from services.owner import owner_name

    assert f"{owner_name()} is the sole user" in CREDENTIAL_SAFETY_DIRECTIVE
    assert "{OWNER}" not in CREDENTIAL_SAFETY_DIRECTIVE


# ---------------------------------------------------------------- settings --

def test_the_picker_lists_the_claude_routes(monkeypatch):
    from routers import settings as settings_router
    from services.claude_routing import claude_route

    monkeypatch.delenv("EDWARD_LLM_PROVIDER", raising=False)
    entries = settings_router.claude_picker_entries()
    assert entries[0]["id"] == claude_route("chat").model
    assert entries[0]["name"].endswith("(Recommended)")
    assert {claude_route(p).model for p in ("chat", "background", "judge")} <= {e["id"] for e in entries}
    assert settings_router.validate_model_for_provider(claude_route("chat").model) is None
    assert settings_router.validate_model_for_provider("gpt-6-astra")


# --------------------------------------------------------- held sentinel ----

def test_the_held_prefix_matches_the_approvals_service():
    from services import approvals_service, turn_ledger

    assert turn_ledger.HELD_FOR_APPROVAL_PREFIX == approvals_service.WAITING_PREFIX


# -------------------------------------------------------------- PR #5 ------

def test_a_task_with_no_error_is_not_a_lookup_failure(monkeypatch):
    from services import orchestrator_service

    async def fake_get_task(task_id):
        return {"id": task_id, "status": "running", "error": None, "worker_conversation_id": "w"}

    monkeypatch.setattr(orchestrator_service, "get_task", fake_get_task)
    out = asyncio.run(orchestrator_service.send_message_to_worker("t1", "hi"))
    assert out == {"error": "Cannot send message to a running worker. Wait for it to complete."}
