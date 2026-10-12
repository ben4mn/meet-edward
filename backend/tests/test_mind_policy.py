"""The mind's reasons, tools and turn policy (proactivity spec §3.1, §5.2; 2026-10-11).

Proactivity is noticing, not a schedule: the morning/evening ticks, the
Sunday reflect, the surplus wakes (reflect_lite, research, prep, free) and
the ``messages`` full-pack wake are gone. What remains wakes for a reason
(``manual``, ``self``, ``mention``, ``surfaces``, ``speak``, ``nightly``),
and each wake runs under AREA 2's ``TurnPolicy`` — fed through
``chat_with_memory(policy=…)`` only when it accepts one (feature-detected),
with the legacy ``allowed_tools`` tuple always passed for
``EDWARD_TOOLSET=legacy``. ``request_wake("messages")`` is a shim that
hands inbound to ``noticing_service.request_notice(None)``, or stores it
itself, so Today never floods.
"""

from __future__ import annotations

import asyncio
import inspect
import sys
import types
from datetime import datetime, timedelta, timezone

import pytest

from tests.area1_fakes import fake_module, hide_turn_policy, install_turn_policy
from tests.test_mind_service import _S, _const, _pack

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 7, 15, 0, tzinfo=timezone.utc)  # a Wednesday


@pytest.fixture(autouse=True)
def _reset():
    import services.mind_service as ms

    ms.reset_state()
    yield
    ms.reset_state()


def _stub(monkeypatch, ms, chat, *, pack=None):
    monkeypatch.setattr(ms, "collect_pack", _const(pack or _pack()))
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    recorded = []

    async def _rec(**kw):
        recorded.append(kw)
        return len(recorded)

    monkeypatch.setattr(ms, "_record_now", _rec)
    return recorded


# ---------------------------------------------------------------- reasons --

def test_the_reasons_are_the_ones_left():
    import services.mind_service as ms

    # ``free`` came back as one budgeted run a day (Ellis's answer #4, 2026-10-11).
    assert ms.REASONS == ("manual", "self", "mention", "surfaces", "speak", "nightly", "free", "messages")
    for gone in ("morning", "evening", "reflect", "reflect_lite", "research", "prep"):
        assert gone not in ms.REASONS, gone


def test_the_surplus_and_full_toolset_names_are_gone():
    import services.mind_service as ms

    for name in ("MIND_TOOLS", "REFLECT_LITE_TOOLS", "RESEARCH_TOOLS", "PREP_TOOLS", "FREE_TIME_TOOLS",
                 "NO_REACH_PROFILES", "profile_for", "SURPLUS_WAKE_REASONS", "_surplus_allows",
                 "_surplus_decision_now", "RESEARCH_MAX_TOOL_ITERATIONS", "FREE_TIME_TIMEOUT_SECONDS",
                 "PREP_TIMEOUT_SECONDS", "REFLECT_LITE_TIMEOUT_SECONDS", "_goals_source"):
        assert not hasattr(ms, name), name
    assert "goals_service" not in inspect.getsource(ms), "the goals are not the mind's any more"


def test_the_thread_ids_keep_the_old_and_add_the_new():
    import services.mind_service as ms

    assert ms.SPEAK_CONVERSATION_ID == "edward-speak"
    assert ms.NIGHTLY_CONVERSATION_ID == "edward-nightly"
    assert {"edward-mind", "edward-surfaces", "edward-free-time", "edward-research", "edward-speak",
            "edward-nightly"} <= set(ms.EDWARD_OWN_THREAD_IDS)


def test_retry_and_rearm_reasons():
    import services.mind_service as ms

    assert ms.RETRY_REASONS == ("self", "mention", "nightly")
    assert ms.REARM_REASONS == ("mention",)


def test_timeouts_and_windows_for_the_new_reasons():
    import services.mind_service as ms

    assert ms.wake_timeout_for("speak") == ms.SPEAK_TIMEOUT_SECONDS == 180
    assert ms.wake_timeout_for("mention") == 420
    assert ms.wake_timeout_for("self") == ms.wake_timeout_for("manual") == 240
    wednesday = datetime(2026, 10, 7, 8, 0, tzinfo=timezone.utc)   # 03:00 in Springfield
    sunday = datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)
    assert ms.wake_timeout_for("nightly", now=wednesday) == 600
    assert ms.wake_timeout_for("nightly", now=sunday) == 900
    assert ms.history_chars_for("speak", 0) == 0
    assert ms.history_chars_for("speak", None) == 0
    assert ms.history_chars_for("nightly", None) == 48_000
    assert ms.history_chars_for("self", None) == ms.MIND_MAX_HISTORY_CHARS


