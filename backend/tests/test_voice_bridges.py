"""Spoken bridges: what Edward says while the model is still thinking.

Ellis asked for these to be fun and randomized (2026-09-05). They must never
repeat back-to-back, and they only fire when the first token is late.
"""

import random

from services.voice.bridges import BRIDGES, bridge_due, pick_bridge


def test_there_are_plenty_of_bridges_and_they_are_short():
    assert len(BRIDGES) >= 12
    for line in BRIDGES:
        assert 4 <= len(line) <= 60, line
        assert line.strip() == line


def test_pick_never_repeats_last_line():
    rng = random.Random(1)
    last = BRIDGES[0]
    for _ in range(200):
        chosen = pick_bridge(rng, last=last)
        assert chosen != last
        last = chosen


def test_pick_covers_the_set_over_time():
    rng = random.Random(7)
    seen = {pick_bridge(rng) for _ in range(500)}
    assert len(seen) >= len(BRIDGES) * 0.8


def test_bridge_due_only_when_first_token_is_late_and_unspoken():
    assert bridge_due(elapsed_s=0.4, first_token_seen=False, already_spoken=False) is False
    assert bridge_due(elapsed_s=1.6, first_token_seen=False, already_spoken=False) is True
    assert bridge_due(elapsed_s=1.6, first_token_seen=True, already_spoken=False) is False
    assert bridge_due(elapsed_s=5.0, first_token_seen=False, already_spoken=True) is False


def test_bridge_threshold_is_configurable():
    assert bridge_due(elapsed_s=0.9, first_token_seen=False, already_spoken=False, threshold_s=0.8) is True
