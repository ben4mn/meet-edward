"""Contradiction flags stop piling up (2026-09-27, followups M5).

``memory_flags`` held 348 unresolved contradiction flags over only 60
distinct memory pairs — one pair (speak-as-Edward vs in-app-only) was
flagged 55 times — because step 7 of the consolidation cycle inserted a
flag for every contradiction the model reported, every hour, without
looking for the one already open. Created per day ran 48–80 against 3–11
resolved, and Edward started leaving Ellis "Which is right?" notes about the
same two rules.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select


class _ScriptedLLM:
    """Answers the cycle's calls in order; anything past the script raises,
    which the merge step's try/except already tolerates."""

    def __init__(self, responses):
        self._responses = list(responses)

    async def ainvoke(self, messages):
        if not isinstance(messages, list):
            raise TypeError("ainvoke takes a list of messages")
        if not self._responses:
            raise AssertionError("script exhausted")

        class _R:
            def __init__(self, c):
                self.content = c

        return _R(self._responses.pop(0))


async def _memory(content: str, **kw) -> str:
    from services import memory_service
    from services.database import MemoryModel, async_session

    mid = str(uuid.uuid4())
    fields = dict(memory_type="fact", importance=0.6, temporal_nature="timeless",
                  source_type="user_stated", confidence=0.8, last_accessed=datetime.utcnow())
    fields.update(kw)
    async with async_session() as session:
        session.add(MemoryModel(id=mid, content=content, embedding=memory_service.get_embedding(content), **fields))
        await session.commit()
    return mid


async def _open_flag(a: str, b: str) -> str:
    from services.database import MemoryFlagModel, async_session

    fid = str(uuid.uuid4())
    async with async_session() as session:
        session.add(MemoryFlagModel(id=fid, memory_id=a, related_memory_id=b, flag_type="contradiction",
                                    description="loves vs hates coffee", resolved=False))
        await session.commit()
    return fid


async def _contradiction_flags():
    from services.database import MemoryFlagModel, async_session

    async with async_session() as session:
        return list((await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.flag_type == "contradiction")
        )).scalars().all())


def _stub_llm(monkeypatch, llm) -> list:
    """Point every ``build_background_llm`` the cycle can reach at ``llm``.
    ``memory_service`` imports it by name for the merge step: a stub on
    ``llm_utils`` alone lets merges reach the real model (CLAUDE.md)."""
    merges = []

    class _NoMerge:
        async def ainvoke(self, messages):
            merges.append(messages)
            raise AssertionError("no real merge call from a test")

    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: llm)
    monkeypatch.setattr("services.memory_service.build_background_llm", lambda *a, **k: _NoMerge())
    return merges


def _cluster(*ids) -> str:
    return '{"clusters": [{"label": "coffee", "type": "same_topic", "memory_ids": [%s]}]}' % (
        ", ".join(f'"{i}"' for i in ids))


def _contradictions(*pairs) -> str:
    items = ", ".join(
        '{"memory_id_a": "%s", "memory_id_b": "%s", "description": "conflict"}' % p for p in pairs)
    return '{"contradictions": [%s]}' % items


@pytest.mark.integration
async def test_a_pair_with_an_open_flag_is_not_flagged_again(clean_db, monkeypatch):
    """The 55-times pair: the model reports the same contradiction every
    hour. An unresolved flag already names the pair (in the other order),
    so the cycle writes nothing new."""
    from services.consolidation_service import _run_consolidation_cycle

    loves = await _memory("Ellis loves coffee")
    hates = await _memory("Ellis hates coffee")
    await _memory("Ellis lives in Springfield")
    await _open_flag(hates, loves)

    _stub_llm(monkeypatch, _ScriptedLLM([_cluster(loves, hates), _contradictions((loves, hates))]))

    out = await _run_consolidation_cycle()

    # Counted whether open or closed: step 9 now keeps both of this pair
    # (neither row is Ellis's own word, decision (b)) and closes the flag.
    flags = await _contradiction_flags()
    assert len(flags) == 1, f"one flag per pair, found {len(flags)}"
    assert out["flags_created"] == 0


