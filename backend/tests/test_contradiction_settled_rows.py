"""Re-enabling consolidation retires nothing the audit or Ellis settled
(integration review, 2026-09-27).

Replayed on the 25 open contradiction flags in production, the first hourly
cycle's step 9 (``resolve_contradictions``) would have retired five rows —
among them the pet-board rule the memory audit corrected that day
(knowledge tier, used dozens of times, Biscuit/Pepper/medication), beaten by
a Sep 22 episode ("the household animals had not been fed"). Both sides
read as Ellis speaking to ``rank_says_who``: the audit's relabel ref
``{"audit": {"op": "relabel", "was": "heard"}}`` names no profile, so it
took the chat default, and so did the empty ref of a heartbeat or journal
read still labelled user_stated. Rank then took user_stated over inferred.

Rules pinned here:

  * rank decides only between rows that could be Ellis speaking — never a row
    from a heartbeat, journal/wake (``edward_thought``), scheduled-event or
    worker thread (``mind_service.NOT_OWNERS_CONVERSATION_SOURCES``), and
    never one the audit moved off ``heard`` (relabel or demote);
  * nothing sensitive (``memory_decay_service._held_as_sensitive``) and
    nothing knowledge-tier is retired by rank or reinforcement — the pair
    goes to Ellis when a side is his own word, else both are kept;
  * a pin Ellis confirmed (``source_ref`` kind ``owner_confirmed``) is settled:
    kept as it is against any row, never retired, never asked about again.
"""

from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select

AUDIT_RELABEL = {"audit": {"at": "2026-09-27", "cat": "L1", "op": "relabel", "was": "heard"}}
AUDIT_DEMOTE = {"kind": "extraction", "conversation_id": "edward-mind", "turn_source": "mind", "profile": "mind",
                "audit": {"at": "2026-09-27", "cat": "instructions", "op": "demote",
                          "was": "instruction/heard/owner"}}
PIN = {"kind": "owner_confirmed", "audit": "2026-09-27", "pin": "p_invited", "replaces": ["old-invited-rule"]}
CHAT = {"kind": "extraction", "profile": "chat", "turn_source": "chat"}
WAKE = {"kind": "extraction", "profile": "mind", "turn_source": "mind", "conversation_id": "edward-mind"}

SOURCES = {"hb": "heartbeat", "journal": "edward_thought", "reminder": "scheduled_event",
           "edward-mind": "edward_thought", "worker": "orchestrator_worker", "owner": "user"}


class _Row:
    def __init__(self, id, source_type="inferred", content="a memory", *, origin="read", about="owner",
                 tier="belief", memory_type="context", temporal_nature="temporary", ref=None,
                 conversation=None, reinforcement_count=0):
        self.id = id
        self.content = content
        self.source_type = source_type
        self.origin = origin
        self.about = about
        self.tier = tier
        self.memory_type = memory_type
        self.temporal_nature = temporal_nature
        self.source_ref = json.dumps(ref) if ref is not None else None
        self.source_conversation_id = conversation
        self.reinforcement_count = reinforcement_count
        self.valid_until = None
        self.importance = 0.8
        self.used_count = 0


# The shapes of the production pairs (25 open flags, 2026-09-27), with invented ids and content.
PET_RULE = dict(id="pet-rule", source_type="inferred", origin="read", tier="knowledge",
                ref=AUDIT_RELABEL, conversation="journal", reinforcement_count=22,
                content="Pet-board interpretation, corrected September 27, 2026: Biscuit, Maple, Pepper and "
                        "Mr. Whiskers. Do not let absent board schedules override a specific report of a "
                        "missed dose or medication. Do not repeat an alert merely because feeding is unlogged.")
FED_EPISODE = dict(id="fed-episode", source_type="user_stated", origin="read", conversation="journal",
                   content="On the morning of September 22, 2026, Rowan Hale said the household animals "
                           "had not been fed.")
