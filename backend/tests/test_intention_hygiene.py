"""Intention hygiene (2026-09-25 production audit).

An ``intention`` is a thing Edward decided to do or watch; the wake pack
lists every live one under "Open intentions (N)", and the Sunday
reflection is the only thing that closes them. The audit found the list
growing without bound, for three reasons this file pins:

  * the extractor filed what Edward *did* (``origin=did``) as an
    intention — an action and its outcome is a record, never a plan, so it
    is coerced to ``context``;
  * an intention from a wake with no ``valid_until`` lived forever — under
    the ``mind`` profile it now gets ``now + 72h`` (a one-shot plan that
    matters longer can be renewed, or linked to an event, which holds it);
  * "I looked and decided to leave it" was stored as an intention to leave
    it — the mind addendum now says that decision is a thought
    (``context``), not an intention.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 25, 16, 0, tzinfo=timezone.utc)
NAIVE_NOW = NOW.replace(tzinfo=None)


def _item(**over):
    base = {
        "content": "I decided to watch Acme stock for Ellis",
        "memory_type": "intention",
        "importance": 0.7,
        "source_type": "inferred",
        "confidence": 0.8,
        "origin": "thought",
        "about": "self",
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# origin=did is never an intention
# ---------------------------------------------------------------------------


def test_a_did_row_filed_as_an_intention_becomes_context():
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(
        _item(content="I pushed Ellis about Biscuit's dose; he opened it", origin="did"), now=NOW,
    )
    assert fields["memory_type"] == "context"
    assert fields["origin"] == "did"
    assert "memory_type" in misses, "the coercion is reported, so a drifting extractor is visible"


def test_a_did_row_that_was_coerced_defaults_to_temporary_not_evolving():
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(_item(origin="did"), now=NOW, profile="mind")
    assert fields["temporal_nature"] == "temporary"
    assert fields["valid_until"] is None, "a context row gets no intention horizon"


def test_a_did_row_is_coerced_under_every_profile():
    from services.memory_service import coerce_extracted_fields

    for profile in ("chat", "heartbeat", "mind"):
        fields, _ = coerce_extracted_fields(_item(origin="did"), now=NOW, profile=profile)
        assert fields["memory_type"] == "context", profile


def test_other_did_rows_are_untouched():
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(
        _item(memory_type="context", origin="did", temporal_nature="temporary"), now=NOW,
    )
    assert fields["memory_type"] == "context"
    assert "memory_type" not in misses


# ---------------------------------------------------------------------------
# A wake's intention without a horizon lapses in three days
# ---------------------------------------------------------------------------


def test_a_mind_intention_with_no_valid_until_gets_seventy_two_hours():
    from services.memory_service import MIND_INTENTION_DEFAULT_HOURS, coerce_extracted_fields

    assert MIND_INTENTION_DEFAULT_HOURS == 72
    fields, _ = coerce_extracted_fields(_item(), now=NOW, profile="mind")
    assert fields["memory_type"] == "intention"
    assert fields["valid_until"] == NAIVE_NOW + timedelta(hours=72)
    assert fields["valid_until"].tzinfo is None, "naive UTC, like every other valid_until"


def test_a_mind_intention_with_an_unusable_valid_until_also_gets_the_default():
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields(_item(valid_until="next week"), now=NOW, profile="mind")
    assert fields["valid_until"] == NAIVE_NOW + timedelta(hours=72)
    assert "valid_until" in misses


def test_a_mind_intention_keeps_the_horizon_it_named():
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(
        _item(valid_until="2026-10-18T00:00:00Z"), now=NOW, profile="mind",
    )
    assert fields["valid_until"] == datetime(2026, 10, 18)


def test_a_chat_intention_is_not_given_a_default_horizon():
    """Ellis asking in chat ("keep an eye on Acme stock") is his to end; only a
    wake's own plans lapse by default."""
    from services.memory_service import coerce_extracted_fields

    for profile in ("chat", "heartbeat"):
        fields, _ = coerce_extracted_fields(_item(), now=NOW, profile=profile)
        assert fields["valid_until"] is None, profile


def test_the_default_profile_is_chat():
    from services.memory_service import coerce_extracted_fields

    fields, _ = coerce_extracted_fields(_item(), now=NOW)
    assert fields["valid_until"] is None


# ---------------------------------------------------------------------------
# The mind addendum
# ---------------------------------------------------------------------------


def test_the_mind_addendum_says_leaving_it_alone_is_a_thought_not_an_intention():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM

    text = MIND_EXTRACTION_ADDENDUM.lower()
    assert "leave" in text and "alone" in text
    line = next(l for l in MIND_EXTRACTION_ADDENDUM.splitlines() if "alone" in l.lower())
    assert '"memory_type": "context"' in line
    assert "not an intention" in line.lower()


def test_the_mind_addendum_says_what_he_did_is_never_an_intention():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM

    line = next(l for l in MIND_EXTRACTION_ADDENDUM.splitlines() if '"origin": "did"' in l)
    assert "never" in line.lower() and "intention" in line.lower()


def test_the_mind_addendum_says_an_intention_without_a_horizon_lapses():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM

    line = next(l for l in MIND_EXTRACTION_ADDENDUM.splitlines() if '"memory_type": "intention"' in l)
    assert "72 hours" in line


