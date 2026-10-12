"""
``self_observations`` substrate (Track F1).

The vision track's feedback loop (dream → evolution) needs a place to
record "Edward noticed X about Edward." Repeated observations with the
same signature must collapse into one row with an incrementing
``occurrences`` counter so the synthesis judge can require a minimum
signal before proposing anything.

The ``record_observation`` helper is the single write path; the table
is the substrate for every emitter (tool failures, push ignored,
retrieval misses, etc).
"""

from __future__ import annotations

import pytest
from sqlalchemy import select


def test_model_exists_with_expected_columns():
    """Guard against accidental removal of the F1 schema."""
    from services.database import SelfObservationModel

    cols = set(SelfObservationModel.__table__.columns.keys())
    expected = {
        "id",
        "kind",
        "source",
        "severity",
        "confidence",
        "description",
        "evidence_json",
        "signature",
        "occurrences",
        "status",
        "created_at",
        "last_seen_at",
    }
    missing = expected - cols
    assert not missing, (
        f"SelfObservationModel missing columns: {missing}. "
        f"Present: {sorted(cols)}"
    )


@pytest.mark.integration
async def test_record_observation_creates_new_row(clean_db):
    from services.observe import record_observation
    from services.database import async_session, SelfObservationModel

    await record_observation(
        kind="tool_failure",
        source="apple_mcp",
        signature="apple_mcp.sms_send.timeout",
        description="Apple MCP sms_send timed out",
        severity=0.4,
        confidence=0.9,
        evidence={"tool": "sms_send", "last_latency_ms": 12000},
    )

    async with async_session() as session:
        r = await session.execute(select(SelfObservationModel))
        rows = list(r.scalars().all())

    assert len(rows) == 1
    row = rows[0]
    assert row.kind == "tool_failure"
    assert row.source == "apple_mcp"
    assert row.signature == "apple_mcp.sms_send.timeout"
    assert row.occurrences == 1
    assert row.status == "open"


@pytest.mark.integration
async def test_record_observation_increments_occurrences_on_same_signature(clean_db):
    """Two calls with the same signature must collapse into one row."""
    from services.observe import record_observation
    from services.database import async_session, SelfObservationModel

    sig = "apple_mcp.sms_send.timeout"
    await record_observation(
        kind="tool_failure",
        source="apple_mcp",
        signature=sig,
        description="first occurrence",
    )
    await record_observation(
        kind="tool_failure",
        source="apple_mcp",
        signature=sig,
        description="second occurrence",
    )

    async with async_session() as session:
        r = await session.execute(select(SelfObservationModel))
        rows = list(r.scalars().all())

    assert len(rows) == 1, (
        "Same-signature observations must collapse; got "
        f"{len(rows)} rows."
    )
    assert rows[0].occurrences == 2


@pytest.mark.integration
async def test_different_signatures_create_separate_rows(clean_db):
    from services.observe import record_observation
    from services.database import async_session, SelfObservationModel

    await record_observation(
        kind="tool_failure",
        source="apple_mcp",
        signature="apple_mcp.a.timeout",
        description="a",
    )
    await record_observation(
        kind="tool_failure",
        source="apple_mcp",
        signature="apple_mcp.b.timeout",
        description="b",
    )

    async with async_session() as session:
        r = await session.execute(select(SelfObservationModel))
        rows = list(r.scalars().all())

    assert len(rows) == 2


@pytest.mark.integration
async def test_record_observation_writes_a_hit_per_call(clean_db):
    """Signature collapsing keeps one row; hits keep *when*, so a health
    check can see whether a pattern stopped after a fix."""
    from services.observe import record_observation
    from services.database import async_session, SelfObservationHitModel

    oid = await record_observation(
        kind="tool_failure", source="tool_loop", signature="tool_failure.web_search.timeout",
        description="web_search timed out", evidence={"result": "x" * 2000},
    )
    await record_observation(
        kind="tool_failure", source="tool_loop", signature="tool_failure.web_search.timeout",
        description="web_search timed out",
    )
    async with async_session() as s:
        hits = (await s.execute(
            select(SelfObservationHitModel).where(SelfObservationHitModel.observation_id == oid)
        )).scalars().all()
    assert len(hits) == 2
    assert hits[0].evidence_json is not None and len(hits[0].evidence_json) <= 500
    assert hits[1].evidence_json is None