PAIRS = {
    "pet rule vs Sep 22 episode": (PET_RULE, FED_EPISODE),
    "pet rule vs heartbeat read": (PET_RULE, dict(
        id="cats-read", source_type="user_stated", tier="knowledge", memory_type="fact", temporal_nature="evolving",
        conversation="hb", content="Ellis and Rowan Hale have cats named Pepper and Maple.")),
    "package": (dict(id="package-guess", source_type="inferred", origin="thought", ref=AUDIT_RELABEL,
                     conversation="reminder", content="Ellis is awaiting a package the courier said was misrouted"),
                dict(id="package-read", source_type="user_stated", conversation="hb",
                     content="Ellis is expecting a package from the hardware store that arrived today")),
    "job title": (dict(id="title-guess", source_type="inferred", origin="thought", tier="observation",
                       memory_type="fact", temporal_nature="evolving", ref=AUDIT_RELABEL,
                       content="Ellis is a team lead."),
                  dict(id="title-read", source_type="user_stated", tier="knowledge", memory_type="preference",
                       temporal_nature="evolving", conversation="hb",
                       content="Ellis is a staff engineer at Acme Group")),
    "under the weather": (dict(id="unwell-episode", source_type="inferred", ref=AUDIT_RELABEL,
                               conversation="journal",
                               content="On September 21, 2026, Rowan Hale said she felt under the weather"),
                          dict(id="alerts-rule", source_type="user_stated", about="contact", tier="knowledge",
                               conversation="journal", content="Ellis wants in-app alerts when Rowan needs a hand")),
    "the show": (dict(id="show-invite", source_type="inferred", tier="observation", ref=AUDIT_RELABEL,
                      conversation="journal", content="Ellis invited Parker to ride along to the Paper Lanterns show"),
                 dict(id="show-plan", source_type="user_stated", tier="observation", conversation="journal",
                      content="Ellis and Rowan Hale plan to attend the Paper Lanterns show downtown")),
}


@pytest.mark.parametrize("source", ["heartbeat", "edward_thought", "scheduled_event", "orchestrator_worker"])
def test_rank_is_not_trusted_for_a_row_from_a_thread_where_owner_is_not_speaking(source):
    from services.consolidation_service import rank_says_who
    from services.mind_service import NOT_OWNERS_CONVERSATION_SOURCES

    assert source in NOT_OWNERS_CONVERSATION_SOURCES
    row = _Row("r", "user_stated", conversation="c")  # the empty ref of a legacy read
    assert rank_says_who(row, {"c": source}) is False
    assert rank_says_who(row, {"c": "user"}) is True
    assert rank_says_who(row) is True, "with no sources read, the row's own ref decides, as before"


def test_owners_own_reply_inside_a_journal_entry_still_ranks_as_his():
    """The one exception the standing-instructions filter makes
    (``mind_service.is_owners_instruction``): Ellis replying inside a journal
    entry. Once the PWA chat keeps its turn across steps, such rows carry
    ``turn_source`` chat and must still win by rank, not come back to Ellis as
    a question about his own words (verification of the integration fixes)."""
    from services.consolidation_service import decide_contradiction, rank_says_who

    reply = _Row("reply", "user_stated", origin="heard", conversation="journal",
                 ref={"kind": "extraction", "profile": "chat", "turn_source": "chat", "conversation_id": "journal"})
    assert rank_says_who(reply, SOURCES) is True
    no_turn = _Row("wake", "user_stated", conversation="journal", ref={"kind": "extraction", "conversation_id": "journal"})
    assert rank_says_who(no_turn, SOURCES) is False, "a journal row with no turn of Ellis's is still not his"
    other_thread = _Row("x", "user_stated", origin="heard", conversation="journal",
                        ref={"kind": "extraction", "profile": "chat", "turn_source": "chat", "conversation_id": "owner"})
    assert rank_says_who(other_thread, SOURCES) is False, "a ref from another thread does not vouch for this one"
    legacy = _Row("legacy", "inferred")
    verdict = decide_contradiction(reply, legacy, conversation_sources=SOURCES)
    assert verdict[0] == "auto", verdict


@pytest.mark.parametrize("ref", [AUDIT_RELABEL, AUDIT_DEMOTE], ids=["relabel", "demote"])
def test_rank_is_not_trusted_for_a_row_the_audit_moved_off_heard(ref):
    from services.consolidation_service import rank_says_who

    assert rank_says_who(_Row("r", ref=ref)) is False


