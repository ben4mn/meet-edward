"""Pre-deploy review of the mind (2026-09-25), before ``mind_enabled`` runs
this code unattended.

* A wake that does not answer escalations (since 2026-10-11 every reason
  but a mention: the nightly, a self wake, a manual one) is not shown the
  escalations waiting on Ellis and does not stamp them: they wait for the
  wake that can answer. It used to see them, be told not to push or message
  anyone, and stamp them considered — so a message that arrived in that
  window was swallowed.
* A wake cancelled from outside (the scheduler's ``wait_for`` ran out while
  it waited for the thread behind a long reflection, or mid-turn) still
  leaves one ledger row saying so, counts an attempt on the escalations its
  turn was served — none when no turn ran — and re-raises the cancellation.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from tests.test_mind_service import _S, _const, _pack

ESCALATION = {"id": "ev-1", "sender": "Parker", "chat": "Flight chat", "text": "@edward did the flight move?",
              "at": "2026-09-25T13:59:00"}


@pytest.fixture(autouse=True)
def _leave_no_wake_state_behind():
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
    books = {"rows": [], "considered": [], "attempted": []}

    async def _rec(**kw):
        books["rows"].append(kw)
        return len(books["rows"])

    async def _considered(ids, batch_id):
        books["considered"].append(list(ids))
        return len(ids)

    async def _attempt(ids):
        books["attempted"].append(list(ids))
        return []

    monkeypatch.setattr(ms, "_record_now", _rec)
    monkeypatch.setattr(ms, "_mark_considered", _considered)
    monkeypatch.setattr(ms, "_note_failed_attempt", _attempt)
    monkeypatch.setattr(ms, "_still_waiting", lambda ids: _const(set(ids))())
    ms.reset_state()
    return books


# ---------------------------------------------------------------------------
# A wake that cannot answer an escalation neither sees nor stamps it
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", ["nightly", "self", "manual"])
async def test_a_wake_that_does_not_answer_neither_shows_nor_stamps_escalations(monkeypatch, reason):
    import services.mind_service as ms

    seen = []

    async def chat(**kw):
        seen.append(kw["message"])
        return "Left a note."

    books = _stub(monkeypatch, ms, chat, pack=_pack(reason=reason, escalations=[dict(ESCALATION)]))
    monkeypatch.setattr(ms, "nightly_has_material", _const(True))
    monkeypatch.setattr(ms, "_after_nightly", _const({}))
    out = await ms.wake(reason)
    assert out["ok"] is True
    assert "Waiting on you" not in seen[0] and "did the flight move" not in seen[0]
    assert books["considered"] == [], "they stay for the next wake that can reply or push"


async def test_a_failed_wake_that_does_not_answer_counts_no_attempt_on_escalations(monkeypatch):
    import services.mind_service as ms

    async def boom(**kw):
        raise RuntimeError("codex 500")

    books = _stub(monkeypatch, ms, boom, pack=_pack(reason="self", escalations=[dict(ESCALATION)]))
    out = await ms.wake("self")
    assert out["ok"] is False
    assert books["attempted"] == [], "it was never served them"


async def test_a_wake_that_can_reply_still_sees_and_stamps_them(monkeypatch):
    import services.mind_service as ms

    seen = []

    async def chat(**kw):
        seen.append(kw["message"])
        return "Replied."

    books = _stub(monkeypatch, ms, chat, pack=_pack(reason="mention", escalations=[dict(ESCALATION)]))
    await ms.wake("mention")
    assert "Waiting on you (1)" in seen[0] and "did the flight move" in seen[0]
    assert books["considered"] == [["ev-1"]]


@pytest.mark.integration
async def test_an_escalation_during_the_nightly_waits_for_the_mention_wake(clean_db, monkeypatch):
    from sqlalchemy import select

    import services.mind_service as ms
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(id="ev-9", source="imessage", event_type="message_received", sender="Parker",
                                  summary="are you coming tonight?", triage_status="escalated"))
        await s.commit()

    seen = []

    async def chat(**kw):
        seen.append(kw["message"])
        return "Closed two intentions."

    monkeypatch.setattr(ms, "_messages_source", _const([]))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)

    async def row():
        async with async_session() as s:
            return (await s.execute(select(HeartbeatEventModel).where(HeartbeatEventModel.id == "ev-9"))).scalar_one()

    monkeypatch.setattr(ms, "nightly_has_material", _const(True))
    monkeypatch.setattr(ms, "_after_nightly", _const({}))
    ms.reset_state()
    await ms.wake("nightly")
    assert "are you coming tonight" not in seen[-1]
    r = await row()
    assert r.notified_at is None and not r.mind_attempts

    ms.reset_state()
    await ms.wake("mention")
    assert "are you coming tonight" in seen[-1]
    assert (await row()).notified_at is not None


# ---------------------------------------------------------------------------
# A cancelled wake leaves one row and re-raises
# ---------------------------------------------------------------------------


async def test_a_wake_cancelled_while_waiting_for_the_thread_is_recorded(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        raise AssertionError("no turn ran")

    books = _stub(monkeypatch, ms, chat, pack=_pack(reason="self", escalations=[dict(ESCALATION)]))
    async with ms.thread_lock(ms.MIND_CONVERSATION_ID):  # a long reflection holds the thread
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ms.wake("self"), timeout=0.2)
    assert len(books["rows"]) == 1
    row = books["rows"][0]
    assert row["source"] == "self" and row["outcome"] == "failed"
    assert "cancelled" in row["note"].lower() and "waiting" in row["note"].lower()
    assert books["attempted"] == [], "a wake that never ran was served nothing"
    assert books["considered"] == []
    assert not ms.thread_lock(ms.MIND_CONVERSATION_ID).locked()


async def test_a_wake_cancelled_mid_turn_is_recorded_and_counts_an_attempt(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        await asyncio.Event().wait()

    books = _stub(monkeypatch, ms, chat, pack=_pack(reason="mention", escalations=[dict(ESCALATION)]))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ms.wake("mention"), timeout=0.2)
    assert len(books["rows"]) == 1
    row = books["rows"][0]
    assert row["outcome"] == "failed" and "mid-turn" in row["note"].lower()
    assert books["attempted"] == [["ev-1"]]
    assert books["considered"] == []
    assert not ms.thread_lock(ms.MIND_CONVERSATION_ID).locked()


async def test_the_cancellation_is_never_swallowed(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        await asyncio.Event().wait()

    books = _stub(monkeypatch, ms, chat)
    task = asyncio.ensure_future(ms.wake("manual"))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [r["outcome"] for r in books["rows"]] == ["failed"]


async def test_a_wake_cancelled_after_its_row_was_written_writes_no_second_row(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        return "Replied."

    books = _stub(monkeypatch, ms, chat, pack=_pack(reason="mention", escalations=[dict(ESCALATION)]))

    async def hangs(ids, batch_id):
        await asyncio.Event().wait()

    monkeypatch.setattr(ms, "_mark_considered", hangs)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ms.wake("mention"), timeout=0.2)
    assert [r["outcome"] for r in books["rows"]] == ["left_alone"]
    assert books["attempted"] == []


async def test_a_cancelled_pulse_writes_no_mind_row(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        await asyncio.Event().wait()

    books = _stub(monkeypatch, ms, chat)
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            ms.wake("surfaces", message="pulse", conversation_id="edward-surfaces", tools=("update_widget",),
                    turn_source="pulse", skip_extraction=True, record=False),
            timeout=0.2)
    assert books["rows"] == []


@pytest.mark.integration
async def test_a_cancelled_wake_settles_its_books_in_the_database(clean_db, monkeypatch):
    from sqlalchemy import select

    import services.mind_service as ms
    from services.database import HeartbeatEventModel, LedgerEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(id="ev-7", source="imessage", event_type="message_received", sender="Parker",
                                  summary="flight moved?", triage_status="escalated"))
        await s.commit()

    async def chat(**kw):
        await asyncio.Event().wait()

    monkeypatch.setattr(ms, "_messages_source", _const([]))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)
    ms.reset_state()

    # Waiting behind another turn: a row, and no attempt counted.
    async with ms.thread_lock(ms.MIND_CONVERSATION_ID):
        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(ms.wake("self"), timeout=3.0)
    # Mid-turn: a row, and one attempt on what it was served.
    ms.reset_state()
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(ms.wake("mention"), timeout=3.0)

    async with async_session() as s:
        ev = (await s.execute(select(HeartbeatEventModel).where(HeartbeatEventModel.id == "ev-7"))).scalar_one()
        rows = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.kind == "mind")
                                .order_by(LedgerEventModel.created_at))).scalars().all()
    assert ev.mind_attempts == 1 and ev.notified_at is None
    assert [(r.source, r.outcome) for r in rows] == [("self", "failed"), ("mention", "failed")]
    assert "waiting" in rows[0].reason.lower() and "mid-turn" in rows[1].reason.lower()


def test_there_is_no_morning_family_brief_any_more():
    """The morning tick is gone (2026-10-11), and with it the family brief
    on the shared house screen."""
    import services.mind_service as ms

    for reason in ms.WAKE_REASONS:
        assert "family brief" not in ms.render_pack(_pack(reason=reason)).lower(), reason
