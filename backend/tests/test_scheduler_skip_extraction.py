"""A scheduled event can opt its turn out of memory extraction.

The daily memory-health report runs as a scheduled event so Ellis lands in a
readable conversation when he taps the push. Its trigger is a wall of
figures about the memory store itself; extracting memories from that would
add a row like "Edward's store has 1,522 live memories" every morning.
``event_metadata`` ``{"skip_extraction": true}`` turns extraction off for
that turn only.
"""

from __future__ import annotations

from pathlib import Path


class _E:
    def __init__(self, meta):
        self.event_metadata = meta


def test_extraction_kwargs_for_event():
    from services.scheduler_service import extraction_kwargs_for_event

    assert extraction_kwargs_for_event(_E('{"type": "memory_health_report", "skip_extraction": true}')) == {"skip_extraction": True}
    assert extraction_kwargs_for_event(_E('{"type": "wakeup_tick"}')) == {}
    assert extraction_kwargs_for_event(_E(None)) == {}
    assert extraction_kwargs_for_event(_E("not json")) == {}
    assert extraction_kwargs_for_event(_E('{"skip_extraction": "yes"}')) == {}, "only a real true counts"


def test_execute_event_passes_the_kwargs_to_both_attempts():
    src = (Path(__file__).resolve().parents[1] / "services/scheduler_service.py").read_text()
    body = src[src.index("async def _execute_event("):]
    assert body.count("**extraction_kwargs_for_event(event)") >= 2
