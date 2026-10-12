"""Owned sequences must outlive a database restore.

On 2026-09-07 the production database was restored from a backup after an
evolution test run truncated it. `pg_dump`-style restores write rows with
their original ids and do not advance the owning sequence, so every sequence
came back pointing at a value the table had already used.

The damage was silent and specific. `imessage_outgoing_sends` is the ledger
the heartbeat listener consults to recognise Edward's own iCloud-echoed
replies; with `last_value=20` against `max(id)=176`, every insert died on a
primary-key collision from 2026-09-08 onward. Echo suppression fell back to
an in-memory deque (maxlen 20, lost on restart) that the ledger had been
built to replace, which re-opened the self-trigger path that
`test_imessage_self_trigger_dedup` exists to close. `skills_audit` — the
guardrail added after the 2026-07 mass skill-disable — was broken the same
way and just as quietly.

Nothing detected either for three days. `init_db` now repairs any sequence
that has fallen behind its column, so the next restore self-heals.
"""

import pytest
from sqlalchemy import text

from services.database import engine, resync_sequences


async def _seq_for(conn, table: str) -> str:
    return (await conn.execute(text(f"SELECT pg_get_serial_sequence('{table}', 'id')"))).scalar()


async def test_a_rewound_sequence_is_repaired(clean_db):
    """The restore case: the sequence trails rows that already exist."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _resync_probe (id SERIAL PRIMARY KEY, v TEXT)"
        ))
        await conn.execute(text("TRUNCATE _resync_probe RESTART IDENTITY"))
        # Rows land with explicit ids, exactly as a restore writes them.
        await conn.execute(text(
            "INSERT INTO _resync_probe (id, v) VALUES (1,'a'), (2,'b'), (176,'c')"
        ))
        seq = await _seq_for(conn, "_resync_probe")
        await conn.execute(text(f"SELECT setval('{seq}', 20, true)"))

    await resync_sequences()

    async with engine.begin() as conn:
        # The next insert must not collide.
        await conn.execute(text("INSERT INTO _resync_probe (v) VALUES ('after')"))
        new_id = (await conn.execute(
            text("SELECT id FROM _resync_probe WHERE v='after'")
        )).scalar()
        assert new_id > 176, f"sequence still behind the table: got id={new_id}"
        await conn.execute(text("DROP TABLE _resync_probe"))


async def test_a_healthy_sequence_is_left_alone(clean_db):
    """Repair must not skip ids on a database that is already correct."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _resync_ok (id SERIAL PRIMARY KEY, v TEXT)"
        ))
        await conn.execute(text("TRUNCATE _resync_ok RESTART IDENTITY"))
        await conn.execute(text("INSERT INTO _resync_ok (v) VALUES ('a'), ('b')"))

    await resync_sequences()

    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO _resync_ok (v) VALUES ('c')"))
        got = (await conn.execute(text("SELECT id FROM _resync_ok WHERE v='c'"))).scalar()
        assert got == 3, f"repair skipped ids on a healthy sequence: {got}"
        await conn.execute(text("DROP TABLE _resync_ok"))


async def test_an_empty_table_is_left_alone(clean_db):
    """max(id) is NULL here — the repair must not crash or rewind."""
    async with engine.begin() as conn:
        await conn.execute(text(
            "CREATE TABLE IF NOT EXISTS _resync_empty (id SERIAL PRIMARY KEY, v TEXT)"
        ))
        await conn.execute(text("TRUNCATE _resync_empty RESTART IDENTITY"))

    await resync_sequences()

    async with engine.begin() as conn:
        await conn.execute(text("INSERT INTO _resync_empty (v) VALUES ('a')"))
        got = (await conn.execute(text("SELECT id FROM _resync_empty WHERE v='a'"))).scalar()
        assert got == 1
        await conn.execute(text("DROP TABLE _resync_empty"))


def test_init_db_runs_the_repair():
    """A repair nothing calls is not a repair."""
    import inspect

    from services import database

    assert "resync_sequences" in inspect.getsource(database.init_db), (
        "init_db must resync sequences, or the next restore silently breaks "
        "the dedup ledger again"
    )
