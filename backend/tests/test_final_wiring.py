"""The wave-2 seams (plan Phases 4–7, 2026-09-24): surplus jobs are
attributed in the spend ledger, their wakes route through one effort table,
and the mind can see and use what the product, trust and hub work built."""
from __future__ import annotations

import pytest

import services.codex_chat_model as ccm
from services import llm_utils
from services import mind_service as ms
from services.call_purpose import purpose_scope

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("EDWARD_MIND_MODEL", "EDWARD_MIND_REASONING_EFFORT", "EDWARD_MIND_EFFORT_MAP"):
        monkeypatch.delenv(var, raising=False)


def test_a_call_inside_a_surplus_scope_is_labelled_with_the_job():
    llm = ccm.ChatCodexResponses(model="gpt-6-astra", api_key="x")
    with purpose_scope("surplus:research"):
        ctx = llm._call_context({"reasoning": {"effort": "high"}})
    assert ctx["purpose"] == "surplus:research"


def test_outside_a_scope_the_model_or_caller_label_still_wins():
    llm = ccm.ChatCodexResponses(model="gpt-6-astra", api_key="x", purpose="extraction")
    assert llm._call_context({})["purpose"] == "extraction"


def test_surplus_wakes_route_through_the_one_effort_table():
    for reason, effort in {"reflect_lite": "high", "research": "high", "prep": "medium"}.items():
        assert llm_utils.MIND_EFFORT_BY_REASON[reason] == effort
        assert ms.route_for_wake(reason, "mind", "gpt-6-astra")[1] == effort


def test_the_mind_can_ask_how_it_knows_but_never_forget():
    assert "how_do_i_know" in ms.LEGACY_WAKE_TOOLS
    assert "forget_about" not in ms.LEGACY_WAKE_TOOLS
    for name in ("set_goal", "update_goal", "list_goals", "suggest_idea"):
        assert name not in ms.LEGACY_WAKE_TOOLS, "goals and ideas are not the mind's since 2026-10-11"


def test_the_pack_shows_approvals_and_who_is_at_the_hub():
    """(Goals left the pack with unit G, 2026-10-11.)"""
    from datetime import datetime, timezone
    pack = ms.MindPack(now=datetime(2026, 9, 24, 18, 0, tzinfo=timezone.utc), reason="self", last_wake_at=None,
                       attention={}, messages=[], calendar=[], board="", notes="", pushes="", verdicts=[],
                       thoughts=[], actions=[], surfaces={})
    pack.approvals = ["Held for your approval (#3): iMessage to Parker — 'running late'"]
    pack.hub_presence = "At the hub: Rowan (since 17:40)"
    text = ms.render_pack(pack)
    assert "Held for your approval (#3)" in text
    assert "At the hub: Rowan" in text
