"""Edward's own live state — ``services/wake_state.py`` and ``GET /api/mind/now``
(2026-09-25).

The design system says Edward's animation is a claim, so it must be true:
"never 'working' when nothing runs; never 'fine' when his hands are down".
The PWA's live Edward reads one cheap endpoint for it: whether a wake (or
the surfaces pulse's turn) is running right now, what his last wakes did,
when he wakes next, what waits on Ellis (held approvals, unread thoughts),
the quota meter and his hands in Chrome. Not ``/api/presence`` — that is
Ellis's device beacons.

Every source is isolated: one that fails degrades to null or 0, never a 500.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

NOW = datetime(2026, 9, 25, 21, 30, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_wake_state():
    from services import wake_state

    wake_state.reset()
    yield
    wake_state.reset()


# ---------------------------------------------------------------------------
# wake_state: begin / end / current
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_begin_end_current():
    from services import wake_state

    assert wake_state.current() is None
    token = wake_state.begin("morning", "edward-mind", now=NOW)
    assert wake_state.current() == {"reason": "morning", "started_at": "2026-09-25T21:30:00Z",
                                    "conversation_id": "edward-mind"}
    wake_state.end(token)
    assert wake_state.current() is None
    wake_state.end(token)  # ending twice is harmless


@pytest.mark.unit
def test_a_mind_wake_is_shown_over_the_pulse():
    from services import wake_state

    pulse = wake_state.begin("surfaces", "edward-surfaces", now=NOW)
    assert wake_state.current()["reason"] == "surfaces"
    mind = wake_state.begin("mention", "edward-mind", now=NOW + timedelta(seconds=5))
    assert wake_state.current()["reason"] == "mention"
    wake_state.end(mind)
    assert wake_state.current()["reason"] == "surfaces"
    wake_state.end(pulse)
    assert wake_state.current() is None


@pytest.mark.unit
async def test_end_without_a_token_ends_this_tasks_own_wake():
    from services import wake_state

    started = asyncio.Event()
    release = asyncio.Event()

    async def other():
        wake_state.begin("free", "edward-free-time", now=NOW)
        started.set()
        await release.wait()
        wake_state.end()

    task = asyncio.ensure_future(other())
    await started.wait()
    mine = wake_state.begin("morning", "edward-mind", now=NOW + timedelta(seconds=1))
    wake_state.end()  # this task's own — not the other task's
    assert wake_state.current()["reason"] == "free"
    release.set()
    await task
    assert wake_state.current() is None
    wake_state.end(mine)


@pytest.mark.unit
async def test_a_wake_and_the_pulse_turn_say_they_are_waking(monkeypatch):
    import services.mind_service as ms
    from services import wake_state
    from tests.test_mind_service import _S, _const, _pack

    seen = []

    async def chat(**kw):
        seen.append(wake_state.current())
        return "Quiet."

    monkeypatch.setattr(ms, "collect_pack", _const(_pack()))
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    monkeypatch.setattr(ms, "_record_now", _const(1))
    ms.reset_state()
    await ms.wake("evening")
    await ms.wake("surfaces", message="pulse", conversation_id="edward-surfaces", tools=("update_widget",),
                  system_prompt="S", turn_source="pulse", skip_extraction=True, record=False)
    ms.reset_state()
    assert [(w["reason"], w["conversation_id"]) for w in seen] == [("evening", "edward-mind"),
                                                                   ("surfaces", "edward-surfaces")]
    assert wake_state.current() is None


@pytest.mark.unit
async def test_a_failing_wake_still_ends_its_waking(monkeypatch):
    import services.mind_service as ms
    from services import wake_state
    from tests.test_mind_service import _S, _const, _pack

    async def boom(**kw):
        raise RuntimeError("a bug")

    monkeypatch.setattr(ms, "collect_pack", _const(_pack()))
    monkeypatch.setattr(ms, "_ensure_conversation", _const(None))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", boom)
    monkeypatch.setattr(ms, "_record_now", _const(1))
    ms.reset_state()
    out = await ms.wake("manual")
    ms.reset_state()
    assert out["ok"] is False and wake_state.current() is None


# ---------------------------------------------------------------------------
# Pure assembly
# ---------------------------------------------------------------------------


def _row(minutes_ago, source, outcome, note, thought_id=None):
    at = (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None).isoformat() + "Z"
    row = {"id": minutes_ago, "at": at, "source": source, "outcome": outcome, "note": note}
    if thought_id:
        row["thought_id"] = thought_id
    return row


@pytest.mark.unit
def test_compose_now_is_the_documented_shape():
    from services import wake_state

    out = wake_state.compose_now(
        now=NOW,
        waking={"reason": "mention", "started_at": "2026-09-25T21:29:40Z", "conversation_id": "edward-mind"},
        recent=[_row(12, "messages", "acted", "Replied to Parker about the flight.", "t-parker"),
                _row(70, "free", "left_alone", ""),
                _row(130, "evening", "failed", "ValueError: HTTP 503"),
                _row(200, "self", "skipped", "Codex quota exhausted"),
                _row(260, "morning", "acted", "Left a note on the vet."),
                _row(300, "manual", "left_alone", "sixth")],
        next_wake_at=datetime(2026, 9, 26, 12, 0),  # naive UTC from the scheduled_events table
        approvals_waiting=2,
        unread_thoughts=3,
        quota={"used_percent": 41.0, "exhausted": False, "resets_at": NOW + timedelta(hours=5)},
        hands={"connected": False, "down_for_s": 95},
    )
    assert out == {
        "now": "2026-09-25T21:30:00Z",
        "waking": {"reason": "mention", "started_at": "2026-09-25T21:29:40Z", "conversation_id": "edward-mind"},
        "last": {"at": "2026-09-25T21:18:00Z", "reason": "messages", "outcome": "acted",
                 "line": "Replied to Parker about the flight.", "thought_id": "t-parker"},
        "recent": [
            {"at": "2026-09-25T21:18:00Z", "reason": "messages", "outcome": "acted",
             "line": "Replied to Parker about the flight.", "thought_id": "t-parker"},
            {"at": "2026-09-25T20:20:00Z", "reason": "free", "outcome": "left_alone", "line": None,
             "thought_id": None},
            {"at": "2026-09-25T19:20:00Z", "reason": "evening", "outcome": "failed", "line": "ValueError: HTTP 503",
             "thought_id": None},
            {"at": "2026-09-25T18:10:00Z", "reason": "self", "outcome": "skipped", "line": "Codex quota exhausted",
             "thought_id": None},
            {"at": "2026-09-25T17:10:00Z", "reason": "morning", "outcome": "acted", "line": "Left a note on the vet.",
             "thought_id": None},
        ],
        "next_wake_at": "2026-09-26T12:00:00Z",
        "approvals_waiting": 2,
        "unread_thoughts": 3,
        "quota": {"used_percent": 41.0, "exhausted": False, "resets_at": "2026-09-26T02:30:00Z", "period": "week"},
        "hands": {"connected": False, "down_for_s": 95},
    }


@pytest.mark.unit
def test_compose_now_with_nothing_readable():
    from services import wake_state

    out = wake_state.compose_now(now=NOW, waking=None, recent=None, next_wake_at=None, approvals_waiting=None,
                                 unread_thoughts=None, quota=None, hands=None)
    assert out == {
        "now": "2026-09-25T21:30:00Z", "waking": None, "last": None, "recent": [], "next_wake_at": None,
        "approvals_waiting": 0, "unread_thoughts": 0,
        "quota": {"used_percent": None, "exhausted": False, "resets_at": None, "period": "week"},
        "hands": {"connected": None, "down_for_s": None},
    }


@pytest.mark.unit
def test_quota_view_from_the_meter():
    from services import wake_state
    from services.llm_rate_gate import QuotaMeter, QuotaWindow

    now = NOW.timestamp()
    meter = QuotaMeter(windows={"primary": QuotaWindow(41.0, 7 * 86400, 5 * 3600 + 600)}, sampled_at=now - 600)
    assert wake_state.quota_view(meter, now) == {"used_percent": 41.0, "exhausted": False,
                                                 "resets_at": NOW + timedelta(hours=5)}
    capped = QuotaMeter(windows={"primary": QuotaWindow(100.0, 7 * 86400, 3600)}, limit_reached=True, sampled_at=now)
    assert wake_state.quota_view(capped, now)["exhausted"] is True
    stale = QuotaMeter(windows={"primary": QuotaWindow(88.0, 7 * 86400, 60)}, sampled_at=now - 3600)
    assert wake_state.quota_view(stale, now) == {"used_percent": None, "exhausted": False, "resets_at": None}, \
        "the window reset since the sample: the old reading is not the truth"
    assert wake_state.quota_view(None, now) == {"used_percent": None, "exhausted": False, "resets_at": None}


@pytest.mark.unit
def test_hands_view():
    from services import wake_state

    assert wake_state.hands_view({"connected": True, "tabs": 4}) == {"connected": True, "down_for_s": None}
    assert wake_state.hands_view({"connected": False, "down_for_s": 95}) == {"connected": False, "down_for_s": 95}
    assert wake_state.hands_view(None) == {"connected": None, "down_for_s": None}


# ---------------------------------------------------------------------------
# I/O and the route
# ---------------------------------------------------------------------------


async def _client():
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    token = create_access_token()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t", cookies={"edward_token": token})


async def _seed():
    import services.mind_service as ms
    from services.database import (ConversationModel, PendingApprovalModel, ScheduledEventModel, async_session)

    await ms._record_now(source="evening", outcome="acted", note="Left a note on the vet.", tools=[],
                         detail={"thought_id": "t-vet"})
    await ms._record_now(source="messages", outcome="left_alone", note="Banter.", tools=[])
    utc = datetime.utcnow()
    async with async_session() as s:
        s.add(ScheduledEventModel(id="ev-plain", description="text Sam", scheduled_at=utc + timedelta(minutes=30),
                                  next_fire_at=utc + timedelta(minutes=30), status="pending"))
        s.add(ScheduledEventModel(id="ev-wake", description="look at the vet thread", scheduled_at=utc,
                                  next_fire_at=utc + timedelta(hours=2), status="pending",
                                  event_metadata=json.dumps({"type": "mind_wake", "why": "vet"})))
        s.add(ScheduledEventModel(id="ev-tick", description="Evening wake", scheduled_at=utc,
                                  next_fire_at=utc + timedelta(hours=5), status="pending",
                                  event_metadata=json.dumps({"type": "wakeup_tick", "kind": "evening"})))
        s.add(ScheduledEventModel(id="ev-done", description="old wake", scheduled_at=utc,
                                  next_fire_at=utc + timedelta(minutes=5), status="completed",
                                  event_metadata=json.dumps({"type": "mind_wake"})))
        s.add(PendingApprovalModel(tool_name="send_imessage", args_json="{}", status="pending"))
        s.add(PendingApprovalModel(tool_name="send_imessage", args_json="{}", status="pending"))
        s.add(PendingApprovalModel(tool_name="send_imessage", args_json="{}", status="pending",
                                   created_at=utc - timedelta(hours=30)))  # expired by age
        s.add(PendingApprovalModel(tool_name="send_imessage", args_json="{}", status="approved"))
        s.add(ConversationModel(id="c-thought", title="Replied to Parker", source="edward_thought",
                                notified_user=True, message_count=2))
        s.add(ConversationModel(id="c-hb-hidden", title="hb", source="heartbeat", notified_user=True,
                                message_count=1))
        s.add(ConversationModel(id="c-read", title="read", source="edward_thought", notified_user=False,
                                message_count=5))
        s.add(ConversationModel(id="c-user", title="Ellis's", source="user", notified_user=True, message_count=9))
        await s.commit()
    return utc


@pytest.mark.integration
async def test_the_route_reads_every_source(clean_db, monkeypatch):
    from services import browser_ext_service, wake_state

    utc = await _seed()
    monkeypatch.setattr(browser_ext_service, "status", lambda: {"connected": False, "down_for_s": 42})
    token = wake_state.begin("mention", "edward-mind")
    try:
        async with await _client() as c:
            r = await c.get("/api/mind/now")
    finally:
        wake_state.end(token)
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"now", "waking", "last", "recent", "next_wake_at", "approvals_waiting", "unread_thoughts",
                         "quota", "hands"}
    assert body["now"].endswith("Z")
    assert body["waking"]["reason"] == "mention" and body["waking"]["started_at"].endswith("Z")
    assert body["last"]["reason"] == "messages" and body["last"]["line"] == "Banter."
    assert [w["reason"] for w in body["recent"]] == ["messages", "evening"]
    assert body["recent"][1]["thought_id"] == "t-vet"
    next_at = datetime.fromisoformat(body["next_wake_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    assert abs((next_at - (utc + timedelta(hours=2))).total_seconds()) < 2, "the earliest pending mind wake"
    assert body["approvals_waiting"] == 2
    assert body["unread_thoughts"] == 1
    assert body["hands"] == {"connected": False, "down_for_s": 42}
    assert set(body["quota"]) == {"used_percent", "exhausted", "resets_at", "period"}


@pytest.mark.integration
async def test_a_failing_source_degrades_and_never_500s(clean_db, monkeypatch):
    from services import browser_ext_service, wake_state

    await _seed()

    async def boom(*a, **k):
        raise RuntimeError("db down")

    def hands_boom():
        raise RuntimeError("no link")

    monkeypatch.setattr(wake_state, "_approvals_waiting", boom)
    monkeypatch.setattr(wake_state, "_recent", boom)
    monkeypatch.setattr(browser_ext_service, "status", hands_boom)
    async with await _client() as c:
        r = await c.get("/api/mind/now")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["approvals_waiting"] == 0 and body["last"] is None and body["recent"] == []
    assert body["hands"] == {"connected": None, "down_for_s": None}
    assert body["unread_thoughts"] == 1, "the other sources still answer"


@pytest.mark.integration
async def test_an_in_process_retry_counts_as_the_next_wake(clean_db, monkeypatch):
    import services.mind_service as ms
    from services import wake_state

    async def fake_wake(reason, **kw):
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    ms.reset_state()
    ms.request_wake("evening", delay_s=600, extra="x", attempt=1)
    try:
        out = await wake_state.snapshot()
    finally:
        ms.reset_state()
    at = datetime.fromisoformat(out["next_wake_at"].replace("Z", "+00:00"))
    assert 590 <= (at - datetime.now(timezone.utc)).total_seconds() <= 600


@pytest.mark.integration
async def test_a_requested_wake_that_has_started_is_not_the_next_wake(clean_db, monkeypatch):
    """``next_wake_at`` is a wake still to come: once a requested wake (a
    "Wake now", a coalesced ask, a retry) starts running it is ``waking``,
    and its due time — now in the past — is no longer the next wake."""
    import services.mind_service as ms
    from services import wake_state

    release = asyncio.Event()
    started = asyncio.Event()

    async def fake_wake(reason, **kw):
        started.set()
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    ms.reset_state()
    try:
        ms.request_wake("manual", delay_s=0)
        await asyncio.wait_for(started.wait(), timeout=2)
        assert ms.pending_wake_due() is None
        out = await wake_state.snapshot()
        assert out["next_wake_at"] is None
    finally:
        release.set()
        ms.reset_state()
