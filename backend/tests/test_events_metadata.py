"""GET /api/events carries each event's metadata (review follow-up
2026-09-26): the Edward tab tells a ``mind_wake`` he set for himself, a
goal's check-in and the fixed ticks apart from Ellis's reminders, and that
lives only in ``event_metadata``."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.unit


def _event(meta):
    t = datetime(2026, 9, 26, 12, 0)
    return SimpleNamespace(
        id="e1", conversation_id="edward-mind", description="Check on the vet", scheduled_at=t, next_fire_at=t,
        recurrence_pattern=None, status="pending", created_by="edward", delivery_channel=None, last_fired_at=None,
        fire_count=0, last_result=None, created_at=t, updated_at=t, event_metadata=meta,
    )


def test_an_event_carries_its_metadata_and_its_type():
    from routers.events import _model_to_schema

    out = _model_to_schema(_event('{"type": "mind_wake", "why": "the vet", "memory_id": "m1"}')).model_dump()
    assert out["event_metadata"] == {"type": "mind_wake", "why": "the vet", "memory_id": "m1"}
    assert out["event_type"] == "mind_wake"


@pytest.mark.parametrize("raw", [None, "", "not json", "[1, 2]"])
def test_an_event_without_readable_metadata_has_none(raw):
    from routers.events import _model_to_schema

    out = _model_to_schema(_event(raw)).model_dump()
    assert out["event_metadata"] is None and out["event_type"] is None
