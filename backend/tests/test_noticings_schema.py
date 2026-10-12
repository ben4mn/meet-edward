"""The noticings table and ``edward_notes.dismissed_at`` (AREA 1, 2026-10-11).

Why: proactivity is "notice, then speak" (docs/research-2026-10-10/
proactivity-spec.md §6). Every private noticing, every care alert and every
gate decision is a ``noticings`` row, so "did noticing work?" is a query. A
note Ellis pressed "Done" on carries ``dismissed_at`` so the learning ladder
can tell a dismissal from a note that simply expired. Both are additive and
idempotent in ``init_db``; no memory row is touched.
"""

import pytest
from sqlalchemy import text

pytestmark = pytest.mark.integration

NOTICING_COLUMNS = {
    "id", "created_at", "updated_at", "origin", "pass_id", "kind", "category", "claim", "sensitive",
    "sources_json", "chat_identifier", "who_json", "dedupe_key", "closes_at", "suggested", "status",
    "due_at", "attempts", "decision", "decision_reason", "decided_at", "floor_push", "surfaced_at",
    "channel", "push_log_id", "note_id", "thought_id", "ledger_id", "outcome", "outcome_at",
}


async def _columns(table: str) -> set:
    from services.database import engine

    async with engine.connect() as conn:
        rows = (await conn.execute(text(
            "SELECT column_name FROM information_schema.columns WHERE table_name = :t"), {"t": table})).all()
    return {r[0] for r in rows}


async def test_init_db_creates_the_noticings_table_with_every_column(clean_db):
    assert NOTICING_COLUMNS <= await _columns("noticings")


async def test_init_db_twice_is_idempotent(clean_db):
    from services.database import init_db

    await init_db()
    await init_db()
    assert NOTICING_COLUMNS <= await _columns("noticings")
    assert "dismissed_at" in await _columns("edward_notes")


async def test_noticings_indexes_exist(clean_db):
    from services.database import engine

    async with engine.connect() as conn:
        names = {r[0] for r in (await conn.execute(text(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'noticings'"))).all()}
    assert {"idx_noticings_created", "idx_noticings_status_due", "idx_noticings_dedupe",
            "idx_noticings_chat", "idx_noticings_category_surfaced"} <= names


async def test_a_noticing_row_round_trips_with_its_defaults(clean_db):
    from services.database import NoticingModel, async_session

    async with async_session() as s:
        s.add(NoticingModel(kind="deadline", claim="BA's 9:31 pm text says check-in closes at 10 pm."))
        await s.commit()
    async with async_session() as s:
        row = (await s.execute(text("SELECT origin, status, attempts, sensitive, floor_push, sources_json, "
                                    "created_at FROM noticings"))).one()
    assert row.origin == "noticer" and row.status == "open" and row.attempts == 0
    assert row.sensitive is False and row.floor_push is False and row.sources_json == "[]"
    assert row.created_at is not None


async def test_clean_db_truncates_noticings(clean_db):
    # conftest's pinned truncate list names the table, so a row from one test
    # never leaks into the next.
    from services import database
    from services.database import NoticingModel, async_session
    from tests.conftest import _truncate_all

    async with async_session() as s:
        s.add(NoticingModel(kind="security", claim="x"))
        await s.commit()
    await _truncate_all(database, text)
    async with async_session() as s:
        assert (await s.execute(text("SELECT count(*) FROM noticings"))).scalar() == 0
