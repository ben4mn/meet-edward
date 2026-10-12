"""The mind never texts Ellis (2026-09-25).

The judgement check's safety scenario ``late_dose`` failed: an evening wake
saw Biscuit's 6pm carprofen 50 minutes late with Ellis away and called
``send_message(recipient="Ellis", …)``. Telling Ellis was right; the channel was
wrong. The mind reaches Ellis by ``send_push_notification`` (it lands in a
thread and goes through the push ledger, quiet hours, the interrupt ladder
and his "Right call?") or ``leave_note`` (Today). A text from the messaging
tools skips all of that, and ``send_imessage`` goes out from Ellis's own
number.

* The prompt says so, in the "Act" bullet.
* The send tools refuse a text to Ellis — by name ("Ellis", "Ellis Reed", "me",
  "myself") or by one of his own handles (``heartbeat_config.
  imessage_owner_handles``, phones compared on the last ten digits) — in a
  turn nobody asked for (``mind``; ``pulse``, the surfaces wake). The
  executor refuses it before the taint gate, so Ellis is never asked to
  approve a text to himself.
* Ellis asking in chat ("text me the address") is untouched, and so is a
  reply to Ellis's own ping in his self-chat (``owner_direct``), which is
  answering him where he asked — in that chat, not on any channel.
* Other people who happen to be called Ellis-something are other people.
* Review follow-ups (2026-09-25): send_imessage is checked on the target it
  delivers to (chat_id, else contact); a phone is found inside "tel:…",
  "whatsapp:…" and "Ellis (+1…)"; the ping window is the database's clock;
  scheduled events, the heartbeat and workers still send as before.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from services.graph import tools as graph_tools

REFUSAL = ("Tool error: Ellis is reached by send_push_notification (if it can't wait) or leave_note — not by text. "
           "If you meant someone else named Ellis, use their full name or number.")
OWNER_PHONE = "+12015550100"
OWNER_EMAIL = "owner@example.com"
HANDLES = [OWNER_PHONE, OWNER_EMAIL]
OWNER_ALIASES = ["Ellis", "owner", " Ellis Reed ", "ELLIS REED", "me", "Me", "myself"]
OWNER_HANDLES = ["+1 (201) 555-0100", "2015550100", "201-555-0100", "iMessage;-;+12015550100", "Owner@Example.com",
               "tel:+12015550100", "whatsapp:+12015550100", "Ellis (+12015550100)", "sms:2015550100",
               "imessage:+12015550100", "iMessage:owner@example.com", "Ellis Reed <+1 201 555 0100>"]
NOT_OWNER = ["Ellison", "Ellis Moore", "Ellis Franklin Jr", "Ellis Franklin", "Parker", "+12015550199",
           "parker@example.com", "whatsapp:+12015550199", "Parker (555-0100)", "tel:555-0100", "chat2015550100",
           "iMessage;+;chat2015550100", "Room 0100"]


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("target", OWNER_ALIASES + OWNER_HANDLES)
def test_names_owner_by_name_or_by_his_own_handle(target):
    assert graph_tools.names_owner(target, HANDLES) is True


@pytest.mark.unit
@pytest.mark.parametrize("target", NOT_OWNER + ["", None])
def test_whole_names_only_and_other_numbers_are_other_people(target):
    assert graph_tools.names_owner(target, HANDLES) is False


@pytest.mark.unit
def test_without_known_handles_his_names_still_count():
    assert graph_tools.names_owner("Ellis", []) is True
    assert graph_tools.names_owner(OWNER_PHONE, []) is False


@pytest.mark.unit
def test_send_targets_reads_each_send_tool():
    t = graph_tools.send_targets
    assert t("send_message", {"recipient": "Ellis", "message": "x"}) == ["Ellis"]
    # Only the target delivery uses: a chat_id wins, the contact is ignored.
    assert t("send_imessage", {"contact": "Parker", "chat_id": "chat1", "message": "x"}) == ["chat1"]
    assert t("send_imessage", {"contact": "Ellis", "chat_id": "chat-family", "message": "x"}) == ["chat-family"]
    assert t("send_imessage", {"contact": "Parker", "message": "x"}) == ["Parker"]
    assert t("send_imessage", {"contact": "Parker", "chat_id": "", "message": "x"}) == ["Parker"]
    assert t("send_imessage", {"contact": "Parker", "chat_id": None, "message": "x"}) == ["Parker"]
    assert t("send_sms", {"phone_number": OWNER_PHONE, "message": "x"}) == [OWNER_PHONE]
    assert t("send_whatsapp", {"phone_number": OWNER_PHONE, "message": "x"}) == [OWNER_PHONE]
    assert t("send_push_notification", {"title": "x", "body": "y"}) == []
    assert t("send_message", None) == []


@pytest.mark.unit
def test_the_prompt_says_owner_is_reached_by_push_or_note_never_by_text():
    from services.mind_service import MIND_SYSTEM_PROMPT

    act = next(line for line in MIND_SYSTEM_PROMPT.splitlines() if line.startswith("- **Act**"))
    assert "You reach Ellis only by `tell_owner` (or the note and push tools)" in act
    assert "`leave_note` and `send_push_notification` do the same" in act
    for tool in ("send_message", "send_imessage", "send_sms", "send_whatsapp"):
        assert f"`{tool}`" in act.split("never by", 1)[1], tool


@pytest.mark.unit
def test_the_prompt_keeps_answering_owner_in_his_own_chat():
    """Ellis messaging Edward in his own self-chat (``owner_direct``) is a
    mention wake with required_tools=["send_imessage"] that must be
    answered, and the guard lets that reply through. The line that forbids
    texting Ellis must say so, or the prompt and the wake contradict."""
    from services.mind_service import MIND_SYSTEM_PROMPT

    act = next(line for line in MIND_SYSTEM_PROMPT.splitlines() if line.startswith("- **Act**"))
    never = act.split("never by", 1)[1]
    assert "except to answer them in their own chat when they have just messaged you there" in never


@pytest.mark.unit
def test_the_refusal_says_how_to_reach_another_owner():
    assert graph_tools.TEXT_TO_OWNER_REFUSAL == REFUSAL
    assert REFUSAL.startswith("Tool error: ")


# ---------------------------------------------------------------------------
# The tools
# ---------------------------------------------------------------------------


@pytest.fixture
def _sends(monkeypatch):
    """Fake Messages.app, Twilio and Contacts; a turn of a chosen source;
    Ellis's handles known; no stand-downs; no ping from Ellis waiting."""
    import services.contacts_service as contacts
    import services.heartbeat.triage_service as ts
    import services.imessage_service as imsg
    import services.twilio_service as twilio
    from services import send_review_service as srs
    from services import turn_ledger

    sent, looked_up = [], []

    def fake_dm(contact, message):
        sent.append(("imessage", contact))
        return {"success": True, "to": contact}

    def fake_chat(chat_id, message):
        sent.append(("imessage-chat", chat_id))
        return {"success": True, "to": chat_id}

    async def fake_sms(to, message):
        sent.append(("sms", to))
        return {"to": to, "status": "queued"}

    async def fake_wa(to, message):
        sent.append(("whatsapp", to))
        return {"to": to, "status": "queued"}

    async def fake_routed(recipient, message, channel):
        sent.append(("message", recipient))
        return f"SMS sent from Edward to {recipient} (Status: queued)"

    def fake_lookup(query):
        looked_up.append(query)
        return {"success": True, "matches": [{"name": "Ellis Reed", "phones": [OWNER_PHONE]}]}

    async def no_window(*a, **k):
        return None

    async def approve(message, target):
        return {"approved": True, "reason": ""}

    async def no_standdown(target):
        return None

    async def handles():
        return list(HANDLES)

    async def nobody_waiting():
        return []

    async def not_notified(*a, **k):
        return None

    monkeypatch.setattr(imsg, "send_imessage", fake_dm)
    monkeypatch.setattr(imsg, "send_imessage_to_chat", fake_chat)
    monkeypatch.setattr(imsg, "is_available", lambda: True)
    monkeypatch.setattr(twilio, "send_sms", fake_sms)
    monkeypatch.setattr(twilio, "send_whatsapp", fake_wa)
    monkeypatch.setattr(twilio, "is_configured", lambda: True)
    monkeypatch.setattr(contacts, "lookup_contact", fake_lookup)
    monkeypatch.setattr(ts, "register_listening_window_for_chat", no_window)
    monkeypatch.setattr(srs, "review_outgoing_message", approve)
    monkeypatch.setattr("services.conversation_service.mark_user_notified", not_notified)
    monkeypatch.setattr(graph_tools, "_send_message_routed", fake_routed)
    monkeypatch.setattr(graph_tools, "_active_standdown_for", no_standdown)
    monkeypatch.setattr(graph_tools, "_owner_handles", handles, raising=False)
    monkeypatch.setattr(graph_tools, "_chats_where_owner_is_waiting", nobody_waiting, raising=False)
    graph_tools.reset_send_history()
    state = {"source": "mind"}
    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source=state["source"], id="t1"))
    yield SimpleNamespace(sent=sent, looked_up=looked_up, state=state)
    graph_tools.reset_send_history()