def test_one_cycle_flags_a_pair_once_whatever_the_order():
    from services.consolidation_service import select_new_contradictions

    known = {"a": "Ellis loves coffee", "b": "Ellis hates coffee", "c": "Ellis lives in Springfield"}
    reported = [
        {"memory_id_a": "a", "memory_id_b": "b", "description": "loves vs hates"},
        {"memory_id_a": "b", "memory_id_b": "a", "description": "hates vs loves"},
        {"memory_id_a": "a", "memory_id_b": "a", "description": "itself"},
        {"memory_id_a": "a", "memory_id_b": "zz", "description": "not loaded"},
    ]
    assert select_new_contradictions(reported, known, open_pairs=set()) == [("a", "b", "loves vs hates")]
    assert select_new_contradictions(reported, known, open_pairs={("a", "b")}) == []


# ---------------------------------------------------------------------------
# A flag whose question has already been answered by time closes itself
# ---------------------------------------------------------------------------
#
# 24 unresolved flags pointed at a retired or deleted memory on 2026-09-27.
# There is nothing left to decide: the lapsed side is already out of
# retrieval, so the flag only holds a slot in the daily triage and can
# still become a "Which is right?" question for Ellis.


class _Row:
    def __init__(self, id, source_type="user_stated", valid_until=None, reinforcement_count=0):
        self.id = id
        self.source_type = source_type
        self.valid_until = valid_until
        self.reinforcement_count = reinforcement_count


def test_a_lapsed_or_missing_side_makes_the_flag_moot():
    from datetime import timedelta

    from services.consolidation_service import decide_contradiction

    now = datetime(2026, 9, 27, 12, 0)
    live = _Row("a")
    lapsed = _Row("b", valid_until=now - timedelta(days=1))
    assert decide_contradiction(live, lapsed, now=now) == ("moot", "one side lapsed")
    assert decide_contradiction(lapsed, live, now=now) == ("moot", "one side lapsed")
    assert decide_contradiction(live, None, now=now) == ("moot", "one side missing")
    # a horizon still ahead is a live memory: the flag is not moot
    ahead = _Row("c", valid_until=now + timedelta(days=3))
    assert decide_contradiction(live, ahead, now=now)[0] != "moot"


async def _flag_row(fid: str):
    from services.database import MemoryFlagModel, async_session

    async with async_session() as session:
        return (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == fid))).scalar_one()


async def _valid_until(mid: str):
    from services.database import MemoryModel, async_session

    async with async_session() as session:
        return (await session.execute(select(MemoryModel.valid_until).where(MemoryModel.id == mid))).scalar_one()


@pytest.mark.integration
async def test_a_flag_on_a_lapsed_memory_is_resolved_automatically(clean_db):
    from datetime import timedelta

    from services.consolidation_service import resolve_contradictions

    stamp = datetime.utcnow() - timedelta(days=2)
    current = await _memory("Edward speaks only as Edward")
    lapsed = await _memory("Send Ellis in-app reminders only", valid_until=stamp)
    fid = await _open_flag(current, lapsed)

    assert await resolve_contradictions() == 1
    row = await _flag_row(fid)
    assert row.resolved is True and row.resolved_at is not None
    assert "[needs Ellis]" not in (row.description or ""), "nothing is left for Ellis to decide"
    assert await _valid_until(current) is None, "the live side is never retired by a moot flag"
    assert await _valid_until(lapsed) == stamp, "the lapsed side keeps its own horizon"


@pytest.mark.integration
async def test_a_flag_whose_memory_is_gone_is_resolved_automatically(clean_db):
    from services.consolidation_service import resolve_contradictions

    kept = await _memory("Ellis drinks oat milk")
    fid = await _open_flag(kept, str(uuid.uuid4()))

    assert await resolve_contradictions() == 1
    assert (await _flag_row(fid)).resolved is True
    assert await _valid_until(kept) is None


# ---------------------------------------------------------------------------
# A cycle raises a few flags, the ones worth Ellis's attention first
# ---------------------------------------------------------------------------


class _Mem:
    def __init__(self, id, *, importance=0.5, used_count=0, origin="read", source_type="inferred", source_ref=None):
        self.id = id
        self.importance = importance
        self.used_count = used_count
        self.origin = origin
        self.source_type = source_type
        self.source_ref = source_ref


def test_contradiction_priority_ranks_owners_word_then_weight_then_reach():
    from services.consolidation_service import contradiction_priority

    plain = contradiction_priority(_Mem("a"), _Mem("b"))
    owners = contradiction_priority(_Mem("a", origin="heard", source_type="user_stated",
                                       source_ref='{"kind": "extraction", "turn_source": "chat"}'), _Mem("b"))
    confirmed = contradiction_priority(_Mem("a", source_ref='{"kind": "owner_confirmed"}'), _Mem("b"))
    weighty = contradiction_priority(_Mem("a", importance=0.9), _Mem("b"))
    reached = contradiction_priority(_Mem("a", used_count=400), _Mem("b", used_count=300))
    assert owners > weighty > plain, "a conflict with something Ellis said outranks importance alone"
    assert confirmed == owners
    assert reached > plain, "a pair retrieved into hundreds of turns colours more answers"


