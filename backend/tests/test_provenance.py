"""Provenance — "how do you know that?" answered from records (plan Phase 5).

Muse lost a user's trust in one sentence: asked how it knew something from
their texts, it said it only saw "the incoming notification stream", when
it had synced their whole chat.db. It narrated its own plumbing from
nothing. Edward already stores ``origin``/``about`` and the source
conversation on every memory; this adds ``memories.source_ref`` (a small
JSON of where the row came from: the turn, the extraction profile, the
event ids when the caller knows them) and a ``how_do_i_know`` tool that
answers only from those records.
"""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from services import provenance_service as ps


# ---------------------------------------------------------------- pure --

@pytest.mark.unit
def test_build_and_dump_source_ref_are_compact_and_bounded():
    ref = ps.build_source_ref("extraction", conversation_id="c-1", turn_id="t-1", turn_source="chat",
                              profile="chat", extra={"event_ids": [f"e{i}" for i in range(40)], "empty": None})
    assert ref["kind"] == "extraction" and ref["conversation_id"] == "c-1" and ref["profile"] == "chat"
    assert "empty" not in ref
    assert len(ref["event_ids"]) == ps.MAX_LIST_ITEMS
    text = ps.dump_source_ref(ref)
    assert len(text) <= ps.MAX_SOURCE_REF_CHARS
    assert ps.parse_source_ref(text) == ref
    assert ps.parse_source_ref(None) == {} and ps.parse_source_ref("not json") == {}
    assert ps.dump_source_ref({}) is None


@pytest.mark.unit
def test_merge_source_ref_keeps_the_first_and_notes_later_sources():
    first = ps.dump_source_ref({"kind": "extraction", "conversation_id": "c-1"})
    second = ps.dump_source_ref({"kind": "remember", "conversation_id": "c-2", "turn_id": "t-9"})
    merged = ps.parse_source_ref(ps.merge_source_ref(first, second))
    assert merged["conversation_id"] == "c-1"
    assert merged["also"] == [{"kind": "remember", "conversation_id": "c-2", "turn_id": "t-9"}]
    for i in range(10):
        merged = ps.parse_source_ref(ps.merge_source_ref(ps.dump_source_ref(merged),
                                                         ps.dump_source_ref({"kind": "extraction", "conversation_id": f"x{i}"})))
    assert len(merged["also"]) == ps.MAX_ALSO
    assert ps.merge_source_ref(first, None) == first
    assert ps.merge_source_ref(None, None) is None


@pytest.mark.unit
@pytest.mark.parametrize("existing", [None, "", "not json", "[1, 2]"])
def test_a_row_without_provenance_stays_unrecorded_when_merged(existing):
    """merge_source_ref(None, new) returned ``new``, so the first source to
    repeat a pre-provenance row became where it came from (e213dba7, from
    Ellis's chat, came to read as a wake's). Batch A1 guarded its own call in
    memory_service; the helper now does it for every caller (2026-09-27)."""
    second = ps.dump_source_ref({"kind": "remember", "conversation_id": "c-2", "turn_id": "t-9",
                                 "turn_source": "mind"})
    merged = ps.parse_source_ref(ps.merge_source_ref(existing, second))
    assert merged["kind"] == "unrecorded"
    assert "conversation_id" not in merged and "turn_source" not in merged
    assert merged["also"] == [{"kind": "remember", "conversation_id": "c-2", "turn_id": "t-9",
                               "turn_source": "mind"}]


@pytest.mark.unit
def test_describe_origin_says_what_the_record_says():
    assert "Ellis said" in ps.describe_origin("heard", "owner")
    assert "read" in ps.describe_origin("read", "contact")
    assert "concluded" in ps.describe_origin("thought", "self")
    assert "did" in ps.describe_origin("did", "self")


@pytest.mark.unit
def test_fold_source_is_named():
    assert ps.fold_source("fold:relationship_facts:42") == ("relationship_facts", "42")
    assert ps.fold_source("c-1") is None
    assert ps.fold_source(None) is None


@pytest.mark.unit
def test_format_provenance_cites_stored_fields_and_ends_with_the_rule():
    record = {
        "memory": {"id": "m-1", "content": "Ellis's sister lives in Boston", "origin": "heard", "about": "owner",
                   "source_type": "user_stated", "created_at": "2026-09-12T14:03:00Z", "confidence": 0.9,
                   "tier": "belief", "reinforcement_count": 3, "source_conversation_id": "c-1"},
        "conversation": {"id": "c-1", "title": "Weekend plans", "source": "user", "channel": "text"},
        "source_ref": {"kind": "extraction", "profile": "chat", "turn_source": "chat", "turn_id": "t-9"},
        "chat": None,
        "fold": None,
    }
    text = ps.format_provenance([record], claim="where does my sister live")
    for needle in ("m-1", "Ellis's sister lives in Boston", "heard/owner", "user_stated", "2026-09-12",
                   "0.90", "belief", "3", "Weekend plans", "c-1", "source user", "channel text",
                   '"profile": "chat"', "t-9"):
        assert needle in text, needle
    assert text.rstrip().endswith(ps.PROVENANCE_RULE)


