"""Use is what the answer drew on, not what was shown (memory audit §2.1/§7.4, 2026-09-27).

``used_count`` feeds the tier ladder (belief at 3 uses; knowledge at 10
plus 5 restatements) and stretches decay's half-life. It was credited in
``streaming`` for every memory whose ``(memory_id: …)`` tag survived into
the prompt — which is retrieval under another name. The 100 most-retrieved
rows took 70% of all retrievals, 84 of them from before Aug 15, and the
more often a stale row was shown the higher it climbed: one dated July
episode had 2,387 retrievals and 1,294 "uses".

Contract:
  * inside a turn, ``mark_memories_used`` (the display hook streaming
    calls) holds the ids it was given and credits nothing yet;
  * post-turn extraction credits only the shown memories the answer drew
    on — its words reuse the memory's distinctive words that Ellis's own
    message did not already carry (``cited_memory_ids``);
  * outside a turn ``mark_memories_used`` credits at once, as before.
"""

from __future__ import annotations

import uuid

import pytest


def _m(content, mid=None):
    from services.memory_service import Memory

    return Memory(id=mid or str(uuid.uuid4()), content=content, memory_type="fact", importance=0.5)


# ---------------------------------------------------------------------------
# cited_memory_ids (pure)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_an_answer_that_uses_the_memory_cites_it():
    from services.memory_service import cited_memory_ids

    salmon = _m("Biscuit eats grain-free salmon kibble twice a day", "m-food")
    vet = _m("Biscuit's vet is Dr. Alvarez at Riverside Animal Clinic", "m-vet")
    answer = "Biscuit gets grain-free salmon kibble, twice a day."
    assert cited_memory_ids(answer, [salmon, vet], prompt="what does the dog eat?") == ["m-food"]


@pytest.mark.unit
def test_echoing_owners_own_words_is_not_a_citation():
    from services.memory_service import cited_memory_ids

    salmon = _m("Biscuit eats grain-free salmon kibble twice a day", "m-food")
    prompt = "Remember that Biscuit eats grain-free salmon kibble twice a day"
    answer = "Got it: Biscuit eats grain-free salmon kibble twice a day."
    assert cited_memory_ids(answer, [salmon], prompt=prompt) == []


@pytest.mark.unit
def test_an_unrelated_answer_cites_nothing():
    from services.memory_service import cited_memory_ids

    stale = _m("On July 10-12, 2026, Rowan and Ellis repainted the guest bedroom a pale green", "m-old")
    assert cited_memory_ids("The weather tomorrow is sunny, high of 78.", [stale], prompt="weather?") == []


@pytest.mark.unit
def test_one_shared_word_is_not_enough_and_names_do_not_count():
    from services.memory_service import cited_memory_ids

    m = _m("Ellis and Edward agreed the Springfield marathon training starts in October", "m-run")
    assert cited_memory_ids("Ellis, Edward here — October it is.", [m], prompt="hi") == []


@pytest.mark.unit
def test_a_long_memory_needs_more_than_two_words():
    from services.memory_service import CITATION_MAX_REQUIRED_WORDS, cited_memory_ids

    m = _m("Sprinkle Shed reminders on July 24, July 31 and August 28 go to the family group chat "
           "with the pickup address, the order total, the flavors and the coupon code", "m-long")
    assert cited_memory_ids("I'll send the family group the reminder.", [m], prompt="hi") == []
    assert CITATION_MAX_REQUIRED_WORDS >= 3


@pytest.mark.unit
@pytest.mark.parametrize("answer", [
    "You and Rowan have the Pine Valley trip in July 2026 — nothing else on the calendar.",
    "Rowan is coming over in July.",
    "Your 2026 July plans: Rowan visiting.",
    "Rowan's Wednesday, July 8 plans are set for 2026.",
])
def test_a_month_a_year_and_a_name_are_not_a_citation(answer):
    """Review of batch A1, 2026-09-27: the audit's own stale row was "cited"
    by any answer naming Rowan and a month — july, 2026 and rowan were
    three of its eight words. Dated episodes and a frequently named person
    are exactly the rows that climbed by display."""
    from services.memory_service import cited_memory_ids

    stale = _m("On July 10-12, 2026, Rowan and Ellis repainted the guest bedroom a pale green", "m-old")
    assert cited_memory_ids(answer, [stale], prompt="whats coming up?") == []