async def _send(tool: str, target: str, message: str = "Biscuit's 6pm carprofen is still marked late.") -> str:
    args = {
        "send_message": {"recipient": target, "message": message},
        "send_imessage": {"contact": target, "message": message},
        "send_sms": {"phone_number": target, "message": message},
        "send_whatsapp": {"phone_number": target, "message": message},
    }[tool]
    return await getattr(graph_tools, tool).ainvoke(args)


@pytest.mark.parametrize("target", OWNER_ALIASES)
async def test_a_mind_turn_cannot_text_owner_by_name(_sends, target):
    out = await _send("send_message", target)
    assert out == REFUSAL
    assert _sends.sent == []


@pytest.mark.parametrize("tool", ["send_message", "send_imessage", "send_sms", "send_whatsapp"])
@pytest.mark.parametrize("target", [OWNER_PHONE, "+1 (201) 555-0100", "2015550100"])
async def test_a_mind_turn_cannot_text_owner_at_his_own_number(_sends, tool, target):
    out = await _send(tool, target)
    assert out == REFUSAL, (tool, target, out)
    assert _sends.sent == []


async def test_send_imessage_refuses_owner_before_asking_contacts(_sends):
    out = await _send("send_imessage", "Ellis")
    assert out == REFUSAL
    assert _sends.looked_up == [], "no Contacts lookup for a send that is refused anyway"
    out = await graph_tools.send_imessage.ainvoke(
        {"contact": "x", "chat_id": f"iMessage;-;{OWNER_PHONE}", "message": "the pill"})
    assert out == REFUSAL
    out = await _send("send_imessage", OWNER_EMAIL.upper())
    assert out == REFUSAL
    assert _sends.sent == []


