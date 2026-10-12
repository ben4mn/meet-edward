"""Continuity between wakes (2026-09-25).

The mind's thread window is 24K characters; one pack is 12–20K, so a wake
could not hold even the previous one, and consecutive wakes only knew each
other through ``did`` memories. Every pack now carries a short "Your last
few wakes" section: the previous five rows of the mind's ledger — reason,
outcome, when, and his closing line (at most 200 characters).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tests.test_mind_service import NOW, _pack

pytestmark = pytest.mark.unit


def _row(minutes_ago, source, outcome, note):
    at = (NOW - timedelta(minutes=minutes_ago)).replace(tzinfo=None).isoformat() + "Z"
    return {"id": minutes_ago, "at": at, "source": source, "outcome": outcome, "note": note}


def test_the_pack_lists_the_last_few_wakes_newest_first():
    import services.mind_service as ms

    rows = [
        _row(30, "messages", "left_alone", "Parker's thread is banter; nothing for Ellis."),
        _row(90, "free", "acted", "Rewrote the state document and closed two stale intentions."),
        _row(200, "evening", "failed", "ValueError: Codex API error (HTTP 503): upstream connect error"),
        _row(400, "self", "skipped", "Codex quota exhausted"),
        _row(600, "morning", "acted", "x" * 500),
        _row(900, "manual", "left_alone", "the sixth is not shown"),
    ]
    text = ms.render_pack(_pack(recent_wakes=rows))
    assert "Your last few wakes (newest first):" in text
    section = text.split("Your last few wakes (newest first):", 1)[1]
    assert section.index("messages: left_alone") < section.index("free: acted") < section.index("evening: failed")
    assert "30m ago" in section and "Parker's thread is banter" in section
    assert "HTTP 503" in section and "self: skipped" in section
    assert "x" * 200 in section and "x" * 201 not in section, "a closing line is at most 200 characters"
    assert "the sixth is not shown" not in text


def test_no_earlier_wakes_says_so():
    import services.mind_service as ms

    text = ms.render_pack(_pack(recent_wakes=[]))
    assert "Your last few wakes (newest first):" in text
    assert "(none recorded)" in text.split("Your last few wakes (newest first):", 1)[1].split("\n\n")[0]


def test_the_section_sits_before_what_he_did():
    import services.mind_service as ms

    text = ms.render_pack(_pack(recent_wakes=[_row(5, "manual", "left_alone", "Quiet.")]))
    assert text.index("Your last few wakes") < text.index("What you did lately")


@pytest.mark.integration
async def test_collect_pack_reads_the_last_five_wakes_from_the_ledger(clean_db, monkeypatch):
    import services.mind_service as ms

    for i in range(7):
        await ms._record_now(source="messages", outcome="left_alone", note=f"wake {i}", tools=[])
    ms.reset_state()
    p = await ms.collect_pack("morning")
    assert [w["note"] for w in p.recent_wakes] == ["wake 6", "wake 5", "wake 4", "wake 3", "wake 2"]
    assert "wake 6" in ms.render_pack(p)
