"""Ellis resolves an open contradiction by choosing which memory to keep.

The old PATCH only flipped ``resolved``; it never retired the losing memory,
so both kept being retrieved. ``POST /api/consolidation/flags/{id}/resolve``
with ``{"keep": "a" | "b"}`` retires the other side the same way the
automatic path does, and the flags list can carry both memory texts so the
panel can show them side by side.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select


async def _seed(content: str) -> str:
    from services.memory_service import Memory, store_memory
    m = await store_memory(Memory(id=None, content=content, memory_type="fact", importance=0.8,
                                  source_type="user_stated", confidence=0.9))
    return m.id


async def _flag(a: str, b: str) -> str:
    from services.database import async_session, MemoryFlagModel
    fid = str(uuid.uuid4())
    async with async_session() as session:
        session.add(MemoryFlagModel(id=fid, memory_id=a, flag_type="contradiction",
                                    description="meds", related_memory_id=b))
        await session.commit()
    return fid


async def test_keep_b_retires_a_and_resolves(clean_db):
    from routers.consolidation import keep_flag_side
    from services.consolidation_models import FlagKeepUpdate
    from services.database import async_session, MemoryModel, MemoryFlagModel

    a = await _seed("No medication is scheduled for any pet")
    b = await _seed("Biscuit needs a steroid every morning")
    fid = await _flag(a, b)

    out = await keep_flag_side(fid, FlagKeepUpdate(keep="b"))
    assert out.resolved is True and out.resolved_at

    async with async_session() as session:
        ma = (await session.execute(select(MemoryModel).where(MemoryModel.id == a))).scalar_one()
        mb = (await session.execute(select(MemoryModel).where(MemoryModel.id == b))).scalar_one()
        flag = (await session.execute(select(MemoryFlagModel).where(MemoryFlagModel.id == fid))).scalar_one()
    assert ma.valid_until is not None and ma.confidence == pytest.approx(0.2)
    assert mb.valid_until is None
    assert flag.resolved is True


async def test_unknown_flag_is_404(clean_db):
    from fastapi import HTTPException
    from routers.consolidation import keep_flag_side
    from services.consolidation_models import FlagKeepUpdate

    with pytest.raises(HTTPException) as exc:
        await keep_flag_side("nope", FlagKeepUpdate(keep="a"))
    assert exc.value.status_code == 404


async def test_flags_list_can_carry_both_texts(clean_db):
    from routers.consolidation import get_flags

    a = await _seed("Ellis drinks oat milk")
    b = await _seed("Ellis is lactose tolerant and drinks whole milk")
    await _flag(a, b)

    plain = await get_flags(resolved=False, limit=50, offset=0)
    assert plain[0].memory_content is None

    rich = await get_flags(resolved=False, limit=50, offset=0, with_content=True)
    assert rich[0].memory_content == "Ellis drinks oat milk"
    assert rich[0].related_memory_content == "Ellis is lactose tolerant and drinks whole milk"


# Review 2026-09-26 (dreaming-connections-raw, dreams-flag-false-settled):
# a connection is shown as its two memories, not "Related · 41% · First ·
# Second"; and a deep-linked flag past the newest page is read directly.


async def _connect(a: str, b: str) -> str:
    from services.database import async_session, MemoryConnectionModel
    cid = str(uuid.uuid4())
    async with async_session() as session:
        session.add(MemoryConnectionModel(id=cid, memory_id_a=a, memory_id_b=b,
                                          connection_type="related", strength=0.41))
        await session.commit()
    return cid


async def test_connections_list_can_carry_both_texts(clean_db):
    from routers.consolidation import get_connections

    a = await _seed("Biscuit takes carprofen at 6 pm")
    b = await _seed("Biscuit's evening dose makes her sleepy")
    await _connect(a, b)

    plain = await get_connections(limit=50, offset=0)
    assert plain[0].memory_a_content is None

    rich = await get_connections(limit=50, offset=0, with_content=True)
    assert rich[0].memory_a_content == "Biscuit takes carprofen at 6 pm"
    assert rich[0].memory_b_content == "Biscuit's evening dose makes her sleepy"


async def test_one_flag_is_read_directly_with_its_texts(clean_db):
    from fastapi import HTTPException
    from routers.consolidation import get_flag

    a = await _seed("No medication is scheduled for any pet")
    b = await _seed("Biscuit needs a steroid every morning")
    fid = await _flag(a, b)

    flag = await get_flag(fid)
    assert flag.id == fid and flag.resolved is False
    assert flag.memory_content == "No medication is scheduled for any pet"
    assert flag.related_memory_content == "Biscuit needs a steroid every morning"

    with pytest.raises(HTTPException) as exc:
        await get_flag("nope")
    assert exc.value.status_code == 404