def test_escalations_are_served_to_a_mention_only():
    import services.mind_service as ms

    assert ms.serves_escalations("mention") is True
    for reason in ("manual", "self", "nightly", "speak", "surfaces", "messages"):
        assert ms.serves_escalations(reason) is False, reason


def test_speak_and_the_legacy_wakes_do_not_move_your_last_wake():
    import services.mind_service as ms

    assert not ms.moves_last_wake("speak")
    for old in ("free", "reflect_lite", "prep", "research"):
        assert not ms.moves_last_wake(old), "old ledger rows still read as attention-blind"
    for reason in ("manual", "self", "mention", "nightly"):
        assert ms.moves_last_wake(reason)


# ------------------------------------------------------------ routing --

def test_route_for_wake_no_longer_raises_effort_with_headroom(monkeypatch):
    import services.mind_service as ms
    from services import llm_utils

    assert "decision" not in inspect.signature(ms.route_for_wake).parameters
    monkeypatch.setattr(llm_utils, "effort_with_headroom",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("no headroom raise")))
    for var in ("EDWARD_MIND_MODEL", "EDWARD_MIND_REASONING_EFFORT", "EDWARD_MIND_EFFORT_MAP", "EDWARD_SPEAK_MODEL"):
        monkeypatch.delenv(var, raising=False)
    assert ms.route_for_wake("self", "mind", "gpt-6-astra") == ("gpt-6-astra", "high")
    assert ms.route_for_wake("surfaces", "pulse", "gpt-6-astra")[1] == "low"


def test_edward_speak_model_routes_the_speak_wake_only(monkeypatch):
    import services.mind_service as ms

    monkeypatch.delenv("EDWARD_MIND_MODEL", raising=False)
    monkeypatch.setenv("EDWARD_SPEAK_MODEL", "gpt-5.6-terra")
    assert ms.route_for_wake("speak", "mind", "gpt-6-astra")[0] == "gpt-5.6-terra"
    assert ms.route_for_wake("self", "mind", "gpt-6-astra")[0] == "gpt-6-astra"
    monkeypatch.delenv("EDWARD_SPEAK_MODEL")
    assert ms.route_for_wake("speak", "mind", "gpt-6-astra")[0] == "gpt-6-astra"


def test_edward_speak_model_on_claude_is_passed_as_named(monkeypatch):
    import services.mind_service as ms
    from services import llm_utils

    monkeypatch.setattr(llm_utils, "anthropic_enabled", lambda: True)
    monkeypatch.setenv("EDWARD_SPEAK_MODEL", "claude-sonnet-5-5")
    assert ms.route_for_wake("speak", "mind", "gpt-6-astra")[0] == "claude-sonnet-5-5"


# ------------------------------------------------------- legacy tuples --

def test_the_legacy_wake_tools_reach_people_and_owner():
    import services.mind_service as ms

    for name in ("send_imessage", "send_message", "stand_down", "send_push_notification", "leave_note",
                 "tell_owner", "remember", "close_intention", "wake_me", "say_on_screen"):
        assert name in ms.LEGACY_WAKE_TOOLS, name
    for name in ("forget_about", "set_goal", "update_goal", "list_goals", "suggest_idea"):
        assert name not in ms.LEGACY_WAKE_TOOLS, name


def test_the_quiet_tools_reach_nobody():
    import services.mind_service as ms

    for name in ("send_imessage", "send_message", "send_push_notification", "show_on_screen", "say_on_screen",
                 "update_widget", "stand_down", "schedule_event"):
        assert name not in ms.LEGACY_QUIET_TOOLS, name
    for name in ("leave_note", "tell_owner", "remember", "close_intention", "edit_document", "save_document"):
        assert name in ms.LEGACY_QUIET_TOOLS, name


def test_legacy_speak_tools_follow_the_channel():
    import services.mind_service as ms

    note = ms.legacy_speak_tools("note")
    assert "tell_owner" in note and "leave_note" in note
    assert not {"send_push_notification", "send_imessage", "send_message"} & set(note)
    assert "send_push_notification" in ms.legacy_speak_tools("push")
    thread = ms.legacy_speak_tools("thread")
    assert "send_imessage" in thread and "send_message" in thread


