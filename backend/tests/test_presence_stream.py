"""GET /api/presence/stream — the menu-bar companion's live feed (Session F).

Server-sent events: a `state` frame (AttentionState summary) on connect and
every heartbeat. The `interrupt` frames went with the interrupt ladder
(2026-10-11). Pure framing is tested here; the endpoint is a thin loop.
"""

import json
from datetime import datetime, timezone

import pytest

from routers.presence import format_sse, stream_frames_once

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)


def test_format_sse_produces_event_and_data_lines():
    out = format_sse("state", {"a": 1})
    assert out == 'event: state\ndata: {"a": 1}\n\n'


def test_stream_frames_once_emits_a_state_frame_only():
    frames = stream_frames_once(device_id="bar-1", attention={"owner_present": True}, seen=set())
    assert [f.split("\n")[0] for f in frames] == ["event: state"]
    assert json.loads(frames[0].split("data: ")[1]) == {"owner_present": True}


def test_stream_frames_once_does_not_repeat_seen_interrupts():
    seen = {"d1"}
    frames = stream_frames_once(device_id="bar-1", attention={"owner_present": False}, seen=seen)
    assert [f.split("\n")[0] for f in frames] == ["event: state"]