def test_owners_word_comes_first_whatever_the_other_pair_weighs():
    """The score was a sum — Ellis's +1, importance, 0.1 per e-fold of use —
    so a heavily used pair of Edward's own rows outscored a conflict with
    Ellis's word (1.77 against 1.50 on live values) and
    took the one question on Today, or a slot in the cycle's five."""
    from services.consolidation_service import contradiction_priority, select_new_contradictions

    rows = {
        "owner": _Mem("owner", importance=0.5, source_ref='{"kind": "owner_confirmed"}'),
        "quiet": _Mem("quiet", importance=0.5),
        "loud_a": _Mem("loud_a", importance=1.0, used_count=1200),
        "loud_b": _Mem("loud_b", importance=0.9, used_count=937),
    }
    assert contradiction_priority(rows["owner"], rows["quiet"]) > contradiction_priority(rows["loud_a"], rows["loud_b"])
    reported = [{"memory_id_a": "loud_a", "memory_id_b": "loud_b", "description": "loud"},
                {"memory_id_a": "owner", "memory_id_b": "quiet", "description": "owner's word"}]
    assert [p[2] for p in select_new_contradictions(reported, rows, open_pairs=set(), cap=1)] == ["owner's word"]


_ECHO = dict(origin="heard", source_type="user_stated",
             source_ref='{"kind": "extraction", "profile": "mind", "turn_source": "mind"}')


@pytest.mark.parametrize("fields", [
    _ECHO,  # a blanket "in-app only" rule a mind turn minted as Ellis's
    dict(origin="heard", source_type="user_stated"),  # no provenance, its conversation gone
])
def test_a_reminder_echo_is_not_owners_word(fields):
    """"Ellis's word" was any heard + user_stated row, which counts the
    reminder-turn echoes the memory audit showed are not his (§2.2): the
    top 11 open pairs on the live store all took Ellis's first place through
    one of two such rows."""
    from services.consolidation_service import contradiction_priority

    assert contradiction_priority(_Mem("a", **fields), _Mem("b"))[0] == 0


@pytest.mark.parametrize("fields,source,owners", [
    (_ECHO, "scheduled_event", False),
    (dict(origin="heard", source_type="user_stated"), None, False),
    (dict(origin="heard", source_type="inferred"), "user", True),
    (dict(origin="heard", source_ref='{"kind": "extraction", "turn_source": "voice"}'), None, True),
    (dict(origin="read", source_type="user_stated"), "user", False),
    (dict(source_ref='{"kind": "owner_confirmed"}'), "heartbeat", True),
])
def test_owners_word_is_the_rule_the_decay_pass_uses(fields, source, owners):
    """The decay pass's ``owners_own_instruction`` asks for Ellis's own chat (a
    ``user`` conversation, or a chat/voice turn) or his confirmation; the
    contradiction side now asks the same, reading where the row came from."""
    from services.consolidation_service import contradiction_priority
    from services.memory_decay_service import owners_own_word

    mem = _Mem("a", **fields)
    mem.source_conversation_id = "conv-a"
    sources = {"conv-a": source} if source else {}
    assert contradiction_priority(mem, _Mem("b"), conversation_sources=sources)[0] == int(owners)
    assert owners_own_word(mem, conversation_source=source) is owners


def test_the_cap_holds_and_keeps_the_highest_priority_pairs():
    from services.consolidation_service import MAX_NEW_CONTRADICTION_FLAGS_PER_CYCLE as CAP
    from services.consolidation_service import select_new_contradictions

    rows = {f"m{i}": _Mem(f"m{i}", importance=0.05 * i) for i in range(2 * (CAP + 3))}
    rows["owner"] = _Mem("owner", importance=0.1, origin="heard", source_type="user_stated",
                       source_ref='{"kind": "extraction", "turn_source": "chat"}')
    reported = [{"memory_id_a": f"m{2 * i}", "memory_id_b": f"m{2 * i + 1}", "description": f"p{i}"}
                for i in range(CAP + 2)]
    reported.append({"memory_id_a": "owner", "memory_id_b": "m0", "description": "owner's word"})

    picked = select_new_contradictions(reported, rows, open_pairs=set(), cap=CAP)

    assert len(picked) == CAP
    descs = [p[2] for p in picked]
    assert descs[0] == "owner's word", "Ellis's own word first, though it was reported last"
    assert "p0" not in descs and "p1" not in descs, "the two least important pairs wait for a later cycle"