# ---------------------------------------------------------- the policy --

def test_default_policy_values_per_reason():
    import services.mind_service as ms

    v = ms.policy_values("manual", timeout_s=240)
    assert (v["source"], v["owner_asked"], v["may_reach_people"], v["may_push"]) == ("mind", False, False, True)
    assert v["deadline_s"] == 210
    v = ms.policy_values("nightly", timeout_s=600)
    assert (v["may_reach_people"], v["may_push"]) == (False, False)
    v = ms.policy_values("surfaces", timeout_s=240)
    assert (v["source"], v["may_reach_people"], v["may_push"]) == ("pulse", False, False)
    v = ms.policy_values("mention", reply_to=("chat-fam",), timeout_s=420)
    assert (v["may_reach_people"], v["may_push"], v["reply_to"]) == (True, True, ("chat-fam",))
    v = ms.policy_values("self", timeout_s=240)
    assert (v["may_reach_people"], v["may_push"], v["reply_to"]) == (False, True, ())
    v = ms.policy_values("speak", timeout_s=180)
    assert (v["may_reach_people"], v["may_push"]) == (False, False), "speak's policy is its caller's"


def test_legacy_tools_follow_the_policy():
    import services.mind_service as ms

    sends = {"send_imessage", "send_message", "send_sms", "send_whatsapp"}
    assert not sends & set(ms.legacy_tools_for("manual", ms.policy_values("manual", timeout_s=240)))
    assert "send_push_notification" in ms.legacy_tools_for("manual", ms.policy_values("manual", timeout_s=240))
    assert sends & set(ms.legacy_tools_for("mention", ms.policy_values("mention", timeout_s=420)))
    assert tuple(ms.legacy_tools_for("nightly", ms.policy_values("nightly", timeout_s=600))) == ms.LEGACY_QUIET_TOOLS
    no_push = ms.legacy_tools_for("self", {**ms.policy_values("self", timeout_s=240), "may_push": False})
    assert "send_push_notification" not in no_push


def test_make_policy_builds_turn_policy_when_area_2_is_there(monkeypatch):
    import services.mind_service as ms

    TurnPolicy = install_turn_policy(monkeypatch)
    p = ms.make_policy(ms.policy_values("mention", reply_to=("c1",), timeout_s=420))
    assert isinstance(p, TurnPolicy)
    assert p.source == "mind" and p.may_reach_people and p.reply_to == ("c1",) and p.deadline_s == 390


def test_make_policy_is_none_without_area_2(monkeypatch):
    import services.mind_service as ms

    hide_turn_policy(monkeypatch)
    assert ms.make_policy(ms.policy_values("manual", timeout_s=240)) is None


def test_policy_kwargs_pass_policy_only_when_accepted():
    import services.mind_service as ms

    async def with_policy(message, conversation_id, policy=None, allowed_tools=None):
        return ""

    async def without_policy(message, conversation_id, allowed_tools=None):
        return ""

    async def any_kwargs(**kw):
        return ""

    sentinel = object()
    assert ms._policy_kwargs(with_policy, sentinel, ("a",)) == {"policy": sentinel, "allowed_tools": ["a"]}
    assert ms._policy_kwargs(without_policy, sentinel, ("a",)) == {"allowed_tools": ["a"]}
    assert ms._policy_kwargs(any_kwargs, sentinel, ("a",))["policy"] is sentinel
    assert ms._policy_kwargs(with_policy, None, ("a",)) == {"allowed_tools": ["a"]}


async def test_a_wake_passes_its_policy_and_its_legacy_tools(monkeypatch):
    import services.mind_service as ms

    TurnPolicy = install_turn_policy(monkeypatch)
    calls = []

    async def chat(message, conversation_id, system_prompt, model, temperature, graph, policy=None,
                   allowed_tools=None, **kw):
        calls.append({"policy": policy, "allowed_tools": allowed_tools, **kw})
        return "Left it."

    _stub(monkeypatch, ms, chat)
    out = await ms.wake("manual")
    assert out["ok"]
    p = calls[0]["policy"]
    assert isinstance(p, TurnPolicy)
    assert (p.source, p.owner_asked, p.may_reach_people, p.may_push) == ("mind", False, False, True)
    assert p.deadline_s == ms.MIND_TIMEOUT_SECONDS - 30
    assert "send_imessage" not in calls[0]["allowed_tools"] and "send_push_notification" in calls[0]["allowed_tools"]


