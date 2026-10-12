"""Daily memory-health report, recorded for the health report.

After the 2026-09-05 remediation the plan set 30-day targets. This script
measures them from the DB and the backend log. Since 2026-10-11 it no
longer hands them to Edward to push Ellis a link: ``--push`` writes one
``memory_report`` ledger row (``test_memory_report_no_push.py``). The
verdict logic is pure and tested; collection is a thin wrapper.
"""

from __future__ import annotations

import pytest


def _metrics(**over):
    base = dict(
        memories_last_24h=9,
        memories_by_source={"heartbeat": 4, "chat": 5},
        live=1520,
        unused_live=918,
        entity_ok=17,
        entity_timeouts=2,
        budget_exhausted=0,
        cycles=22,
        open_flags=1,
        checkpoint_mb=374,
        disk_free_gb=25,
    )
    base.update(over)
    return base


def test_all_targets_met_is_green():
    from scripts.memory_health_report import assess

    verdict = assess(_metrics())
    assert verdict["status"] == "on track"
    assert verdict["misses"] == []


def test_each_target_is_checked():
    from scripts.memory_health_report import assess

    assert "memories/day" in assess(_metrics(memories_last_24h=15))["misses"][0]
    assert "entity" in assess(_metrics(entity_ok=3, entity_timeouts=7))["misses"][0]
    assert "budget" in assess(_metrics(budget_exhausted=1))["misses"][0]
    assert "checkpoint" in assess(_metrics(checkpoint_mb=600))["misses"][0]
    assert "disk" in assess(_metrics(disk_free_gb=9))["misses"][0]


def test_too_few_entity_calls_is_not_a_miss():
    from scripts.memory_health_report import assess

    assert assess(_metrics(entity_ok=1, entity_timeouts=2))["misses"] == []


def test_open_flags_are_reported_not_failed():
    from scripts.memory_health_report import assess

    verdict = assess(_metrics(open_flags=3))
    assert verdict["status"] == "on track"
    assert any("3 contradiction" in line for line in verdict["lines"])


def test_push_body_is_short_and_leads_with_the_verdict():
    from scripts.memory_health_report import assess, format_push

    title, body = format_push(assess(_metrics(memories_last_24h=20, open_flags=1)))
    assert title.startswith("Memory: needs a look")
    assert body.splitlines()[0].startswith("memories/day")
    assert len(body) <= 400
    assert "1 contradiction" in body

    title, body = format_push(assess(_metrics()))
    assert title.startswith("Memory: on track")
