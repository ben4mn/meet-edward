"""R/G TDD for Task #10 — Weekly Journal API.

The journal aggregates the learn-loop's exhaust into one queryable
endpoint so the frontend can show "what Edward did and decided this
week." Without it, all the new observations / wakeup ticks ship
invisibly.

Endpoint: ``GET /api/journal/week`` returns counts + recent items for:
* self_observations (open / superseded, by kind)
* wakeup_ticks (decision_action breakdown + recent narratives)
* memory_flags (resolved vs open)
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest


@pytest.mark.integration
async def test_journal_week_returns_observation_counts_by_kind(
    clean_db,
):
    from services.database import (
        async_session,
        SelfObservationModel,
    )
    from services.journal_service import build_weekly_journal

    async with async_session() as session:
        for kind, sig in [
            ("user_correction", "user_correction.thats_wrong"),
            ("user_correction", "user_correction.actually"),
            ("push_ignored", "push_ignored.+1555"),
        ]:
            session.add(SelfObservationModel(
                kind=kind,
                source="test",
                signature=sig,
                description="x",
                occurrences=1,
                status="open",
                last_seen_at=datetime.utcnow(),
            ))
        await session.commit()

    summary = await build_weekly_journal()
    obs = summary["observations"]
    counts = {row["kind"]: row["count"] for row in obs["by_kind"]}
    assert counts.get("user_correction") == 2
    assert counts.get("push_ignored") == 1


@pytest.mark.integration
async def test_journal_week_includes_wake_breakdown(clean_db):
    """Since the mind rebuild (Phase 3) the Journal reads the mind's ledger."""
    from services import mind_service
    from services.journal_service import build_weekly_journal

    for outcome in ("acted", "acted", "left_alone", "skipped"):
        await mind_service._record_now(source="morning", outcome=outcome, note="test", tools=[])

    summary = await build_weekly_journal()
    outcomes = {row["outcome"]: row["count"] for row in summary["wakes"]["by_outcome"]}
    assert outcomes.get("acted") == 2
    assert outcomes.get("left_alone") == 1
    assert outcomes.get("skipped") == 1


@pytest.mark.integration
async def test_journal_week_caps_to_seven_days(clean_db):
    """Observations older than 7 days are excluded from totals."""
    from services.database import async_session, SelfObservationModel
    from services.journal_service import build_weekly_journal

    now = datetime.utcnow()
    async with async_session() as session:
        # In-window
        session.add(SelfObservationModel(
            kind="user_correction",
            source="test",
            signature="recent",
            description="recent",
            occurrences=1,
            status="open",
            last_seen_at=now - timedelta(days=2),
        ))
        # Out of window
        session.add(SelfObservationModel(
            kind="user_correction",
            source="test",
            signature="ancient",
            description="ancient",
            occurrences=1,
            status="open",
            last_seen_at=now - timedelta(days=15),
        ))
        await session.commit()

    summary = await build_weekly_journal()
    obs = summary["observations"]
    total = obs.get("total_in_window", 0)
    assert total == 1, f"expected 1 within 7d; got {total}"


@pytest.mark.integration
async def test_journal_includes_recent_wake_notes(clean_db):
    from services import mind_service
    from services.journal_service import build_weekly_journal

    await mind_service._record_now(source="morning", outcome="acted",
                                   note="3 unread + standup at 9; pushed Ellis the shape of the day", tools=[])

    summary = await build_weekly_journal()
    recent = summary["wakes"]["recent"]
    assert len(recent) == 1
    assert recent[0]["note"].startswith("3 unread")
