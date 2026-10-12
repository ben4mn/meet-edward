"""The owner's verdict on a heartbeat decision.

The heartbeat view shows what Edward decided and asks "right call?". The
verdict is stored in a small table; this pins the vocabulary and the
validation. (It once also fed the legacy triage engine's false-dismissal
signals; their only readers left with that engine, so a verdict writes no
signal row now — ``test_public_heartbeat_slim`` covers that.)
"""

import pytest

from services.heartbeat_feedback_service import VERDICTS, normalize_verdict


def test_verdict_vocabulary():
    assert set(VERDICTS) == {"right_call", "should_have_told_me", "should_have_noted", "not_worth_noting"}


def test_normalize_accepts_canonical_and_spaced_forms():
    assert normalize_verdict("right_call") == "right_call"
    assert normalize_verdict("Should have told me") == "should_have_told_me"
    assert normalize_verdict("  not-worth-noting ") == "not_worth_noting"


def test_normalize_rejects_garbage():
    with pytest.raises(ValueError):
        normalize_verdict("meh")
    with pytest.raises(ValueError):
        normalize_verdict("")
