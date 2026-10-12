"""A wake's push opens its journal entry — through ``mind_service.wake``
(2026-09-25; the wiring in mind_service: ``begin_entry`` before the turn,
``record_entry(conversation_id=…)``, ``finish_entry`` after the journal
step, and the mind log finding pushes by their entry)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from services import thought_journal as tj

NOW = datetime.now(timezone.utc)
CLOSING = "Parker asked me directly where Ellis was, so I told Ellis I had answered."


@pytest.fixture(autouse=True)
def _no_open_entries():
    yield
    tj._OPEN_ENTRIES.clear()


def _pack():
    from services.mind_service import MindPack

    return MindPack(now=NOW, reason="mention", last_wake_at=None, attention={"owner_present": False},
                    messages=[], calendar=[], board="", notes="", pushes="", verdicts=[], thoughts=[], actions=[],
                    surfaces={}, escalations=[{"id": "ev-1", "sender": "Parker", "chat": "Family",
                                               "text": "@edward is Ellis free at 3?", "at": NOW.isoformat()}],
                    state_document=None, extra=None)


class _S:
    model = "gpt-6-astra"
    temperature = 0.4
    system_prompt = "be edward"
    provider = "openai"


def _const(v):
    async def f(*a, **k):
        return v

    return f


def _wire(monkeypatch, script):
    import services.mind_service as ms
    from services.graph.tools import send_push_notification
    from tests.fixtures.tool_loop import FakeGraph, patch_loop_io

    sent = []

    async def send(**kw):
        sent.append(kw)
        return {"sent": 1, "failed": 0, "total": 1}

    monkeypatch.setattr("services.push_service.is_configured", lambda: True)
    monkeypatch.setattr("services.push_service.send_push_notification", send)
    # A wake's push goes through reach_service (2026-10-11): daytime, so
    # quiet hours never turn it into a note whenever the suite runs.
    from services import reach_service

    monkeypatch.setattr(reach_service, "_now", lambda: datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc))
    monkeypatch.delenv("EDWARD_OWNER_CHANNEL", raising=False)
    patch_loop_io(monkeypatch, tools=[send_push_notification], script=script)
    monkeypatch.setattr(ms, "collect_pack", _const(_pack()))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(FakeGraph()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")

    async def still_waiting(ids):
        return set(ids)

    monkeypatch.setattr(ms, "_still_waiting", still_waiting)
    ms.reset_state()
    return ms, sent


def _script():
    from tests.fixtures.tool_loop import ai, call

    # In the offer grammar (reach_service lints a wake's push): whose
    # message and when, one question.
    return [ai("", call("send_push_notification", title="Parker asked about 3pm",
                        body="Parker's 2:55 pm text asked if you're free at 3; I told him 4. Tell him 3 instead?")),
            ai(CLOSING)]


@pytest.mark.integration
async def test_a_wakes_push_opens_its_entry(clean_db, monkeypatch):
    from services.conversation_service import get_conversation
    from services.database import PushLogModel, async_session

    ms, sent = _wire(monkeypatch, _script())
    out = await ms.wake("mention")
    tid = out["thought_id"]
    assert tid and sent[0]["url"] == f"/chat?c={tid}" and sent[0]["conversation_id"] == tid
    conv = await get_conversation(tid)
    assert conv.title == "Pushed you: Parker asked about 3pm" and conv.notified_user is True
    assert tj.current_entry_id() is None and not tj.is_open_entry(tid)

    # The mind's log still finds the push, now by its entry.
    async with async_session() as s:
        s.add(PushLogModel(source="reach:mention", title="Parker asked about 3pm", body="I told him 4.", url=f"/chat?c={tid}",
                           conversation_id=tid, sent_at=datetime.utcnow()))
        await s.commit()
    log = await ms.log(days=1)
    wake = next(w for w in log["wakes"] if w.get("thought_id") == tid)
    assert [p["title"] for p in wake["pushes"]] == ["Parker asked about 3pm"]


@pytest.mark.integration
async def test_the_link_resolves_when_the_journal_times_out(clean_db, monkeypatch):
    from services.conversation_service import get_conversation

    ms, sent = _wire(monkeypatch, _script())

    async def slow(**kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(tj, "record_entry", slow)
    monkeypatch.setattr(tj, "JOURNAL_TIMEOUT_SECONDS", 0.2)
    out = await ms.wake("mention")
    eid = sent[0]["conversation_id"]
    assert out["thought_id"] == eid
    conv = await get_conversation(eid)
    assert conv is not None and conv.title == "Pushed you: Parker asked about 3pm"
    assert CLOSING in conv.initial_message


@pytest.mark.integration
async def test_the_pulse_links_as_before(clean_db, monkeypatch):
    ms, sent = _wire(monkeypatch, _script())
    out = await ms.wake("surfaces", message="[PULSE] tick", conversation_id="edward-surfaces",
                        tools=["send_push_notification"], turn_source="pulse", skip_extraction=True, record=False)
    assert out["ok"] is True and not out.get("thought_id")
    assert sent[0]["url"] == "/chat?c=edward-surfaces" and sent[0]["conversation_id"] == "edward-surfaces"
