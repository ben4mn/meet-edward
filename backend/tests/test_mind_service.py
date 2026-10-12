"""The wake — mind rebuild Phase 2 (docs/mind-rebuild-plan-2026-09-18.md).

One primitive replaces every judgment call: ``mind_service.wake(reason)``
runs a real Edward turn in the ``edward-mind`` thread with a lean tool set
and a raw pack, records what it decided in ``ledger_events``, and never
raises. Reachable by hand (``POST /api/mind/wake``) and by the ``wake_me``
tool until Phase 3 wires the ticks to it.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest


NOW = datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc)


def _pack(**kw):
    from services.mind_service import MindPack

    base = dict(
        now=NOW,
        reason="manual",
        last_wake_at=None,
        attention={"owner_present": True, "active_surface": "mac:mini"},
        messages=[
            {"sender": "Parker", "chat_identifier": "chat1", "text": "the climbing gym is closed Saturday",
             "is_from_me": False,
             "timestamp": "2026-09-18T13:02:00"},
            {"sender": None, "chat_identifier": "chat1", "text": "ugh, there go my plans", "is_from_me": True,
             "timestamp": "2026-09-18T13:05:00"},
        ],
        calendar=["15:00 1:1 with Sam"],
        board="Biscuit: bowls AM ✓ PM –",
        notes="",
        pushes="(nothing pushed to Ellis recently)",
        verdicts=[],
        thoughts=[],
        actions=[],
        surfaces={"widget": "default", "house_screen": "its normal pages", "mini_screen": "the stock scene"},
        escalations=[],
        state_document=None,
        extra=None,
    )
    base.update(kw)
    return MindPack(**base)


# ---------------------------------------------------------------------------
# The pack is raw
# ---------------------------------------------------------------------------


def test_render_pack_is_raw_and_in_order():
    from services.mind_service import render_pack

    text = render_pack(_pack())
    assert text.startswith("[WAKE: manual]")
    assert "the climbing gym is closed Saturday" in text and "ugh, there go my plans" in text
    assert text.index("the climbing gym is closed") < text.index("ugh, there go my plans")
    for banned in ("score", "boost", "priority", "suppress", "affinity", "threshold"):
        assert banned not in text.lower(), banned
    assert "15:00 1:1 with Sam" in text
    assert "Biscuit: bowls" in text


def test_render_pack_names_what_changed_since_last_wake():
    from services.mind_service import render_pack

    text = render_pack(_pack(last_wake_at=NOW - timedelta(hours=2)))
    assert "Since your last wake (2h ago): 2 new messages" in text
    text = render_pack(_pack(last_wake_at=None))
    assert "first wake since a restart" in text


def test_render_pack_lists_thoughts_actions_and_verdicts_with_ids():
    from services.memory_service import Memory
    from services.mind_service import render_pack

    thought = Memory(id="m-1", content="Watching the gym's hours for Ellis's Saturday climb", memory_type="intention",
                     importance=0.7,
                     origin="thought", about="self", valid_until=datetime(2026, 10, 18))
    did = Memory(id="m-2", content="Pushed Ellis about the gym closure; he opened it", memory_type="context",
                 importance=0.5, origin="did", about="self")
    text = render_pack(_pack(thoughts=[thought], actions=[did],
                             verdicts=[{"kind": "heartbeat", "verdict": "should_have_told_me",
                                        "note": "Parker's flight", "at": "2026-09-18T12:00:00Z"}]))
    assert "Open intentions (1):" in text and "Recent thoughts (0):" in text
    assert "(memory_id: m-1)" in text and "until 2026-10-18" in text
    assert "What you did lately" in text and "(memory_id: m-2)" in text
    assert "Ellis's verdicts since your last wake" in text and "should_have_told_me" in text


def test_render_pack_reason_specific_extra_and_escalations():
    from services.mind_service import render_pack

    text = render_pack(_pack(reason="self", extra="You asked to be woken to check the gym's hours at five.",
                             escalations=[{"id": "ev-1", "sender": "Rowan", "chat": "chat9",
                                           "text": "did Biscuit get her pill?", "at": "2026-09-18T14:10:00"}]))
    assert "[WAKE: self]" in text
    assert "You asked to be woken to check the gym's hours" in text
    assert "Waiting on you (1)" in text and "ev-1" in text and "did Biscuit get her pill?" in text


def test_render_pack_is_bounded():
    from services.mind_service import MAX_MESSAGES_IN_PACK, render_pack

    many = [{"sender": f"P{i}", "chat_identifier": "c", "text": f"msg {i}", "is_from_me": False} for i in range(200)]
    text = render_pack(_pack(messages=many))
    assert "msg 199" in text and "msg 0" not in text
    assert text.count("\n  P") <= MAX_MESSAGES_IN_PACK


# ---------------------------------------------------------------------------
# Outcome classification (pure)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tools,expected",
    [
        ([], "left_alone"),
        ([{"n": "remember_search", "ok": True}], "left_alone"),
        ([{"n": "web_search", "ok": True}, {"n": "read_document", "ok": True}], "left_alone"),
        ([{"n": "remember", "ok": True}], "acted"),
        ([{"n": "send_push_notification", "ok": True}], "acted"),
        ([{"n": "wake_me", "ok": True}], "acted"),
        ([{"n": "send_push_notification", "ok": False}], "left_alone"),
    ],
)
def test_wake_outcome(tools, expected):
    from services.mind_service import wake_outcome

    assert wake_outcome(tools) == expected


# ---------------------------------------------------------------------------
# The turn
# ---------------------------------------------------------------------------


class _S:
    model = "gpt-6-astra"
    temperature = 0.4
    system_prompt = "be edward"
    provider = "openai"


def _const(v):
    async def f(*a, **k):
        return v

    return f


def _stub(monkeypatch, ms, chat):
    monkeypatch.setattr(ms, "collect_pack", _const(_pack()))
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    recorded = []

    async def _rec(**kw):
        recorded.append(kw)
        return 1

    monkeypatch.setattr(ms, "_record_now", _rec)
    ms.reset_state()
    return recorded


async def test_wake_runs_one_turn_in_the_mind_thread(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Left everything alone; nothing needs me."

    recorded = _stub(monkeypatch, ms, chat)
    out = await ms.wake("manual")
    assert out["ok"] is True and out["note"].startswith("Left everything alone")
    kw = calls[0]
    assert kw["conversation_id"] == ms.MIND_CONVERSATION_ID == "edward-mind"
    assert kw["allowed_tools"] == list(ms.legacy_tools_for("manual", ms.policy_values("manual", timeout_s=240)))
    assert kw["max_history_chars"] == ms.MIND_MAX_HISTORY_CHARS == 24_000
    assert kw["extraction_profile"] == "mind"
    assert kw["turn_source"] == "mind"
    assert kw["system_prompt"].startswith(ms.MIND_SYSTEM_PROMPT)
    assert kw["message"].startswith("[WAKE: manual]")
    assert kw.get("required_tools") is None
    assert recorded and recorded[0]["outcome"] == "left_alone" and recorded[0]["source"] == "manual"


async def test_wake_passes_required_tools_and_extra_through(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "Replied to Parker."

    _stub(monkeypatch, ms, chat)
    captured = {}

    async def collect(reason, *, now=None, extra=None):
        captured["extra"] = extra
        return _pack(reason=reason, extra=extra)

    monkeypatch.setattr(ms, "collect_pack", collect)
    await ms.wake("mention", extra="Parker tagged you in chat1", required_tools=["send_imessage"])
    assert calls[0]["required_tools"] == ["send_imessage"]
    assert captured["extra"] == "Parker tagged you in chat1"
    assert "Parker tagged you" in calls[0]["message"]


async def test_wake_failure_is_recorded_and_swallowed(monkeypatch):
    import services.mind_service as ms

    async def boom(**kw):
        raise RuntimeError("codex 401")

    recorded = _stub(monkeypatch, ms, boom)
    out = await ms.wake("manual")
    assert out["ok"] is False and "codex 401" in out["note"]
    assert recorded[0]["outcome"] == "failed"


async def test_wake_skips_when_quota_exhausted(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        raise AssertionError("no turn when the quota is exhausted")

    recorded = _stub(monkeypatch, ms, chat)
    monkeypatch.setattr(ms, "quota_state", lambda: "exhausted")
    out = await ms.wake("morning")
    assert out["ok"] is True and out["skipped"] is True
    assert recorded[0]["outcome"] == "skipped"


async def test_wake_is_serialised(monkeypatch):
    import services.mind_service as ms

    running = {"now": 0, "max": 0}

    async def chat(**kw):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.05)
        running["now"] -= 1
        return "ok"

    _stub(monkeypatch, ms, chat)
    await asyncio.gather(ms.wake("a"), ms.wake("b"))
    assert running["max"] == 1


async def test_request_wake_coalesces(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def fake_wake(reason, **kw):
        calls.append(reason)
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    ms.reset_state()
    ms.request_wake("mention", delay_s=0.05)
    ms.request_wake("mention", delay_s=0.05)
    ms.request_wake("mention", delay_s=0.05)
    await asyncio.sleep(0.2)
    assert calls == ["mention"]


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_wake_records_a_ledger_row(clean_db, monkeypatch):
    from sqlalchemy import select

    import services.mind_service as ms
    from services.database import LedgerEventModel, async_session

    async def chat(**kw):
        return "Nothing needs me right now."

    monkeypatch.setattr(ms, "collect_pack", _const(_pack()))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    ms.reset_state()
    await ms.wake("manual")
    for _ in range(20):
        await asyncio.sleep(0.02)
    async with async_session() as s:
        rows = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "mind"))).scalars().all()
    assert len(rows) == 1
    assert rows[0].source == "manual" and rows[0].outcome == "left_alone"
    assert rows[0].reason.startswith("Nothing needs me")

    recent = await ms.recent(limit=5)
    assert recent and recent[0]["outcome"] == "left_alone"


@pytest.mark.integration
async def test_ensure_conversation_creates_the_mind_thread(clean_db):
    import services.mind_service as ms
    from services.conversation_service import get_conversation

    await ms._ensure_conversation()
    conv = await get_conversation(ms.MIND_CONVERSATION_ID)
    assert conv is not None and conv.source == "edward_thought"


@pytest.mark.integration
async def test_collect_pack_carries_thoughts_actions_and_verdicts(clean_db):
    import services.mind_service as ms
    from services.heartbeat_feedback_service import record_feedback
    from services.database import HeartbeatEventModel, async_session
    from services.memory_service import Memory, store_memory

    await store_memory(Memory(id=None, content="Watching the gym's hours for Ellis's Saturday climb",
                              memory_type="intention",
                              importance=0.7, origin="thought", about="self"))
    await store_memory(Memory(id=None, content="Pushed Ellis about the gym at 16:35", memory_type="context",
                              importance=0.5, origin="did", about="self"))
    await store_memory(Memory(id=None, content="Ellis's dog is Biscuit", memory_type="fact", importance=0.8))
    async with async_session() as s:
        s.add(HeartbeatEventModel(id="ev-1", source="imessage", event_type="message", sender="Parker",
                                  summary="flight moved", triage_status="dismissed"))
        await s.commit()
    await record_feedback("ev-1", "should_have_told_me", note="that was a real change")

    ms.reset_state()  # an earlier test's in-process last_wake_at would hide the verdict
    pack = await ms.collect_pack("manual")
    assert [m.content for m in pack.thoughts] == ["Watching the gym's hours for Ellis's Saturday climb"]
    assert [m.content for m in pack.actions] == ["Pushed Ellis about the gym at 16:35"]
    assert pack.verdicts and pack.verdicts[0]["verdict"] == "should_have_told_me"
    text = ms.render_pack(pack)
    assert "Ellis's dog is Biscuit" not in text, "facts about Ellis come through retrieval, not the pack"


@pytest.mark.integration
async def test_leave_note_tool_writes_a_mind_note(clean_db):
    from services.graph.tools import leave_note
    from services.notes_service import recent_notes

    out = await leave_note.ainvoke({"title": "Gym", "body": "Closed Saturday; watching for the reopening.",
                                    "needs_you": False})
    assert out == "ok"
    notes = await recent_notes()
    assert notes and notes[0].kind == "mind" and notes[0].title == "Gym"


@pytest.mark.integration
async def test_wake_me_tool_schedules_a_mind_wake(clean_db):
    import json

    from services.graph.tools import wake_me
    from services.scheduled_events_service import list_events

    out = await wake_me.ainvoke({"when": "2099-01-01T16:30:00", "why": "check the gym's hours at five"})
    assert out.startswith("ok"), out
    events = await list_events(status="pending")
    assert len(events) == 1
    ev = events[0]
    meta = json.loads(ev.event_metadata)
    assert meta == {"type": "mind_wake", "why": "check the gym's hours at five"}
    assert ev.conversation_id == "edward-mind"
    assert "check the gym's hours" in ev.description


async def test_wake_me_rejects_the_past_and_garbage():
    from services.graph.tools import wake_me

    assert (await wake_me.ainvoke({"when": "2001-01-01T00:00:00", "why": "x"})).startswith("Tool error:")
    assert (await wake_me.ainvoke({"when": "tomorrowish", "why": "x"})).startswith("Tool error:")


@pytest.mark.integration
async def test_scheduler_routes_mind_wake_events_to_the_mind(clean_db, monkeypatch):
    import json

    from services import scheduler_service
    from services.database import ScheduledEventModel, async_session
    from services.scheduled_events_service import _utcnow

    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, kw.get("extra")))
        return {"ok": True, "note": "checked"}

    monkeypatch.setattr("services.mind_service.wake", fake_wake)
    async with async_session() as s:
        ev = ScheduledEventModel(id="ev-mind", description="[mind wake] check the gym's hours", scheduled_at=_utcnow(),
                                 next_fire_at=_utcnow(), status="processing", conversation_id="edward-mind",
                                 event_metadata=json.dumps({"type": "mind_wake", "why": "check the gym's hours"}))
        s.add(ev)
        await s.commit()
        await s.refresh(ev)
    await scheduler_service._execute_event(ev)
    assert calls == [("self", "check the gym's hours")]
    async with async_session() as s:
        row = await s.get(ScheduledEventModel, "ev-mind")
    assert row.status == "completed"


# ---------------------------------------------------------------------------
# The stage (2026-09-28): the mind may say a line on this Mac's screen;
# composing the scene is the pulse's.
# ---------------------------------------------------------------------------


def test_the_mind_can_say_a_line_on_the_screen_but_not_set_the_scene():
    import services.mind_service as ms

    assert "say_on_screen" in ms.LEGACY_WAKE_TOOLS
    assert "compose_screen" not in ms.LEGACY_WAKE_TOOLS
    assert "say_on_screen" not in ms.LEGACY_QUIET_TOOLS
    assert {"compose_screen", "say_on_screen"} <= ms.ACTING_TOOLS
    assert "set_screen_layout" not in ms.ACTING_TOOLS


def test_the_pack_falls_back_to_the_stock_scene():
    from services.mind_service import render_pack

    text = render_pack(_pack(surfaces={}))
    assert "This Mac's screen now: the stock scene" in text


def test_the_judgement_check_keeps_the_screen_out():
    """It runs the real model with every tool bound (AREA 2's one toolset):
    the eval guards fake Chrome and refuse osascript/open/screencapture,
    so a line on the real screen is never part of a check (2026-10-11)."""
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "scripts" / "judgement_eval.py").read_text()
    assert "install_eval_guards()" in src and 'eval_guards.install_for_eval("judgement")' in src
