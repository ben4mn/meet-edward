"""A push or a note from a wake opens its journal entry (2026-09-25).

Before: the push tool linked to the conversation it ran in, so every push a
wake sent opened ``/chat?c=edward-mind`` — the mind's raw thread of 10–20K
character packs — and a note linked nowhere. Tapping "Parker asked about
3pm" should open the entry that says what Edward saw, did and why.

A wake allocates its entry's id before the turn (``begin_entry``, a
task-local); the push and note tools link to ``/chat?c=<entry id>`` and
use that id for acks and the unread dot; the wake writes the entry under
that id afterwards (``record_entry(conversation_id=…)``). The link is
handed out before the entry exists, so it must always come to resolve:
``record_entry`` is the only creator, falls back to a minimal entry when
its write fails, and ``finish_entry`` writes one when the journal never
ran (a timeout). While the wake is still running the conversations API
answers a placeholder instead of 404.
"""

from __future__ import annotations

import asyncio
import contextvars
import json

import pytest

from services import thought_journal as tj

PUSH_ARGS = {"title": "Parker asked about 3pm", "body": "I told him Ellis's in a meeting till 4."}


@pytest.fixture(autouse=True)
def _no_open_entries():
    yield
    tj._OPEN_ENTRIES.clear()


def _fresh(fn):
    """Run a sync check in its own context, as a wake's task would."""
    return contextvars.copy_context().run(fn)


# ------------------------------------------------------------------ pure --

class TestTheEntryId:
    pytestmark = pytest.mark.unit

    def test_nothing_pending_outside_a_wake(self):
        assert _fresh(tj.current_entry_id) is None

    def test_begin_allocates_and_sets_it(self):
        def run():
            eid = tj.begin_entry()
            assert len(eid) == 36 and tj.current_entry_id() == eid
            assert tj.is_open_entry(eid)
            again = tj.begin_entry()
            assert again != eid and tj.current_entry_id() == again
            return eid

        _fresh(run)

    def test_clear_entry_is_for_the_pulse(self):
        def run():
            eid = tj.begin_entry()
            assert tj.clear_entry() is None
            assert tj.current_entry_id() is None
            # Clearing never closes the entry itself: a wake whose context a
            # pulse inherited still links to it.
            assert tj.is_open_entry(eid)

        _fresh(run)

    def test_the_link_rules(self):
        own = "edward-mind"
        assert tj.entry_url(None, own, None) is None
        assert tj.entry_url("/today", own, None) == "/today"
        assert tj.entry_url(None, own, "e1") == "/chat?c=e1"
        assert tj.entry_url("  ", own, "e1") == "/chat?c=e1"
        # A link to the wake's own raw thread means "this thought".
        assert tj.entry_url("/chat?c=edward-mind", own, "e1") == "/chat?c=e1"
        # Anywhere else he chose stays his choice.
        assert tj.entry_url("/today", own, "e1") == "/today"
        assert tj.entry_url("/chat?c=chat-7", own, "e1") == "/chat?c=chat-7"


async def test_tools_in_child_tasks_see_and_mark_the_entry():
    eid = tj.begin_entry()

    async def tool():
        assert tj.current_entry_id() == eid
        tj.note_linked("send_push_notification", PUSH_ARGS, "Push notification sent to 1 device(s).")

    await asyncio.create_task(tool())
    assert [a["name"] for a in tj._pending_entry.get().linked] == ["send_push_notification"]


# ----------------------------------------------------------------- tools --

def _fake_push(monkeypatch, result=None):
    sent, notified = [], []

    async def send(**kw):
        sent.append(kw)
        return result or {"sent": 1, "failed": 0, "total": 1}

    async def mark(cid):
        notified.append(cid)
        return True

    monkeypatch.setattr("services.push_service.is_configured", lambda: True)
    monkeypatch.setattr("services.push_service.send_push_notification", send)
    monkeypatch.setattr("services.conversation_service.mark_user_notified", mark)
    return sent, notified, []


async def test_a_push_from_a_wake_links_to_its_entry(monkeypatch):
    from services.graph.tools import send_push_notification, set_current_conversation_id

    sent, notified, _ = _fake_push(monkeypatch)
    set_current_conversation_id("edward-mind")
    eid = tj.begin_entry()
    out = await send_push_notification.ainvoke(PUSH_ARGS)
    assert out.startswith("Push notification sent"), out
    assert sent[0]["url"] == f"/chat?c={eid}"
    # Opening the entry acks the push (push_log).
    assert sent[0]["conversation_id"] == eid
    assert notified == [eid]
    assert tj._pending_entry.get().linked[0]["args"]["title"] == "Parker asked about 3pm"