@pytest.mark.unit
def test_the_stored_time_is_owners_clock_with_its_zone(monkeypatch):
    """``created_at`` is UTC. ``_memory_dict`` marks it with a Z so the record
    would say "UTC", but the old formatter clipped at 20 characters and cut
    the " UTC" off: a find stored at 6:58 pm in Springfield read "stored:
    2026-10-07 23:58:00" — a time Edward took as Ellis's, five hours ahead of
    the clock in his system prompt, or the next day's date after 7 pm
    (judgement check, 2026-10-11). It is Ellis's local time, named."""
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    monkeypatch.setattr(timezone_utils, "_tz", None)

    def stored(created_at):
        record = {"memory": {"id": "t-skis", "content": "x", "created_at": created_at},
                  "conversation": None, "source_ref": {}, "chat": None, "fold": None}
        return next(line for line in ps.format_record(1, record) if "stored:" in line).strip()

    assert stored("2026-10-07T23:58:00Z") == "stored: Wed 2026-10-07 6:58 pm CDT"
    assert stored("2026-10-08T00:53:12.345678Z") == "stored: Wed 2026-10-07 7:53 pm CDT"
    assert stored("2026-01-15T14:03:00+00:00") == "stored: Thu 2026-01-15 8:03 am CST"
    assert stored(None) == "stored: not recorded"


@pytest.mark.unit
def test_format_provenance_says_not_recorded_when_nothing_is():
    record = {"memory": {"id": "m-2", "content": "x", "origin": "heard", "about": "owner", "source_type": "inferred",
                         "created_at": None, "confidence": 0.5, "tier": "observation", "reinforcement_count": 0,
                         "source_conversation_id": None},
              "conversation": None, "source_ref": {}, "chat": None, "fold": None}
    text = ps.format_provenance([record])
    assert "not recorded" in text
    assert text.rstrip().endswith(ps.PROVENANCE_RULE)
    empty = ps.format_provenance([], claim="unicorns")
    assert "No memory" in empty and empty.rstrip().endswith(ps.PROVENANCE_RULE)


@pytest.mark.unit
def test_source_context_is_task_local_and_merges():
    assert ps.current_source_context() == {}
    with ps.source_context(event_ids=["e1"], chat_identifier="chat123"):
        assert ps.current_source_context() == {"event_ids": ["e1"], "chat_identifier": "chat123"}
        with ps.source_context(wake_reason="messages"):
            assert ps.current_source_context()["wake_reason"] == "messages"
            assert ps.current_source_context()["event_ids"] == ["e1"]
    assert ps.current_source_context() == {}


@pytest.mark.unit
def test_heartbeat_and_wake_source_fields_for_the_callers_to_wrap_with():
    class Ev:
        id = "hb-1"
        chat_identifier = "chat123"
        source = "imessage"
        source_id = "imessage:187462"

    assert ps.heartbeat_source_fields(Ev()) == {"event_ids": ["hb-1"], "chat_identifier": "chat123",
                                                "event_source": "imessage", "rowids": [187462]}

    class Mail:
        id = "hb-2"
        chat_identifier = None
        source = "gmail"
        source_id = "gmail:abc"

    assert ps.heartbeat_source_fields(Mail()) == {"event_ids": ["hb-2"], "event_source": "gmail",
                                                  "source_ids": ["gmail:abc"]}
    assert ps.wake_source_fields("messages", [{"id": "e1"}, {"id": "e2"}]) == {
        "wake_reason": "messages", "event_ids": ["e1", "e2"]}
    assert ps.wake_source_fields("morning") == {"wake_reason": "morning"}


# --------------------------------------------------------- integration --

@pytest.mark.integration
async def test_remember_records_where_it_was_written(clean_db):
    from services import turn_ledger
    from services.graph.tools import remember, set_current_conversation_id
    from services.memory_service import get_memory_by_id

    set_current_conversation_id("conv-remember")
    rec = turn_ledger.begin_turn(conversation_id="conv-remember", source="chat", model=None, provider=None)
    try:
        with ps.source_context(url="https://example.com/rates"):
            out = await remember.ainvoke({"content": "Mortgage rates dipped to 5.9% this week",
                                          "memory_type": "fact", "about": "world", "origin": "read"})
    finally:
        turn_ledger.end_turn(rec, "completed")
    mid = out.split("memory_id: ")[1].rstrip(")")
    m = await get_memory_by_id(mid)
    ref = ps.parse_source_ref(m.source_ref)
    assert ref["kind"] == "remember"
    assert ref["conversation_id"] == "conv-remember"
    assert ref["turn_id"] == rec.id and ref["turn_source"] == "chat"
    assert ref["url"] == "https://example.com/rates"