async def test_the_surfaces_wake_cannot_text_owner_either(_sends):
    _sends.state["source"] = "pulse"
    assert await _send("send_message", "Ellis") == REFUSAL
    assert _sends.sent == []


# Only the mind and the pulse are bound. Ellis asking in chat or voice, an
# event he (or Edward) scheduled, the heartbeat and a worker send as before.
@pytest.mark.parametrize("source", ["chat", "voice", "scheduled_event", "heartbeat", "worker"])
@pytest.mark.parametrize("tool,target", [("send_message", "Ellis"), ("send_message", "me"),
                                         ("send_sms", OWNER_PHONE), ("send_imessage", OWNER_PHONE)])
async def test_owner_asking_in_chat_still_gets_his_text(_sends, source, tool, target):
    _sends.state["source"] = source
    out = await _send(tool, target, message="The address is 12 Oak St.")
    assert "sent" in out.lower(), out
    assert len(_sends.sent) == 1


@pytest.mark.parametrize("target", ["Ellison", "Ellis Moore", "Ellis Franklin Jr", "+12015550199"])
async def test_a_mind_turn_may_still_message_other_people(_sends, target):
    out = await _send("send_message", target, message=f"hello {target}")
    assert "sent" in out.lower(), out
    assert _sends.sent == [("message", target)]


async def test_a_reply_to_owners_own_ping_in_his_self_chat_goes_through(_sends, monkeypatch):
    async def owner_is_waiting():
        return [OWNER_PHONE]

    monkeypatch.setattr(graph_tools, "_chats_where_owner_is_waiting", owner_is_waiting, raising=False)
    out = await graph_tools.send_imessage.ainvoke(
        {"contact": OWNER_PHONE, "chat_id": OWNER_PHONE, "message": "Biscuit's pill is marked given at 6:05."})
    assert "sent" in out.lower(), out
    assert _sends.sent == [("imessage-chat", OWNER_PHONE)]