async def test_a_wake_works_with_a_loop_that_takes_no_policy(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    seen = []

    async def chat(message, conversation_id, system_prompt, model, temperature, graph, turn_source="mind",
                   reasoning_effort=None, nudge_for_tools=None, required_tools=None, allowed_tools=None,
                   max_history_chars=None, extraction_profile="chat", skip_extraction=False, memory_query=None,
                   max_tool_iterations=None):
        seen.append(allowed_tools)
        return "Left it."

    _stub(monkeypatch, ms, chat)
    out = await ms.wake("manual")
    assert out["ok"] and seen and seen[0]


async def test_an_explicit_policy_and_timeout_win(monkeypatch):
    import services.mind_service as ms

    TurnPolicy = install_turn_policy(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "ok"

    _stub(monkeypatch, ms, chat)
    mine = TurnPolicy(source="mind", owner_asked=False, may_reach_people=True, may_push=False, reply_to=("c7",),
                      deadline_s=60)
    await ms.wake("speak", message="[SPEAK] x", conversation_id=ms.SPEAK_CONVERSATION_ID, policy=mine,
                  timeout_s=90, skip_extraction=True, max_history_chars=0, tools=ms.legacy_speak_tools("thread"))
    assert calls[0]["policy"] is mine
    assert calls[0]["max_history_chars"] == 0


async def test_timeout_s_bounds_the_turn(monkeypatch):
    import services.mind_service as ms

    async def slow(**kw):
        await asyncio.sleep(5)
        return "late"

    recorded = _stub(monkeypatch, ms, slow)
    out = await ms.wake("speak", message="[SPEAK] x", conversation_id=ms.SPEAK_CONVERSATION_ID, timeout_s=0.05,
                        tools=ms.legacy_speak_tools("note"))
    assert not out["ok"] and "0.05" in out["note"]
    assert recorded and recorded[-1]["outcome"] == "failed"


async def test_a_mention_wake_replies_where_it_was_asked(monkeypatch):
    import services.mind_service as ms

    install_turn_policy(monkeypatch)
    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Answered Parker."

    pack = _pack(reason="mention", escalations=[
        {"id": "ev-1", "sender": "Parker", "chat": "Family", "chat_identifier": "chat-fam", "text": "@edward when?",
         "at": "2026-10-07T14:59:00"},
        {"id": "ev-2", "sender": "Sam", "chat": "Work", "chat_identifier": "chat-work", "text": "ok",
         "at": "2026-10-07T14:58:00"},
    ])
    _stub(monkeypatch, ms, chat, pack=pack)
    monkeypatch.setattr(ms, "_drop_handled_escalations", _const(False))
    monkeypatch.setattr(ms, "_mentions_waiting", _const({"ev-1"}))
    monkeypatch.setattr(ms, "_mark_considered", _const(2))
    await ms.wake("mention", required_tools=["send_imessage"], mention_ids=["ev-1"])
    p = calls[0]["policy"]
    assert p.reply_to == ("chat-fam",) and p.may_reach_people and p.may_push


async def test_a_manual_wake_is_not_served_the_escalations(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Left it."

    pack = _pack(escalations=[{"id": "ev-1", "sender": "Parker", "chat": "Family", "chat_identifier": "c1",
                               "text": "where are you", "at": "2026-10-07T14:59:00"}])
    _stub(monkeypatch, ms, chat, pack=pack)
    await ms.wake("manual")
    assert "where are you" not in calls[0]["message"]


async def test_wake_nightly_runs_in_its_own_thread(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Quiet night."

    _stub(monkeypatch, ms, chat)
    monkeypatch.setattr(ms, "nightly_has_material", _const(True))
    monkeypatch.setattr(ms, "_after_nightly", _const({}))
    await ms.wake("nightly")
    assert calls[0]["conversation_id"] == ms.NIGHTLY_CONVERSATION_ID
    assert calls[0]["max_history_chars"] == 48_000


# ------------------------------------------------------------- the shim --

def test_the_messages_shim_hands_inbound_to_the_noticer(monkeypatch):
    import services.mind_service as ms

    asked = []
    mod = types.ModuleType("services.noticing_service")
    mod.request_notice = lambda event_ids=None, **kw: asked.append(event_ids) or True
    fake_module(monkeypatch, "services.noticing_service", mod)
    assert ms.request_wake("messages") is True
    assert asked == [None]
    assert not ms._state.pending, "no wake is scheduled for messages any more"


async def test_the_shim_falls_back_to_storing_inbound_itself(monkeypatch):
    import services.mind_service as ms

    fake_module(monkeypatch, "services.noticing_service", None)
    stored = []

    async def fake_store():
        stored.append(True)
        return 3

    monkeypatch.setattr(ms, "store_unseen_inbound", fake_store)
    assert ms.request_wake("messages") is True
    for _ in range(10):
        await asyncio.sleep(0)
    assert stored == [True]
    assert not ms._state.pending


async def test_wake_messages_is_the_shim_too(monkeypatch):
    import services.mind_service as ms

    asked = []
    mod = types.ModuleType("services.noticing_service")
    mod.request_notice = lambda event_ids=None, **kw: asked.append(event_ids) or True

    async def never(**kw):
        raise AssertionError("no model turn for messages")

    fake_module(monkeypatch, "services.noticing_service", mod)
    _stub(monkeypatch, ms, never)
    out = await ms.wake("messages")
    assert out["ok"] and out["skipped"] and asked == [None]


def test_the_mention_pattern_matches_triage():
    import services.mind_service as ms

    assert ms.MENTION_RE.pattern == r"@edward\b"
    assert ms.MENTION_RE.search("hey @Edward can you") and not ms.MENTION_RE.search("edward")


def test_storable_skips_pings_mentions_and_owner_asking():
    import services.mind_service as ms

    def ev(**kw):
        return types.SimpleNamespace(**{"summary": "", "raw_data": None, "triage_status": "escalated", **kw})

    assert ms.storable_inbound(ev(summary="dinner at 7?"))
    assert not ms.storable_inbound(ev(summary="@edward dinner?"))
    assert not ms.storable_inbound(ev(summary="x", raw_data='{"full_text": "long ... @Edward what time"}'))
    assert not ms.storable_inbound(ev(summary="x", raw_data='{"ping": {"kind": "mention", "ping_id": 4}}'))
    assert not ms.storable_inbound(ev(summary="x", raw_data='{"owner_direct": true}'))
    assert not ms.storable_inbound(ev(summary="x", triage_status="ping"))


@pytest.mark.integration
async def test_store_unseen_inbound_stamps_only_what_no_one_must_answer(clean_db):
    from sqlalchemy import select

    import services.mind_service as ms
    from services.database import HeartbeatEventModel, LedgerEventModel, async_session

    old = datetime.utcnow() - timedelta(hours=30)
    async with async_session() as s:
        for i, (summary, raw, status, created) in enumerate([
            ("can you bring $20", None, "escalated", None),
            ("@edward what time is dinner", None, "escalated", None),
            ("hey", '{"owner_direct": true}', "escalated", None),
            ("ping row", '{"ping": {"kind": "mention", "ping_id": 1}}', "escalated", None),
            ("pinged", None, "ping", None),
            ("old one", None, "escalated", old),
            ("already", None, "dismissed", None),
        ]):
            kw = {"created_at": created} if created else {}
            s.add(HeartbeatEventModel(id=f"ev-{i}", source="imessage", event_type="message_received", sender="X",
                                      chat_identifier="c1", summary=summary, raw_data=raw, triage_status=status,
                                      **kw))
        await s.commit()
    n = await ms.store_unseen_inbound()
    assert n == 1
    async with async_session() as s:
        rows = {r.id: r for r in (await s.execute(select(HeartbeatEventModel))).scalars().all()}
        ledger = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "notice"))).scalars().all()
    assert rows["ev-0"].notified_at is not None and rows["ev-0"].notification_batch_id.startswith("stored:")
    for i in range(1, 7):
        assert rows[f"ev-{i}"].notification_batch_id is None, i
    assert len(ledger) == 1 and ledger[0].source == "stub" and ledger[0].outcome == "ok"


def test_the_daily_cap_inside_a_wake_is_a_failure_that_never_retries():
    """``LLMSpendCapReached`` in a speak or self wake (spec §3.3.6): the wake
    records ``failed``, not transient, and is not asked for again."""
    import services.mind_service as ms
    from services.llm_spend import LLMSpendCapReached

    assert ms.is_transient_error(LLMSpendCapReached("Today's budget is spent.")) is False
    out = {"ok": False, "transient": False, "note": "LLMSpendCapReached: Today's budget is spent."}
    assert ms.should_retry_wake("self", out, 0) is False
