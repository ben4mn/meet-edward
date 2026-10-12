"""scripts/prune_checkpoint_history.py runs to the end and never prints a password.

On 2026-09-27 (memory-audit reset) ``--apply`` deleted the old checkpoints and
then crashed before VACUUM: ``AsyncConnection.execution_options`` is a
coroutine and was called as if it returned the connection. The script also
printed the whole DATABASE_URL, password included.
"""
from __future__ import annotations

import pytest

from tests.test_gc_checkpoints import _counts, _seed, saver  # noqa: F401  (fixture)

pytestmark = pytest.mark.integration


async def test_apply_prunes_old_checkpoints_and_runs_through_vacuum(saver, capsys):  # noqa: F811
    from scripts import prune_checkpoint_history as prune

    await _seed(saver, "edward-mind", turns=4)
    before = await _counts()

    assert await prune.main(apply=True) == 0

    after = await _counts()
    assert after["checkpoints"] == 1 < before["checkpoints"]
    assert "vacuumed" in capsys.readouterr().out


async def test_the_database_url_is_printed_without_its_password(saver, capsys, monkeypatch):  # noqa: F811
    from scripts import prune_checkpoint_history as prune

    import services.database as db

    monkeypatch.setattr(db, "DATABASE_URL", "postgresql+asyncpg://edward:s3cr3t-pw@localhost:5433/edward_test")
    await prune.main(apply=False)

    out = capsys.readouterr().out
    assert "s3cr3t-pw" not in out
    assert "localhost:5433/edward_test" in out
