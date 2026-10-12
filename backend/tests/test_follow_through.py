"""Follow-through: a promise made in a ping is kept in that chat
(proactivity spec §2.3(a), G10; 2026-10-11).

The two-way relay is the pattern worth keeping: Edward is asked in a group,
says he'll look, and comes back to the group with the answer. In code:

1. a ``schedule``/``wake_me`` made while a ping is being answered stamps
   ``event_metadata.ping`` (``scheduled_events_service.create_event``,
   from ``services.pings.hooks.CURRENT_PING``);
2. when it fires, ``mind_service.follow_through_for(meta)`` finds the chat
   — from that stamp, or from the intention's ``source_ref`` written in a
   ping turn — and the ``self`` wake may reply there
   (``TurnPolicy(may_reach_people=True, reply_to=(chat,))``);
3. a wake with no such chat reaches nobody but Ellis;
4. while a ping turn is running in that chat, the wake waits ten minutes
   instead of talking over it.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from tests.area1_fakes import hide_pings, install_pings_hooks, install_turn_policy
from tests.test_mind_service import _S, _const, _pack

NOW = datetime(2026, 10, 7, 23, 58, tzinfo=timezone.utc)
PING_META = {"type": "mind_wake", "why": "tell the group when a used pair under $150 shows up",
             "ping": {"ping_id": 41, "chat_identifier": "chat-trivia", "chat_guid": "any;+;chat-trivia",
                      "is_group": True, "conversation_id": "ping-41"}}


@pytest.fixture(autouse=True)
def _reset():
    import services.mind_service as ms

    ms.reset_state()
    yield
    ms.reset_state()


def _stub(monkeypatch, ms, chat, *, pack=None):
    monkeypatch.setattr(ms, "collect_pack", _const(pack or _pack(reason="self")))
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "follow_through_context", _const({}))
    recorded = []

    async def _rec(**kw):
        recorded.append(kw)
        return len(recorded)

    monkeypatch.setattr(ms, "_record_now", _rec)
    return recorded


# ------------------------------------------------------- the stamp --

@pytest.mark.integration
async def test_an_event_made_while_answering_a_ping_carries_the_chat(clean_db, monkeypatch):
    from services import scheduled_events_service as ses

    hooks = install_pings_hooks(monkeypatch)
    ctx = {"ping_id": 41, "chat_identifier": "chat-trivia", "chat_guid": "any;+;chat-trivia", "is_group": True,
           "conversation_id": "ping-41", "must_answer": True, "third_party": True, "inflight": 0, "sends": [],
           "tools": [], "stop": {}, "media": {}}
    token = hooks.CURRENT_PING.set(ctx)
    try:
        ev = await ses.create_event("look for used skis", datetime.utcnow() + timedelta(hours=2),
                                    event_metadata={"type": "mind_wake", "why": "skis under $150"})
    finally:
        hooks.CURRENT_PING.reset(token)
    meta = json.loads(ev.event_metadata)
    assert meta["ping"] == {"ping_id": 41, "chat_identifier": "chat-trivia", "chat_guid": "any;+;chat-trivia",
                            "is_group": True, "conversation_id": "ping-41"}
    assert meta["why"] == "skis under $150" and meta["type"] == "mind_wake"


@pytest.mark.integration
async def test_no_ping_no_stamp_and_no_pings_module_is_fine(clean_db, monkeypatch):
    from services import scheduled_events_service as ses

    install_pings_hooks(monkeypatch)
    ev = await ses.create_event("water the plants", datetime.utcnow() + timedelta(hours=1),
                                event_metadata={"type": "mind_wake"})
    assert "ping" not in json.loads(ev.event_metadata)
    hide_pings(monkeypatch)
    ev = await ses.create_event("water the ferns", datetime.utcnow() + timedelta(hours=3),
                                event_metadata={"type": "mind_wake"})
    assert "ping" not in json.loads(ev.event_metadata)


@pytest.mark.unit
def test_ping_stamp_is_pure_and_keeps_an_existing_one():
    from services.scheduled_events_service import ping_stamp

    ctx = {"ping_id": 7, "chat_identifier": "c", "chat_guid": None, "is_group": False, "conversation_id": "ping-7",
           "sends": [1], "tools": [], "must_answer": True}
    assert ping_stamp(ctx) == {"ping_id": 7, "chat_identifier": "c", "chat_guid": None, "is_group": False,
                               "conversation_id": "ping-7"}
    assert ping_stamp(None) is None
    assert ping_stamp({"ping_id": 7}) is None, "no chat, nothing to follow through in"


# ------------------------------------------------ follow_through_for --

@pytest.mark.unit
async def test_follow_through_from_the_stamp():
    import services.mind_service as ms

    ft = await ms.follow_through_for(PING_META)
    assert ft is not None
    assert ft.chat_identifier == "chat-trivia" and ft.chat_guid == "any;+;chat-trivia" and ft.is_group
    assert ft.ping_id == 41 and ft.why.startswith("tell the group")
    assert ft.requested_by_owner is False
    assert ft.reply_to == ("chat-trivia", "any;+;chat-trivia")
    ft = await ms.follow_through_for({**PING_META, "requested_by_owner": True})
    assert ft.requested_by_owner is True


@pytest.mark.unit
async def test_no_chat_no_follow_through():
    import services.mind_service as ms

    assert await ms.follow_through_for(None) is None
    assert await ms.follow_through_for({}) is None
    assert await ms.follow_through_for({"type": "mind_wake", "why": "check the oven"}) is None


@pytest.mark.integration
async def test_follow_through_from_the_intentions_source_ref(clean_db):
    import services.mind_service as ms
    from services.database import MemoryModel, async_session

    async with async_session() as s:
        s.add(MemoryModel(id="m-ping", content="Tell Trivia crew when used skis under $150 show up",
                          memory_type="intention", origin="thought", about="self",
                          source_ref=json.dumps({"kind": "extraction", "turn_source": "ping", "ping_id": 41,
                                                 "chat_identifier": "chat-trivia", "conversation_id": "ping-41"})))
        s.add(MemoryModel(id="m-owner", content="Remind Ellis's self-chat", memory_type="intention",
                          origin="thought", about="self",
                          source_ref=json.dumps({"turn_source": "ping_owner", "chat_identifier": "chat-owner"})))
        s.add(MemoryModel(id="m-chat", content="Check the oven", memory_type="intention", origin="thought",
                          about="self", source_ref=json.dumps({"turn_source": "chat", "chat_identifier": "x"})))
        await s.commit()
    ft = await ms.follow_through_for({"type": "mind_wake", "memory_id": "m-ping", "why": "skis"})
    assert ft is not None and ft.chat_identifier == "chat-trivia" and ft.ping_id == 41 and ft.memory_id == "m-ping"
    ft = await ms.follow_through_for({"type": "mind_wake", "memory_id": "m-owner"})
    assert ft is not None and ft.chat_identifier == "chat-owner"
    assert await ms.follow_through_for({"type": "mind_wake", "memory_id": "m-chat"}) is None
    assert await ms.follow_through_for({"type": "mind_wake", "memory_id": "missing"}) is None


# ---------------------------------------------------- the self wake --

@pytest.mark.unit
async def test_a_self_wake_with_a_ping_may_reply_in_that_chat(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    install_pings_hooks(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Told the group about the $140 pair."

    _stub(monkeypatch, ms, chat)
    out = await ms.wake("self", extra=PING_META["why"], event_meta=PING_META)
    assert out["ok"]
    p = calls[0]["policy"]
    assert p.may_reach_people and p.may_push and not p.owner_asked
    assert p.reply_to == ("chat-trivia", "any;+;chat-trivia")
    assert "send_imessage" in calls[0]["allowed_tools"] or "send_message" in calls[0]["allowed_tools"]


@pytest.mark.unit
async def test_requested_by_owner_makes_it_his_ask(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    install_pings_hooks(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "ok"

    _stub(monkeypatch, ms, chat)
    await ms.wake("self", event_meta={**PING_META, "requested_by_owner": True})
    assert calls[0]["policy"].owner_asked is True


@pytest.mark.unit
async def test_a_self_wake_without_a_ping_reaches_only_owner(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    install_pings_hooks(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Left it."

    _stub(monkeypatch, ms, chat)
    await ms.wake("self", extra="check the oven timer", event_meta={"type": "mind_wake", "why": "oven"})
    p = calls[0]["policy"]
    assert not p.may_reach_people and p.may_push and p.reply_to == ()
    assert not {"send_imessage", "send_message"} & set(calls[0]["allowed_tools"])


@pytest.mark.unit
async def test_a_chat_with_a_ping_turn_running_waits_ten_minutes(monkeypatch):
    import services.mind_service as ms
    from services import scheduled_events_service as ses

    install_turn_policy(monkeypatch)
    install_pings_hooks(monkeypatch, in_flight=["any;+;chat-trivia"])
    made = []

    async def fake_create(description, scheduled_at, **kw):
        made.append({"description": description, "scheduled_at": scheduled_at, **kw})
        return type("E", (), {"id": "ev-new"})()

    async def never(**kw):
        raise AssertionError("no turn while a ping turn runs in that chat")

    _stub(monkeypatch, ms, never)
    monkeypatch.setattr(ses, "create_event", fake_create)
    before = datetime.utcnow()
    out = await ms.wake("self", extra="skis", event_meta=PING_META)
    assert out["ok"] and out["skipped"] and out.get("rearmed")
    assert len(made) == 1
    at = made[0]["scheduled_at"]
    assert timedelta(minutes=9) < at - before < timedelta(minutes=11)
    assert made[0]["event_metadata"]["ping"] == PING_META["ping"]
    assert made[0]["event_metadata"]["rearmed"] == 1


@pytest.mark.unit
async def test_rearming_stops_after_an_hour(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    install_pings_hooks(monkeypatch, in_flight=["chat-trivia"])
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Posted it."

    _stub(monkeypatch, ms, chat)
    out = await ms.wake("self", event_meta={**PING_META, "rearmed": ms.MAX_FOLLOW_THROUGH_REARMS})
    assert out["ok"] and not out.get("skipped") and calls


# -------------------------------------------------------- the pack --

@pytest.mark.unit
def test_the_follow_through_block_leads_the_pack():
    import services.mind_service as ms

    p = _pack(reason="self", extra="tell the group when a used pair under $150 shows up", follow_through={
        "chat_name": "Trivia crew", "chat_identifier": "chat-trivia",
        "why": "tell the group when a used pair under $150 shows up",
        "asked": "Wed 7:28pm by Parker",
        "thread": ["Parker: anyone seen used Rossignols?", "Edward: I'll keep an eye out under $150"],
        "pings": ['7:28 pm Parker in Trivia crew "@edward look at used skis" → answered 7:29 pm (61 s)'],
    })
    text = ms.render_pack(p)
    head = text.split("Who you are")[0]
    assert "Follow-through" in head and "Trivia crew" in head
    assert "The promise: tell the group when a used pair under $150 shows up" in head
    assert "Asked: Wed 7:28pm by Parker" in head
    assert "      Edward: I'll keep an eye out under $150" in head
    assert "answered 7:29 pm (61 s)" in head
    assert "Reply in that chat when you have something, or when the promise is due" in head
    assert "close the intention" in head
    assert "Follow-through" not in ms.render_pack(_pack(reason="self"))


@pytest.mark.unit
async def test_follow_through_context_reads_the_chat_and_its_pings(monkeypatch):
    import services.mind_service as ms
    from services.heartbeat import listener_imessage

    install_pings_hooks(monkeypatch, lines=[
        '7:28 pm Parker in Trivia crew "@edward look at used skis" → answered 7:29 pm (61 s)',
        '6:02 pm Sam in Work "@edward standup?" → answered 6:03 pm (40 s)',
    ])
    asked = []

    def fake_recent(hours, contact, limit):
        asked.append((contact, limit))
        return [
            {"text": "anyone seen used Rossignols?", "is_from_me": False, "sender": "Parker", "chat_name": "Trivia crew"},
            {"text": "I'll keep an eye out under $150", "is_from_me": True, "by_edward": True},
            {"text": "thanks!", "is_from_me": True, "by_edward": False},
        ]

    monkeypatch.setattr(listener_imessage, "get_recent_messages_across_chats", fake_recent)
    ft = await ms.follow_through_for({**PING_META, "ping": {**PING_META["ping"], "chat_name": "Trivia crew"}})
    ctx = await ms.follow_through_context(ft, NOW)
    assert asked and asked[0] == ("chat-trivia", ms.FOLLOW_THROUGH_THREAD_LINES)
    assert ctx["chat_name"] == "Trivia crew" and ctx["chat_identifier"] == "chat-trivia"
    assert ctx["thread"] == ["Parker: anyone seen used Rossignols?", "Edward: I'll keep an eye out under $150",
                             "Ellis: thanks!"]
    assert ctx["pings"] == ['7:28 pm Parker in Trivia crew "@edward look at used skis" → answered 7:29 pm (61 s)']
    assert ctx["why"].startswith("tell the group")


# ------------------------------------------- the reply check (AREA 2) --
#
# Review 2026-10-11: AREA 2's tool loop nudges any turn whose policy has a
# ``reply_to`` and that ends with no send there ("You were asked in <chat>
# and haven't replied there. Reply with send_message…"), unless the
# policy's final text answers (``final_text_is_reply``). A follow-through
# that finds nothing is asked to say so in one line and look again — a
# nudge would post "no pair under $150 yet" into the group from Ellis's
# number on every look. Its closing line answers; only a mention is nudged.

def _final_text_answers(p) -> bool:
    fn = getattr(p, "final_text_answers", None)
    return bool(fn()) if callable(fn) else bool(getattr(p, "final_text_is_reply", None))


@pytest.mark.unit
async def test_a_follow_through_that_finds_nothing_is_never_nudged_into_the_chat(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    install_pings_hooks(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Nothing under $150 yet; looking again tomorrow."

    _stub(monkeypatch, ms, chat)
    await ms.wake("self", extra=PING_META["why"], event_meta=PING_META)
    p = calls[0]["policy"]
    assert p.reply_to and _final_text_answers(p) is True


@pytest.mark.unit
def test_only_a_mention_must_reply_in_its_chat():
    import services.mind_service as ms

    ft = ms.FollowThrough(chat_identifier="chat-trivia", chat_guid=None, chat_name="Trivia crew", why="skis",
                          requested_by_owner=False)
    assert ms.policy_values("self", follow_through=ft, timeout_s=240)["final_text_is_reply"] is True
    assert not ms.policy_values("mention", reply_to=("chat-fam",), timeout_s=420).get("final_text_is_reply")


@pytest.mark.unit
def test_a_speak_thread_may_end_in_drop_without_a_nudge(monkeypatch):
    from services import noticing_service

    install_turn_policy(monkeypatch)
    import services.mind_service as ms

    p = ms.make_policy(noticing_service.speak_policy_kwargs("thread", "chat-trivia", 90))
    assert p.reply_to == ("chat-trivia",) and _final_text_answers(p) is True
