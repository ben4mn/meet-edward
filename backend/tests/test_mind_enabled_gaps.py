"""What has to be true before ``heartbeat_config.mind_enabled`` goes on
(plan 2026-09-24, C1 "gaps that go live when mind_enabled is turned on").

* Stand-downs bind the send tools themselves. The mind path ignored
  ``chat_standdowns`` although ``send_imessage`` is bound and the
  ``stand_down`` docstring promises enforcement. In an autonomous turn a
  send to a stood-down chat is a ``Tool error:``; Ellis asking in a chat or
  voice turn still goes through. A fresh ``@edward`` mention re-invites.
* Edward's own iMessages (signed "- Edward 🤖") are Edward's, not Ellis's:
  in the pack, in the pulse, and in the identity filter (an echo of his
  own reply is not "Ellis already answered").
* Today no longer lists an escalation the mind has already considered
  (``notified_at``); one the mind gave up on stays.
* A send is idempotent: the same text to the same recipient within ten
  minutes is not sent again (a timed-out wake keeps its sends).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.graph import tools as graph_tools

NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "target,standdown,expected",
    [
        ("+15551234567", "+15551234567", True),
        ("(555) 123-4567", "+1 555 123 4567", True),
        ("iMessage;+;chat100000000000000001", "chat100000000000000001", True),
        ("chat100000000000000001", "iMessage;+;chat100000000000000001", True),
        ("iMessage;-;+15551234567", "+15551234567", True),
        ("Rowan@Example.com", "rowan@example.com", True),
        ("chat111", "chat222", False),
        ("+15551234567", "+15557654321", False),
        ("", "chat1", False),
    ],
)
def test_send_target_matches_a_standdown(target, standdown, expected):
    assert graph_tools.same_chat(target, standdown) is expected


def test_normalize_send_text():
    n = graph_tools.normalize_send_text
    assert n("On my way!\n\n- Edward 🤖") == n("on my   way!") == "on my way!"
    assert n("On my way!") != n("On my way?")


def test_duplicate_send_at():
    history = [("p:5551234567", "on my way!", NOW - timedelta(minutes=4))]
    assert graph_tools.duplicate_send_at(history, "p:5551234567", "on my way!", NOW) == NOW - timedelta(minutes=4)
    assert graph_tools.duplicate_send_at(history, "p:5551234567", "running late", NOW) is None
    assert graph_tools.duplicate_send_at(history, "p:5557654321", "on my way!", NOW) is None
    assert graph_tools.duplicate_send_at(history, "p:5551234567", "on my way!", NOW + timedelta(minutes=7)) is None
    assert graph_tools.SEND_DEDUPE_WINDOW_SECONDS == 600


def test_send_key_canonicalises_the_recipient():
    k = graph_tools.send_key
    assert k("+1 (555) 123-4567") == k("5551234567") == "p:5551234567"
    assert k("iMessage;+;chat99") == k("chat99")


def test_delivered_results():
    d = graph_tools.looks_delivered
    for ok in ("iMessage sent to +1555", "SMS sent from Edward to +1", "WhatsApp sent to x (Status: queued)",
               "Sent via iMessage (Twilio failed): iMessage sent to x", "iMessage sent to group chat chat1"):
        assert d(ok), ok
    for no in ("Failed to send iMessage: x", "Tool error: stand-down", "iMessage not available.", "Already sent to x"):
        assert not d(no), no


def test_pack_labels_edwards_own_messages():
    from services.mind_service import has_edward_signature, render_pack, speaker
    from tests.test_mind_service import _pack

    mine = {"sender": None, "chat_identifier": "c1", "text": "Landing at 6:40.\n\n- Edward 🤖", "is_from_me": True,
            "timestamp": "2026-09-24T13:05:00"}
    owners = {"sender": None, "chat_identifier": "c1", "text": "thanks!", "is_from_me": True,
            "timestamp": "2026-09-24T13:06:00"}
    assert has_edward_signature(mine["text"]) and not has_edward_signature(owners["text"])
    assert speaker(mine) == "Edward" and speaker(owners) == "Ellis"
    text = render_pack(_pack(messages=[mine, owners]))
    assert "Edward in c1: Landing at 6:40." in text
    assert "Ellis: thanks!" in text


def test_pulse_trigger_labels_edwards_own_messages():
    from services.expression_service import PulseSignals, build_pulse_trigger

    s = PulseSignals(now=NOW, attention={}, recent_messages=[
        {"chat_identifier": "c1", "text": "On it. — Edward", "is_from_me": True},
        {"chat_identifier": "c1", "text": "cool", "is_from_me": True},
    ], board_summary="", current_widget=None, current_screen=None)
    text = build_pulse_trigger(s)
    assert "Edward in c1: On it." in text and "Ellis: cool" in text


def _ev(id, *, text="hi", from_user=False, chat="c1", raw=None, source="imessage"):
    return SimpleNamespace(id=id, source=source, summary=text, is_from_user=from_user, chat_identifier=chat,
                           raw_data=json.dumps(raw) if raw else None, triage_status="pending", acted_at=None)


def test_identity_filter_does_not_mistake_edwards_echo_for_owner():
    import re

    from services.heartbeat.mind_path import identity_filter

    mention = re.compile(r"@edward\b", re.IGNORECASE)
    events = [
        _ev("echo", text="We land at 6:40.\n\n- Edward 🤖", from_user=True, chat="c-parker"),
        _ev("parker", text="and which gate?", chat="c-parker"),
        _ev("self-echo", text="Done. — Edward", from_user=True, chat="c-self", raw={"owner_direct": True}),
    ]
    v = identity_filter(events, mention_re=mention)
    assert [e.id for e in v.keep] == ["parker"], "Edward answering is not Ellis being there"
    assert {e.id for e in v.dismissed} == {"echo", "self-echo"}
    assert v.mentions == [], "his own echo in Ellis's self-chat is never a ping"


def test_today_skips_escalations_the_mind_has_considered():
    from services.today_service import TodayInputs, compose_today

    t = (NOW - timedelta(minutes=30)).isoformat().replace("+00:00", "Z")
    inputs = TodayInputs(heartbeat=[
        {"id": "seen", "contact_name": "Parker", "summary": "flight?", "triage_status": "escalated", "created_at": t,
         "chat_identifier": "c-parker", "notified_at": t, "notification_batch_id": "mind:17"},
        {"id": "new", "contact_name": "Rowan", "summary": "dinner?", "triage_status": "escalated", "created_at": t,
         "chat_identifier": "c-rowan", "notified_at": None, "notification_batch_id": None},
        {"id": "given-up", "contact_name": "Sam", "summary": "call me", "triage_status": "escalated",
         "created_at": t, "chat_identifier": "c-s", "notified_at": t, "notification_batch_id": "mind:gave-up"},
    ])
    needs = compose_today(inputs, now=NOW)["needs"]
    ids = {n["id"] for n in needs if n["kind"] == "heartbeat"}
    assert ids == {"heartbeat:new", "heartbeat:given-up"}


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


@pytest.fixture
def _sender(monkeypatch):
    """A fake Messages.app and Twilio, and a turn of a chosen source."""
    import services.imessage_service as imsg
    import services.heartbeat.triage_service as ts
    from services import send_review_service as srs
    from services import turn_ledger

    sent = []

    def fake_dm(contact, message):
        sent.append(("dm", contact, message))
        return {"success": True, "to": contact}

    def fake_chat(chat_id, message):
        sent.append(("chat", chat_id, message))
        return {"success": True, "to": chat_id}

    async def no_window(*a, **k):
        return None

    async def approve(message, target):
        return {"approved": True, "reason": ""}

    monkeypatch.setattr(imsg, "send_imessage", fake_dm)
    monkeypatch.setattr(imsg, "send_imessage_to_chat", fake_chat)
    monkeypatch.setattr(imsg, "is_available", lambda: True)
    monkeypatch.setattr(ts, "register_listening_window_for_chat", no_window)
    monkeypatch.setattr(srs, "review_outgoing_message", approve)
    graph_tools.reset_send_history()
    state = {"source": "mind"}
    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source=state["source"], id="t1"))
    yield SimpleNamespace(sent=sent, state=state)
    graph_tools.reset_send_history()


@pytest.mark.integration
async def test_send_imessage_refuses_a_stood_down_chat_in_an_autonomous_turn(clean_db, _sender):
    from services.heartbeat.triage_service import record_standdown

    await record_standdown("chat100000000000000001", requested_by="Parker")
    out = await graph_tools.send_imessage.ainvoke(
        {"contact": "x", "message": "Landing at 6:40", "chat_id": "iMessage;+;chat100000000000000001"})
    assert out.startswith("Tool error:") and "stand" in out.lower() and "Parker" in out
    assert _sender.sent == []

    out = await graph_tools.send_imessage.ainvoke({"contact": "+15551230000", "message": "hi"})
    assert "sent" in out.lower(), "other chats are unaffected"


@pytest.mark.integration
async def test_send_message_refuses_a_stood_down_number(clean_db, _sender, monkeypatch):
    from services.heartbeat.triage_service import record_standdown

    async def enabled(skill):
        return skill == "imessage_applescript"

    monkeypatch.setattr("services.skills_service.is_skill_enabled", enabled)
    await record_standdown("+15551234567", requested_by="Sam")
    out = await graph_tools.send_message.ainvoke(
        {"recipient": "(555) 123-4567", "message": "Ellis is on his way", "channel": "imessage"})
    assert out.startswith("Tool error:") and "stand" in out.lower()
    assert _sender.sent == []


@pytest.mark.integration
async def test_owner_asking_in_chat_still_goes_through(clean_db, _sender):
    from services.heartbeat.triage_service import record_standdown

    await record_standdown("chat42", requested_by="Parker")
    _sender.state["source"] = "chat"
    out = await graph_tools.send_imessage.ainvoke({"contact": "x", "message": "Ellis says hi", "chat_id": "chat42"})
    assert "sent" in out.lower()
    assert _sender.sent and _sender.sent[0][1] == "chat42"


@pytest.mark.integration
async def test_a_released_or_expired_standdown_does_not_block(clean_db, _sender):
    from services.database import ChatStanddownModel, async_session
    from services.heartbeat.triage_service import _release_standdown, record_standdown

    await record_standdown("chat-a", requested_by="Parker")
    await _release_standdown("chat-a")
    await record_standdown("chat-b", requested_by="Sam")
    async with async_session() as s:
        row = await s.get(ChatStanddownModel, "chat-b")
        row.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        await s.commit()
    for chat in ("chat-a", "chat-b"):
        out = await graph_tools.send_imessage.ainvoke({"contact": "x", "message": f"hello {chat}", "chat_id": chat})
        assert "sent" in out.lower(), out


async def test_the_same_send_twice_is_sent_once(_sender, monkeypatch):
    async def no_standdown(target):
        return None

    monkeypatch.setattr(graph_tools, "_active_standdown_for", no_standdown)
    first = await graph_tools.send_imessage.ainvoke({"contact": "+15551230000", "message": "Landing at 6:40"})
    again = await graph_tools.send_imessage.ainvoke({"contact": "+1 555 123 0000", "message": "landing at  6:40"})
    other = await graph_tools.send_imessage.ainvoke({"contact": "+15551230000", "message": "Gate B12"})
    assert "sent" in first.lower() and "sent" in other.lower()
    assert again.startswith("Already sent to +1 555 123 0000 at ") and again.endswith("— not sending again.")
    assert [m for _, _, m in _sender.sent] == ["Landing at 6:40\n\n- Edward 🤖", "Gate B12\n\n- Edward 🤖"]


async def test_a_failed_send_is_not_remembered(_sender, monkeypatch):
    import services.imessage_service as imsg

    async def no_standdown(target):
        return None

    calls = []

    def flaky(contact, message):
        calls.append(message)
        return {"success": len(calls) > 1, "error": "Messages timed out"}

    monkeypatch.setattr(graph_tools, "_active_standdown_for", no_standdown)
    monkeypatch.setattr(imsg, "send_imessage", flaky)
    first = await graph_tools.send_imessage.ainvoke({"contact": "+15551230000", "message": "hi"})
    second = await graph_tools.send_imessage.ainvoke({"contact": "+15551230000", "message": "hi"})
    assert first.startswith("Failed") and "sent" in second.lower() and len(calls) == 2


# ---------------------------------------------------------------------------
# The mind path
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_a_mention_re_invites_edward_into_a_stood_down_chat(clean_db, monkeypatch):
    import re

    from services.heartbeat import mind_path
    from services.heartbeat.triage_service import _get_active_standdown, record_standdown

    await record_standdown("c-group", requested_by="Parker")
    await record_standdown("c-quiet", requested_by="Sam")
    monkeypatch.setattr("services.mind_service.request_wake", lambda *a, **k: None)

    class _Session:
        async def commit(self):
            return None

    events = [_ev("m1", text="@edward come back, what time do we land?", chat="c-group"),
              _ev("q1", text="still here", chat="c-quiet")]
    await mind_path.run_mind_cycle(events, session=_Session(), cycle_number=1,
                                   mention_re=re.compile(r"@edward\b", re.IGNORECASE))
    assert await _get_active_standdown("c-group") is None, "a fresh mention is a re-invitation"
    assert await _get_active_standdown("c-quiet") is not None, "an ordinary message is not"


@pytest.mark.integration
async def test_escalations_the_mind_considered_leave_today(clean_db):
    from services.database import HeartbeatEventModel, async_session
    from services.today_service import _heartbeat

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        s.add(HeartbeatEventModel(id="seen", source="imessage", event_type="message_received", sender="Parker",
                                  chat_identifier="c1", summary="flight?", triage_status="escalated",
                                  notified_at=datetime.utcnow(), notification_batch_id="mind:3"))
        s.add(HeartbeatEventModel(id="new", source="imessage", event_type="message_received", sender="Rowan",
                                  chat_identifier="c2", summary="dinner?", triage_status="escalated"))
        s.add(HeartbeatEventModel(id="gave-up", source="imessage", event_type="message_received", sender="Sam",
                                  chat_identifier="c3", summary="call me", triage_status="escalated",
                                  notified_at=datetime.utcnow(), notification_batch_id="mind:gave-up"))
        await s.commit()
    rows = await _heartbeat(now)
    assert {r["id"] for r in rows} == {"new", "gave-up"}


# ---------------------------------------------------------------------------
# AREA 3 (2026-10-11): Ellis's own @edward and the fallback filter (R1, R2)
# ---------------------------------------------------------------------------


def test_owners_own_at_edward_is_a_mention_and_does_not_silence_his_friends():
    """R1: the identity filter dropped Ellis's own from-me @edward as 'Ellis wrote
    it'. R2: it also put his chat in replied_chats, so a friend's message in
    the same batch was dismissed as 'Ellis is already there'. This is the
    flag-off / degraded path; with pings on these rows never reach triage."""
    import re

    from services.heartbeat.mind_path import identity_filter

    mention = re.compile(r"@edward\b", re.IGNORECASE)
    events = [
        _ev("owner-ping", text="@edward look at tickets for the Saturday show", from_user=True, chat="c-trivia"),
        _ev("parker", text="also is anyone driving?", chat="c-trivia"),
        _ev("owner-plain", text="on my way", from_user=True, chat="c-sam"),
        _ev("sam", text="cool", chat="c-sam"),
    ]
    v = identity_filter(events, mention_re=mention)
    assert [e.id for e in v.mentions] == ["owner-ping"]
    assert [e.id for e in v.keep] == ["parker"], "Ellis's ping is not Ellis answering the chat"
    assert {e.id for e in v.dismissed} == {"owner-plain", "sam"}


def test_a_mention_past_the_200_character_summary_is_still_a_mention():
    import re

    from services.heartbeat.mind_path import identity_filter

    long_text = "x " * 150 + "@edward can you check?"
    events = [_ev("m", text=long_text[:200], chat="c-trivia", raw={"full_text": long_text})]
    v = identity_filter(events, mention_re=re.compile(r"@edward\b", re.IGNORECASE))
    assert [e.id for e in v.mentions] == ["m"]


def test_structural_echoes_and_ping_rows_are_dismissed():
    import re

    from services.heartbeat.mind_path import identity_filter

    events = [
        _ev("echo", text="@edward here, unsigned", from_user=True, chat="c-trivia", raw={"edward_echo": "rowid"}),
        _ev("handled", text="@edward hi", chat="c-trivia", raw={"ping": {"kind": "mention", "ping_id": 3}}),
        _ev("owner-handled", text="what's on", from_user=True, chat="c-self",
            raw={"owner_direct": True, "ping": {"kind": "owner_direct", "ping_id": 4}}),
        _ev("parker", text="anyone?", chat="c-trivia"),
    ]
    v = identity_filter(events, mention_re=re.compile(r"@edward\b", re.IGNORECASE))
    assert {e.id for e in v.dismissed} == {"echo", "handled", "owner-handled"}
    assert v.mentions == [] and [e.id for e in v.keep] == ["parker"]


def test_an_owner_direct_ping_does_not_silence_its_chat():
    import re

    from services.heartbeat.mind_path import identity_filter

    events = [
        _ev("owner", text="hey", from_user=True, chat="c-self", raw={"owner_direct": True}),
        _ev("other", text="still there?", chat="c-self"),
    ]
    v = identity_filter(events, mention_re=re.compile(r"@edward\b", re.IGNORECASE))
    assert [e.id for e in v.keep] == ["other"]


@pytest.mark.integration
async def test_owners_own_mention_releases_the_stand_down(clean_db, monkeypatch):
    import re

    from services.heartbeat import mind_path
    from services.heartbeat.triage_service import _get_active_standdown, record_standdown

    await record_standdown("c-group", requested_by="Parker")
    wakes = []
    monkeypatch.setattr("services.mind_service.request_wake", lambda *a, **k: wakes.append((a, k)))

    class _Session:
        async def commit(self):
            return None

    events = [_ev("b1", text="@edward you can answer here again", from_user=True, chat="c-group")]
    await mind_path.run_mind_cycle(events, session=_Session(), cycle_number=1,
                                   mention_re=re.compile(r"@edward\b", re.IGNORECASE))
    assert await _get_active_standdown("c-group") is None
    assert wakes and wakes[0][0][0] == "mention"
