"""``ledger_events`` — one narrow table for the things a health check
wants to see beyond turns: what the expression pulse decided, which
background callers the rate gate deferred, how scheduled events ended,
where the browser hands failed, and what a voice session was like.

Same retention as the turn ledger; rows are a few hundred bytes.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest

from services import event_ledger as el


# ---------------------------------------------------------------- pure --

def test_pulse_decision_changed_when_a_surface_tool_was_used():
    tools = [{"n": "get_widget_state_tool", "ok": True}, {"n": "update_widget", "ok": True}]
    outcome, reason = el.pulse_decision("Put the disc game on the widget.", tools)
    assert outcome == "changed"
    assert reason.startswith("Put the disc game")


def test_pulse_decision_left_alone_without_surface_tools():
    outcome, _ = el.pulse_decision("Nothing to change.", [{"n": "get_widget_state_tool", "ok": True}])
    assert outcome == "left_alone"
    assert el.pulse_decision("x", [])[0] == "left_alone"


def test_surfaces_touched_lists_each_surface_once():
    tools = [{"n": "update_widget"}, {"n": "show_on_screen"}, {"n": "update_widget"}, {"n": "web_search"}]
    assert el.surfaces_touched(tools) == ["screen", "widget"]


def test_the_stage_tools_touch_the_screen():
    """The stage (2026-09-28): setting a scene or saying a line is a pulse that changed something."""
    assert el.surfaces_touched([{"n": "compose_screen"}, {"n": "say_on_screen"}]) == ["screen"]
    assert el.pulse_decision("Set the scene.", [{"n": "compose_screen", "ok": True}])[0] == "changed"
    assert el.pulse_decision("Said hi.", [{"n": "say_on_screen", "ok": True}])[0] == "changed"


def test_delivered_detects_any_outbound_tool():
    assert el.delivered([{"n": "web_search", "ok": True}, {"n": "send_push_notification", "ok": True}])
    assert el.delivered([{"n": "send_imessage", "ok": True}])
    assert not el.delivered([{"n": "send_imessage", "ok": False}])  # a failed send delivered nothing
    assert not el.delivered([{"n": "remember_update", "ok": True}])
    assert not el.delivered([])


def test_pick_caller_skips_llm_plumbing_and_names_the_job():
    frames = [
        ("services.llm_rate_gate", "acquire"),
        ("services.codex_chat_model", "_agenerate"),
        ("langchain_core.language_models.chat_models", "agenerate"),
        ("services.reflection_service", "generate_queries"),
        ("services.graph.streaming", "_spawn_post_turn_work"),
    ]
    assert el.pick_caller(frames) == "reflection_service.generate_queries"
    assert el.pick_caller([("asyncio.events", "_run")]) == "unknown"
    assert el.pick_caller([("routers.chat", "chat_simple")]) == "routers.chat.chat_simple"


def test_bounded_detail_caps_size():
    text = el.bounded_detail({"a": "x" * 5000, "b": 1})
    assert len(text) <= el.MAX_DETAIL_CHARS
    assert el.bounded_detail(None) is None


def test_voice_stats_start_at_zero():
    s = el.new_voice_stats()
    assert s == {"turns": 0, "bridges": 0, "barge_ins": 0, "interrupts": 0, "restarts": 0, "errors": 0}


# ------------------------------------------------------------- wiring --

def test_rate_gate_deferral_is_recorded(monkeypatch):
    from services.llm_rate_gate import LLMRateGate, LLMRateLimitDeferred

    recorded = []
    monkeypatch.setattr(el, "record_event", lambda **kw: recorded.append(kw))

    class Clock:
        now = 1000.0
        def time(self): return self.now
        async def sleep(self, s): self.now += s

    clock = Clock()
    gate = LLMRateGate(clock=clock.time, sleep=clock.sleep, wall_clock=clock.time)
    gate.note_429('{"error": {"message": "usage limit", "resets_in_seconds": 600}}')
    with pytest.raises(LLMRateLimitDeferred):
        asyncio.run(gate.acquire("background"))
    assert recorded and recorded[0]["kind"] == "rate_gate" and recorded[0]["outcome"] == "deferred"
    assert "cooldown" in recorded[0]["reason"]
    assert recorded[0]["source"]  # the caller label, never empty


def test_browser_failure_reply_is_recorded(monkeypatch):
    from services.graph import tools as gtools

    recorded = []
    monkeypatch.setattr(el, "record_event", lambda **kw: recorded.append(kw))
    reply = gtools._hands_reply(RuntimeError("page did not settle within 8s (tab 41)"), "Clicking [3]")
    assert reply.startswith("Clicking [3] failed")
    assert recorded[0]["kind"] == "browser" and recorded[0]["source"] == "click"
    assert recorded[0]["outcome"] == "failed" and "settle" in recorded[0]["reason"]


def test_scheduler_records_event_outcomes():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "services" / "scheduler_service.py").read_text()
    assert 'kind="scheduled_event"' in text and '"completed"' in text and '"failed"' in text


def test_pulse_records_its_decision():
    from pathlib import Path

    text = (Path(__file__).resolve().parent.parent / "services" / "expression_service.py").read_text()
    assert "pulse_decision(" in text and 'kind="decision"' in text


# ---------------------------------------------------------- integration --

@pytest.mark.integration
async def test_record_event_now_writes_row_and_prune_clears_old(clean_db):
    from sqlalchemy import select, func
    from services.database import async_session, LedgerEventModel
    from services import turn_ledger as tl

    await el.record_event_now(kind="scheduled_event", source="scheduler", outcome="completed",
                              reason=None, ref_id="ev-1", detail={"attempts": 1, "delivered": True})
    async with async_session() as s:
        row = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.ref_id == "ev-1"))).scalar_one()
        assert row.kind == "scheduled_event" and '"delivered":true' in row.detail_json
        s.add(LedgerEventModel(kind="rate_gate", source="x", outcome="deferred",
                               created_at=datetime.utcnow() - timedelta(days=tl.RETENTION_DAYS + 1)))
        await s.commit()

    deleted = await tl.prune()
    assert deleted["ledger_events"] == 1
    async with async_session() as s:
        n = (await s.execute(select(func.count()).select_from(LedgerEventModel))).scalar_one()
    assert n == 1


# ------------------------------------------------- caller label fixes --

def test_background_llm_carries_its_purpose_and_gate_uses_it(monkeypatch):
    """The gate cannot see who asked from inside a langchain task's stack
    (the first two days recorded 372 deferrals, every one 'unknown'), so
    the model built by build_background_llm remembers its caller and hands
    it to the gate."""
    from services.llm_rate_gate import LLMRateGate, LLMRateLimitDeferred

    recorded = []
    monkeypatch.setattr(el, "record_event", lambda **kw: recorded.append(kw))

    class Clock:
        now = 1000.0
        def time(self): return self.now
        async def sleep(self, s): self.now += s

    clock = Clock()
    gate = LLMRateGate(clock=clock.time, sleep=clock.sleep, wall_clock=clock.time)
    gate.note_429('{"error": {"message": "usage limit", "resets_in_seconds": 600}}')
    with pytest.raises(LLMRateLimitDeferred):
        asyncio.run(gate.acquire("background", purpose="memory_service.extract_and_store_memories"))
    assert recorded[0]["source"] == "memory_service.extract_and_store_memories"


def test_build_background_llm_stamps_purpose_from_its_caller(monkeypatch):
    from services import llm_utils

    class FakeManager:
        _auth_source = "oauth"
        def get_api_key(self): return "tok"

    monkeypatch.setattr("services.codex_auth_service.get_codex_auth_manager", lambda: FakeManager())
    llm = llm_utils.build_background_llm()
    assert "test_event_ledger." in getattr(llm, "purpose", "")