@pytest.mark.integration
async def test_extraction_records_the_turn_and_profile(clean_db, monkeypatch):
    from services import memory_service, turn_ledger

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = ('[{"content": "Ellis is training for the Springfield half marathon", "memory_type": "fact", '
                           '"importance": 0.7, "temporal_nature": "evolving", "source_type": "user_stated", '
                           '"confidence": 0.9, "origin": "heard", "about": "owner"}]')
            return R()

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr("services.entity_service.extract_entities", _noop)

    rec = turn_ledger.begin_turn(conversation_id="c-heartbeat", source="heartbeat", model=None, provider=None)
    try:
        with ps.source_context(event_ids=["hb-1", "hb-2"], chat_identifier="chat-777", rowids=[1234]):
            stored = await memory_service.extract_and_store_memories(
                messages=[{"role": "user", "content": "training for the Springfield half"}],
                conversation_id="c-heartbeat", extraction_profile="heartbeat",
            )
    finally:
        turn_ledger.end_turn(rec, "completed")
    assert stored
    m = await memory_service.get_memory_by_id(stored[0].id)
    ref = ps.parse_source_ref(m.source_ref)
    assert ref == {"kind": "extraction", "conversation_id": "c-heartbeat", "turn_id": rec.id,
                   "turn_source": "heartbeat", "profile": "heartbeat", "event_ids": ["hb-1", "hb-2"],
                   "chat_identifier": "chat-777", "rowids": [1234]}


@pytest.mark.integration
async def test_reinforcement_keeps_the_first_source_and_notes_the_next(clean_db):
    from services.memory_service import Memory, get_memory_by_id, store_memories_with_conflict_detection

    first = await store_memories_with_conflict_detection([Memory(
        id=None, content="Ellis drinks oat milk", memory_type="preference", importance=0.5,
        source_conversation_id="c-1", source_ref=ps.dump_source_ref({"kind": "extraction", "conversation_id": "c-1"}))])
    again = await store_memories_with_conflict_detection([Memory(
        id=None, content="Ellis drinks oat milk", memory_type="preference", importance=0.5,
        source_conversation_id="c-2", source_ref=ps.dump_source_ref({"kind": "extraction", "conversation_id": "c-2"}))])
    assert again[0].id == first[0].id
    ref = ps.parse_source_ref((await get_memory_by_id(first[0].id)).source_ref)
    assert ref["conversation_id"] == "c-1"
    assert ref["also"] == [{"kind": "extraction", "conversation_id": "c-2"}]


@pytest.mark.integration
async def test_how_do_i_know_answers_from_the_records(clean_db):
    from services.conversation_service import create_conversation
    from services.database import IMessageChatConversationModel, async_session
    from services.graph.tools import how_do_i_know
    from services.memory_service import Memory, store_memory

    await create_conversation(conversation_id="c-theo", title="Theo", source="heartbeat")
    async with async_session() as session:
        session.add(IMessageChatConversationModel(chat_identifier="+15551230000", conversation_id="c-theo",
                                                  chat_name="Theo", is_group=False))
        await session.commit()
    m = await store_memory(Memory(
        id=None, content="Theo is moving to Denver in November", memory_type="fact", importance=0.6,
        source_type="tool_derived", origin="read", about="contact", source_conversation_id="c-theo",
        source_ref=ps.dump_source_ref({"kind": "extraction", "profile": "heartbeat", "turn_source": "heartbeat"})))
    folded = await store_memory(Memory(
        id=None, content="Rowan prefers aisle seats", memory_type="preference", importance=0.5,
        source_conversation_id="fold:preferences:17"))

    out = await how_do_i_know.ainvoke({"memory_id": m.id})
    assert "Theo is moving to Denver in November" in out
    assert "read/contact" in out
    assert "c-theo" in out and "source heartbeat" in out
    assert "+15551230000" in out, "the iMessage chat the thread maps to is a record too"
    assert '"profile": "heartbeat"' in out
    assert out.rstrip().endswith(ps.PROVENANCE_RULE)

    out = await how_do_i_know.ainvoke({"claim": "where is Theo moving"})
    assert m.id in out

    out = await how_do_i_know.ainvoke({"memory_id": folded.id})
    assert "preferences" in out and "17" in out

    out = await how_do_i_know.ainvoke({"memory_id": "no-such-memory"})
    assert "No memory" in out and out.rstrip().endswith(ps.PROVENANCE_RULE)
    out = await how_do_i_know.ainvoke({})
    assert out.startswith("Tool error:")


@pytest.mark.integration
async def test_provenance_route(clean_db):
    from fastapi import FastAPI
    from httpx import ASGITransport, AsyncClient

    from routers import memories as memories_router
    from services.memory_service import Memory, store_memory

    m = await store_memory(Memory(id=None, content="Ellis's dentist is Dr. Park", memory_type="fact", importance=0.5,
                                  source_conversation_id="c-x",
                                  source_ref=ps.dump_source_ref({"kind": "remember", "conversation_id": "c-x"})))
    app = FastAPI()
    app.include_router(memories_router.router, prefix="/api")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as client:
        r = await client.get(f"/api/memories/{m.id}/provenance")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["memory"]["id"] == m.id
        assert body["source_ref"] == {"kind": "remember", "conversation_id": "c-x"}
        assert body["conversation"] is None, "the thread is gone; the record says so"
        assert body["summary"]
        r = await client.get("/api/memories/nope/provenance")
        assert r.status_code == 404