def test_other_audit_refs_say_nothing_about_who_spoke():
    """A reword or a retype left the row's origin alone: such a row is read
    like any row with no provenance, as before."""
    from services.consolidation_service import rank_says_who

    assert rank_says_who(_Row("r", ref={"audit": {"at": "2026-09-27", "op": "reword"}})) is True
    assert rank_says_who(_Row("r", ref={"audit": {"at": "2026-09-27", "op": "relabel_about", "was": "self"}})) is True


@pytest.mark.parametrize("name", list(PAIRS))
def test_the_production_pairs_are_kept_not_retired(name):
    from services.consolidation_service import decide_contradiction

    a, b = (_Row(**kw) for kw in PAIRS[name])
    for pair in ((a, b), (b, a)):
        verdict = decide_contradiction(*pair, conversation_sources=SOURCES)
        assert verdict[0] == "keep_both", (name, verdict)


def test_a_sensitive_loser_is_never_retired_by_rank():
    """Two legacy rows rank can read (no provenance, no thread): the
    inferred one used to be retired whatever it said."""
    from services.consolidation_service import decide_contradiction

    said = _Row("said", "user_stated", origin="heard", content="Biscuit's steroid is at six in the morning")
    dose = _Row("dose", "inferred", content="Biscuit's steroid dose moved to seven in the morning")
    assert decide_contradiction(said, dose)[0] == "keep_both"
    assert decide_contradiction(dose, said)[0] == "keep_both"


def test_a_knowledge_tier_loser_is_never_retired():
    from services.consolidation_service import decide_contradiction

    said = _Row("said", "user_stated", origin="heard", content="Ellis's desk is in the loft")
    known = _Row("known", "inferred", tier="knowledge", content="Ellis's desk is in the garage")
    assert decide_contradiction(said, known)[0] == "keep_both"
    tie_a = _Row("a", "inferred", tier="knowledge", content="The garage code is 1234", reinforcement_count=1)
    tie_b = _Row("b", "inferred", content="The garage code is 5678", reinforcement_count=4)
    assert decide_contradiction(tie_a, tie_b)[0] == "keep_both", "reinforcement retires no knowledge row either"


def test_a_sensitive_loser_against_owners_own_word_is_his_call():
    from services.consolidation_service import decide_contradiction

    said = _Row("said", "user_stated", origin="heard", ref=CHAT, content="Biscuit's steroid is at six")
    heard = _Row("heard", "inferred", origin="heard", ref=dict(CHAT, turn_source="voice"),
                 content="Biscuit's steroid moved to seven")
    assert decide_contradiction(said, heard)[0] == "open"


def test_an_ordinary_loser_is_still_retired_by_rank():
    from services.consolidation_service import decide_contradiction

    said = _Row("said", "user_stated", origin="heard", content="Ellis's dentist is Dr. Park")
    guess = _Row("guess", "inferred", content="Ellis's dentist is Dr. Novak")
    assert decide_contradiction(guess, said) == ("auto", "said", "guess")


OTHERS = {
    "a wake's belief": dict(id="w", source_type="inferred", origin="thought", ref=WAKE, conversation="edward-mind",
                            content="Edward stays out of group chats unless mentioned"),
    "a heartbeat read": dict(id="h", source_type="user_stated", conversation="hb",
                             content="Ellis asked Edward to answer in the group chat"),
    "a legacy row rank would retire": dict(id="l", source_type="inferred",
                                           content="Edward replies in group chats"),
    "Ellis's own chat": dict(id="c", source_type="user_stated", origin="heard", ref=CHAT, conversation="owner",
                           content="Reply in the book club group for me"),
    "another pin": dict(id="p2", source_type="user_stated", origin="heard", tier="knowledge",
                        memory_type="instruction", temporal_nature="timeless", ref=dict(PIN, pin="p_filler"),
                        content="No filler in replies"),
}


@pytest.mark.parametrize("other", list(OTHERS))
def test_a_pin_is_settled(other):
    """The five pins were written inside the hourly lookback, so they sit in
    the first cycle's candidates beside the wake-written beliefs about the
    same topic (group chats vs in-app only) — the topic of 5 of the last 9
    "Which is right?" notes."""
    from services.consolidation_service import decide_contradiction

    pin = _Row("pin", "user_stated", origin="heard", tier="knowledge", memory_type="instruction",
               temporal_nature="timeless", ref=PIN, content="Edward answers when invited into a thread")
    row = _Row(**OTHERS[other])
    for pair in ((pin, row), (row, pin)):
        verdict = decide_contradiction(*pair, conversation_sources=SOURCES)
        assert verdict[0] == "keep_both", (other, verdict)