@pytest.mark.unit
def test_an_answer_that_uses_a_dated_memory_still_cites_it():
    from services.memory_service import cited_memory_ids

    stale = _m("On July 10-12, 2026, Rowan and Ellis repainted the guest bedroom a pale green", "m-old")
    answer = "In July you and Rowan repainted the guest bedroom."
    assert cited_memory_ids(answer, [stale], prompt="anything I should know?") == ["m-old"]


@pytest.mark.unit
def test_empty_inputs_are_safe():
    from services.memory_service import cited_memory_ids

    assert cited_memory_ids("", [_m("Biscuit eats salmon")], prompt="") == []
    assert cited_memory_ids("anything", [], prompt="") == []


# ---------------------------------------------------------------------------
# The display hook inside a turn credits nothing
# ---------------------------------------------------------------------------


async def _seed(*contents):
    from services import database, memory_service
    from services.database import MemoryModel

    ids = []
    async with database.async_session() as s:
        for c in contents:
            mid = str(uuid.uuid4())
            s.add(MemoryModel(id=mid, content=c, embedding=memory_service.get_embedding(c), memory_type="fact",
                              importance=0.5, source_type="user_stated", confidence=0.9))
            ids.append(mid)
        await s.commit()
    return ids


async def _used(mid):
    from services import database
    from services.database import MemoryModel

    async with database.async_session() as s:
        row = await s.get(MemoryModel, mid)
        return row.used_count, row.last_used_at


def _turn(source="chat"):
    from services import turn_ledger

    rec = turn_ledger.TurnRecord.start(conversation_id="c-1", source=source, model=None, provider=None)
    return rec, turn_ledger._current.set(rec)


@pytest.mark.integration
async def test_being_shown_in_a_turn_is_not_a_use(clean_db):
    from services import memory_service, turn_ledger

    (mid,) = await _seed("Biscuit eats grain-free salmon kibble twice a day")
    rec, token = _turn()
    try:
        await memory_service.mark_memories_used([mid])
    finally:
        turn_ledger._current.reset(token)
    assert await _used(mid) == (0, None)


@pytest.mark.integration
async def test_extraction_credits_only_what_the_answer_drew_on(clean_db, monkeypatch):
    from services import memory_service, turn_ledger

    food, vet = await _seed("Biscuit eats grain-free salmon kibble twice a day",
                            "Biscuit's vet is Dr. Alvarez at Riverside Animal Clinic")

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = "[]"

            return R()

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    retrieved = [memory_service.Memory(id=food, content="Biscuit eats grain-free salmon kibble twice a day",
                                       memory_type="fact", importance=0.5),
                 memory_service.Memory(id=vet, content="Biscuit's vet is Dr. Alvarez at Riverside Animal Clinic",
                                       memory_type="fact", importance=0.5)]
    rec, token = _turn()
    try:
        await memory_service.mark_memories_used([food, vet])  # what streaming does at prompt time
        await memory_service.extract_and_store_memories(
            messages=[{"role": "human", "content": "what should I feed the dog tonight?"},
                      {"role": "assistant", "content": "Grain-free salmon kibble, same as every day."}],
            conversation_id="c-1",
            existing_memories=retrieved,
        )
    finally:
        turn_ledger._current.reset(token)
    used_food, at_food = await _used(food)
    used_vet, at_vet = await _used(vet)
    assert (used_food, used_vet) == (1, 0)
    assert at_food is not None and at_vet is None


@pytest.mark.integration
async def test_a_memory_that_was_not_shown_is_not_credited(clean_db, monkeypatch):
    """When the turn recorded what it showed, only those can be cited."""
    from services import memory_service, turn_ledger

    (food,) = await _seed("Biscuit eats grain-free salmon kibble twice a day")

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = "[]"

            return R()

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    rec, token = _turn()
    try:
        await memory_service.mark_memories_used([str(uuid.uuid4())])  # something else was shown
        await memory_service.extract_and_store_memories(
            messages=[{"role": "human", "content": "dinner for the dog?"},
                      {"role": "assistant", "content": "Grain-free salmon kibble."}],
            conversation_id="c-1",
            existing_memories=[memory_service.Memory(id=food, content="Biscuit eats grain-free salmon kibble twice "
                                                     "a day", memory_type="fact", importance=0.5)],
        )
    finally:
        turn_ledger._current.reset(token)
    assert (await _used(food))[0] == 0