async def test_an_urgent_push_from_a_wake_links_to_its_entry(monkeypatch):
    """The interrupt ladder is gone (2026-10-11): an urgent push is a plain
    push, still linked to the entry."""
    from services.graph.tools import send_push_notification, set_current_conversation_id

    sent, _, _ = _fake_push(monkeypatch)
    set_current_conversation_id("edward-mind")
    eid = tj.begin_entry()
    out = await send_push_notification.ainvoke(dict(PUSH_ARGS, urgency="high"))
    assert out.startswith("Push notification sent"), out
    assert sent[0]["url"] == f"/chat?c={eid}" and sent[0]["conversation_id"] == eid
    assert tj._pending_entry.get().linked


async def test_a_push_he_pointed_elsewhere_keeps_its_url(monkeypatch):
    from services.graph.tools import send_push_notification, set_current_conversation_id

    sent, _, _ = _fake_push(monkeypatch)
    set_current_conversation_id("edward-mind")
    eid = tj.begin_entry()
    await send_push_notification.ainvoke(dict(PUSH_ARGS, url="/settings/room/house"))
    assert sent[0]["url"] == "/settings/room/house" and sent[0]["conversation_id"] == eid


async def test_a_push_outside_a_wake_links_as_before(monkeypatch):
    from services.graph.tools import send_push_notification, set_current_conversation_id

    sent, notified, _ = _fake_push(monkeypatch)
    set_current_conversation_id("chat-1")
    await send_push_notification.ainvoke(PUSH_ARGS)
    assert sent[0]["url"] == "/chat?c=chat-1" and sent[0]["conversation_id"] == "chat-1"
    assert notified == ["chat-1"]


async def test_a_push_that_reached_nobody_hands_out_no_link(monkeypatch):
    from services.graph.tools import send_push_notification, set_current_conversation_id

    _fake_push(monkeypatch, result={"sent": 0, "failed": 1, "total": 1})
    set_current_conversation_id("edward-mind")
    tj.begin_entry()
    out = await send_push_notification.ainvoke(PUSH_ARGS)
    assert out.startswith("Push notification failed")
    assert tj._pending_entry.get().linked == []


def _fake_note(monkeypatch):
    left = []

    async def leave(**kw):
        left.append(kw)
        return "note-1"

    monkeypatch.setattr("services.notes_service.leave_note", leave)
    return left


async def test_a_note_from_a_wake_links_to_its_entry(monkeypatch):
    from services.graph.tools import leave_note, set_current_conversation_id

    left = _fake_note(monkeypatch)
    set_current_conversation_id("edward-research")
    eid = tj.begin_entry()
    out = await leave_note.ainvoke({"title": "What I found on the vet", "body": "Two clinics open Sunday."})
    assert out == "ok"
    assert left[0]["url"] == f"/chat?c={eid}"
    assert tj._pending_entry.get().linked[0]["name"] == "leave_note"


async def test_a_note_he_pointed_elsewhere_hands_out_no_entry_link(monkeypatch):
    from services.graph.tools import leave_note, set_current_conversation_id

    left = _fake_note(monkeypatch)
    set_current_conversation_id("edward-mind")
    tj.begin_entry()
    await leave_note.ainvoke({"title": "Pet board", "body": "Biscuit's dose moved.", "url": "/hub/pets"})
    assert left[0]["url"] == "/hub/pets"
    assert tj._pending_entry.get().linked == []


async def test_a_note_outside_a_wake_has_no_link(monkeypatch):
    from services.graph.tools import leave_note, set_current_conversation_id

    left = _fake_note(monkeypatch)
    set_current_conversation_id("chat-1")
    await leave_note.ainvoke({"title": "x", "body": "y"})
    assert left[0]["url"] is None


# ------------------------------------------------------------- the entry --

PUSH_ACTION = {"name": "send_push_notification", "args": PUSH_ARGS, "ok": True, "held": False,
               "result": "Push notification sent to 1 device(s)."}


async def _mind_row() -> int:
    from services import event_ledger

    lid = await event_ledger.record_event_now(kind="mind", source="mention", outcome="acted",
                                              reason="Parker asked me directly.", detail={"tools": ["x"]})
    assert lid
    return lid


async def _client():
    from httpx import ASGITransport, AsyncClient

    from main import app
    from services.auth_service import create_access_token

    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                       cookies={"edward_token": create_access_token()})


@pytest.mark.integration
async def test_the_entry_is_written_under_the_linked_id(clean_db):
    from services.conversation_service import get_conversation

    eid = tj.begin_entry()
    tid = await tj.record_entry(reason="mention", woke_for="Parker mentioned me in Family", reasoning=[],
                                closing_note="Parker asked me directly.", actions=[PUSH_ACTION], ledger_id=None,
                                wake_thread="edward-mind", turn_id="t-1", conversation_id=eid)
    assert tid == eid
    conv = await get_conversation(eid)
    assert conv.title == "Pushed you: Parker asked about 3pm" and conv.notified_user is True
    assert await tj.finish_entry(tid, reason="mention", closing_note="…") == eid
    assert tj.current_entry_id() is None and not tj.is_open_entry(eid)


