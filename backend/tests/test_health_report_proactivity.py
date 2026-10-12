"""The health report after the proactivity rebuild (2026-10-11).

* The surplus queue's section is gone with the queue.
* ``proactivity`` is ``noticing_service.proactivity_report(days)`` — the
  trust line and the verdict a health check reads first. Until that module
  is merged (or when it fails) the section says so; it never sinks the
  report.
* The daily memory report no longer pushes: it writes a ``memory_report``
  ledger row, and the report shows the newest one.
"""

from __future__ import annotations

import json
import sys
import types
from datetime import datetime

import pytest

from services import health_report_service as hr
from tests.area1_fakes import fake_module


def _bare(**kw):
    return hr.compose_report(days=7, turns=[], tool_rows=[], hits=[], observations=[], signals=[], storage={}, **kw)


@pytest.mark.unit
def test_the_surplus_section_is_gone():
    assert "surplus" not in _bare(events=[{"kind": "surplus", "source": "research", "outcome": "done",
                                           "created_at": datetime(2026, 10, 9, 3)}])
    assert not hasattr(hr, "summarize_surplus")


@pytest.mark.unit
def test_the_newest_memory_report_row_shows():
    events = [
        {"kind": "memory_report", "source": "script", "outcome": "ok", "reason": None,
         "detail_json": json.dumps({"live": 1370, "flagged": 2}), "created_at": datetime(2026, 10, 10, 14, 5)},
        {"kind": "memory_report", "source": "script", "outcome": "ok", "reason": None,
         "detail_json": json.dumps({"live": 1372, "flagged": 1}), "created_at": datetime(2026, 10, 11, 14, 5)},
        {"kind": "mind", "source": "nightly", "outcome": "left_alone", "created_at": datetime(2026, 10, 11, 8)},
    ]
    rep = _bare(events=events)
    assert rep["memory_report"]["at"] == "2026-10-11T14:05:00Z"
    assert rep["memory_report"]["outcome"] == "ok"
    assert rep["memory_report"]["summary"] == {"live": 1372, "flagged": 1}
    assert rep["memory_report"]["reports"] == 2


@pytest.mark.unit
def test_no_memory_report_row_says_none():
    assert _bare(events=[])["memory_report"] is None


@pytest.mark.unit
async def test_proactivity_comes_from_the_noticing_service(monkeypatch):
    calls = []

    async def proactivity_report(days=7):
        calls.append(days)
        return {"mode": "off", "verdict": "off", "trust": "…"}

    fake = types.ModuleType("services.noticing_service")
    fake.proactivity_report = proactivity_report
    fake_module(monkeypatch, "services.noticing_service", fake)
    assert await hr.proactivity_section(7) == {"mode": "off", "verdict": "off", "trust": "…"}
    assert calls == [7]


@pytest.mark.unit
async def test_proactivity_without_the_noticing_service_says_so(monkeypatch):
    fake_module(monkeypatch, "services.noticing_service", None)  # import raises ImportError
    monkeypatch.setenv("EDWARD_PROACTIVITY", "shadow")
    out = await hr.proactivity_section(7)
    assert out["verdict"] == "unknown" and out["mode"] == "shadow"
    assert "not merged" in out["error"]


@pytest.mark.unit
async def test_a_failing_proactivity_report_never_sinks_the_report(monkeypatch):
    async def proactivity_report(days=7):
        raise RuntimeError("database is down")

    fake = types.ModuleType("services.noticing_service")
    fake.proactivity_report = proactivity_report
    fake_module(monkeypatch, "services.noticing_service", fake)
    out = await hr.proactivity_section(7)
    assert out["verdict"] == "unknown" and "database is down" in out["error"]


@pytest.mark.integration
async def test_build_report_carries_proactivity(clean_db, monkeypatch):
    async def proactivity_report(days=7):
        return {"mode": "off", "verdict": "off"}

    fake = types.ModuleType("services.noticing_service")
    fake.proactivity_report = proactivity_report
    fake_module(monkeypatch, "services.noticing_service", fake)
    rep = await hr.build_report(days=1)
    assert rep["proactivity"] == {"mode": "off", "verdict": "off"}
    assert "surplus" not in rep
    assert "memory_report" in rep
