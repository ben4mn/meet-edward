"""The heartbeat thinks in the mind — mind rebuild Phase 6a.

A triage cycle applies only identity filters (who wrote it, was it already
answered, is it a duplicate mention) and hands the rest to the mind as raw
escalations with a wake request. The legacy engine (the rule chain and the
Layer 2 classifier) is gone, so the mind path is the only path whatever
``heartbeat_config.mind_enabled`` says (the column stays for the API and the
Listening panel; ``test_public_heartbeat_slim`` covers the flag-off cycle).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

MENTION = re.compile(r"@edward\b", re.IGNORECASE)


def _ev(id, *, text="hi", from_user=False, chat="c1", raw=None, source="imessage"):
    return SimpleNamespace(id=id, source=source, summary=text, is_from_user=from_user, chat_identifier=chat,
                           raw_data=json.dumps(raw) if raw else None, triage_status="pending", acted_at=None)


def test_identity_filter_drops_from_me_and_dupes_only():
    from services.heartbeat.mind_path import identity_filter

    events = [
        _ev("owner", text="on my way", from_user=True, chat="c-rowan"),          # Ellis wrote it
        _ev("m1", text="@edward what time is dinner", chat="c-group"),            # first mention in the chat
        _ev("m2", text="@edward hello?", chat="c-group"),                         # duplicate mention
        _ev("answered", text="can you grab milk", chat="c-rowan"),              # Ellis already replied there
        _ev("parker", text="the trivia night moved to Friday", chat="c-parker"),     # genuine
        _ev("sam", text="I'm at the gym", chat="c-sam"),                          # genuine, boring — not our call
        _ev("ping", text="edward remind me", from_user=True, chat="c-self", raw={"owner_direct": True}),
    ]
    v = identity_filter(events, mention_re=MENTION)
    assert [e.id for e in v.keep] == ["parker", "sam"], "nothing about the text is judged"
    assert [e.id for e in v.mentions] == ["m1", "ping"]
    assert {e.id for e in v.dismissed} == {"owner", "m2", "answered"}


def test_a_mention_wake_may_message():
    from services.mind_service import legacy_tools_for, policy_values

    tools = legacy_tools_for("mention", policy_values("mention", timeout_s=420))
    for name in ("send_imessage", "send_message", "stand_down", "read_imessage_thread", "list_chat_participants"):
        assert name in tools


def test_mind_prompt_carries_the_interjection_rules():
    from services.mind_service import MIND_SYSTEM_PROMPT

    low = MIND_SYSTEM_PROMPT.lower()
    for phrase in ("whose message", "you are edward, never ellis", "shoulder test", "pings are not yours here",
                   "must reply"):
        assert phrase in low, phrase


def test_render_pack_shows_each_escalation_thread():
    from services.mind_service import render_pack
    from tests.test_mind_service import _pack

    text = render_pack(_pack(escalations=[{
        "id": "ev-1", "sender": "Rowan", "chat": "c9", "text": "did Biscuit get her pill?", "at": "2026-09-18T14:10:00",
        "thread": ["Ellis: leaving now", "Rowan: ok", "Rowan: did Biscuit get her pill?"],
    }]))
    assert "did Biscuit get her pill?" in text
    assert "      Ellis: leaving now" in text and "      Rowan: ok" in text


def test_triage_cycle_always_goes_to_the_mind():
    """The cycle no longer branches on ``mind_enabled``: there is one path."""
    src = (Path(__file__).resolve().parents[1] / "services" / "heartbeat" / "triage_service.py").read_text()
    body = src[src.index("async def run_triage_cycle("):]
    assert "run_mind_cycle(" in body
    assert "_rule_pre_filter(" not in body
    assert 'getattr(config, "mind_enabled"' not in body


async def _seed(session, config_kw):
    from services.database import HeartbeatConfigModel, HeartbeatEventModel

    session.add(HeartbeatConfigModel(id="default", enabled=True, **config_kw))
    session.add(HeartbeatEventModel(id="parker", source="imessage", event_type="message_received", sender="Parker",
                                    chat_identifier="c-parker", summary="the trivia night moved to Friday",
                                    triage_status="pending"))
    session.add(HeartbeatEventModel(id="owner", source="imessage", event_type="message_sent", sender="me",
                                    chat_identifier="c-rowan", summary="on my way", is_from_user=True,
                                    triage_status="pending"))
    await session.commit()


@pytest.mark.integration
async def test_mind_enabled_cycle_never_calls_the_classifier(clean_db, monkeypatch):
    from sqlalchemy import select

    from services.database import HeartbeatEventModel, async_session
    from services.heartbeat import triage_service

    # The Layer 2 classifier and the rule chain left with the legacy engine.
    for gone in ("build_background_llm", "_rule_pre_filter", "_classify_events"):
        assert not hasattr(triage_service, gone), gone

    wakes = []
    monkeypatch.setattr("services.mind_service.request_wake", lambda reason, **kw: wakes.append((reason, kw)))

    async with async_session() as s:
        await _seed(s, {"mind_enabled": True})
    await triage_service.run_triage_cycle(1)

    async with async_session() as s:
        rows = {r.id: r for r in (await s.execute(select(HeartbeatEventModel))).scalars().all()}
    assert rows["parker"].triage_status == "escalated"
    assert rows["owner"].triage_status == "dismissed"
    assert wakes == [("messages", {"delay_s": 60.0})]


@pytest.mark.integration
async def test_mention_requests_an_immediate_wake_that_must_reply(clean_db, monkeypatch):
    from services.database import HeartbeatEventModel, async_session
    from services.heartbeat import triage_service

    wakes = []
    monkeypatch.setattr("services.mind_service.request_wake", lambda reason, **kw: wakes.append((reason, kw)))
    async with async_session() as s:
        await _seed(s, {"mind_enabled": True})
        s.add(HeartbeatEventModel(id="m1", source="imessage", event_type="message_received", sender="Sam",
                                  chat_identifier="c-group", summary="@edward what time is dinner",
                                  triage_status="pending"))
        await s.commit()
    await triage_service.run_triage_cycle(2)
    assert wakes and wakes[0][0] == "mention"
    # The mention's own id rides along (2026-09-26): a retry or follow-up
    # whose mention is gone runs with no forced reply.
    assert wakes[0][1] == {"delay_s": 0.0, "required_tools": ["send_imessage"], "mention_ids": ["m1"]}


@pytest.mark.integration
async def test_config_flag_round_trips_over_the_api(clean_db):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        r = await c.patch("/api/heartbeat/config", json={"mind_enabled": True})
        assert r.status_code == 200, r.text
        r = await c.get("/api/heartbeat/status")
        assert r.status_code == 200
        assert r.json()["mind_enabled"] is True
