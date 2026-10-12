"""``/api/health/report["pings"]``: did every ping get an answer, and how
fast (AREA 3, pings-media-spec §2.13, 2026-10-11).

The section is the runner's daily ping-answer metric: counts by status and
by who asked, answered within 2 minutes, the median latency, pings open too
long, self-replies (must stay 0) and a bucket per day. A failing section is
``{"error": …}``; it never sinks the report.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from services import health_report_service as hr
from services.pings import hooks, store


async def _ping(ref, **kw):
    from services.database import async_session

    fields = dict(channel="imessage", kind="mention", from_owner=True, third_party=False, sender_known=True,
                  asked_at=hooks.utcnow() - timedelta(minutes=3), chat_identifier="chat123", chat_name="Trivia crew",
                  is_group=True, text="@edward hi")
    fields.update(kw)
    async with async_session() as session:
        pid = await hooks.create_ping(session, source_ref=ref, **fields)
        await session.commit()
    return pid


@pytest.mark.integration
async def test_the_report_carries_the_pings_section(clean_db):
    pid = await _ping("imessage:1")
    await store.mark(pid, status="answered", latency_s=42.0, answered_at=hooks.utcnow())

    rep = await hr.build_report(days=7)

    pings = rep["pings"]
    assert pings["days"] == 7 and pings["total"] == 1
    assert pings["by_status"]["answered"] == 1
    assert pings["answered_within_2m"] == 1 and pings["median_latency_s"] == 42
    assert pings["self_replies"] == 0
    assert len(pings["by_day"]) == 7


@pytest.mark.integration
async def test_a_failing_pings_section_is_an_error_not_a_500(clean_db, monkeypatch):
    async def broken(days):
        raise RuntimeError("relation \"pings\" does not exist\nDETAIL: Failing row contains (someone's text)")

    monkeypatch.setattr(hooks, "report", broken)

    rep = await hr.build_report(days=1)

    assert "error" in rep["pings"]
    assert "someone's text" not in rep["pings"]["error"]
    assert "trust" in rep
