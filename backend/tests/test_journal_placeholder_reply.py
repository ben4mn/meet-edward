"""A reply to a journal entry before it is written (2026-09-25).

A wake's push links ``/chat?c=<entry id>`` before the entry exists; the
entry is written when the wake's turn ends (up to ~240 s). Opened in that
window, the conversations API answers a placeholder — but the chat page
still shows the composer, and a reply made ``/api/chat`` create an
ordinary user conversation under the entry's id. When the wake then wrote
its entry, the insert collided, ``create_thought_conversation`` swallowed
the error, and ``record_entry`` found Ellis's conversation under the id and
called it the entry: the narrative was never written, and the ledger row
and the unread dot pointed at his chat.

Now both chat entry points refuse a reply into an entry that is still
being written (409, one plain sentence), and the journal never takes over
a conversation that is not an entry: it writes under a fresh id instead,
and the link on Ellis's phone opens his own conversation.
"""

from __future__ import annotations

import contextvars
import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import thought_journal as tj

PUSH_ARGS = {"title": "Parker asked about 3pm", "body": "I told him Ellis's in a meeting till 4."}
PUSH_ACTION = {"name": "send_push_notification", "args": PUSH_ARGS, "ok": True, "held": False,
               "result": "Push notification sent to 1 device(s)."}
REFUSAL = "Edward is still writing this entry — open it again in a moment."


@pytest.fixture(autouse=True)
def _no_open_entries():
    yield
    tj._OPEN_ENTRIES.clear()


def _open_entry() -> str:
    """An entry a wake is still writing, begun in its own context as a
    wake's task would."""
    return contextvars.copy_context().run(tj.begin_entry)


# ------------------------------------------------------------ the router --

@pytest.fixture
def chat_app(monkeypatch):
    from routers import chat as chat_router

    calls = SimpleNamespace(created=[], incremented=[], streamed=[], answered=[], exists=False)

    async def _settings():
        return SimpleNamespace(system_prompt="x", model="m", temperature=0.5, provider="openai",
                               debug_streaming=False)

    async def _graph():
        return object()

    async def _create(cid, title, **kw):
        calls.created.append(cid)

    async def _exists(cid):
        return calls.exists

    async def _increment(cid):
        calls.incremented.append(cid)

    async def _artifact(**kw):
        return kw["message"]

    async def _wrap(**kw):
        calls.streamed.append(kw["conversation_id"])
        yield {"type": "done"}

    async def _chat_with_memory(**kw):
        calls.answered.append(kw["conversation_id"])
        return "ok"

    monkeypatch.setattr(chat_router, "get_settings", _settings)
    monkeypatch.setattr(chat_router, "get_graph", _graph)
    monkeypatch.setattr(chat_router, "create_conversation", _create)
    monkeypatch.setattr(chat_router, "conversation_exists", _exists)
    monkeypatch.setattr(chat_router, "increment_message_count", _increment)
    monkeypatch.setattr(chat_router, "_message_with_artifact_context", _artifact)
    monkeypatch.setattr(chat_router, "_wrap_chat_stream", _wrap)
    monkeypatch.setattr(chat_router, "chat_with_memory", _chat_with_memory)

    app = FastAPI()
    app.include_router(chat_router.router, prefix="/api")
    return SimpleNamespace(client=TestClient(app), calls=calls)


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/simple"])
def test_a_reply_into_an_entry_still_being_written_is_refused(chat_app, path):
    eid = _open_entry()
    r = chat_app.client.post(path, json={"message": "wait, what did Parker say?", "conversation_id": eid})
    assert r.status_code == 409, r.text
    assert r.json()["detail"] == REFUSAL
    # Nothing was made under the entry's id for the wake to collide with.
    assert chat_app.calls.created == [] and chat_app.calls.incremented == []
    assert chat_app.calls.streamed == [] and chat_app.calls.answered == []


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/simple"])
def test_a_reply_into_a_written_entry_goes_through(chat_app, path):
    """The entry's row lands a moment before its wake closes it: from then
    on a reply is an ordinary reply inside the entry."""
    eid = _open_entry()
    chat_app.calls.exists = True
    r = chat_app.client.post(path, json={"message": "why?", "conversation_id": eid})
    assert r.status_code == 200, r.text
    assert chat_app.calls.incremented == [eid] and chat_app.calls.created == []


