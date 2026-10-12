"""The pulse is a surfaces wake — mind rebuild Phase 10.

One primitive, not two. ``run_expression_pulse`` keeps its signals, its
change-driven cadence and its own ledger rows (kind ``decision``, source
``pulse``) so the pulse plan's targets stay measurable, but the turn
itself is ``mind_service.wake("surfaces", ...)`` with the surface tool
profile in the ``edward-surfaces`` thread. ``expression_service`` no
longer calls ``chat_with_memory`` at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from services import expression_service as es
from tests.test_expression_service import _S, _const, _signals


def test_one_wake_implementation():
    src = (Path(__file__).resolve().parents[1] / "services" / "expression_service.py").read_text()
    assert "chat_with_memory(" not in src
    assert "from services.graph import chat_with_memory" not in src


def _stub_pulse(monkeypatch, ms, chat, signals=None):
    es.reset_pulse_state()
    monkeypatch.setattr(es, "collect_signals", _const(signals or _signals()))
    monkeypatch.setattr(es, "_ensure_conversation", _const(None))
    monkeypatch.setattr(es, "recent_decisions", _const([]))
    monkeypatch.setattr(es, "_quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")


async def test_surfaces_wake_uses_the_pulse_tools_and_thread(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        # One lock per thread since 2026-09-24: the surfaces wake holds its
        # own thread's lock and leaves the mind's free for a mention.
        assert ms.thread_lock(es.EXPRESSION_CONVERSATION_ID).locked(), "a surfaces wake holds its thread's lock"
        assert not ms.thread_lock(ms.MIND_CONVERSATION_ID).locked(), "and not the mind's"
        return "Put the dinner reminder on the widget."

    _stub_pulse(monkeypatch, ms, chat)
    out = await es.run_expression_pulse("manual")
    assert out["ok"] and "dinner" in out["note"].lower()
    kw = calls[0]
    assert kw["conversation_id"] == es.EXPRESSION_CONVERSATION_ID
    assert kw["allowed_tools"] == list(es.LEGACY_PULSE_TOOLS)
    assert kw["system_prompt"].startswith(es.EXPRESSION_SYSTEM_PROMPT)
    assert kw["turn_source"] == "pulse" and kw["skip_extraction"] is True
    assert kw["max_history_chars"] == es.PULSE_MAX_HISTORY_CHARS
    assert kw.get("required_tools") is None
    assert "Dinner at 7?" in kw["message"]


async def test_surfaces_wake_keeps_the_pulse_ledger_not_the_minds(monkeypatch):
    import services.mind_service as ms
    from services import event_ledger

    async def chat(**kw):
        return "Left it alone."

    recorded, mind_rows = [], []
    _stub_pulse(monkeypatch, ms, chat)
    # The mind's state is module-level; an earlier test's wake must not
    # stand in for this one's precondition.
    monkeypatch.setattr(ms._state, "last_wake_at", None)
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: recorded.append(kw))

    async def mind_record(**kw):
        mind_rows.append(kw)
        return 1

    monkeypatch.setattr(ms, "_record_now", mind_record)
    await es.run_expression_pulse("manual")
    assert mind_rows == [], "the pulse's rows stay kind=decision/source=pulse"
    assert recorded and recorded[-1]["kind"] == "decision" and recorded[-1]["source"] == "pulse"
    assert recorded[-1]["outcome"] == "left_alone"
    assert ms._state.last_wake_at is None, "a surfaces turn is not the mind's last wake"


async def test_surfaces_wake_failure_falls_back_to_the_composed_card(monkeypatch):
    import services.mind_service as ms
    from services import event_ledger

    async def boom(**kw):
        raise RuntimeError("codex 401")

    fallback, recorded = [], []

    async def fake_refresh(reason, **kw):
        fallback.append(reason)
        return {}

    _stub_pulse(monkeypatch, ms, boom)
    monkeypatch.setattr(es, "refresh_widget_expression", fake_refresh)
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: recorded.append(kw))
    out = await es.run_expression_pulse("cron")
    assert out["ok"] is False and fallback == ["pulse-fallback"]
    assert recorded[-1]["outcome"] == "failed" and recorded[-1]["reason"].startswith("RuntimeError")


async def test_wake_with_a_message_skips_the_mind_pack(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "ok"

    async def no_pack(*a, **k):
        raise AssertionError("a caller that brings its own message needs no pack")

    monkeypatch.setattr(ms, "collect_pack", no_pack)
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    ms.reset_state()
    out = await ms.wake("surfaces", message="hello", conversation_id="thread-x", tools=("update_widget",),
                        system_prompt="S", turn_source="pulse", skip_extraction=True, record=False)
    assert out["ok"] and out["note"] == "ok"
    kw = calls[0]
    assert kw["message"] == "hello" and kw["conversation_id"] == "thread-x"
    assert kw["allowed_tools"] == ["update_widget"] and kw["system_prompt"].startswith("S")


# ---------------------------------------------------------------------------
# The signals are bounded (2026-09-24, plan C1)
# ---------------------------------------------------------------------------


async def test_hung_signals_fail_the_pulse_on_its_deadline(monkeypatch):
    import asyncio
    import time

    import services.mind_service as ms
    from services import event_ledger

    async def hangs(**kw):
        await asyncio.sleep(3600)

    async def chat(**kw):
        raise AssertionError("no turn without signals")

    fallback, recorded = [], []

    async def fake_refresh(reason, **kw):
        fallback.append(reason)
        return {}

    _stub_pulse(monkeypatch, ms, chat)
    monkeypatch.setattr(es, "collect_signals", hangs)
    monkeypatch.setattr(es, "SIGNALS_TIMEOUT_SECONDS", 0.1)
    monkeypatch.setattr(es, "refresh_widget_expression", fake_refresh)
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: recorded.append(kw))
    t0 = time.monotonic()
    out = await es.run_expression_pulse("cron")
    assert time.monotonic() - t0 < 1.0
    assert out["ok"] is False and "signals" in out["note"].lower()
    assert recorded[-1]["outcome"] == "failed" and recorded[-1]["reason"] == "SignalsTimeout"
    assert fallback == ["pulse-fallback"]


@pytest.mark.integration
async def test_a_hung_signal_source_is_cut_off_on_its_own_bound(clean_db, monkeypatch):
    import asyncio
    import time

    async def hangs(*a, **k):
        await asyncio.sleep(3600)

    monkeypatch.setattr("services.screen_service.get_snapshot", hangs)
    monkeypatch.setattr("services.display_service.get_calendar", hangs)
    monkeypatch.setattr(es, "SIGNAL_SOURCE_TIMEOUT_SECONDS", 0.2)
    t0 = time.monotonic()
    sig = await es.collect_signals()
    assert time.monotonic() - t0 < 3.0
    assert sig.mini_screen is None and sig.calendar == []
