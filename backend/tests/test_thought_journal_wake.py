"""A wake that acts writes one journal entry; Ellis reads it, judges it, and
finds it on Today (2026-09-25).

The wake still writes its ledger row and still runs in its own thread; the
entry is an extra ``edward_thought`` conversation built from the turn's
record (``TurnRecord.actions`` / ``reasoning``) and his closing sentences.
Its id rides on the ledger row (``detail.thought_id``), the conversations
API marks it with a ``thought`` field (with Ellis's verdict), the mind's log
carries ``thought_id``, and Today's digest links to it.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest
from langchain_core.messages import AIMessage

NOW = datetime.now(timezone.utc)

SEND = {"name": "send_imessage",
        "args": {"contact": "Parker", "message": "Ellis's in a meeting till 4 - Edward 🤖", "chat_id": "chat123"},
        "result": "iMessage sent to group chat chat123"}
PUSH = {"name": "send_push_notification", "args": {"title": "Parker asked about 3pm"},
        "result": "Push notification sent to 1 device(s)."}
REMEMBER = {"name": "remember", "args": {"content": "Parker asked about Ellis at 3pm"}, "result": "ok (memory_id: m-1)"}
NOTE = {"name": "leave_note", "args": {"title": "What I found on the vet", "body": "…"}, "result": "ok"}
CLOSING = ("Parker asked me directly where Ellis was. The calendar says a meeting until 4, "
           "so I told him that and nothing more.")


def _pack(**kw):
    from services.mind_service import MindPack

    base = dict(
        now=NOW, reason="mention", last_wake_at=None, attention={"owner_present": False},
        messages=[{"sender": "+15550001", "chat_identifier": "chat123", "chat_name": "Family",
                   "text": "@edward is Ellis free at 3?", "is_from_me": False}],
        calendar=["14:00 1:1 with Dana"], board="", notes="", pushes="", verdicts=[], thoughts=[], actions=[],
        surfaces={}, escalations=[{"id": "ev-1", "sender": "Parker", "chat": "Family",
                                   "text": "@edward is Ellis free at 3?", "at": NOW.isoformat()}],
        state_document=None, extra=None,
    )
    base.update(kw)
    return MindPack(**base)


class _S:
    model = "gpt-6-astra"
    temperature = 0.4
    system_prompt = "be edward"
    provider = "openai"


def _const(v):
    async def f(*a, **k):
        return v

    return f


def _reasoning(text, rid):
    return AIMessage(content="", additional_kwargs={"reasoning_items": [
        {"type": "reasoning", "id": rid, "summary": [{"type": "summary_text", "text": text}]}]})


def _fake_chat(actions, text=CLOSING, reasoning=("**Reading**\n\nParker asked whether Ellis is free at 3.",)):
    """A turn that did ``actions`` — recorded the way the executors record
    them — and closed with ``text``."""
    calls = []

    async def chat(**kw):
        from services import turn_ledger

        calls.append(kw)
        rec = turn_ledger.begin_turn(conversation_id=kw["conversation_id"], source=kw["turn_source"],
                                     model="m", provider="openai", message=kw["message"])
        for i, r in enumerate(reasoning):
            rec.note_reasoning(_reasoning(r, f"r{i}"))
        for a in actions:
            rec.note_tool(a["name"], a["result"])
            rec.note_action(a["name"], a["args"], a["result"])
        rec.note_content(text, streaming=False)
        turn_ledger.end_turn(rec, "completed")
        return text

    chat.calls = calls
    return chat


def _stub(monkeypatch, chat, pack=None):
    import services.mind_service as ms
    from services import turn_ledger

    monkeypatch.setattr(turn_ledger, "_schedule", lambda coro: coro.close())
    monkeypatch.setattr(ms, "collect_pack", _const(pack or _pack()))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(object()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")
    monkeypatch.setattr(ms, "chat_with_memory", chat)

    async def still_waiting(ids):
        return set(ids)

    monkeypatch.setattr(ms, "_still_waiting", still_waiting)
    ms.reset_state()
    return ms


async def _entries():
    from sqlalchemy import select

    from services.database import ConversationModel, async_session

    async with async_session() as s:
        rows = (await s.execute(select(ConversationModel).where(ConversationModel.source == "edward_thought")))\
            .scalars().all()
    return [r for r in rows if r.context_pack and "mind_action" in r.context_pack]


async def _ledger(ledger_id):
    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        return (await s.execute(select(LedgerEventModel).where(LedgerEventModel.id == ledger_id))).scalar_one()


async def _client():
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       cookies={"edward_token": create_access_token()})


# ----------------------------------------------------------------- prompt --

def test_the_prompt_asks_for_a_why_owner_can_read():
    from services.mind_service import MIND_SYSTEM_PROMPT, render_pack

    assert "End with one line saying what you did and why." not in MIND_SYSTEM_PROMPT
    assert "two or three plain sentences for Ellis" in MIND_SYSTEM_PROMPT
    assert "what you noticed, what you decided, and why" in MIND_SYSTEM_PROMPT
    assert "When you leave it alone, one line is enough." in MIND_SYSTEM_PROMPT
    assert "Then one line on what you did and why." not in render_pack(_pack())


def test_the_pack_names_the_chats_its_messages_came_from():
    """So the journal can say "Family" instead of "chat123"."""
    from services import thought_journal as tj

    assert tj.chat_names(_pack())["chat123"] == "Family"


@pytest.mark.integration
async def test_a_chat_the_pack_did_not_name_is_named_from_the_heartbeat(clean_db, monkeypatch):
    from services.database import HeartbeatEventModel, async_session

    async with async_session() as s:
        s.add(HeartbeatEventModel(id="ev-9", source="imessage", event_type="message_received", sender="+1303",
                                  chat_identifier="chat777", chat_name="Book club", summary="x",
                                  triage_status="escalated"))
        await s.commit()
    send = {"name": "send_imessage", "args": {"contact": "Parker", "message": "yes", "chat_id": "iMessage;+;chat777"},
            "result": "iMessage sent to group chat iMessage;+;chat777"}
    ms = _stub(monkeypatch, _fake_chat([send]))
    out = await ms.wake("mention")
    entry = (await _entries())[0]
    assert entry.id == out["thought_id"] and entry.title == "Replied to Parker in Book club"
    assert "- Sent an iMessage to Parker (Book club): “yes”" in entry.initial_message


# ----------------------------------------------------------------- wake --

@pytest.mark.integration
async def test_a_wake_that_replies_writes_one_journal_entry(clean_db, monkeypatch):
    ms = _stub(monkeypatch, _fake_chat([SEND, REMEMBER]))
    out = await ms.wake("mention", required_tools=["send_imessage"])
    assert out["ok"] is True and out["outcome"] == "acted"
    tid = out["thought_id"]
    assert tid

    entries = await _entries()
    assert [e.id for e in entries] == [tid]
    entry = entries[0]
    assert entry.title == "Replied to Parker in Family"
    assert entry.notified_user is True
    narrative = entry.initial_message
    assert narrative.startswith("**Why I looked** — A mention: Parker mentioned me in Family")
    assert "**What I noticed** — Parker asked whether Ellis is free at 3." in narrative
    assert "- Sent an iMessage to Parker (Family): “Ellis's in a meeting till 4”" in narrative
    assert "- Also: remembered: Parker asked about Ellis at 3pm" in narrative
    assert narrative.rstrip().endswith(f"**Why** — {CLOSING}")

    pack = json.loads(entry.context_pack)
    assert pack["kind"] == "mind_action" and pack["ledger_id"] == out["ledger_id"]
    assert pack["reason"] == "mention" and pack["wake_thread"] == "edward-mind"
    assert pack["turn_id"] == out["turn_id"] and pack["outward"] is True
    assert pack["acted_at"].endswith("Z")
    assert pack["pack_excerpt"].startswith("[WAKE: mention]")
    assert pack["actions"] == ["Sent an iMessage to Parker (Family): “Ellis's in a meeting till 4”"]

    row = await _ledger(out["ledger_id"])
    assert json.loads(row.detail_json)["thought_id"] == tid
    assert len(row.detail_json) <= 600

    log = await ms.log(days=1)
    assert log["wakes"][0]["thought_id"] == tid


@pytest.mark.integration
async def test_a_wake_that_only_remembers_writes_no_entry(clean_db, monkeypatch):
    ms = _stub(monkeypatch, _fake_chat([REMEMBER], text="Nothing needs me; noted Parker's question."))
    out = await ms.wake("manual")
    assert out["ok"] is True and out["outcome"] == "acted"
    assert not out.get("thought_id")
    assert await _entries() == []
    log = await ms.log(days=1)
    assert "thought_id" not in log["wakes"][0]


@pytest.mark.integration
async def test_a_failed_send_alone_writes_no_entry(clean_db, monkeypatch):
    failed = dict(SEND, result="Failed to send iMessage: Messages is not running")
    ms = _stub(monkeypatch, _fake_chat([failed]))
    out = await ms.wake("mention")
    assert not out.get("thought_id") and await _entries() == []


@pytest.mark.integration
async def test_the_pulse_never_writes_an_entry(clean_db, monkeypatch):
    ms = _stub(monkeypatch, _fake_chat([PUSH, {"name": "update_widget", "args": {"sections": []},
                                               "result": "Widget updated with 1 section(s)."}]))
    out = await ms.wake("surfaces", message="[PULSE] tick", conversation_id="edward-surfaces",
                        tools=["update_widget", "send_push_notification"], turn_source="pulse",
                        skip_extraction=True, record=False)
    assert out["ok"] is True and not out.get("thought_id")
    assert await _entries() == []


@pytest.mark.integration
async def test_a_wake_with_its_own_trigger_that_leaves_a_note_is_journaled_without_a_ledger_row(clean_db,
                                                                                                   monkeypatch):
    ms = _stub(monkeypatch, _fake_chat([NOTE], text="Quinn asked for the link at 2:10 pm; I left Ellis a note."))
    out = await ms.wake("speak", message="[SPEAK] Quinn asked for your referral link at 2:10 pm",
                        conversation_id=ms.SPEAK_CONVERSATION_ID, tools=list(ms.legacy_speak_tools("note")),
                        turn_source="mind", record=False)
    tid = out["thought_id"]
    entries = await _entries()
    assert [e.id for e in entries] == [tid]
    pack = json.loads(entries[0].context_pack)
    assert pack["ledger_id"] is None and pack["wake_thread"] == "edward-speak"
    assert entries[0].initial_message.startswith(
        "**Why I looked** — Something I noticed: Quinn asked for your referral link at 2:10 pm")
    # A note is not a message to anyone: no unread dot.
    assert entries[0].notified_user is False


@pytest.mark.integration
async def test_a_wake_that_sent_then_timed_out_is_still_journaled(clean_db, monkeypatch):
    """A send cannot be taken back: a wake that replied and then ran out of
    time still shows what it did, and says it did not finish."""

    async def chat(**kw):
        from services import turn_ledger

        rec = turn_ledger.begin_turn(conversation_id=kw["conversation_id"], source=kw["turn_source"],
                                     model="m", provider="openai", message=kw["message"])
        rec.note_tool(SEND["name"], SEND["result"])
        rec.note_action(SEND["name"], SEND["args"], SEND["result"])
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            turn_ledger.end_turn(rec, "stopped")
            raise

    ms = _stub(monkeypatch, chat)
    monkeypatch.setattr(ms, "wake_timeout_for", lambda reason, now=None: 0.2)
    out = await ms.wake("mention")
    assert out["ok"] is False
    entries = await _entries()
    assert [e.id for e in entries] == [out["thought_id"]]
    assert "- Sent an iMessage to Parker (Family)" in entries[0].initial_message
    assert "**Why** — This wake did not finish (" in entries[0].initial_message
    pack = json.loads(entries[0].context_pack)
    row = await _ledger(pack["ledger_id"])
    assert row.outcome == "failed" and json.loads(row.detail_json)["thought_id"] == out["thought_id"]


@pytest.mark.integration
async def test_the_journal_never_fails_or_holds_up_a_wake(clean_db, monkeypatch):
    from services import thought_journal

    ms = _stub(monkeypatch, _fake_chat([SEND]))

    async def boom(**kw):
        raise RuntimeError("journal broke")

    monkeypatch.setattr(thought_journal, "record_entry", boom)
    out = await ms.wake("mention")
    assert out["ok"] is True and out["outcome"] == "acted" and not out.get("thought_id")

    async def slow(**kw):
        await asyncio.sleep(30)

    monkeypatch.setattr(thought_journal, "record_entry", slow)
    monkeypatch.setattr(thought_journal, "JOURNAL_TIMEOUT_SECONDS", 0.2)
    ms.reset_state()
    started = asyncio.get_running_loop().time()
    out = await ms.wake("mention")
    assert out["ok"] is True and not out.get("thought_id")
    assert asyncio.get_running_loop().time() - started < 5


# ------------------------------------------------------------ Ellis's side --

@pytest.mark.integration
async def test_conversations_api_marks_entries_with_their_verdict(clean_db, monkeypatch):
    from services.conversation_service import create_conversation

    ms = _stub(monkeypatch, _fake_chat([SEND]))
    out = await ms.wake("mention")
    tid, lid = out["thought_id"], out["ledger_id"]
    # A thought conversation whose pack is cut mid-JSON must not break the list.
    await create_conversation("broken", title="broken", source="edward_thought",
                              context_pack='{"kind": "mind_action", "ledger_id": 4')

    async with await _client() as c:
        r = await c.get("/api/conversations?group=edwards")
        assert r.status_code == 200, r.text
        by_id = {x["id"]: x for x in r.json()["conversations"]}
        thought = by_id[tid]["thought"]
        assert thought == {"kind": "mind_action", "ledger_id": lid, "reason": "mention",
                           "acted_at": thought["acted_at"], "outward": True, "verdict": None, "reached": True}
        assert thought["acted_at"].endswith("Z")
        assert by_id["edward-mind"]["thought"] is None
        assert by_id["broken"]["thought"] is None

        r = await c.post("/api/mind/verdict", json={"ledger_id": lid, "verdict": "right_call"})
        assert r.status_code == 200, r.text
        r = await c.post("/api/mind/verdict", json={"ledger_id": lid, "verdict": "wrong_call",
                                                    "note": "don't answer for me"})
        assert r.status_code == 200, r.text

        r = await c.get("/api/conversations?group=edwards")
        by_id = {x["id"]: x for x in r.json()["conversations"]}
        assert by_id[tid]["thought"]["verdict"] == "wrong_call"

        r = await c.get(f"/api/conversations/{tid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["thought"]["verdict"] == "wrong_call" and body["thought"]["ledger_id"] == lid
        assert body["messages"][0]["role"] == "assistant"
        assert body["messages"][0]["content"].startswith("**Why I looked**")

        r = await c.get("/api/conversations/edward-mind")
        assert r.json()["thought"] is None


@pytest.mark.integration
async def test_the_api_says_whether_an_entry_reached_someone(clean_db):
    """``thought.reached`` (followups L3, 2026-09-27): a note or a held send
    is outward but reached nobody, and the "reached out" chip reads this,
    not ``outward``. An entry written before the field says ``null`` —
    unknown, never a guess from ``outward``."""
    from services.conversation_service import create_conversation

    def pack(ledger_id, **extra):
        return json.dumps({"kind": "mind_action", "ledger_id": ledger_id, "reason": "free",
                           "acted_at": "2026-09-27T10:00:00Z", "outward": True, **extra})

    await create_conversation("note-only", title="Left a note", source="edward_thought",
                              context_pack=pack(9001, reached=False))
    await create_conversation("legacy", title="Older entry", source="edward_thought", context_pack=pack(9002))

    async with await _client() as c:
        r = await c.get("/api/conversations?group=edwards")
        assert r.status_code == 200, r.text
        by_id = {x["id"]: x for x in r.json()["conversations"]}
        assert by_id["note-only"]["thought"]["reached"] is False
        assert by_id["legacy"]["thought"]["reached"] is None

        r = await c.get("/api/conversations/note-only")
        assert r.status_code == 200, r.text
        assert r.json()["thought"]["reached"] is False


@pytest.mark.integration
async def test_a_verdict_on_an_entry_reaches_the_next_pack(clean_db, monkeypatch):
    ms = _stub(monkeypatch, _fake_chat([SEND]))
    out = await ms.wake("mention")
    await ms.record_verdict(out["ledger_id"], "wrong_call", "don't answer for me")
    verdicts = await ms._verdicts_source(None)
    v = next(v for v in verdicts if v["kind"] == "mind_verdict")
    assert v["verdict"] == "wrong_call" and v["note"] == "don't answer for me"
    assert v["about"].startswith("mention: acted — Parker asked me directly")


# ------------------------------------------------------------------ Today --

def test_today_did_links_each_action_to_its_entry():
    from services.today_service import TodayInputs, compose_today

    now = datetime(2026, 9, 25, 15, 0, tzinfo=timezone.utc)
    actions = [
        {"ledger_id": 7, "thought_id": "t-7", "title": "Replied to Parker in Family",
         "first_line": "Sent an iMessage to Parker (Family): “in a meeting till 4”", "at": now - timedelta(hours=1)},
        {"ledger_id": 3, "thought_id": "t-3", "title": "Old news", "first_line": "x",
         "at": now - timedelta(hours=25)},
    ]
    did = compose_today(TodayInputs(actions=actions), now=now)["did"]
    rows = [d for d in did if d["kind"] == "action"]
    assert rows == [{"id": "action:7", "kind": "action", "title": "Replied to Parker in Family",
                     "detail": "Sent an iMessage to Parker (Family): “in a meeting till 4”",
                     "at": "2026-09-25T14:00:00Z", "href": "/chat?c=t-7"}]


@pytest.mark.integration
async def test_today_loads_the_days_entries(clean_db, monkeypatch):
    from services import today_service

    ms = _stub(monkeypatch, _fake_chat([SEND]))
    out = await ms.wake("mention")
    rows = await today_service._mind_actions(datetime.now(timezone.utc))
    assert rows and rows[0]["thought_id"] == out["thought_id"] and rows[0]["ledger_id"] == out["ledger_id"]
    assert rows[0]["title"] == "Replied to Parker in Family"
    assert rows[0]["first_line"].startswith("Sent an iMessage to Parker (Family)")

    today = await today_service.get_today()
    action_rows = [d for d in today["did"] if d["kind"] == "action"]
    assert action_rows and action_rows[0]["href"] == f"/chat?c={out['thought_id']}"


# --------------------------------------------------------- end to end --

@pytest.mark.integration
async def test_end_to_end_through_the_real_tool_loop(clean_db, monkeypatch):
    """The real ``chat_with_memory`` loop with a scripted model: the
    executor records the send, the loop records the reasoning, the wake
    writes the entry."""
    import services.mind_service as ms
    from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, call, patch_loop_io

    tools = [FakeTool("send_imessage", "iMessage sent to group chat chat123"),
             FakeTool("remember", "ok (memory_id: m-1)"), FakeTool("remember_search", "nothing")]
    script = [
        ai("", call("remember_search", query="Ellis this afternoon"),
           additional_kwargs={"reasoning_items": [{"type": "reasoning", "id": "r1", "summary": [
               {"type": "summary_text", "text": "**Reading the thread**\n\nParker asked if Ellis is free at 3."}]}]}),
        ai("", call("send_imessage", contact="Parker", message="Ellis's in a meeting till 4", chat_id="chat123"),
           call("remember", content="Told Parker Ellis is busy till 4", origin="did")),
        ai(CLOSING),
    ]
    patch_loop_io(monkeypatch, tools=tools, script=script)
    monkeypatch.setattr(ms, "collect_pack", _const(_pack()))
    monkeypatch.setattr(ms, "_settings", _const(_S()))
    monkeypatch.setattr(ms, "_graph", _const(FakeGraph()))
    monkeypatch.setattr(ms, "quota_state", lambda: "ok")

    async def still_waiting(ids):
        return set(ids)

    monkeypatch.setattr(ms, "_still_waiting", still_waiting)
    ms.reset_state()

    out = await ms.wake("mention", required_tools=["send_imessage"])
    assert out["ok"] is True and out["outcome"] == "acted", out
    assert tools[0].calls == [{"contact": "Parker", "message": "Ellis's in a meeting till 4", "chat_id": "chat123"}]
    entry = (await _entries())[0]
    assert entry.id == out["thought_id"] and entry.title == "Replied to Parker in Family"
    assert "**What I noticed** — Parker asked if Ellis is free at 3." in entry.initial_message
    assert "- Sent an iMessage to Parker (Family): “Ellis's in a meeting till 4”" in entry.initial_message
    assert "- Also: remembered: Told Parker Ellis is busy till 4" in entry.initial_message
    assert entry.initial_message.rstrip().endswith(f"**Why** — {CLOSING}")
    assert "remember_search" not in entry.initial_message
