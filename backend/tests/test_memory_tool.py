"""``memory`` — one tool for memories and documents (tools-spec §3.11).

Each action wraps today's implementation. The rails: no hard delete from
Edward's own turns (``remember_forget`` is not reachable — 23 wake calls in
30 days deleted rows; retiring keeps history), Ellis's heard/owner rows and
``owner_confirmed`` pins change only in his own turn, ``forget`` runs only
in Ellis's own conversation, and no document is ever deleted.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from services.graph import tools as legacy
from services.toolset import memory as MEM
from services.toolset import policy as P
from services.toolset.result import ToolFailure

OWNER = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
WAKE = P.TurnPolicy(source="mind")


class Recorder:
    """A stand-in for a legacy @tool: records the args, returns a reply."""

    def __init__(self, reply="ok"):
        self.calls = []
        self.reply = reply

    async def ainvoke(self, args):
        self.calls.append(dict(args))
        return self.reply(args) if callable(self.reply) else self.reply


@pytest.fixture
def tools(monkeypatch):
    names = ["remember", "remember_update", "close_intention", "remember_search", "update_memory_confidence",
             "how_do_i_know", "forget_about", "read_document", "save_document", "edit_document",
             "search_documents", "list_documents", "remember_forget", "delete_document"]
    recs = SimpleNamespace(**{n: Recorder() for n in names})
    for n in names:
        monkeypatch.setattr(legacy, n, getattr(recs, n))
    return recs


async def run(policy, **kw):
    with P.policy_scope(policy):
        return await MEM.memory_impl(**kw)


@pytest.mark.unit
class TestRouting:
    async def test_search_merges_memories_and_documents(self, tools):
        tools.remember_search.reply = "Found 1 memories matching 'dentist':\n- [fact] Dr. Novak (ID: m1)"
        tools.search_documents.reply = "Found 1 document(s) matching 'dentist':\n- **Teeth** (ID: d1)"
        out = await run(WAKE, action="search", query="dentist")
        assert "Dr. Novak" in out and "Teeth" in out
        assert tools.remember_search.calls == [{"query": "dentist", "memory_type": None}]

    async def test_search_one_kind(self, tools):
        await run(WAKE, action="search", query="x", kind="memories", type="fact")
        assert tools.remember_search.calls == [{"query": "x", "memory_type": "fact"}]
        assert tools.search_documents.calls == []
        await run(WAKE, action="search", query="", kind="documents", tags="state")
        assert tools.list_documents.calls == [{"tags": "state"}]

    async def test_remember(self, tools):
        tools.remember.reply = "ok (memory_id: m9)"
        out = await run(WAKE, action="remember", content="Ellis likes figs", type="preference", about="owner",
                        origin="read")
        assert out == "ok (memory_id: m9)"
        assert tools.remember.calls[0]["memory_type"] == "preference"
        assert tools.remember.calls[0]["origin"] == "read"

    async def test_remember_defaults(self, tools):
        await run(WAKE, action="remember", content="I decided to watch ACME")
        call = tools.remember.calls[0]
        assert (call["memory_type"], call["about"], call["origin"]) == ("context", "self", "thought")

    async def test_update_text_and_horizon(self, tools):
        await run(WAKE, action="update", id="m1", content="new words", until="2026-12-01T09:00:00")
        assert tools.remember_update.calls == [{"memory_id": "m1", "new_content": "new words",
                                                "valid_until": "2026-12-01T09:00:00"}]

    async def test_until_now_retires(self, tools):
        tools.remember_update.reply = "ok (retired as of 2026-10-11 07:00 UTC)"
        out = await run(WAKE, action="update", id="m1", until="now")
        assert "retired" in out
        sent = tools.remember_update.calls[0]["valid_until"]
        assert sent.endswith("Z")

    async def test_close_why_docs(self, tools):
        await run(WAKE, action="close", id="m1", outcome="Briefed Theo")
        assert tools.close_intention.calls == [{"memory_id": "m1", "outcome": "Briefed Theo"}]
        await run(WAKE, action="why", id="m1")
        assert tools.how_do_i_know.calls == [{"memory_id": "m1", "claim": None}]
        tools.read_document.reply = "# Edward: state\nall good"
        assert (await run(WAKE, action="doc_read", id="d1")).startswith("# Edward: state")
        tools.save_document.reply = 'Document saved: "Plan" (ID: d2)'
        await run(WAKE, action="doc_write", title="Plan", content="steps")
        assert tools.save_document.calls == [{"title": "Plan", "content": "steps", "tags": None}]
        tools.edit_document.reply = 'Document updated: "Plan" (ID: d2)'
        await run(WAKE, action="doc_write", id="d2", content="more steps")
        assert tools.edit_document.calls == [{"document_id": "d2", "title": None, "content": "more steps",
                                              "tags": None}]

    async def test_missing_arguments_are_named(self, tools):
        with pytest.raises(ToolFailure, match="content"):
            await run(WAKE, action="remember")
        with pytest.raises(ToolFailure, match="id"):
            await run(WAKE, action="update", content="x")
        with pytest.raises(ToolFailure, match="action must be one of"):
            await run(WAKE, action="delete", id="m1")

    async def test_legacy_failure_strings_become_failures(self, tools):
        tools.remember_update.reply = "Memory with ID m404 not found. Cannot update."
        with pytest.raises(ToolFailure, match="not found"):
            await run(WAKE, action="update", id="m404", content="x")
        tools.read_document.reply = "Document d404 not found."
        with pytest.raises(ToolFailure, match="not found"):
            await run(WAKE, action="doc_read", id="d404")


@pytest.mark.unit
class TestNothingIsDeleted:
    async def test_no_action_reaches_a_hard_delete(self, tools):
        for action in ("search", "remember", "update", "close", "why", "doc_read", "doc_write"):
            try:
                await run(WAKE, action=action, query="x", content="x", id="m1", outcome="o", title="t")
            except ToolFailure:
                pass
        assert tools.remember_forget.calls == [] and tools.delete_document.calls == []

    async def test_forget_runs_only_in_owners_own_conversation(self, tools):
        for policy in (WAKE, P.TurnPolicy(source="ping"), P.TurnPolicy(source="scheduled_event", owner_asked=True),
                       P.TurnPolicy(source="heartbeat")):
            with pytest.raises(ToolFailure, match="Ellis's own"):
                await run(policy, action="forget", query="Alex")
        assert tools.forget_about.calls == []
        tools.forget_about.reply = "Would forget 3 memories about Alex..."
        await run(OWNER, action="forget", query="Alex")
        await run(P.TurnPolicy(source="ping_owner", owner_asked=True), action="forget", query="Alex", confirm=True)
        assert [c["subject"] for c in tools.forget_about.calls] == ["Alex", "Alex"]
        assert tools.forget_about.calls[1]["confirm"] is True


@pytest.mark.integration
class TestOwnersRowsOnTheDatabase:
    async def test_a_wake_cannot_rewrite_or_lower_a_pin(self, clean_db):
        from tests.test_memory_tools_keep_owners_rows import PIN, _Turn, _row, _seed_pin

        mid = await _seed_pin()
        with _Turn("mind"), P.policy_scope(WAKE):
            with pytest.raises(ToolFailure, match="Ellis"):
                await MEM.memory_impl(action="update", id=mid, content="no rules")
            with pytest.raises(ToolFailure, match="Ellis"):
                await MEM.memory_impl(action="update", id=mid, confidence=0.1)
            with pytest.raises(ToolFailure, match="Ellis"):
                await MEM.memory_impl(action="update", id=mid, until="now")
        row = await _row(mid)
        assert row.content == PIN and row.confidence == pytest.approx(0.9) and row.valid_until is None

    async def test_his_own_rows_are_his_to_retire(self, clean_db):
        from tests.test_memory_tools_keep_owners_rows import _Turn, _row, _seed

        mid = await _seed(content="I'm watching ACME", memory_type="intention", origin="thought", about="self")
        with _Turn("mind"), P.policy_scope(WAKE):
            out = await MEM.memory_impl(action="update", id=mid, until="now")
        assert "retired" in out
        row = await _row(mid)
        assert row is not None and row.valid_until is not None

    async def test_doc_edit_replaces_exactly_one_match(self, clean_db):
        from services.document_service import Document, get_document_by_id, save_document

        doc = await save_document(Document(id=None, title="Edward: state", content="mood: calm\nnext: walk", tags="state"))
        with P.policy_scope(WAKE):
            out = await MEM.memory_impl(action="doc_edit", id=doc.id, old="mood: calm", new="mood: busy")
            assert out.startswith("Document updated")
            with pytest.raises(ToolFailure, match="0 times"):
                await MEM.memory_impl(action="doc_edit", id=doc.id, old="mood: calm", new="x")
            await MEM.memory_impl(action="doc_edit", id=doc.id, append="note: rain")
        got = await get_document_by_id(doc.id)
        assert got.content == "mood: busy\nnext: walk\nnote: rain"
