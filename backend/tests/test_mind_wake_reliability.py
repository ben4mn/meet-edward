"""The mind cannot be wedged, and a failing wake cannot loop (2026-09-24).

Plan ``docs/muse-and-beyond-plan-2026-09-24.md`` C1/D5/D6:

* The pack is collected *before* the thread's lock and under its own
  deadline; each source has a short bound of its own. A hung apple-mcp
  calendar call used to run inside the one wake lock with no deadline and
  freeze every later wake and pulse, with no ledger row.
* One lock per thread: the surfaces pulse writes ``edward-surfaces`` and
  no longer holds the mind's lock, so a mention never waits behind it.
* A wake that fails or times out counts an attempt on each escalation it
  was served; the third gives up on it (``notified_at`` stamped with batch
  ``mind:gave-up``) instead of re-serving it to every wake forever.
* Mentions get 420 s (the old path needed that after overruns caused
  duplicate replies); the Sunday reflection gets 900 s and a 100K window.
* A mention that arrives while a wake is already running is not dropped:
  one more wake follows.
* Retrieval embeds a short digest of the pack, not the whole pack.
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone

import pytest

from tests.test_mind_service import _S, _const, _pack

NOW = datetime(2026, 9, 24, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _leave_no_wake_state_behind():
    """Later files (the pulse tests) read ``_state.last_wake_at``."""
    yield
    import services.mind_service as ms

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
    ms.reset_state()
    return recorded


# ---------------------------------------------------------------------------
# Pure
# ---------------------------------------------------------------------------


def test_timeouts_by_reason():
    import services.mind_service as ms

    assert ms.MENTION_TIMEOUT_SECONDS == 420
    assert ms.NIGHTLY_SUNDAY_TIMEOUT_SECONDS == 900
    assert ms.wake_timeout_for("mention") == 420
    assert ms.wake_timeout_for("nightly", now=datetime(2026, 10, 11, 8, 0, tzinfo=timezone.utc)) == 900
    for reason in ("manual", "self", "surfaces"):
        assert ms.wake_timeout_for(reason) == ms.MIND_TIMEOUT_SECONDS == 240


def test_history_window_by_reason():
    import services.mind_service as ms

    assert ms.NIGHTLY_MAX_HISTORY_CHARS == 48_000
    assert ms.history_chars_for("nightly", None) == 48_000
    assert ms.history_chars_for("manual", None) == ms.MIND_MAX_HISTORY_CHARS
    assert ms.history_chars_for("nightly", 5_000) == 5_000, "an explicit window wins"


def test_attempt_outcome():
    import services.mind_service as ms

    assert ms.MAX_WAKE_ATTEMPTS == 3
    assert ms.attempt_outcome(None) == (1, False)
    assert ms.attempt_outcome(1) == (2, False)
    assert ms.attempt_outcome(2) == (3, True)
    assert ms.attempt_outcome(7) == (8, True)


def test_memory_query_is_a_short_digest_of_the_salient_parts():
    import services.mind_service as ms

    p = _pack(reason="mention", extra="Parker tagged you in the flight chat",
              escalations=[{"id": "ev-1", "sender": "Parker", "chat": "c1", "text": "@edward what time do we land?",
                            "at": "2026-09-24T13:59:00"}],
              messages=[{"sender": "Parker", "chat_identifier": "c1", "text": "x" * 5000, "is_from_me": False}])
    q = ms.memory_query_for(p)
    assert q.startswith("mention")
    assert "Parker tagged you" in q and "what time do we land" in q
    assert len(q) <= ms.MEMORY_QUERY_CHARS + 20
    assert len(q) < len(ms.render_pack(p)) / 4


def test_render_pack_says_what_it_could_not_read():
    import services.mind_service as ms

    text = ms.render_pack(_pack(calendar=[], unavailable=["calendar", "messages (timed out)"]))
    assert "Not readable this wake: calendar, messages (timed out)" in text
    assert "Today: (the calendar could not be read this wake" in text
    assert "Not readable" not in ms.render_pack(_pack())


def test_render_pack_marks_escalations_an_earlier_wake_did_not_finish():
    import services.mind_service as ms

    text = ms.render_pack(_pack(escalations=[
        {"id": "ev-1", "sender": "Parker", "chat": "c1", "text": "flight?", "at": "t", "attempts": 2},
    ]))
    assert "served to 2 earlier wakes that did not finish" in text
    assert "check the thread before you reply" in text


# ---------------------------------------------------------------------------
# The wake
# ---------------------------------------------------------------------------


async def test_a_hung_pack_fails_the_wake_on_its_deadline(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        raise AssertionError("no turn without a pack")

    recorded = _stub(monkeypatch, ms, chat)

    async def hangs(reason, **kw):
        await asyncio.sleep(3600)

    monkeypatch.setattr(ms, "collect_pack", hangs)
    monkeypatch.setattr(ms, "PACK_TIMEOUT_SECONDS", 0.1)
    t0 = time.monotonic()
    out = await ms.wake("manual")
    assert time.monotonic() - t0 < 1.0
    assert out["ok"] is False and out["error_class"] == "PackTimeout"
    assert recorded and recorded[0]["outcome"] == "failed" and recorded[0]["source"] == "manual"
    assert recorded[0]["note"].startswith("PackTimeout")


async def test_the_pack_is_collected_outside_the_lock(monkeypatch):
    import services.mind_service as ms

    release = asyncio.Event()
    collected_while_locked = []

    async def chat(**kw):
        await release.wait()
        return "done"

    _stub(monkeypatch, ms, chat)

    async def collect(reason, **kw):
        collected_while_locked.append(ms.thread_lock(ms.MIND_CONVERSATION_ID).locked())
        return _pack(reason=reason)

    monkeypatch.setattr(ms, "collect_pack", collect)
    first = asyncio.ensure_future(ms.wake("manual"))
    await asyncio.sleep(0.05)
    second = asyncio.ensure_future(ms.wake("self"))
    await asyncio.sleep(0.05)
    assert collected_while_locked == [False, True], "the second pack was collected while the first turn ran"
    release.set()
    await asyncio.gather(first, second)


async def test_the_pulse_does_not_wait_behind_the_mind(monkeypatch):
    import services.mind_service as ms

    release = asyncio.Event()
    finished = []

    async def chat(**kw):
        if kw["conversation_id"] == ms.MIND_CONVERSATION_ID:
            await release.wait()
        finished.append(kw["conversation_id"])
        return "ok"

    _stub(monkeypatch, ms, chat)
    mind = asyncio.ensure_future(ms.wake("manual"))
    await asyncio.sleep(0.05)
    out = await asyncio.wait_for(
        ms.wake("surfaces", message="pulse", conversation_id="edward-surfaces", tools=("update_widget",),
                system_prompt="S", turn_source="pulse", skip_extraction=True, record=False),
        timeout=1.0,
    )
    assert out["ok"] and finished == ["edward-surfaces"]
    assert not mind.done()
    release.set()
    await mind


async def test_wakes_in_one_thread_still_run_one_at_a_time(monkeypatch):
    import services.mind_service as ms

    running = {"now": 0, "max": 0}

    async def chat(**kw):
        running["now"] += 1
        running["max"] = max(running["max"], running["now"])
        await asyncio.sleep(0.05)
        running["now"] -= 1
        return "ok"

    _stub(monkeypatch, ms, chat)
    await asyncio.gather(ms.wake("manual"), ms.wake("mention"), ms.wake("self"))
    assert running["max"] == 1


async def test_the_turn_timeout_follows_the_reason(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        await asyncio.sleep(1.0)
        return "late"

    recorded = _stub(monkeypatch, ms, chat)
    asked = []

    def timeout_for(reason, now=None):
        asked.append(reason)
        return 0.05

    monkeypatch.setattr(ms, "wake_timeout_for", timeout_for)
    out = await ms.wake("mention", required_tools=["send_imessage"])
    assert asked == ["mention"]
    assert out["ok"] is False and recorded[0]["outcome"] == "failed"
    assert "Timeout" in recorded[0]["note"]


async def test_the_nightly_gets_the_larger_window_and_a_digest_query(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def chat(**kw):
        calls.append(kw)
        return "reflected"

    _stub(monkeypatch, ms, chat, pack=_pack(reason="nightly"))
    monkeypatch.setattr(ms, "nightly_has_material", _const(True))
    monkeypatch.setattr(ms, "_after_nightly", _const({}))
    await ms.wake("nightly")
    assert calls[0]["max_history_chars"] == ms.NIGHTLY_MAX_HISTORY_CHARS
    assert calls[0]["memory_query"] and calls[0]["memory_query"].startswith("nightly")
    assert len(calls[0]["memory_query"]) < len(calls[0]["message"])


async def test_a_mention_during_a_running_wake_is_not_dropped(monkeypatch):
    import services.mind_service as ms

    release = asyncio.Event()
    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, kw.get("required_tools")))
        if len(calls) == 1:
            await release.wait()
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(True))
    ms.reset_state()
    ms.request_wake("mention", delay_s=0, required_tools=["send_imessage"])
    await asyncio.sleep(0.05)
    assert calls == [("mention", ["send_imessage"])]
    ms.request_wake("mention", delay_s=0, required_tools=["send_imessage"])  # arrives mid-wake
    ms.request_wake("mention", delay_s=0, required_tools=["send_imessage"])  # still one more, not two
    release.set()
    await asyncio.sleep(0.1)
    assert calls == [("mention", ["send_imessage"]), ("mention", ["send_imessage"])]


async def test_a_rearmed_wake_with_nothing_waiting_is_skipped(monkeypatch):
    import services.mind_service as ms

    release = asyncio.Event()
    calls = []

    async def fake_wake(reason, **kw):
        calls.append(reason)
        if len(calls) == 1:
            await release.wait()
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(False))
    ms.reset_state()
    ms.request_wake("mention", delay_s=0)
    await asyncio.sleep(0.05)
    ms.request_wake("mention", delay_s=0)
    release.set()
    await asyncio.sleep(0.1)
    assert calls == ["mention"], "the first wake already served it"


async def test_requests_before_a_wake_starts_still_coalesce(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def fake_wake(reason, **kw):
        calls.append(reason)
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(True))
    ms.reset_state()
    for _ in range(3):
        ms.request_wake("mention", delay_s=0.05)
        ms.request_wake("manual", delay_s=0.05)
    await asyncio.sleep(0.2)
    assert sorted(calls) == ["manual", "mention"]


# ---------------------------------------------------------------------------
# Wake retry (2026-09-25): a one-hour Codex outage (401 "Incorrect API key",
# earlier 503s) lost the evening wake outright — failed wakes were never
# re-requested. A transient failure now asks for the wake again after
# 10 / 20 / 40 minutes, keeping its profile; anything else never retries.
# ---------------------------------------------------------------------------


def test_which_failures_are_transient():
    import httpx

    import services.mind_service as ms
    from services.codex_chat_model import CodexStreamError
    from services.graph.tool_deadlines import LLMCallTimeout

    transient = [
        CodexStreamError("server_error", "boom"),
        CodexStreamError("stream_truncated", "closed early"),
        ValueError("Codex API error (HTTP 401): refreshed token also rejected. Run `codex login`"),
        ValueError("Codex API error (HTTP 401): auth rejected and refresh failed. Run `codex login`"),
        ValueError("OpenAI token expired and refresh failed. Run `codex login` to re-authenticate."),
        ValueError("Codex API error (HTTP 503): upstream connect error"),
        ValueError("Codex API error (HTTP 502): bad gateway"),
        httpx.ConnectError("connection refused"),
        httpx.ReadTimeout("read timed out"),
        ConnectionResetError("reset by peer"),
        LLMCallTimeout(300),
    ]
    permanent = [
        CodexStreamError("invalid_request_error", "bad schema"),
        ValueError("Codex API error (HTTP 429): rate gate cooldown 60s."),
        ValueError("Codex weekly usage cap reached for this ChatGPT account (resets in 2d)"),
        ValueError("Codex API error (HTTP 400): model not supported"),
        asyncio.TimeoutError(),  # the wake's own deadline: it may have acted
        KeyError("x"),
        RuntimeError("a bug"),
    ]
    assert [ms.is_transient_error(e) for e in transient] == [True] * len(transient)
    assert [ms.is_transient_error(e) for e in permanent] == [False] * len(permanent)


def test_the_retry_plan():
    import services.mind_service as ms

    assert ms.MAX_WAKE_RETRIES == 3
    assert [ms.retry_delay(n) for n in (0, 1, 2)] == [600, 1200, 2400]
    assert set(ms.RETRY_REASONS) == {"self", "mention", "nightly"}
    failed = {"ok": False, "transient": True, "note": "ValueError: HTTP 503"}
    assert ms.should_retry_wake("nightly", failed, 0) is True
    assert ms.should_retry_wake("nightly", failed, 2) is True
    assert ms.should_retry_wake("nightly", failed, 3) is False, "three retries at most"
    assert ms.should_retry_wake("messages", failed, 0) is False, "inbound is noticed, never woken for"
    assert ms.should_retry_wake("speak", failed, 0) is False, "the speaker re-runs its gate instead"
    assert ms.should_retry_wake("manual", failed, 0) is False
    assert ms.should_retry_wake("nightly", dict(failed, transient=False), 0) is False
    assert ms.should_retry_wake("nightly", {"ok": True, "skipped": True, "note": "quota exhausted"}, 0) is False
    timed_out = {"ok": True, "skipped": False, "outcome": "left_alone",
                 "note": "I stopped before finishing: the model did not answer within 300s. No tools ran."}
    assert ms.should_retry_wake("self", timed_out, 0) is True, "a turn whose model never answered"
    assert ms.should_retry_wake("self", dict(timed_out, outcome="acted"), 0) is False, "it acted: never twice"
    assert ms.should_retry_wake("self", {"ok": True, "outcome": "left_alone", "note": "Quiet."}, 0) is False


def test_the_retry_note_keeps_the_original_extra_once():
    import services.mind_service as ms

    first = ms.retry_extra("Why: check the 2pm", 1, "ValueError: Codex API error (HTTP 503)")
    assert first.startswith("Why: check the 2pm")
    assert "Retry 1 of 3" in first and "HTTP 503" in first
    second = ms.retry_extra(first, 2, "CodexStreamError: server_error")
    assert second.count("Why: check the 2pm") == 1
    assert "Retry 2 of 3" in second and "Retry 1 of 3" not in second
    assert "Retry 1 of 3" in ms.retry_extra(None, 1, "x")


async def test_a_transient_failure_asks_for_the_wake_again_with_backoff(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        raise ValueError("Codex API error (HTTP 503): upstream connect error")

    _stub(monkeypatch, ms, chat)
    asked = []
    monkeypatch.setattr(ms, "request_wake", lambda reason, **kw: asked.append((reason, kw)))
    out = await ms.wake("self", extra="E")
    assert out["ok"] is False and out["transient"] is True
    (reason, kw), = asked
    assert reason == "self" and kw["delay_s"] == 600 and kw["attempt"] == 1
    assert kw["extra"].startswith("E") and "Retry 1 of 3" in kw["extra"]
    await ms.wake("self", extra=kw["extra"], attempt=1)
    assert asked[1][1]["delay_s"] == 1200 and asked[1][1]["attempt"] == 2
    assert asked[1][1]["extra"].count("E\n") <= 1 and "Retry 2 of 3" in asked[1][1]["extra"]
    await ms.wake("self", extra=asked[1][1]["extra"], attempt=3)
    assert len(asked) == 2, "the third retry is the last"


async def test_a_permanent_failure_or_another_reason_is_never_retried(monkeypatch):
    import services.mind_service as ms

    async def bug(**kw):
        raise KeyError("x")

    _stub(monkeypatch, ms, bug)
    asked = []
    monkeypatch.setattr(ms, "request_wake", lambda reason, **kw: asked.append(reason))
    await ms.wake("self")

    async def down(**kw):
        raise ValueError("Codex API error (HTTP 503): down")

    monkeypatch.setattr(ms, "chat_with_memory", down)
    await ms.wake("speak", message="[SPEAK] x")
    await ms.wake("manual")
    assert asked == []


async def test_a_retried_wake_keeps_its_profile_and_its_purpose(monkeypatch):
    import services.mind_service as ms
    from services.call_purpose import purpose_scope

    async def down(**kw):
        raise ValueError("Codex API error (HTTP 401): refreshed token also rejected.")

    _stub(monkeypatch, ms, down)
    asked = []
    monkeypatch.setattr(ms, "request_wake", lambda reason, **kw: asked.append((reason, kw)))
    meta = {"type": "mind_wake", "why": "the vet"}
    with purpose_scope("mind:self"):
        await ms.wake("self", extra="The vet.", tools=ms.LEGACY_QUIET_TOOLS, event_meta=meta, timeout_s=200)
    await ms.wake("mention", required_tools=["send_imessage"])
    (reason, kw), (mention, mkw) = asked
    assert reason == "self"
    assert kw["profile"]["tools"] == ms.LEGACY_QUIET_TOOLS
    assert kw["profile"]["event_meta"] == meta and kw["profile"]["timeout_s"] == 200
    assert kw["purpose"] == "mind:self"
    assert mention == "mention" and mkw["required_tools"] == ["send_imessage"] and mkw["purpose"] is None


async def test_the_retry_runs_later_with_its_profile(monkeypatch):
    import services.mind_service as ms
    from services.call_purpose import current_purpose

    calls = []

    async def flaky(**kw):
        calls.append((kw, current_purpose()))
        if len(calls) == 1:
            raise ValueError("Codex API error (HTTP 503): down")
        return "Back. Nothing needs you."

    _stub(monkeypatch, ms, flaky)

    async def collect(reason, **kw):
        return _pack(reason=reason, extra=kw.get("extra"))

    monkeypatch.setattr(ms, "collect_pack", collect)
    monkeypatch.setattr(ms, "retry_delay", lambda n: 0.01)
    await ms.wake("self", extra="Why: the vet called back?", tools=ms.LEGACY_QUIET_TOOLS)
    for _ in range(100):
        await asyncio.sleep(0.02)
        if len(calls) == 2:
            break
    assert len(calls) == 2
    retry_kw, purpose = calls[1]
    assert "Retry 1 of 3" in retry_kw["message"] and "the vet called back" in retry_kw["message"]
    assert retry_kw["allowed_tools"] == list(ms.LEGACY_QUIET_TOOLS)
    assert purpose == "mind:self"


async def test_a_mention_retry_with_nothing_waiting_is_skipped(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, kw))
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(False))
    ms.reset_state()
    ms.request_wake("mention", delay_s=0, extra="x", required_tools=["send_imessage"], attempt=1)
    await asyncio.sleep(0.05)
    assert calls == [], "the escalation was given up on or answered: no mention to retry"
    monkeypatch.setattr(ms, "has_pending_escalations", _const(True))
    ms.request_wake("mention", delay_s=0, extra="x", required_tools=["send_imessage"], attempt=1)
    await asyncio.sleep(0.05)
    assert calls and calls[0][1]["attempt"] == 1


async def test_a_retry_does_not_swallow_a_normal_request_for_the_same_reason(monkeypatch):
    import services.mind_service as ms

    release = asyncio.Event()
    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, kw.get("attempt", 0)))
        await release.wait()
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(True))
    ms.reset_state()
    ms.request_wake("self", delay_s=0, extra="retry", attempt=1)
    ms.request_wake("self", delay_s=0, extra="new")
    await asyncio.sleep(0.05)
    release.set()
    await asyncio.sleep(0.05)
    assert sorted(calls) == [("self", 0), ("self", 1)]


# ---------------------------------------------------------------------------
# Integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_a_hung_calendar_leaves_the_pack_on_time_and_says_so(clean_db, monkeypatch):
    import services.mind_service as ms
    from services import display_service

    class _Hangs:
        name = "calendar"

        async def ainvoke(self, args):
            await asyncio.sleep(3600)

    monkeypatch.setattr("services.display_service._find_calendar_tool", lambda: _Hangs())
    monkeypatch.setattr(display_service, "CALENDAR_MCP_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(display_service, "_calendar_cache", {"data": None, "fetched_at": 0.0})
    ms.reset_state()
    t0 = time.monotonic()
    pack = await asyncio.wait_for(ms.collect_pack("self"), timeout=ms.PACK_TIMEOUT_SECONDS)
    assert time.monotonic() - t0 < 5.0
    assert "calendar" in pack.unavailable
    assert "Not readable this wake: calendar" in ms.render_pack(pack)


@pytest.mark.integration
async def test_a_source_that_hangs_is_cut_off_on_its_own_bound(clean_db, monkeypatch):
    import services.mind_service as ms

    async def hangs():
        await asyncio.sleep(3600)

    monkeypatch.setattr(ms, "PACK_SOURCE_TIMEOUT_SECONDS", 0.2)
    monkeypatch.setattr(ms, "_escalations", hangs)
    ms.reset_state()
    t0 = time.monotonic()
    pack = await ms.collect_pack("mention")
    assert time.monotonic() - t0 < 3.0
    assert "escalations (timed out)" in pack.unavailable
    assert pack.escalations == []


@pytest.mark.integration
async def test_three_failed_wakes_give_up_on_an_escalation(clean_db, monkeypatch):
    from sqlalchemy import select

    import services.mind_service as ms
    from services.database import HeartbeatEventModel, LedgerEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(id="ev-1", source="imessage", event_type="message_received", sender="Parker",
                                  chat_identifier="c1", summary="flight moved?", triage_status="escalated"))
        await s.commit()

    async def boom(**kw):
        raise RuntimeError("codex 500")

    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", boom)

    async def row():
        async with async_session() as s:
            return (await s.execute(select(HeartbeatEventModel).where(HeartbeatEventModel.id == "ev-1"))).scalar_one()

    for n in (1, 2):
        ms.reset_state()
        out = await ms.wake("mention")
        assert out["ok"] is False
        r = await row()
        assert r.mind_attempts == n and r.notified_at is None

    ms.reset_state()
    pack = await ms.collect_pack("mention")
    assert pack.escalations and pack.escalations[0].get("attempts") == 2

    ms.reset_state()
    await ms.wake("mention")
    r = await row()
    assert r.mind_attempts == 3
    assert r.notified_at is not None and r.notification_batch_id == ms.GAVE_UP_BATCH_ID == "mind:gave-up"
    for _ in range(20):
        await asyncio.sleep(0.02)
    async with async_session() as s:
        rows = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "mind")
                                .order_by(LedgerEventModel.created_at))).scalars().all()
    assert [r.outcome for r in rows] == ["failed", "failed", "failed"]
    assert "gave up on 1 escalation" in rows[-1].reason

    ms.reset_state()
    assert (await ms.collect_pack("mention")).escalations == []
    assert await ms.has_pending_escalations() is False


@pytest.mark.integration
async def test_a_second_wake_is_not_served_what_the_first_just_handled(clean_db, monkeypatch):
    """The pack is collected outside the lock, so a wake that waited for
    the lock drops escalations the wake before it stamped meanwhile."""
    import services.mind_service as ms
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(id="ev-1", source="imessage", event_type="message_received", sender="Parker",
                                  chat_identifier="c1", summary="flight moved?", triage_status="escalated"))
        await s.commit()

    release = asyncio.Event()
    messages = []

    async def chat(**kw):
        messages.append(kw["message"])
        if len(messages) == 1:
            await release.wait()
        return "handled"

    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    ms.reset_state()
    first = asyncio.ensure_future(ms.wake("mention"))
    for _ in range(100):
        await asyncio.sleep(0.02)
        if messages:
            break
    second = asyncio.ensure_future(ms.wake("mention"))
    await asyncio.sleep(0.5)  # the second pack is collected while the first turn runs
    release.set()
    await asyncio.gather(first, second)
    assert "flight moved?" in messages[0]
    assert "flight moved?" not in messages[1]