async def test_a_ping_in_his_self_chat_opens_that_chat_only(_sends, monkeypatch):
    """A ping waiting in Ellis's self-chat (here: his email) lets the reply
    go to that chat — not every channel to Ellis from every wake."""
    async def owner_is_waiting():
        return [OWNER_EMAIL]

    monkeypatch.setattr(graph_tools, "_chats_where_owner_is_waiting", owner_is_waiting, raising=False)
    for tool, target in (("send_sms", OWNER_PHONE), ("send_whatsapp", OWNER_PHONE), ("send_message", "Ellis"),
                         ("send_imessage", OWNER_PHONE)):
        out = await _send(tool, target)
        assert out.startswith(REFUSAL), (tool, target, out)
        assert f'chat_id="{OWNER_EMAIL}"' in out, "the refusal points at the chat he is waiting in"
    assert _sends.sent == []
    out = await graph_tools.send_imessage.ainvoke(
        {"contact": "Ellis", "chat_id": f"iMessage;-;{OWNER_EMAIL}", "message": "Given at 6:05."})
    assert "sent" in out.lower(), out
    out = await _send("send_imessage", OWNER_EMAIL, message="And Maple ate.")
    assert "sent" in out.lower(), out
    assert _sends.sent == [("imessage-chat", f"iMessage;-;{OWNER_EMAIL}"), ("imessage", OWNER_EMAIL)]


async def test_send_imessage_checks_only_the_target_it_delivers_to(_sends):
    """Delivery ignores ``contact`` when ``chat_id`` is given, so "Ellis" as
    the contact of a reply into another group's chat is not a text to Ellis."""
    args = {"contact": "Ellis", "chat_id": "chat-family", "message": "Dinner is at 7 at Lupa."}
    assert await graph_tools.text_to_owner_refusal("send_imessage", args) is None
    out = await graph_tools.send_imessage.ainvoke(args)
    assert "sent" in out.lower(), out
    assert _sends.sent == [("imessage-chat", "chat-family")]
    assert _sends.looked_up == []


async def test_the_streaming_executor_refuses_it_too(_sends):
    """The SSE loop's executor (execute_tool_call_with_events) runs the same
    boundary check as the non-streaming one: refused, never run."""
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeTool

    send = FakeTool("send_message", "SMS sent from Edward to Ellis (Status: queued)")
    call = {"name": "send_message", "args": {"recipient": "Ellis", "message": "Biscuit's 6pm pill is late."}}
    events = [e async for e in streaming.execute_tool_call_with_events(call, [send], "edward-mind")]
    assert send.calls == []
    assert events[-1] == {"_result": REFUSAL}
    assert [e.get("result") for e in events if e.get("type") == "tool_end"] == [REFUSAL]


# ---------------------------------------------------------------------------
# Against the database: where Ellis's handles and pings really come from
# ---------------------------------------------------------------------------


async def _configure_handles(handles):
    from services.database import HeartbeatConfigModel, async_session

    async with async_session() as s:
        row = await s.get(HeartbeatConfigModel, "default")
        if row is None:
            row = HeartbeatConfigModel(id="default")
            s.add(row)
        row.imessage_owner_handles = json.dumps(handles)
        await s.commit()


async def _owner_pinged(chat: str):
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(
            id="ping-1", source="imessage", event_type="message_received", sender=chat, chat_identifier=chat,
            summary="@edward did Biscuit get her pill?", is_from_user=True, triage_status="escalated",
            raw_data=json.dumps({"owner_direct": True, "text": "@edward did Biscuit get her pill?"})))
        await s.commit()


@pytest.mark.integration
async def test_owners_handles_come_from_the_heartbeat_config(clean_db):
    assert await graph_tools._owner_handles() == []
    await _configure_handles(HANDLES)
    assert await graph_tools._owner_handles() == HANDLES


@pytest.mark.integration
async def test_a_mind_turn_cannot_text_owner_at_a_configured_handle(clean_db, monkeypatch):
    from services import turn_ledger

    sent = []

    async def fake_routed(recipient, message, channel):
        sent.append(recipient)
        return f"SMS sent from Edward to {recipient} (Status: queued)"

    async def no_standdown(target):
        return None

    monkeypatch.setattr(graph_tools, "_send_message_routed", fake_routed)
    monkeypatch.setattr(graph_tools, "_active_standdown_for", no_standdown)
    monkeypatch.setattr(turn_ledger, "current_turn", lambda: SimpleNamespace(source="mind", id="t1"))
    graph_tools.reset_send_history()
    await _configure_handles(HANDLES)
    assert await _send("send_message", "(201) 555-0100") == REFUSAL
    assert "sent" in (await _send("send_message", "(201) 555-0199")).lower()
    assert sent == ["(201) 555-0199"]
    graph_tools.reset_send_history()


@pytest.mark.integration
async def test_a_pending_ping_from_owner_lets_the_reply_through_an_answered_one_does_not(clean_db):
    await _owner_pinged(OWNER_PHONE)
    assert await graph_tools._chats_where_owner_is_waiting() == [OWNER_PHONE]
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        row = await s.get(HeartbeatEventModel, "ping-1")
        row.notified_at = datetime.utcnow()
        await s.commit()
    assert await graph_tools._chats_where_owner_is_waiting() == []


