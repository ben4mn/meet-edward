"""The heartbeat without the legacy triage engine.

The three-layer engine (rule chain, background-LLM classifier, its own
reply turns) and its sub-engines are gone; every cycle goes to the mind
path. What other code imports from ``triage_service`` stays.
"""

from __future__ import annotations

import json

import pytest


def test_the_legacy_engine_is_gone_and_the_kept_symbols_stay():
    from services.heartbeat import triage_service as ts

    for gone in ("_rule_pre_filter", "_classify_events", "_execute_classification", "TRIAGE_PROMPT",
                 "HEARTBEAT_MIND_PROMPT", "build_background_llm"):
        assert not hasattr(ts, gone), gone
    for kept in ("run_triage_cycle", "record_standdown", "_get_active_standdown", "_release_standdown",
                 "register_listening_window_for_chat", "_active_listeners", "_count_recent_sends",
                 "MENTION_PATTERN"):
        assert hasattr(ts, kept), kept


def test_the_sub_engines_are_gone():
    import importlib

    for name in ("act_queue", "settle_logic", "travel_push_triage", "group_coordination_tracker",
                 "reply_expectation_model", "triage_rules"):
        with pytest.raises(ImportError):
            importlib.import_module(f"services.heartbeat.{name}")


def test_an_sms_compliance_footer_is_stripped_before_the_mind_reads_it():
    from types import SimpleNamespace

    from services.heartbeat import mind_path
    from services.heartbeat.triage_service import _normalize_inbound_message

    event = SimpleNamespace(
        id="e1", source="imessage", is_from_user=False, sender="12345", chat_identifier="12345",
        summary="Your table is ready.\nReply STOP to cancel",
        raw_data=json.dumps({"full_text": "Your table is ready.\nReply STOP to cancel"}),
    )
    assert _normalize_inbound_message(event) is not None
    assert event.summary == "Your table is ready."
    assert mind_path._text(event) == "Your table is ready."


def test_the_send_review_prompt_names_the_owner():
    from services.owner import owner_name
    from services.send_review_service import SEND_REVIEW_SYSTEM

    assert "{OWNER}" not in SEND_REVIEW_SYSTEM
    assert f"addressed to {owner_name()}" in SEND_REVIEW_SYSTEM
    assert f"Edward ({owner_name()}'s AI assistant)" in SEND_REVIEW_SYSTEM
    assert '{"approved": true|false' in SEND_REVIEW_SYSTEM, "the literal JSON braces survive"


def test_the_briefing_prompt_names_the_owner():
    from services.heartbeat.heartbeat_service import BRIEFING_SYSTEM_PROMPT
    from services.owner import owner_name

    assert "{OWNER}" not in BRIEFING_SYSTEM_PROMPT
    assert f"while {owner_name()} was away" in BRIEFING_SYSTEM_PROMPT


def test_presence_holds_nothing_back():
    from types import SimpleNamespace

    from routers.presence import _summary

    state = SimpleNamespace(as_dict=lambda: {"present": True, "fresh_beacons": [1]})
    assert _summary(state) == {"present": True, "watching": 0}


async def test_the_act_queue_route_is_retired_but_keeps_its_shape():
    from routers.heartbeat import get_act_queue_status

    status = await get_act_queue_status()
    assert status["queued"] == 0 and status["worker_alive"] is False and status["retired"] is True
    assert status["queued_ids"] == []


async def _seed(session, *, mind_enabled):
    from services.database import HeartbeatConfigModel, HeartbeatEventModel

    session.add(HeartbeatConfigModel(id="default", enabled=True, mind_enabled=mind_enabled))
    session.add(HeartbeatEventModel(id="parker", source="imessage", event_type="message_received", sender="Parker",
                                    chat_identifier="c-parker", summary="ACME got downgraded", triage_status="pending"))
    session.add(HeartbeatEventModel(id="owner", source="imessage", event_type="message_sent", sender="me",
                                    chat_identifier="c-rowan", summary="on my way", is_from_user=True,
                                    triage_status="pending"))
    await session.commit()


@pytest.mark.integration
@pytest.mark.parametrize("mind_enabled", [True, False])
async def test_every_cycle_goes_to_the_mind_whatever_the_old_flag_says(clean_db, monkeypatch, mind_enabled):
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session
    from services.heartbeat import triage_service

    wakes = []
    monkeypatch.setattr("services.mind_service.request_wake", lambda reason, **kw: wakes.append((reason, kw)))
    async with async_session() as s:
        await _seed(s, mind_enabled=mind_enabled)
    await triage_service.run_triage_cycle(1)

    async with async_session() as s:
        rows = {r.id: r for r in (await s.execute(select(HeartbeatEventModel))).scalars().all()}
    assert rows["parker"].triage_status == "escalated"
    assert rows["owner"].triage_status == "dismissed"
    assert wakes == [("messages", {"delay_s": 60.0})]


@pytest.mark.integration
async def test_a_cycle_with_nothing_pending_does_nothing(clean_db, monkeypatch):
    from services.heartbeat import triage_service

    monkeypatch.setattr("services.mind_service.request_wake",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no wake without events")))
    await triage_service.run_triage_cycle(1)


@pytest.mark.integration
async def test_the_status_says_escalations_go_to_the_mind(clean_db):
    from services.database import HeartbeatConfigModel, async_session
    from services.heartbeat import heartbeat_service

    async with async_session() as s:
        s.add(HeartbeatConfigModel(id="default", enabled=True, mind_enabled=False))
        await s.commit()
    status = await heartbeat_service.get_heartbeat_status()
    assert status["mind_enabled"] is True
    assert "dismiss_reply_review_enabled" not in status and "layer3_async_enabled" not in status


@pytest.mark.integration
async def test_a_verdict_is_stored_and_writes_no_legacy_signal(clean_db):
    from datetime import datetime, timezone

    from sqlalchemy import select

    from services import database
    from services.database import HeartbeatEventModel, HeartbeatFeedbackModel, async_session
    from services.heartbeat_feedback_service import record_feedback

    # The legacy signal table may be dropped from the schema; if it is
    # still there, nothing is written to it.
    signal_model = getattr(database, "HeartbeatFalseDismissalSignalModel", None)

    async with async_session() as s:
        s.add(HeartbeatEventModel(
            id="evt-1", source="imessage", event_type="message", sender="+15550001111",
            contact_name="Sam", chat_identifier="+15550001111", summary="are you coming tonight?",
            triage_status="dismissed", created_at=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        await s.commit()

    out = await record_feedback("evt-1", "should_have_told_me")
    assert out["verdict"] == "should_have_told_me"
    async with async_session() as s:
        assert len((await s.execute(select(HeartbeatFeedbackModel))).scalars().all()) == 1
        if signal_model is not None:
            assert (await s.execute(select(signal_model))).scalars().all() == []