# ---- on the store ----------------------------------------------------------------------------

async def _conversations():
    from services.conversation_service import create_conversation

    for cid, source in SOURCES.items():
        await create_conversation(cid, title=cid, source=source)


async def _insert(kw: dict) -> str:
    from services.database import MemoryModel, async_session

    row = _Row(**kw)
    mid = f"{row.id}-{uuid.uuid4()}"
    async with async_session() as session:
        session.add(MemoryModel(id=mid, content=row.content, memory_type=row.memory_type, importance=0.8,
                                source_type=row.source_type, confidence=0.9, origin=row.origin, about=row.about,
                                tier=row.tier, temporal_nature=row.temporal_nature, source_ref=row.source_ref,
                                source_conversation_id=row.source_conversation_id,
                                reinforcement_count=row.reinforcement_count))
        await session.commit()
    return mid


async def _flag(a: str, b: str) -> str:
    from services.database import MemoryFlagModel, async_session

    fid = str(uuid.uuid4())
    async with async_session() as session:
        session.add(MemoryFlagModel(id=fid, memory_id=a, related_memory_id=b, flag_type="contradiction",
                                    description="conflict [needs Ellis] (a side was written while Ellis was not "
                                                "speaking)", resolved=False))
        await session.commit()
    return fid


async def _state():
    from services.database import MemoryFlagModel, MemoryModel, async_session

    async with async_session() as session:
        flags = (await session.execute(select(MemoryFlagModel))).scalars().all()
        mems = (await session.execute(select(MemoryModel))).scalars().all()
    return {f.id: f for f in flags}, {m.id: m for m in mems}


@pytest.mark.integration
async def test_the_first_cycle_retires_none_of_the_production_shapes(clean_db):
    from services.consolidation_service import resolve_contradictions

    await _conversations()
    ids = {}

    async def row(kw):
        if kw["id"] not in ids:
            ids[kw["id"]] = await _insert(kw)
        return ids[kw["id"]]

    flags = [await _flag(await row(a), await row(b)) for a, b in PAIRS.values()]

    assert await resolve_contradictions() == len(flags)

    by_flag, by_mem = await _state()
    for fid in flags:
        assert by_flag[fid].resolved is True and "[kept both]" in by_flag[fid].description
        assert "[needs Ellis]" not in by_flag[fid].description
    for mid in ids.values():
        assert by_mem[mid].valid_until is None, f"{mid} was retired"
        assert by_mem[mid].confidence == 0.9, f"{mid} was downgraded"


@pytest.mark.integration
async def test_a_pin_flagged_against_a_wake_belief_is_never_put_to_owner(clean_db, monkeypatch):
    from services import contradiction_triage_service as cts
    from services.consolidation_service import resolve_contradictions
    from services.notes_service import recent_notes

    await _conversations()
    pin = await _insert(dict(id="pin", source_type="user_stated", origin="heard", tier="knowledge",
                             memory_type="instruction", temporal_nature="timeless", ref=PIN,
                             content="Edward answers when invited into a thread"))
    wake = await _insert(OTHERS["a wake's belief"])
    triaged = await _flag(pin, wake)

    def no_llm(**kw):
        raise AssertionError("a pin is never a question")

    monkeypatch.setattr(cts, "build_background_llm", no_llm)
    results = await cts.triage_unresolved(limit=10, priority="surplus", purpose="surplus:contradictions",
                                          ask_via="note")
    assert [r["action"] for r in results] == ["kept_both"]
    assert [n for n in await recent_notes(hours=1) if n.title == "Which is right?"] == []

    cycled = await _flag(wake, pin)  # a later cycle flags the pair again
    await resolve_contradictions()
    by_flag, by_mem = await _state()
    assert all(by_flag[f].resolved and "[kept both]" in by_flag[f].description for f in (triaged, cycled))
    assert by_mem[pin].valid_until is None and by_mem[wake].valid_until is None