@pytest.mark.integration
async def test_someone_elses_message_is_not_a_ping_from_owner(clean_db):
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(
            id="parker-1", source="imessage", event_type="message_received", sender="+12015550199",
            chat_identifier="+12015550199", summary="@edward hi", is_from_user=False, triage_status="escalated",
            raw_data=json.dumps({"text": "@edward hi"})))
        s.add(HeartbeatEventModel(
            id="owner-group", source="imessage", event_type="message_received", sender=None,
            chat_identifier="chat-family", summary="see you soon", is_from_user=True, triage_status="escalated",
            raw_data=json.dumps({"owner_direct": False})))
        await s.commit()
    assert await graph_tools._chats_where_owner_is_waiting() == []


@pytest.mark.integration
async def test_the_ping_window_is_the_databases_clock(clean_db, monkeypatch):
    """24 hours by the database's clock (created_at is its now()), not the
    process's: a skewed process clock neither hides a fresh ping nor
    revives a stale one."""
    from sqlalchemy import text

    from services.database import async_session

    class _SkewedClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.now(tz) + timedelta(days=2)

    monkeypatch.setattr(graph_tools, "_dt", _SkewedClock)
    await _owner_pinged(OWNER_PHONE)
    assert await graph_tools._chats_where_owner_is_waiting() == [OWNER_PHONE]

    async def age(hours: int):
        async with async_session() as s:
            await s.execute(text("UPDATE heartbeat_events SET created_at = timezone('UTC', now()) "
                                 "- make_interval(hours => :h) WHERE id = 'ping-1'"), {"h": hours})
            await s.commit()

    await age(23)
    assert await graph_tools._chats_where_owner_is_waiting() == [OWNER_PHONE]
    await age(25)
    assert await graph_tools._chats_where_owner_is_waiting() == []


# ---------------------------------------------------------------------------
# The executor: refused before the taint gate, counted as a failure
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_the_executor_refuses_before_the_taint_gate_asks_owner(clean_db, monkeypatch):
    from langchain_core.messages import ToolMessage
    from sqlalchemy import select

    from services import turn_ledger
    from services.database import PendingApprovalModel, async_session
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io

    monkeypatch.setenv("EDWARD_TAINT_APPROVALS", "on")
    pushed = []

    async def fake_push(**kw):
        pushed.append(kw)
        return {"sent": 1, "failed": 0, "total": 1, "ledger_id": "l1"}

    monkeypatch.setattr("services.push_service.send_push_notification", fake_push)
    await _configure_handles(HANDLES)
    send = FakeTool("send_message", "SMS sent from Edward to Ellis (Status: queued)")
    h = patch_loop_io(monkeypatch, tools=[send], script=[
        ai("", call("send_message", recipient="Ellis", message="Biscuit's 6pm carprofen is still marked late.")),
        ai("Left it."),
    ])
    await streaming.chat_with_memory(message="[WAKE: evening] Biscuit — 6:00pm Carprofen 100mg: LATE (50 min)",
                                     turn_source="mind", skip_extraction=True,
                                     **base_kwargs(FakeGraph(), conversation_id="edward-mind"))
    assert send.calls == []
    results = [m.content for m in h.calls[-1]["messages"] if isinstance(m, ToolMessage)]
    assert results == [REFUSAL]
    async with async_session() as s:
        assert list((await s.execute(select(PendingApprovalModel))).scalars()) == [], \
            "Ellis is never asked to approve a text to himself"
    assert pushed == []
    turn = turn_ledger.last_finished_turn()
    assert turn.tool_failures == 1 and turn.tools[0]["n"] == "send_message" and turn.tools[0]["ok"] is False


@pytest.mark.integration
async def test_the_executor_leaves_a_chat_turn_alone(clean_db, monkeypatch):
    from services.graph import streaming
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io

    monkeypatch.setenv("EDWARD_TAINT_APPROVALS", "on")
    send = FakeTool("send_message", "SMS sent from Edward to me (Status: queued)")
    patch_loop_io(monkeypatch, tools=[send], script=[
        ai("", call("send_message", recipient="me", message="12 Oak St.")), ai("Sent."),
    ])
    await streaming.chat_with_memory(message="text me the address", turn_source="chat", skip_extraction=True,
                                     **base_kwargs(FakeGraph()))
    assert len(send.calls) == 1
