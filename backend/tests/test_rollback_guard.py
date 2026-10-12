"""The rollback guard and the rebuild's own seeds (review of AREA 1,
2026-10-11).

The base carries a forward-compatible guard (``edward/area1-rollback-guard``):
before the rebuild, ``proactive_sweep`` is in ``RETIRED_CRON_EVENTS`` and the
``nightly`` and ``free`` tick kinds in ``RETIRED_WAKEUP_KINDS``, so a
``git revert`` of the rebuild cancels its seeds at boot instead of running
the sweep as ~288 Opus event turns a day and the ticks as full-tool
``manual`` wakes. In the rebuild those seeds are live: its lists win here,
and a revert restores the guard's (and that version of this file).
"""

from __future__ import annotations

import pytest


@pytest.mark.unit
@pytest.mark.parametrize("kind", ["nightly", "free"])
def test_the_rebuilds_ticks_are_live_here(kind):
    from services.scheduler_service import WAKEUP_TICKS, is_retired_wakeup_seed

    assert is_retired_wakeup_seed({"type": "wakeup_tick", "kind": kind}) is False
    assert kind in dict(WAKEUP_TICKS)


@pytest.mark.unit
def test_the_rebuilds_sweep_is_live_here():
    from services.scheduler_service import EXTRA_CRON_EVENTS, RETIRED_CRON_EVENTS

    assert "proactive_sweep" not in RETIRED_CRON_EVENTS
    assert "proactive_sweep" in dict(EXTRA_CRON_EVENTS)


def _documented_rollback_sql() -> str:
    import re
    from pathlib import Path

    doc = (Path(__file__).resolve().parents[2] / "docs" / "architecture" / "scheduler.md").read_text()
    section = doc[doc.index("**Rolling back the rebuild**"):]
    return re.search(r"```sql\n(.*?)```", section, re.S).group(1)


@pytest.mark.integration
async def test_the_documented_rollback_sql_cancels_exactly_the_rebuilds_seeds(clean_db):
    """The by-hand step in scheduler.md, run as written: the sweep and the
    nightly and free ticks go; Ellis's reminders, the pulse, a ``wake_me``,
    a row with no or broken metadata stay."""
    import json

    from sqlalchemy import select, text

    from services.database import ScheduledEventModel, async_session, engine
    from services.scheduled_events_service import _utcnow

    rows = (
        ("sweep", json.dumps({"type": "proactive_sweep"}), "pending", True),
        ("nightly", json.dumps({"type": "wakeup_tick", "kind": "nightly"}), "pending", True),
        ("free", json.dumps({"type": "wakeup_tick", "kind": "free"}), "processing", True),
        ("pulse", json.dumps({"type": "expression_pulse"}), "pending", False),
        ("wake_me", json.dumps({"type": "mind_wake", "why": "the free quote"}), "pending", False),
        ("owner", json.dumps({"created_in": "chat"}), "pending", False),
        ("plain", None, "pending", False),
        ("broken", "{not json", "pending", False),
        ("done", json.dumps({"type": "proactive_sweep"}), "completed", False),
    )
    async with async_session() as s:
        for eid, meta, status, _ in rows:
            s.add(ScheduledEventModel(id=eid, description=eid, scheduled_at=_utcnow(), next_fire_at=_utcnow(),
                                      status=status, event_metadata=meta))
        await s.commit()
    async with engine.begin() as conn:
        await conn.execute(text(_documented_rollback_sql()))
    async with async_session() as s:
        after = {r.id: r.status for r in (await s.execute(select(ScheduledEventModel))).scalars().all()}
    for eid, _meta, status, cancelled in rows:
        assert after[eid] == ("cancelled" if cancelled else status), eid
