"""The settings page shows growth, not just the total.

After the 2026-09-05 remediation the number that matters is how many rows
are live, how many of those have never been used, and how many arrived
today — the three figures that told us extraction was filling the store
with chatter. ``get_memory_stats`` used to return only the grand total.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from sqlalchemy import update


async def test_stats_include_live_unused_and_last_24h(clean_db):
    from services.memory_service import Memory, store_memory, get_memory_stats
    from services.database import async_session, MemoryModel

    a = await store_memory(Memory(id=None, content="Ellis likes oat milk", memory_type="preference", importance=0.7))
    b = await store_memory(Memory(id=None, content="Ellis ran on Tuesday", memory_type="context", importance=0.5))
    c = await store_memory(Memory(id=None, content="retired snapshot", memory_type="context", importance=0.4,
                                  valid_until=datetime.utcnow() - timedelta(hours=1)))
    async with async_session() as session:
        await session.execute(update(MemoryModel).where(MemoryModel.id == a.id).values(used_count=3))
        await session.execute(update(MemoryModel).where(MemoryModel.id == b.id)
                              .values(created_at=datetime.utcnow() - timedelta(days=3)))
        await session.commit()

    stats = await get_memory_stats()
    assert stats["total"] == 3
    assert stats["live"] == 2
    assert stats["unused_live"] == 1
    assert stats["created_last_24h"] == 2  # a and the retired c were created just now