@pytest.mark.unit
@pytest.mark.parametrize("path", ["/api/chat", "/api/chat/simple"])
def test_an_id_that_is_no_open_entry_still_starts_a_conversation(chat_app, path):
    """Voice mode hands out ids before their rows exist, and a wake that
    ended without an entry is no longer open: neither is refused."""
    closed = _open_entry()
    tj._OPEN_ENTRIES[closed].closed = True
    for cid in ("voice-id-1", closed):
        r = chat_app.client.post(path, json={"message": "hello", "conversation_id": cid})
        assert r.status_code == 200, r.text
    assert chat_app.calls.created == ["voice-id-1", closed]


# ------------------------------------------------------------ the journal --

async def _owners_conversation(cid: str) -> None:
    from services.conversation_service import create_conversation

    await create_conversation(cid, "wait, what did Parker say?")


async def _assert_untouched(cid: str) -> None:
    from services.conversation_service import get_conversation

    conv = await get_conversation(cid)
    assert conv.source == "user" and conv.title == "wait, what did Parker say?"
    assert conv.initial_message is None and conv.context_pack is None
    assert conv.notified_user is False


async def _count() -> int:
    from sqlalchemy import func, select

    from services.database import ConversationModel, async_session

    async with async_session() as s:
        return (await s.execute(select(func.count()).select_from(ConversationModel))).scalar()


async def _mind_row() -> int:
    from services import event_ledger

    lid = await event_ledger.record_event_now(kind="mind", source="mention", outcome="acted",
                                              reason="Parker asked me directly.", detail={"tools": ["x"]})
    assert lid
    return lid


@pytest.mark.integration
async def test_the_journal_never_takes_over_owners_conversation(clean_db):
    from services.conversation_service import get_conversation

    eid = tj.begin_entry()
    tj.note_linked("send_push_notification", PUSH_ARGS, "Push notification sent to 1 device(s).")
    await _owners_conversation(eid)

    tid = await tj.record_entry(reason="mention", woke_for="Parker mentioned me in Family", reasoning=[],
                                closing_note="Parker asked me directly.", actions=[PUSH_ACTION],
                                wake_thread="edward-mind", turn_id="t-1", conversation_id=eid)
    assert tid and tid != eid
    entry = await get_conversation(tid)
    assert entry.source == "edward_thought" and tj.is_entry_pack(entry.context_pack)
    assert entry.title == "Pushed you: Parker asked about 3pm" and entry.notified_user is True
    await _assert_untouched(eid)

    # Closing the wake keeps that entry; the link on Ellis's phone already
    # opens something (his own conversation), so no second entry is written.
    assert await tj.finish_entry(tid, reason="mention", closing_note="Parker asked me directly.") == tid
    assert await _count() == 2


@pytest.mark.integration
async def test_the_fallback_never_takes_over_owners_conversation(clean_db):
    """The journal never ran (a timeout) and Ellis's conversation holds the
    entry's id: the minimal entry is written under a fresh id and linked
    from the wake's ledger row, which is what Today and the Thoughts panel
    open; the push opens Ellis's own conversation."""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy import select

    from main import app
    from services.auth_service import create_access_token
    from services.conversation_service import get_conversation
    from services.database import LedgerEventModel, async_session

    eid = tj.begin_entry()
    tj.note_linked("send_push_notification", PUSH_ARGS, "Push notification sent to 1 device(s).")
    await _owners_conversation(eid)
    lid = await _mind_row()

    tid = await tj.finish_entry(None, reason="mention", closing_note="Parker asked me directly.",
                                ledger_id=lid, wake_thread="edward-mind")
    assert tid and tid != eid
    entry = await get_conversation(tid)
    assert entry.source == "edward_thought" and tj.is_entry_pack(entry.context_pack)
    assert entry.initial_message.rstrip().endswith("**Why** — Parker asked me directly.")
    await _assert_untouched(eid)
    assert await _count() == 2

    async with async_session() as s:
        row = (await s.execute(select(LedgerEventModel).where(LedgerEventModel.id == lid))).scalar_one()
    assert json.loads(row.detail_json)["thought_id"] == tid

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        mine = await c.get(f"/api/conversations/{eid}")
        theirs = await c.get(f"/api/conversations/{tid}")
    assert mine.status_code == 200 and mine.json()["source"] == "user"
    assert theirs.status_code == 200 and theirs.json()["thought"]["ledger_id"] == lid