@pytest.mark.integration
async def test_a_cycle_never_writes_more_than_the_cap(clean_db, monkeypatch):
    from services.consolidation_service import MAX_NEW_CONTRADICTION_FLAGS_PER_CYCLE as CAP
    from services.consolidation_service import _run_consolidation_cycle

    ids = [await _memory(f"Ellis's favourite number is {i}") for i in range(2 * (CAP + 2))]
    pairs = [(ids[2 * i], ids[2 * i + 1]) for i in range(CAP + 2)]
    _stub_llm(monkeypatch, _ScriptedLLM([_cluster(*ids), _contradictions(*pairs)]))

    out = await _run_consolidation_cycle()

    assert out["contradictions_found"] == CAP + 2
    assert out["flags_created"] == CAP
    assert len(await _contradiction_flags()) == CAP


@pytest.mark.integration
async def test_a_pair_owner_left_unanswered_is_not_flagged_again_for_a_month(clean_db):
    """The daily triage asks a pair once and closes it as stale when Ellis
    lets it go for a week. Re-flagged by the next hourly cycle, it would be
    asked again a week later, and every week after: the nagging the
    ask-once rule (2026-09-07) was written to stop."""
    from datetime import timedelta

    from services.consolidation_service import REASK_AFTER_DAYS, open_contradiction_pairs
    from services.database import MemoryFlagModel, async_session

    a = await _memory("Edward speaks only as Edward")
    b = await _memory("Edward sends Ellis in-app reminders only")
    c = await _memory("Ellis lives in Springfield")
    d = await _memory("Ellis lives in Denver")
    now = datetime.utcnow()
    async with async_session() as session:
        session.add_all([
            MemoryFlagModel(memory_id=b, related_memory_id=a, flag_type="contradiction", resolved=True,
                            resolved_at=now - timedelta(days=2), description="rules [asked 2026-09-18 09:00]"),
            MemoryFlagModel(memory_id=c, related_memory_id=d, flag_type="contradiction", resolved=True,
                            resolved_at=now - timedelta(days=REASK_AFTER_DAYS + 1),
                            description="cities [asked 2026-08-01 09:00]"),
        ])
        await session.commit()

    held = await open_contradiction_pairs()

    assert tuple(sorted((a, b))) in held, "asked and left unanswered two days ago: not flagged again"
    assert tuple(sorted((c, d))) not in held, "a month on, the question may come back"


@pytest.mark.integration
async def test_a_pair_edward_kept_is_not_flagged_again_for_a_month(clean_db):
    """A flag closed "[kept both]" (decision (b), 2026-09-27) leaves both
    rows live. Without this the hourly cycle re-flagged the same pair every
    hour, and each re-flag spent one of the cycle's five slots."""
    from datetime import timedelta

    from services.consolidation_service import KEPT_BOTH_MARK, REASK_AFTER_DAYS, open_contradiction_pairs
    from services.database import MemoryFlagModel, async_session

    a = await _memory("Edward congratulated Parker at Ellis's request")
    b = await _memory("Edward stayed out of Parker's thread")
    c = await _memory("Ellis's gym is on Fifth")
    d = await _memory("Ellis's gym is on Sixth")
    now = datetime.utcnow()
    async with async_session() as session:
        session.add_all([
            MemoryFlagModel(memory_id=a, related_memory_id=b, flag_type="contradiction", resolved=True,
                            resolved_at=now - timedelta(days=2), description=f"Parker {KEPT_BOTH_MARK} (reason)"),
            MemoryFlagModel(memory_id=c, related_memory_id=d, flag_type="contradiction", resolved=True,
                            resolved_at=now - timedelta(days=REASK_AFTER_DAYS + 1),
                            description=f"gym {KEPT_BOTH_MARK} (reason)"),
        ])
        await session.commit()

    held = await open_contradiction_pairs()

    assert tuple(sorted((a, b))) in held, "kept two days ago: not flagged again"
    assert tuple(sorted((c, d))) not in held, "a month on, the pair may be flagged again"
