"""What Ellis said changes only in his own turn — through the memory tools
too (integration review of the memory audit, 2026-09-27; pre-existing).

``remember_update`` and ``remember_forget`` took any memory id. Both are
in ``MIND_TOOLS`` and ``FREE_TIME_TOOLS`` (``REFLECT_LITE_TOOLS`` derives
from the first; ``RESEARCH_TOOLS`` has ``remember_update``), and
``remember_search`` prints ids. Reproduced on the test cluster: under a
``mind``, ``heartbeat`` or ``scheduled_event`` turn, ``remember_update``
rewrote a ``owner_confirmed`` pin and retired it with a past ``valid_until``,
and ``remember_forget`` hard-deleted it. Production's turn_log for the
last 30 days: 17 mind turns called ``remember_forget``, 28 called
``remember_update``, and heartbeat and scheduled-event turns used them too.

Contract pinned here: from a turn that is not Ellis's own (a wake, the
pulse, a fired event, a heartbeat reply, a worker, a Twilio text), both
tools refuse with ``Tool error:`` to change or delete a ``heard``/``owner``
row, and always a ``owner_confirmed`` one, and leave it exactly as it was.
Ellis's own turn (chat, voice, widget, Alexa — or a write with no turn on
record, which the chat stream's later steps are) still can. Edward's own
rows, what he read, and intentions stay his to change from a wake.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest

pytestmark = pytest.mark.integration

PIN = "No filler: skip the pleasantries and get to the point"


async def _seed(content=PIN, memory_type="instruction", origin="heard", about="owner", source_ref=None,
                temporal_nature="timeless"):
    from services.database import MemoryModel, async_session
    from services.memory_service import get_embedding

    async with async_session() as s:
        row = MemoryModel(content=content, memory_type=memory_type, importance=0.9, origin=origin, about=about,
                          source_type="user_stated" if origin == "heard" else "inferred", confidence=0.9,
                          temporal_nature=temporal_nature, tier="knowledge", embedding=get_embedding(content),
                          source_conversation_id=None, source_ref=source_ref)
        s.add(row)
        await s.commit()
        return row.id


def _pin_ref():
    return json.dumps({"kind": "owner_confirmed", "audit": "2026-09-27", "pin": "p_filler"})


async def _seed_pin(**over):
    return await _seed(source_ref=_pin_ref(), **over)


async def _row(memory_id):
    from services.database import MemoryModel, async_session

    async with async_session() as s:
        return await s.get(MemoryModel, memory_id)


class _Turn:
    def __init__(self, source):
        self.source = source

    def __enter__(self):
        from services import turn_ledger

        rec = turn_ledger.TurnRecord.start(conversation_id="c-1", source=self.source, model=None, provider=None)
        self.token = turn_ledger._current.set(rec)
        return rec

    def __exit__(self, *exc):
        from services import turn_ledger

        turn_ledger._current.reset(self.token)


async def _update(memory_id, **args):
    from services.graph.tools import remember_update

    return await remember_update.ainvoke({"memory_id": memory_id, **args})


async def _forget(memory_id):
    from services.graph.tools import remember_forget

    return await remember_forget.ainvoke({"memory_id": memory_id})


def _past():
    return (datetime.utcnow() - timedelta(hours=1)).replace(microsecond=0).isoformat() + "Z"


NOT_OWNERS_TURNS = ["mind", "pulse", "scheduled_event", "heartbeat", "worker", "twilio"]


@pytest.mark.parametrize("source", NOT_OWNERS_TURNS)
@pytest.mark.parametrize("seed", ["pin", "heard"])
async def test_a_turn_that_is_not_owners_cannot_rewrite_or_retire_his_row(clean_db, source, seed):
    mid = await (_seed_pin() if seed == "pin" else _seed())
    with _Turn(source):
        rewrite = await _update(mid, new_content="Keep it short unless Ellis asks for detail")
        retire = await _update(mid, valid_until=_past())
    assert rewrite.startswith("Tool error:") and retire.startswith("Tool error:"), (rewrite, retire)
    assert "Ellis" in rewrite, "the refusal says why, in his own words"
    row = await _row(mid)
    assert (row.content, row.valid_until) == (PIN, None), "left exactly as it was"


@pytest.mark.parametrize("source", NOT_OWNERS_TURNS)
@pytest.mark.parametrize("seed", ["pin", "heard"])
async def test_a_turn_that_is_not_owners_cannot_delete_his_row(clean_db, source, seed):
    mid = await (_seed_pin() if seed == "pin" else _seed())
    with _Turn(source):
        out = await _forget(mid)
    assert out.startswith("Tool error:"), out
    assert await _row(mid) is not None, "not deleted"


async def test_a_pin_is_refused_whatever_its_labels(clean_db):
    """``owner_confirmed`` is enough on its own: a pin relabelled ``read`` by a
    later pass is still the rule Ellis confirmed."""
    mid = await _seed_pin(origin="read")
    with _Turn("mind"):
        assert (await _update(mid, valid_until=_past())).startswith("Tool error:")
        assert (await _forget(mid)).startswith("Tool error:")
    assert (await _row(mid)).valid_until is None


@pytest.mark.parametrize("source", ["chat", "voice", "widget", "alexa", None])
async def test_owners_own_turn_still_changes_and_forgets_his_rows(clean_db, source):
    """``None``: no turn on record — the chat stream's later steps and
    direct calls, which the chat profile takes as Ellis's."""
    pin, heard = await _seed_pin(), await _seed(content="Ellis prefers short replies in the morning",
                                                memory_type="preference")
    if source is None:
        out = await _update(pin, new_content="Skip the pleasantries")
        gone = await _forget(heard)
    else:
        with _Turn(source):
            out = await _update(pin, new_content="Skip the pleasantries")
            gone = await _forget(heard)
    assert (out, gone) == ("ok", "ok")
    assert (await _row(pin)).content == "Skip the pleasantries"
    assert await _row(heard) is None


@pytest.mark.parametrize("origin,about,memory_type", [
    ("thought", "self", "context"),
    ("did", "self", "context"),
    ("read", "owner", "preference"),
    ("read", "contact", "instruction"),
    ("thought", "self", "intention"),
    ("heard", "owner", "intention"),
])
async def test_a_wake_still_tidies_what_is_not_owners(clean_db, origin, about, memory_type):
    """Edward's own rows, what he read, and intentions — even one Ellis
    voiced, which ``close_intention`` closes from a wake anyway — stay his
    to change."""
    mid = await _seed(content="Watch whether Parker confirms Friday", memory_type=memory_type, origin=origin,
                      about=about, temporal_nature="evolving")
    other = await _seed(content="Parker is at the gym", memory_type=memory_type, origin=origin, about=about,
                        temporal_nature="temporary")
    with _Turn("mind"):
        assert (await _update(mid, valid_until=_past())).startswith("ok")
        assert await _forget(other) == "ok"
    assert (await _row(mid)).valid_until is not None
    assert await _row(other) is None
