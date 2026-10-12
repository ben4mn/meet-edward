"""A retried wake never speaks twice, and never into a thread that no longer
needs it (review of ux/jarvis-polish, 2026-09-26).

* A wake whose turn reached someone (a delivered send, a push) or
  committed to something and *then* failed is not retried: the retry would
  be told "Reply to the mention … Do one of these now" and could text the
  same group twice from Ellis's number.
* A mention wake carries the ids of the mentions it was asked for. A retry
  (or a rearmed follow-up) whose mentions nobody is waiting on any more —
  Ellis pressed Done, a wake handled it, the mind gave up — hands the rest
  of the inbound to the noticer (no wake, no forced send), or does nothing.
* The retry chain runs 10 / 20 / 40 minutes as designed: a retry task may
  ask for its own successor.
* A ``codex login`` 401 (``CodexAuthError`` not marked transient) is not
  retried; the service-side "Incorrect API key" one still is.
* The nightly cannot ``schedule_event`` a full-tool turn, and a speak
  wake does not move "since your last wake" for the wakes that serve Ellis.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from tests.test_mind_service import _S, _const, _pack

pytestmark = pytest.mark.unit


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
    monkeypatch.setattr(ms, "_journal", _const(None))

    async def _rec(**kw):
        return 1

    monkeypatch.setattr(ms, "_record_now", _rec)


def _turn(*actions):
    return SimpleNamespace(id="turn-1", actions=list(actions), tools=[], reasoning=[])


DELIVERED = {"name": "send_imessage", "args": {"chat_id": "chat1", "message": "Dinner is at 7"},
             "result": "iMessage sent to group chat chat1", "ok": True}


# ------------------------------------------------ it acted, then it failed --

def test_should_retry_wake_never_retries_a_failed_turn_that_reached_someone_or_committed():
    import services.mind_service as ms

    failed = {"ok": False, "transient": True, "note": "CodexStreamError: server_error"}
    assert ms.should_retry_wake("mention", failed, 0) is True
    assert ms.should_retry_wake("mention", dict(failed, reached=True), 0) is False, "a send cannot be taken back"
    assert ms.should_retry_wake("self", dict(failed, committed=True), 0) is False, "a push or note stands"
    assert ms.should_retry_wake("self", dict(failed, reached=False, committed=False), 0) is True


async def test_a_mention_wake_that_sent_and_then_failed_is_not_retried(monkeypatch):
    import services.mind_service as ms
    from services.codex_chat_model import CodexStreamError

    async def chat(**kw):
        raise CodexStreamError("server_error", "upstream went away", retryable=True)

    _stub(monkeypatch, ms, chat)
    monkeypatch.setattr(ms, "_new_turn", lambda prior: _turn(DELIVERED))
    asked = []
    monkeypatch.setattr(ms, "request_wake", lambda reason, **kw: asked.append((reason, kw)) or True)
    out = await ms.wake("mention", required_tools=["send_imessage"])
    assert out["ok"] is False and out["transient"] is True
    assert out["reached"] is True
    assert asked == [], "the reply already went out; a retry could send it again, reworded"


async def test_a_wake_that_only_remembered_before_failing_is_still_retried(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        raise ValueError("Codex API error (HTTP 503): upstream connect error")

    _stub(monkeypatch, ms, chat)
    remembered = {"name": "remember", "args": {"content": "x"}, "result": "ok (memory_id: m1)", "ok": True}
    monkeypatch.setattr(ms, "_new_turn", lambda prior: _turn(remembered))
    asked = []
    monkeypatch.setattr(ms, "request_wake", lambda reason, **kw: asked.append((reason, kw)) or True)
    out = await ms.wake("self")
    assert out["reached"] is False and out["committed"] is False
    assert [r for r, _ in asked] == ["self"]


# ------------------------------------------------------ the retry chain --

async def test_the_retry_chain_runs_all_three_retries(monkeypatch):
    """Without monkeypatching request_wake: the second retry is asked for
    from inside the first retry's own task, which used to find its own slot
    busy and give up (one retry instead of three)."""
    import services.mind_service as ms

    calls = []

    async def down(**kw):
        calls.append(kw)
        raise ValueError("Codex API error (HTTP 503): down")

    _stub(monkeypatch, ms, down)

    async def collect(reason, **kw):
        return _pack(reason=reason, extra=kw.get("extra"))

    monkeypatch.setattr(ms, "collect_pack", collect)
    monkeypatch.setattr(ms, "retry_delay", lambda n: 0.01)
    await ms.wake("self")
    for _ in range(200):
        await asyncio.sleep(0.02)
        if len(calls) >= 4 and not ms._state.pending:
            break
    assert len(calls) == 4, "the wake plus retries 1, 2 and 3"
    assert not ms._state.pending, "nothing left scheduled after the last retry"
    assert "Retry 3 of 3" in calls[-1]["message"]


async def test_a_retry_that_joins_one_already_pending_says_so(monkeypatch, capsys):
    import services.mind_service as ms

    monkeypatch.setattr(ms, "request_wake", lambda reason, **kw: False)
    ms._maybe_retry("self", {"ok": False, "transient": True, "note": "HTTP 503"}, attempt=0, extra=None,
                    message=None, required_tools=None, profile={})
    out = capsys.readouterr().out
    assert "retry 1 of 3" not in out
    assert "already pending" in out


# ------------------------------------------- mentions carry their own ids --

async def test_a_mention_retry_whose_mention_is_answered_hands_the_rest_to_the_noticer(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, kw))
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "_notice_inbound", lambda: calls.append(("noticed", {})) or True)
    monkeypatch.setattr(ms, "FOLLOW_UP_MESSAGES_DELAY_S", 0.0)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(True))   # the family chat is still waiting
    monkeypatch.setattr(ms, "_still_waiting", _const(set()))           # Parker's mention: Ellis pressed Done
    ms.request_wake("mention", delay_s=0, extra="x", required_tools=["send_imessage"], attempt=1,
                    mention_ids=["parker-1"])
    for _ in range(20):
        await asyncio.sleep(0.02)
        if calls:
            break
    await asyncio.sleep(0.05)
    assert [r for r, _ in calls] == ["noticed"], "no wake and no forced send into a thread that no longer needs it"


async def test_a_mention_retry_with_its_mention_still_waiting_keeps_the_forced_reply(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, kw))
        return {"ok": True}

    async def waiting(ids):
        return {i for i in ids if i == "parker-1"}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(True))
    monkeypatch.setattr(ms, "_still_waiting", waiting)
    ms.request_wake("mention", delay_s=0, extra="x", required_tools=["send_imessage"], attempt=1,
                    mention_ids=["parker-1", "sam-2"])
    await asyncio.sleep(0.1)
    (reason, kw), = calls
    assert reason == "mention" and kw["required_tools"] == ["send_imessage"]
    assert kw["mention_ids"] == ["parker-1"], "only what is still waiting"


async def test_a_mention_with_nothing_waiting_anywhere_is_skipped(monkeypatch):
    import services.mind_service as ms

    calls = []

    async def fake_wake(reason, **kw):
        calls.append(reason)
        return {"ok": True}

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "FOLLOW_UP_MESSAGES_DELAY_S", 0.0)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(False))
    monkeypatch.setattr(ms, "_still_waiting", _const(set()))
    ms.request_wake("mention", delay_s=0, required_tools=["send_imessage"], attempt=1, mention_ids=["parker-1"])
    await asyncio.sleep(0.1)
    assert calls == []


async def test_mention_ids_merge_when_requests_coalesce_and_when_one_is_rearmed(monkeypatch):
    import services.mind_service as ms

    release = asyncio.Event()
    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, sorted(kw.get("mention_ids") or [])))
        if len(calls) == 1:
            await release.wait()
        return {"ok": True}

    async def waiting(ids):
        return set(ids)

    monkeypatch.setattr(ms, "wake", fake_wake)
    monkeypatch.setattr(ms, "has_pending_escalations", _const(True))
    monkeypatch.setattr(ms, "_still_waiting", waiting)
    ms.request_wake("mention", delay_s=0.05, required_tools=["send_imessage"], mention_ids=["a"])
    ms.request_wake("mention", delay_s=0.05, required_tools=["send_imessage"], mention_ids=["b"])  # coalesces
    await asyncio.sleep(0.1)
    assert calls == [("mention", ["a", "b"])]
    ms.request_wake("mention", delay_s=0, required_tools=["send_imessage"], mention_ids=["c"])  # mid-wake
    ms.request_wake("mention", delay_s=0, required_tools=["send_imessage"], mention_ids=["d"])
    release.set()
    await asyncio.sleep(0.1)
    assert calls[1] == ("mention", ["c", "d"])


async def test_a_mention_wake_whose_mention_was_handled_runs_without_the_forced_send(monkeypatch):
    """The pack is collected before the lock; a messages wake that ran
    meanwhile stamped Parker's mention. The wake still runs for what else is
    waiting, but nothing tells it to reply to a mention that is gone."""
    import services.mind_service as ms

    seen = []

    async def chat(**kw):
        seen.append(kw.get("required_tools"))
        return "Nothing needs me."

    pack = _pack(reason="mention", escalations=[{"id": "family-9", "sender": "Mum", "text": "dinner?"}])
    _stub(monkeypatch, ms, chat, pack=pack)
    monkeypatch.setattr(ms, "_still_waiting", _const({"family-9"}))
    monkeypatch.setattr(ms, "_mark_considered", _const(1))
    await ms.wake("mention", required_tools=["send_imessage"], mention_ids=["parker-1"])
    assert seen == [None]

    pack = _pack(reason="mention", escalations=[{"id": "parker-1", "sender": "Parker", "text": "@edward time?"}])
    _stub(monkeypatch, ms, chat, pack=pack)
    monkeypatch.setattr(ms, "_still_waiting", _const({"parker-1"}))
    await ms.wake("mention", required_tools=["send_imessage"], mention_ids=["parker-1"])
    assert seen[-1] == ["send_imessage"]


async def test_a_failed_mention_wake_asks_its_retry_for_the_same_mentions(monkeypatch):
    import services.mind_service as ms

    async def down(**kw):
        raise ValueError("Codex API error (HTTP 503): down")

    pack = _pack(reason="mention", escalations=[{"id": "parker-1", "sender": "Parker", "text": "@edward time?"}])
    _stub(monkeypatch, ms, down, pack=pack)
    monkeypatch.setattr(ms, "_still_waiting", _const({"parker-1"}))
    monkeypatch.setattr(ms, "_count_failed_attempt", lambda ids, why, detail: _const(why)())
    asked = []
    monkeypatch.setattr(ms, "request_wake", lambda reason, **kw: asked.append((reason, kw)) or True)
    await ms.wake("mention", required_tools=["send_imessage"], mention_ids=["parker-1"])
    (reason, kw), = asked
    assert reason == "mention" and kw["mention_ids"] == ["parker-1"] and kw["attempt"] == 1


# ------------------------------------------------------ auth errors --

def test_a_codex_login_401_is_not_transient_but_the_service_side_one_is():
    import services.mind_service as ms
    from services.codex_chat_model import CodexAuthError

    login = CodexAuthError("Codex API error (HTTP 401): refreshed token also rejected. "
                           "Run `codex login` to mint a new refresh token. Body: {}")
    service_side = CodexAuthError("Codex API error (HTTP 401): the service rejected a freshly refreshed "
                                  "sign-in (\"Incorrect API key provided\").", transient=True)
    assert ms.is_transient_error(login) is False, "every retry would cost a token POST and fail the same way"
    assert ms.is_transient_error(service_side) is True


# ----------------------------------------------------------- free time --

def test_the_nightly_cannot_schedule_a_full_tool_turn():
    import services.mind_service as ms

    assert "schedule_event" not in ms.LEGACY_QUIET_TOOLS and "schedule" not in ms.LEGACY_QUIET_TOOLS
    assert "wake_me" in ms.LEGACY_QUIET_TOOLS, "a self wake carries every guard"


async def test_a_speak_wake_does_not_move_since_your_last_wake(monkeypatch):
    import services.mind_service as ms

    async def chat(**kw):
        return "Told Ellis."

    _stub(monkeypatch, ms, chat, pack=_pack(reason="speak"))
    before = ms._state.last_wake_at
    await ms.wake("speak", pack=_pack(reason="speak"))
    assert ms._state.last_wake_at == before, "a speak turn sees one noticing, not the pack"
    _stub(monkeypatch, ms, chat, pack=_pack(reason="self"))
    await ms.wake("self")
    assert ms._state.last_wake_at is not None
