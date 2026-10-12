"""Standing instructions reach every wake (2026-09-25).

The judgement review found that Ellis's standing instructions ("stay out of
the trivia group") reached a wake only if memory retrieval happened to surface
them, and an active stand-down was invisible until a send was refused. With
the mind acting unattended, what Ellis told him to do or not do must be in
front of him every time — above the messages, and above a mention.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services import mind_service as ms


# Ellis's rules as the memory audit pinned them (2026-09-27): no thread, no
# turn — a row with neither provenance nor a thread is not his any more.
PIN = '{"kind": "owner_confirmed", "audit": "2026-09-27"}'


def _pack(**kw):
    base = dict(now=datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc), reason="mention", last_wake_at=None,
                attention={}, messages=[], calendar=[], board="", notes="", pushes="", verdicts=[],
                thoughts=[], actions=[], surfaces={})
    base.update(kw)
    return ms.MindPack(**base)


@pytest.mark.unit
def test_instructions_open_the_pack_above_the_messages():
    text = ms.render_pack(_pack(instructions=["Stay out of the Trivia night chat, even when @mentioned."]))
    assert "Standing instructions from Ellis" in text
    assert text.index("Standing instructions from Ellis") < text.index("Recent messages")
    assert "Trivia night" in text


@pytest.mark.unit
def test_no_instructions_no_section():
    assert "Standing instructions" not in ms.render_pack(_pack())


@pytest.mark.unit
def test_instruction_lines_read_memories_and_stand_downs():
    now = datetime(2026, 9, 25, 20, 0, tzinfo=timezone.utc)
    mems = [{"content": "Never text my boss for me.", "created_at": now - timedelta(days=3)}]
    downs = [{"chat_identifier": "chat-trivia", "expires_at": now + timedelta(hours=5), "requested_by": "Ellis"}]
    lines = ms.instruction_lines(mems, downs, now, names={"chat-trivia": "Trivia night"})
    assert any("Never text my boss" in l for l in lines)
    assert any("Trivia night" in l and "stand" in l.lower() for l in lines)


@pytest.mark.unit
def test_the_prompt_puts_owner_first_and_asks_before_joining_in():
    low = ms.MIND_SYSTEM_PROMPT.lower()
    assert "must reply" in low  # the interjection rule a mention still follows
    assert "standing instruction" in low and "outrank" in low
    assert "don't start or join" in low
    assert "can't wait" in low  # pushes are for what can't wait
    assert "unsure" in low  # when unsure, a note saying what he'd do


@pytest.mark.integration
async def test_the_pack_collects_live_instructions_and_stand_downs(clean_db):
    from services.database import ChatStanddownModel, MemoryModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        s.add(MemoryModel(content="Stay out of the Trivia night chat.", memory_type="instruction",
                          importance=0.9, origin="heard", about="owner", source_ref=PIN))
        s.add(MemoryModel(content="An old rule that expired.", memory_type="instruction", importance=0.9,
                          origin="heard", about="owner", valid_until=(now - timedelta(days=1)).replace(tzinfo=None),
                          source_ref=PIN))
        s.add(MemoryModel(content="Ellis likes oat milk.", memory_type="preference", importance=0.5))
        s.add(ChatStanddownModel(chat_identifier="chat-trivia", requested_by="Ellis",
                                 expires_at=now + timedelta(hours=4)))
        await s.commit()
    lines = await ms._instructions_source(now)
    text = "\n".join(lines)
    assert "Stay out of the Trivia night chat." in text
    assert "chat-trivia" in text  # the stand-down, named by its chat when no name is known
    assert "expired" not in text and "oat milk" not in text


@pytest.mark.integration
async def test_only_what_owner_said_is_his_standing_instruction(clean_db):
    """Pre-deploy review 2026-09-25: 41 of the 95 live instruction rows were
    ``read`` — inferred from other people's messages in heartbeat threads —
    and one was Edward's own decision (``did/self``). Rendered under "from
    Ellis", they would outrank everything, a mention included. Only what Ellis
    said (``heard``, about him) is his standing instruction."""
    from services.database import MemoryModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        for content, origin, about in (
            ("Ellis said: never text my boss for me.", "heard", "owner"),
            ("Parker wants Ellis to always confirm pickups.", "read", "owner"),
            ("I will stop posting in the flight chat.", "did", "self"),
            ("Rowan asked everyone to use the group calendar.", "read", "contact"),
            ("Only ping me about the vet after 5.", "read", "self"),
        ):
            s.add(MemoryModel(content=content, memory_type="instruction", importance=0.9,
                              origin=origin, about=about, source_ref=PIN))
        await s.commit()
    text = "\n".join(await ms._instructions_source(now))
    assert "never text my boss" in text
    for not_owners in ("Parker wants", "stop posting", "Rowan asked", "ping me about the vet"):
        assert not_owners not in text


# ---------------------------------------------------------------------------
# Only Ellis's own instructions (H1 / memory audit §2.2, 2026-09-27)
# ---------------------------------------------------------------------------
#
# ``origin=heard about=owner`` was not enough: wakes minted heard/owner
# instructions from their own packs (profile mind, edward-mind), and 9 of
# the 35 rows that qualified came from scheduled-event turns — one of the
# two blanket "never contact anyone" rules among them. A row whose
# provenance or thread is Edward's own is not rendered as Ellis's (the
# other blanket rule has no provenance and a deleted thread:
# it stayed his until the memory audit, see the last section); and when
# more rows qualify than the pack shows, the pack says how many it left out
# (15 were dropped silently).


def _ref(**kw):
    from services.provenance_service import build_source_ref, dump_source_ref

    return dump_source_ref(build_source_ref(kw.pop("kind", "extraction"), **kw))


@pytest.mark.integration
async def test_an_instruction_from_edwards_own_turns_or_threads_is_not_owners(clean_db):
    from services.database import ConversationModel, MemoryModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        for cid, source in (("c-owner", "user"), ("c-thought", "edward_thought"), ("c-event", "scheduled_event"),
                            ("c-hb", "heartbeat"), ("c-worker", "orchestrator_worker")):
            s.add(ConversationModel(id=cid, title=cid, source=source))
        rows = (
            ("Ellis: never text my boss for me.", "c-owner", _ref(conversation_id="c-owner", turn_source="chat",
                                                               profile="chat")),
            ("Old: keep the hallway lights off after ten.", None, None),  # pre-provenance, thread gone
            ("Mind: screens stay discreet.", "edward-mind", _ref(conversation_id="edward-mind", profile="mind")),
            ("Pulse: widget stays calm.", "edward-surfaces", None),
            ("Free: tidy the notes.", "edward-free-time", None),
            ("Research: only primary sources.", "edward-research", None),
            ("Thought: reply in-app only.", "c-thought", None),
            ("Event: must not message any external contact.", "c-event", None),
            ("Heartbeat: stay out of the trivia chat.", "c-hb", None),
            ("Worker: raw tool output verbatim.", "c-worker", None),
            ("Gone event: do not contact Parker.", "c-deleted",
             _ref(conversation_id="c-deleted", turn_source="scheduled_event", profile="chat")),
            ("Gone pulse: keep it quiet.", None, _ref(profile="pulse")),
        )
        for content, cid, ref in rows:
            s.add(MemoryModel(content=content, memory_type="instruction", importance=0.9, origin="heard",
                              about="owner", source_conversation_id=cid, source_ref=ref))
        await s.commit()
    text = "\n".join(await ms._instructions_source(now))
    assert "never text my boss" in text
    for not_owners in ("Old:", "Mind:", "Pulse:", "Free:", "Research:", "Thought:", "Event:", "Heartbeat:", "Worker:",
                     "Gone event:", "Gone pulse:"):
        assert not_owners not in text, not_owners


@pytest.mark.unit
def test_the_lines_say_how_many_were_left_out():
    now = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
    mems = [{"content": f"Rule number {i}", "created_at": now - timedelta(days=i)}
            for i in range(ms.MAX_INSTRUCTIONS_IN_PACK + 3)]
    lines = ms.instruction_lines(mems, [], now)
    assert sum(1 for l in lines if "Rule number" in l) == ms.MAX_INSTRUCTIONS_IN_PACK
    tail = lines[-1]
    assert "3 more" in tail and "remember_search" in tail


@pytest.mark.unit
def test_no_left_out_line_when_everything_fits():
    now = datetime(2026, 9, 27, 20, 0, tzinfo=timezone.utc)
    lines = ms.instruction_lines([{"content": "Only one rule", "created_at": now}], [], now)
    assert lines == [l for l in lines if "more" not in l]


@pytest.mark.integration
async def test_the_pack_counts_what_it_leaves_out(clean_db):
    from services.database import MemoryModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        for i in range(ms.MAX_INSTRUCTIONS_IN_PACK + 2):
            s.add(MemoryModel(content=f"Standing rule {i}", memory_type="instruction", importance=0.9,
                              origin="heard", about="owner", source_ref=PIN))
        await s.commit()
    lines = await ms._instructions_source(now)
    assert sum(1 for l in lines if "Standing rule" in l) == ms.MAX_INSTRUCTIONS_IN_PACK
    assert any("2 more" in l for l in lines)


@pytest.mark.unit
def test_a_ref_from_a_later_reinforcement_does_not_decide_whose_it_is():
    """Production, 2026-09-27: rows from Ellis's own chats that predate
    provenance ("don't cancel the lawn service", and another) carry a
    ``mind`` ref — a wake reinforced them and, the row having no ref, its
    ref became the row's. The ref names another conversation than the row's,
    so it says who repeated the row, not who said it; the thread decides."""
    import json

    later = json.dumps({"kind": "extraction", "conversation_id": "edward-mind", "profile": "mind",
                        "turn_source": "mind"})
    assert ms.is_owners_instruction({"source_ref": later, "source_conversation_id": "c-owner"}, {"c-owner": "user"})
    assert not ms.is_owners_instruction({"source_ref": later, "source_conversation_id": "c-ev"},
                                      {"c-ev": "scheduled_event"})
    assert not ms.is_owners_instruction({"source_ref": later, "source_conversation_id": "edward-mind"}, {})
    assert not ms.is_owners_instruction({"source_ref": later, "source_conversation_id": None}, {})


# ---------------------------------------------------------------------------
# Ellis speaking inside one of Edward's threads (review of batch A1, 2026-09-27)
# ---------------------------------------------------------------------------
#
# The thread rule ran last and unconditionally, so Ellis's own reply inside a
# thought-journal entry ("Right call? No" and "never text the family group
# for me again") — stored heard/owner by chat extraction, its ref naming the
# entry with turn_source chat — never reached "Standing instructions from
# Ellis", and the demotion script would have rewritten it to a thought. A
# journal entry opens with Edward's narrative as an *assistant* message, so
# every human message in it is Ellis's. Where the extraction window's human
# messages are other voices — a reminder thread's triggers, a heartbeat
# thread's texts, a worker's task, a wake's packs — a chat ref cannot vouch
# for the row, and the thread still decides.


@pytest.mark.unit
@pytest.mark.parametrize("turn_source", ["chat", "voice"])
def test_owner_replying_inside_a_journal_entry_gives_his_instruction(turn_source):
    ref = _ref(conversation_id="entry-1", turn_source=turn_source, profile="chat")
    assert ms.is_owners_instruction({"source_ref": ref, "source_conversation_id": "entry-1"},
                                  {"entry-1": "edward_thought"})


@pytest.mark.unit
@pytest.mark.parametrize("thread, source", [
    ("sched-1", "scheduled_event"),
    ("hb-1", "heartbeat"),
    ("w-1", "orchestrator_worker"),
    ("edward-mind", "edward_thought"),
    ("edward-surfaces", "edward_thought"),
    ("edward-free-time", "edward_thought"),
    ("edward-research", "edward_thought"),
])
def test_a_chat_ref_does_not_vouch_where_other_voices_share_the_window(thread, source):
    ref = _ref(conversation_id=thread, turn_source="chat", profile="chat")
    assert not ms.is_owners_instruction({"source_ref": ref, "source_conversation_id": thread}, {thread: source})


@pytest.mark.unit
def test_a_journal_entry_row_without_owners_ref_is_still_not_his():
    later = _ref(conversation_id="c-other", turn_source="chat", profile="chat")
    assert not ms.is_owners_instruction({"source_ref": later, "source_conversation_id": "entry-1"},
                                      {"entry-1": "edward_thought"}), "a ref naming another thread says nothing"
    assert not ms.is_owners_instruction({"source_ref": None, "source_conversation_id": "entry-1"},
                                      {"entry-1": "edward_thought"})
    mind = _ref(conversation_id="entry-1", turn_source="mind", profile="mind")
    assert not ms.is_owners_instruction({"source_ref": mind, "source_conversation_id": "entry-1"},
                                      {"entry-1": "edward_thought"})


@pytest.mark.integration
async def test_owners_reply_in_a_journal_entry_reaches_the_pack(clean_db):
    from services.database import ConversationModel, MemoryModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        s.add(ConversationModel(id="entry-1", title="Replied to Parker in Family", source="edward_thought"))
        s.add(MemoryModel(content="Never text the family group for me again", memory_type="instruction",
                          importance=0.9, origin="heard", about="owner", source_conversation_id="entry-1",
                          source_ref=_ref(conversation_id="entry-1", turn_source="chat", profile="chat")))
        await s.commit()
    assert "Never text the family group" in "\n".join(await ms._instructions_source(now))


# ---------------------------------------------------------------------------
# A row nothing on record vouches for is not Ellis's (memory audit applied, 2026-09-27)
# ---------------------------------------------------------------------------
#
# Until the audit, a heard/owner instruction with no provenance whose thread
# was gone stayed Ellis's: nothing said it was not. That is how the
# blanket "in-app only" rule (no provenance, its conversation deleted, 5,400
# retrievals), outranked every mention. The audit pinned Ellis's real rules
# (``source_ref {"kind": "owner_confirmed"}``, no thread), and every
# instruction a turn has written since 2026-09-24 carries its own ref. So
# when the thread is missing — none recorded, or Ellis deleted it — the row is
# his only when its own provenance vouches: a pin, or a turn in which Ellis
# spoke (below: a chat write that lost its turn counts, as it did when
# written).


@pytest.mark.unit
@pytest.mark.parametrize("thread", [None, "c-deleted"])
def test_a_row_with_no_provenance_and_no_thread_is_not_owners(thread):
    assert not ms.is_owners_instruction({"source_ref": None, "source_conversation_id": thread}, {})
    unrecorded = _ref(kind="unrecorded")
    assert not ms.is_owners_instruction({"source_ref": unrecorded, "source_conversation_id": thread}, {})
    audit_merge = '{"kind": "audit", "merged": ["a1", "b2"]}'
    assert not ms.is_owners_instruction({"source_ref": audit_merge, "source_conversation_id": thread}, {})


@pytest.mark.unit
@pytest.mark.parametrize("thread", [None, "c-deleted"])
def test_a_pin_is_owners_with_no_thread(thread):
    assert ms.is_owners_instruction({"source_ref": PIN, "source_conversation_id": thread}, {})


@pytest.mark.unit
@pytest.mark.parametrize("turn_source", ["chat", "voice", "widget", "alexa"])
def test_owners_own_instruction_stays_his_after_he_deletes_the_chat(turn_source):
    ref = _ref(conversation_id="c-deleted", turn_source=turn_source, profile="chat")
    assert ms.is_owners_instruction({"source_ref": ref, "source_conversation_id": "c-deleted"}, {})
    assert ms.is_owners_instruction({"source_ref": ref, "source_conversation_id": None}, {})


@pytest.mark.unit
def test_a_ref_naming_another_thread_does_not_vouch_for_a_row_whose_thread_is_gone():
    """The ref of a row stored before provenance is whatever reinforced it
    first; it names who repeated the row, not who said it."""
    later = _ref(conversation_id="c-other", turn_source="chat", profile="chat")
    assert not ms.is_owners_instruction({"source_ref": later, "source_conversation_id": "c-deleted"},
                                      {"c-other": "user"})


@pytest.mark.unit
def test_a_thread_that_exists_still_decides_as_before():
    assert ms.is_owners_instruction({"source_ref": None, "source_conversation_id": "c-owner"}, {"c-owner": "user"})
    assert ms.is_owners_instruction({"source_ref": None, "source_conversation_id": "c-old"}, {"c-old": None})
    assert not ms.is_owners_instruction({"source_ref": PIN, "source_conversation_id": "c-hb"}, {"c-hb": "heartbeat"})


@pytest.mark.integration
async def test_the_pack_renders_pins_and_owners_turns_but_not_rows_nothing_vouches_for(clean_db):
    from services.database import MemoryModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        for content, cid, ref in (
            ("Pin: never text anyone for me without asking.", None, PIN),
            ("Chat: keep the porch light on Fridays.", "c-deleted",
             _ref(conversation_id="c-deleted", turn_source="chat", profile="chat")),
            ("Legacy: only in-app reminders, never contact anyone.", "c-deleted", None),
            ("Orphan: reply in-app only.", None, None),
            ("Merged: remind Ellis in chat.", None, '{"kind": "audit", "merged": ["a1", "b2"]}'),
        ):
            s.add(MemoryModel(content=content, memory_type="instruction", importance=0.9, origin="heard",
                              about="owner", source_conversation_id=cid, source_ref=ref))
        await s.commit()
    text = "\n".join(await ms._instructions_source(now))
    assert "Pin:" in text and "Chat:" in text
    for not_owners in ("Legacy:", "Orphan:", "Merged:"):
        assert not_owners not in text, not_owners


@pytest.mark.integration
async def test_a_row_with_no_thread_is_judged_by_the_conversation_its_ref_names(clean_db):
    """The pack looks up the ref's conversation too, so a row stored with no
    thread is not taken for one whose thread is gone."""
    from services.database import ConversationModel, MemoryModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as s:
        s.add(ConversationModel(id="c-owner", title="Ellis", source="user"))
        s.add(MemoryModel(content="Ref only: water the ferns on Sundays.", memory_type="instruction",
                          importance=0.9, origin="heard", about="owner", source_conversation_id=None,
                          source_ref=_ref(kind="remember", conversation_id="c-owner")))
        await s.commit()
    assert "water the ferns" in "\n".join(await ms._instructions_source(now))


# Review of this change (2026-09-27): until the same day's fix
# (``tests/test_chat_turn_context.py``), the PWA's chat stream ran each step of
# the turn in a fresh context (``routers/chat._yield_with_heartbeats``), so the
# turn begun in the first step was gone by the time the post-turn extraction
# ran, and in any later step where the model called ``remember``; rows written
# before the fix keep those refs. Their refs
# name the thread and the chat profile but no turn. The write took that as
# Ellis speaking (``memory_service.is_owners_own_turn(None, "chat")`` made the
# row heard/owner); the pack must read it the same way once he deletes the
# chat, or every instruction he gives in the PWA leaves the pack with it.


@pytest.mark.unit
@pytest.mark.parametrize("ref", [
    {"kind": "extraction", "conversation_id": "c-deleted", "profile": "chat"},
    {"kind": "remember", "conversation_id": "c-deleted"},
])
@pytest.mark.parametrize("thread", ["c-deleted", None])
def test_a_chat_write_that_lost_its_turn_stays_owners_after_he_deletes_the_chat(ref, thread):
    import json

    assert ms.is_owners_instruction({"source_ref": json.dumps(ref), "source_conversation_id": thread}, {})


@pytest.mark.unit
@pytest.mark.parametrize("ref", [
    {"kind": "extraction", "conversation_id": "c-deleted", "profile": "heartbeat"},
    {"kind": "extraction", "conversation_id": "c-deleted", "profile": "mind"},
    {"kind": "extraction", "conversation_id": "c-deleted", "profile": "scheduled_event"},
    {"kind": "remember", "conversation_id": "c-deleted", "turn_source": "scheduled_event"},
    {"kind": "schedule_event", "conversation_id": "c-deleted"},
])
def test_a_write_with_no_turn_vouches_only_where_the_write_took_it_as_owner(ref):
    """Only what the write itself took as Ellis speaking: a heartbeat
    extraction with no turn is someone else's thread; another kind of ref
    says nothing about who spoke."""
    import json

    assert not ms.is_owners_instruction({"source_ref": json.dumps(ref), "source_conversation_id": "c-deleted"}, {})


@pytest.mark.integration
async def test_an_instruction_owner_gives_in_the_pwa_stays_his_after_he_deletes_the_chat(clean_db, monkeypatch):
    """End to end through the router's step-per-task stream: the post-turn
    extraction and a ``remember`` in a later step both store heard/owner
    rows; with the conversation gone, the pack still renders both."""
    import asyncio
    import json

    from langchain_core.messages import AIMessage
    from sqlalchemy import select

    from routers import chat as chat_router
    from services import memory_service
    from services.database import MemoryModel, async_session
    from services.graph.tools import remember, set_current_conversation_id
    from services.provenance_service import parse_source_ref
    from tests.fixtures.tool_loop import FakeGraph, ai, call, patch_loop_io

    real_extract = memory_service.extract_and_store_memories
    cid = "c-pwa-deleted"
    patch_loop_io(monkeypatch, tools=[remember], script=[
        ai("", call("remember", content="Never book me anything before 9am.", memory_type="instruction",
                    about="owner", origin="heard", importance=0.9)),
        AIMessage(content="Got it — porch light on Fridays, nothing before 9."),
    ])
    monkeypatch.setattr(memory_service, "extract_and_store_memories", real_extract)

    class _Extractor:
        async def ainvoke(self, messages):
            class R:
                content = json.dumps([{
                    "content": "Keep the porch light on on Fridays.", "memory_type": "instruction",
                    "importance": 0.9, "temporal_nature": "permanent", "source_type": "user_stated",
                    "confidence": 0.95, "origin": "heard", "about": "owner",
                }])

            return R()

    async def _nothing(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _Extractor())
    monkeypatch.setattr("services.entity_service.extract_entities", _nothing)
    monkeypatch.setattr("services.implication_service.run_implication_safe", _nothing)

    set_current_conversation_id(cid)  # the router sets it in its outer task
    inner = chat_router._wrap_chat_stream(
        message="Keep the porch light on on Fridays, and never book me anything before 9am.",
        conversation_id=cid, system_prompt="be edward", model="gpt-6-astra", temperature=0.4,
        graph=FakeGraph(), attachments=None, provider="openai",
    )
    chunks = [c async for c in chat_router._yield_with_heartbeats(inner, heartbeat_interval=10.0)]
    assert '"type": "done"' in chunks[-1]

    rows = []
    for _ in range(100):  # the extraction runs after ``done``
        async with async_session() as s:
            rows = (await s.execute(select(MemoryModel).where(MemoryModel.memory_type == "instruction"))).scalars().all()
        if len(rows) >= 2:
            break
        await asyncio.sleep(0.05)
    by_kind = {parse_source_ref(r.source_ref).get("kind"): r for r in rows}
    assert set(by_kind) == {"extraction", "remember"}, [(r.content, r.source_ref) for r in rows]
    for r in rows:
        assert (r.origin, r.about, r.source_conversation_id) == ("heard", "owner", cid)
        assert parse_source_ref(r.source_ref).get("conversation_id") == cid

    # No conversation ``c-pwa-deleted`` exists: Ellis deleted the chat.
    text = "\n".join(await ms._instructions_source(datetime.now(timezone.utc)))
    assert "porch light" in text, "the post-turn extraction's instruction"
    assert "before 9am" in text, "the instruction remember stored in a later step"