def test_the_mind_prompt_carries_the_addendum():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM, build_extraction_prompt

    assert MIND_EXTRACTION_ADDENDUM in build_extraction_prompt("USER: hi", "None", profile="mind", now=NOW)


# ---------------------------------------------------------------------------
# The extraction pipeline passes its profile through
# ---------------------------------------------------------------------------

_PAYLOAD = (
    '[{"content": "I decided to check on the Parker thread again tomorrow", "memory_type": "intention", '
    '"importance": 0.6, "temporal_nature": "evolving", "source_type": "inferred", "confidence": 0.8, '
    '"origin": "thought", "about": "self"},'
    '{"content": "I texted Parker back about the flight; he replied thanks", "memory_type": "intention", '
    '"importance": 0.5, "temporal_nature": "evolving", "source_type": "inferred", "confidence": 0.9, '
    '"origin": "did", "about": "self"}]'
)


def _stub_pipeline(monkeypatch):
    from services import memory_service

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = _PAYLOAD

            return R()

    stored = []

    async def _store(memories):
        stored.extend(memories)
        return memories

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    monkeypatch.setattr("services.entity_service.extract_entities", _noop)
    return stored


async def test_a_wake_extraction_stores_a_bounded_intention_and_a_did_context(monkeypatch):
    from services import memory_service

    stored = _stub_pipeline(monkeypatch)
    before = datetime.utcnow()
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "Wake pack · morning"},
                  {"role": "assistant", "content": "Replied to Parker. I'll look at the thread again tomorrow."}],
        conversation_id="edward-mind",
        extraction_profile="mind",
    )
    intention = next(m for m in stored if m.origin == "thought")
    assert intention.memory_type == "intention"
    assert intention.valid_until is not None
    horizon = intention.valid_until - before
    assert timedelta(hours=71) < horizon <= timedelta(hours=73)

    did = next(m for m in stored if m.origin == "did")
    assert did.memory_type == "context"
    assert did.valid_until is None


async def test_a_chat_extraction_leaves_the_intention_open(monkeypatch):
    from services import memory_service

    stored = _stub_pipeline(monkeypatch)
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "keep an eye on the Parker thread"}],
        conversation_id="c1",
        extraction_profile="chat",
    )
    intention = next(m for m in stored if m.origin == "thought")
    assert intention.memory_type == "intention"
    assert intention.valid_until is None
    assert next(m for m in stored if m.origin == "did").memory_type == "context"


# ---------------------------------------------------------------------------
# A linked intention is held until its wake (review 2026-09-26): the 72 h
# default used to lapse before a wake_me set five days out, while the tool
# promised the intention "is held from forgetting until this wake has fired".
# ---------------------------------------------------------------------------


def test_a_linked_intentions_horizon_reaches_past_its_fire():
    from services.scheduled_events_service import extended_horizon

    fire = datetime(2026, 9, 30, 9, 0)
    lapses_first = NAIVE_NOW + timedelta(hours=72)            # Sep 28 16:00, before the fire
    assert extended_horizon(lapses_first, fire, None, NAIVE_NOW) == fire + timedelta(days=1)
    assert extended_horizon(fire + timedelta(days=3), fire, None, NAIVE_NOW) is None, "already long enough"
    assert extended_horizon(None, fire, None, NAIVE_NOW) is None, "open-ended stays open-ended"
    recurring = extended_horizon(lapses_first, fire, "0 9 * * 1", NAIVE_NOW)
    assert recurring == NAIVE_NOW + timedelta(days=30)


async def test_remember_in_a_wake_says_when_an_intention_lapses(monkeypatch):
    from services import memory_service
    from services.graph import tools

    async def _store(memories):
        for m in memories:
            m.id = "m-1"
        return memories

    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    monkeypatch.setattr(tools, "_remember_profile", lambda: "mind")
    out = await tools.remember.ainvoke({"content": "Watch the Parker thread for the flight time",
                                        "memory_type": "intention", "about": "self", "origin": "thought"})
    assert out.startswith("ok (memory_id: m-1)")
    assert "lapses" in out and "wake_me" in out
    monkeypatch.setattr(tools, "_remember_profile", lambda: "chat")
    out = await tools.remember.ainvoke({"content": "Watch the Parker thread", "memory_type": "intention",
                                        "about": "self", "origin": "thought"})
    assert out == "ok (memory_id: m-1)"
    assert "72 hours" in tools.remember.description


@pytest.mark.integration
async def test_wake_me_holds_its_intention_until_it_fires(clean_db):
    from sqlalchemy import select

    from services.database import MemoryModel, async_session
    from services.scheduled_events_service import create_event

    now = datetime.utcnow()
    async with async_session() as s:
        s.add(MemoryModel(id="int-1", content="Check whether Parker's flight moved", memory_type="intention",
                          importance=0.6, origin="thought", about="self", valid_until=now + timedelta(hours=72)))
        await s.commit()
    fire = now + timedelta(days=5)
    await create_event(description="[mind wake] Parker's flight", scheduled_at=fire.replace(tzinfo=timezone.utc),
                       conversation_id="edward-mind", delivery_channel="chat",
                       event_metadata={"type": "mind_wake", "why": "Parker's flight"}, memory_id="int-1")
    async with async_session() as s:
        row = (await s.execute(select(MemoryModel).where(MemoryModel.id == "int-1"))).scalar_one()
    assert abs((row.valid_until - (fire + timedelta(days=1))).total_seconds()) < 5