@pytest.mark.integration
async def test_a_failed_entry_write_falls_back_to_a_minimal_entry(clean_db, monkeypatch):
    from services.conversation_service import get_conversation

    def boom(**kw):
        raise RuntimeError("compose broke")

    monkeypatch.setattr(tj, "compose_entry", boom)
    eid = tj.begin_entry()
    tid = await tj.record_entry(reason="mention", woke_for="", reasoning=[],
                                closing_note="Parker asked me directly. Nothing else needed me.",
                                actions=[PUSH_ACTION], wake_thread="edward-mind", conversation_id=eid)
    assert tid == eid
    conv = await get_conversation(eid)
    assert conv.source == "edward_thought" and tj.is_entry_pack(conv.context_pack)
    assert "Parker asked me directly. Nothing else needed me." in conv.initial_message
    assert conv.notified_user is True


@pytest.mark.integration
async def test_the_link_resolves_when_the_journal_never_ran(clean_db, monkeypatch):
    """The wake pushed, then its journal timed out: the link Ellis has on his
    phone still opens an entry, linked from the wake's ledger row."""
    import services.conversation_service as cs
    from services.conversation_service import get_conversation
    from services.graph.tools import send_push_notification, set_current_conversation_id

    real_mark = cs.mark_user_notified
    _fake_push(monkeypatch)
    set_current_conversation_id("edward-mind")
    eid = tj.begin_entry()
    await send_push_notification.ainvoke(PUSH_ARGS)
    monkeypatch.setattr(cs, "mark_user_notified", real_mark)
    lid = await _mind_row()

    tid = await tj.finish_entry(None, reason="mention", closing_note="Parker asked me directly.",
                                ledger_id=lid, wake_thread="edward-mind")
    assert tid == eid
    conv = await get_conversation(eid)
    assert conv.title == "Pushed you: Parker asked about 3pm"
    assert "- Pushed you: “Parker asked about 3pm”" in conv.initial_message
    assert conv.initial_message.rstrip().endswith("**Why** — Parker asked me directly.")
    pack = json.loads(conv.context_pack)
    assert pack["kind"] == "mind_action" and pack["ledger_id"] == lid and pack["outward"] is True
    assert conv.notified_user is True

    from sqlalchemy import select

    from services.database import LedgerEventModel, async_session

    async with async_session() as s:
        row = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.id == lid))).scalar_one()
    assert json.loads(row.detail_json)["thought_id"] == eid

    async with await _client() as c:
        r = await c.get(f"/api/conversations/{eid}")
    assert r.status_code == 200, r.text
    assert r.json()["thought"]["ledger_id"] == lid
    assert r.json()["messages"][0]["content"] == conv.initial_message


@pytest.mark.integration
async def test_an_entry_cut_off_after_its_write_is_kept_and_linked(clean_db):
    """The journal's insert landed, then the timeout cancelled it before
    it linked the ledger row: finish keeps that entry, links it, and does
    not write a second one."""
    from sqlalchemy import func, select

    from services.database import ConversationModel, async_session

    eid = tj.begin_entry()
    tj.note_linked("send_push_notification", PUSH_ARGS, "Push notification sent to 1 device(s).")
    await tj.record_entry(reason="mention", woke_for="", reasoning=[], closing_note="Parker asked.",
                          actions=[PUSH_ACTION], conversation_id=eid)
    lid = await _mind_row()
    assert await tj.finish_entry(None, reason="mention", closing_note="Parker asked.", ledger_id=lid) == eid
    async with async_session() as s:
        n = (await s.execute(select(func.count()).select_from(ConversationModel))).scalar()
    assert n == 1


@pytest.mark.integration
async def test_a_wake_that_linked_nothing_leaves_nothing(clean_db):
    from services.conversation_service import get_conversation

    eid = tj.begin_entry()
    assert await tj.finish_entry(None, reason="messages", closing_note="Nothing needs me.") is None
    assert await get_conversation(eid) is None
    assert tj.current_entry_id() is None and not tj.is_open_entry(eid)


@pytest.mark.integration
async def test_the_api_answers_a_placeholder_while_the_wake_runs(clean_db):
    eid = tj.begin_entry()
    async with await _client() as c:
        r = await c.get(f"/api/conversations/{eid}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["source"] == "edward_thought" and body["messages"][0]["role"] == "assistant"
        assert "still" in body["messages"][0]["content"].lower()
        await tj.finish_entry(None, reason="messages", closing_note="Nothing needs me.")
        r = await c.get(f"/api/conversations/{eid}")
        assert r.status_code == 404
        r = await c.get("/api/conversations/never-was")
        assert r.status_code == 404


@pytest.mark.integration
async def test_create_thought_conversation_takes_an_id(clean_db):
    from services.conversation_service import get_conversation
    from services.thought_conversation_service import create_thought_conversation

    cid = await create_thought_conversation("A title", "A narrative.", conversation_id="fixed-id-1")
    assert cid == "fixed-id-1"
    assert (await get_conversation("fixed-id-1")).initial_message == "A narrative."
