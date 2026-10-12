"""``scripts/run_memory_decay.py --apply`` says what it wrote (2026-09-27).

Batch A2 made the pass return the one ``valid_until`` it stamps on every row
it retires, and which rule retired how many (``by_rule``). The script
printed neither, and told Ellis to find the stamp with psql to undo a pass —
and its ``--revert-since`` clears every ``valid_until`` at or after a time,
which includes every intention's and instruction's horizon still in the
future. ``--revert-stamp`` clears exactly the rows one pass retired.
"""

from __future__ import annotations

import sys

import pytest


async def _run(monkeypatch, capsys, argv, result):
    from scripts import run_memory_decay
    from services import memory_decay_service

    seen = {}

    async def fake_decay(dry_run=True, limit=None, **kw):
        seen.update(dry_run=dry_run, limit=limit)
        return {"scanned": 10, "candidates": 3, "breakdown": {"temporary/inferred": 3}, "sample": ["x"],
                "dry_run": dry_run, **result}

    monkeypatch.setattr(memory_decay_service, "decay_stale_memories", fake_decay)
    monkeypatch.setattr(sys, "argv", ["run_memory_decay.py", *argv])
    assert await run_memory_decay.main() == 0
    return seen, capsys.readouterr().out


async def test_apply_prints_the_stamp_and_the_count_by_rule(monkeypatch, capsys):
    seen, out = await _run(monkeypatch, capsys, ["--apply"], {
        "retired": 3, "retired_ids": ["a", "b", "c"], "stamp": "2026-09-27T09:00:05.123456",
        "by_rule": {"curve": 2, "instruction_horizon": 1},
    })
    assert seen == {"dry_run": False, "limit": None}
    assert "2026-09-27T09:00:05.123456" in out
    assert "--revert-stamp 2026-09-27T09:00:05.123456" in out
    assert "curve" in out and "instruction_horizon" in out


async def test_a_dry_run_shows_the_rules_and_writes_no_stamp(monkeypatch, capsys):
    seen, out = await _run(monkeypatch, capsys, [], {
        "retired": 0, "retired_ids": [], "stamp": None, "by_rule": {"sensitive": 0, "curve": 3},
    })
    assert seen["dry_run"] is True
    assert "curve" in out
    assert "--revert" not in out
    assert "DRY RUN" in out


async def test_apply_with_nothing_retired_says_so_without_a_stamp(monkeypatch, capsys):
    _, out = await _run(monkeypatch, capsys, ["--apply"], {
        "candidates": 0, "retired": 0, "retired_ids": [], "stamp": None, "by_rule": {},
    })
    assert "retired: 0" in out
    assert "--revert" not in out


@pytest.mark.integration
async def test_revert_stamp_clears_only_the_rows_that_pass_retired(clean_db):
    from datetime import datetime, timedelta

    from sqlalchemy import select

    from scripts.run_memory_decay import _revert_stamp
    from services.database import MemoryModel, async_session

    stamp = datetime(2026, 9, 27, 9, 0, 5, 123456)
    later = datetime.utcnow() + timedelta(days=3)
    async with async_session() as s:
        for content, until in (("retired by the pass", stamp), ("an intention's horizon", later),
                               ("retired a second later", stamp + timedelta(seconds=1))):
            s.add(MemoryModel(content=content, memory_type="fact", importance=0.5, valid_until=until))
        await s.commit()

    assert await _revert_stamp(stamp.isoformat()) == 1
    async with async_session() as s:
        rows = {m.content: m.valid_until for m in (await s.execute(select(MemoryModel))).scalars()}
    assert rows["retired by the pass"] is None
    assert rows["an intention's horizon"] == later
    assert rows["retired a second later"] == stamp + timedelta(seconds=1)


@pytest.mark.integration
async def test_revert_since_binds_a_time_not_a_string(clean_db):
    """It raised asyncpg's DataError on every call before (2026-09-27)."""
    from datetime import datetime, timedelta

    from scripts.run_memory_decay import _revert_since
    from services.database import MemoryModel, async_session

    async with async_session() as s:
        s.add(MemoryModel(content="retired", memory_type="fact", importance=0.5,
                          valid_until=datetime(2026, 9, 27, 9, 0, 5)))
        s.add(MemoryModel(content="older", memory_type="fact", importance=0.5,
                          valid_until=datetime(2026, 9, 1) - timedelta(days=1)))
        await s.commit()
    assert await _revert_since("2026-09-27T00:00:00Z") == 1
