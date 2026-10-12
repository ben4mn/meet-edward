"""Hub health tile — reading the watchdog honestly.

`scripts/health_check.sh` writes ~/.edward-health/status.json every 5
minutes. The trap this module exists to avoid: a *stale* green file reads
as "healthy" forever if you only look at the `status` field. That is
exactly the shape of the 2026-08-12 incident, where a dead dependency sat
unnoticed for 9 days while everything downstream looked fine — so an
un-refreshed status file must degrade to "stale", never stay green.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import hub_health_service as svc


NOW = datetime(2026, 8, 22, 19, 0, tzinfo=timezone.utc)


def _stamp(minutes_ago: int) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def test_fresh_healthy_status_reads_healthy():
    raw = {"status": "healthy", "checked_at": _stamp(2), "repairs": [], "problems": []}
    out = svc.assess_watchdog(raw, now=NOW)
    assert out["status"] == "healthy"
    assert out["stale"] is False
    assert out["age_minutes"] == 2


def test_a_green_file_nobody_has_refreshed_is_not_green():
    """The whole point: the watchdog itself can die."""
    raw = {"status": "healthy", "checked_at": _stamp(3 * 24 * 60), "repairs": [], "problems": []}
    out = svc.assess_watchdog(raw, now=NOW)
    assert out["status"] == "stale"
    assert out["stale"] is True


def test_staleness_boundary():
    fresh = {"status": "healthy", "checked_at": _stamp(svc.WATCHDOG_STALE_AFTER_MINUTES - 1)}
    stale = {"status": "healthy", "checked_at": _stamp(svc.WATCHDOG_STALE_AFTER_MINUTES + 1)}
    assert svc.assess_watchdog(fresh, now=NOW)["status"] == "healthy"
    assert svc.assess_watchdog(stale, now=NOW)["status"] == "stale"


def test_reported_problems_surface_as_problem():
    raw = {
        "status": "needs_attention",
        "checked_at": _stamp(1),
        "repairs": [],
        "problems": ["postgres: stale postmaster.pid"],
    }
    out = svc.assess_watchdog(raw, now=NOW)
    assert out["status"] == "problem"
    assert out["problems"] == ["postgres: stale postmaster.pid"]


def test_self_repair_is_reported_but_is_not_a_failure():
    raw = {"status": "repaired", "checked_at": _stamp(1), "repairs": ["backend"], "problems": []}
    out = svc.assess_watchdog(raw, now=NOW)
    assert out["status"] == "repaired"
    assert out["repairs"] == ["backend"]


def test_missing_status_file_is_unknown_not_healthy():
    out = svc.assess_watchdog(None, now=NOW)
    assert out["status"] == "unknown"


def test_unparseable_timestamp_is_unknown_not_healthy():
    raw = {"status": "healthy", "checked_at": "not-a-date"}
    out = svc.assess_watchdog(raw, now=NOW)
    assert out["status"] == "unknown"


# ---------------------------------------------------------------------------
# Rolling several component states into one headline
# ---------------------------------------------------------------------------

def test_rollup_is_the_worst_component():
    assert svc.rollup(["healthy", "healthy"]) == "healthy"
    assert svc.rollup(["healthy", "repaired"]) == "repaired"
    assert svc.rollup(["healthy", "stale"]) == "stale"
    assert svc.rollup(["repaired", "problem", "healthy"]) == "problem"


def test_rollup_of_nothing_is_unknown():
    assert svc.rollup([]) == "unknown"


def test_not_knowing_outranks_a_clean_repair():
    """"I can't tell" is a warning; "I fixed it" is not."""
    assert svc.rollup(["repaired", "unknown"]) == "unknown"


# ---------------------------------------------------------------------------
# Disk: a byte floor alone is not enough
# ---------------------------------------------------------------------------

def test_plenty_of_room_is_healthy():
    assert svc.disk_status(free_gb=180, pct=22.0) == "healthy"


def test_low_free_bytes_is_a_problem():
    assert svc.disk_status(free_gb=4, pct=60.0) == "problem"


def test_nearly_full_is_a_problem_even_with_gigabytes_left():
    """16 GB free reads fine as a byte count, but at 93% used on a volume
    taking daily pg_dumps it is not fine. Found live on 2026-08-22."""
    assert svc.disk_status(free_gb=16, pct=92.8) == "problem"


def test_boundaries():
    assert svc.disk_status(free_gb=100, pct=svc.DISK_PCT_PROBLEM - 0.1) == "healthy"
    assert svc.disk_status(free_gb=100, pct=svc.DISK_PCT_PROBLEM + 0.1) == "problem"
    assert svc.disk_status(free_gb=svc.DISK_FREE_WARN_GB + 1, pct=10.0) == "healthy"
    assert svc.disk_status(free_gb=svc.DISK_FREE_WARN_GB - 1, pct=10.0) == "problem"


# ---------------------------------------------------------------------------
# Backups on a removable drive
# ---------------------------------------------------------------------------

def test_recent_backup_is_healthy():
    assert svc.backup_status(dir_present=True, age_hours=16.0) == "healthy"


def test_stale_backup_is_a_problem():
    assert svc.backup_status(dir_present=True, age_hours=50.0) == "problem"


def test_missing_backup_directory_is_a_problem_not_unknown():
    """Backups live on a removable backup drive. If it is unplugged the
    directory is simply gone — and 'no backups at all' must read as a
    problem, never as a shrug."""
    assert svc.backup_status(dir_present=False, age_hours=None) == "problem"


def test_directory_present_but_empty_is_a_problem():
    assert svc.backup_status(dir_present=True, age_hours=None) == "problem"


def test_backup_boundary():
    assert svc.backup_status(True, svc.BACKUP_STALE_AFTER_HOURS - 1) == "healthy"
    assert svc.backup_status(True, svc.BACKUP_STALE_AFTER_HOURS + 1) == "problem"
