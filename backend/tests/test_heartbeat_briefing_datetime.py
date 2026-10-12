"""Regression tests for the heartbeat briefing datetime crash.

Production logs showed 2,132 instances of:
    Heartbeat briefing failed: can't subtract offset-naive and offset-aware datetimes

Root cause: ``_time_ago`` builds a naive ``now`` (datetime.utcnow()) but
coerces the incoming event timestamp to tz-aware UTC, then subtracts —
naive minus aware raises TypeError. DB rows carry tz-aware created_at,
so every briefing call crashed.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.heartbeat.heartbeat_service import _time_ago


def test_time_ago_accepts_tz_aware_datetime():
    """A tz-aware timestamp (what the DB returns) must not crash."""
    dt = datetime.now(timezone.utc) - timedelta(minutes=5)
    assert _time_ago(dt) == "5m ago"


def test_time_ago_accepts_naive_datetime():
    """Naive UTC timestamps keep working."""
    dt = datetime.utcnow() - timedelta(hours=2)
    assert _time_ago(dt) == "2h ago"


def test_time_ago_none_returns_recently():
    assert _time_ago(None) == "recently"


@pytest.mark.parametrize(
    "delta,expected",
    [
        (timedelta(seconds=10), "just now"),
        (timedelta(minutes=59), "59m ago"),
        (timedelta(hours=23), "23h ago"),
        (timedelta(days=3), "3d ago"),
    ],
)
def test_time_ago_buckets_aware(delta, expected):
    dt = datetime.now(timezone.utc) - delta
    assert _time_ago(dt) == expected
