"""R/G TDD red for P3 — the tier ladder must read use, not just repetition.

``_compute_tier`` reads one number: ``reinforcement_count``. That only
increments when the same fact is extracted again or when consolidation merges
duplicates — so the ladder measures how often something got SAID, and is
entirely decoupled from whether it ever helped.

That is backwards for the thing tiers are used for. Tier multiplies retrieval
score (1.0 / 1.1 / 1.2) and, after P2, multiplies retention. Both are
questions about usefulness. A fact Ellis repeats out of habit climbs; a fact
Edward reaches for on turn after turn does not.

Human consolidation works the other way round: repetition matters, but
retrieval is what actually moves something from episode to knowledge.

Contract — a memory promotes on EITHER path, and needs both to reach the top:
  * observation -> belief: restated (>=2) OR genuinely used (>=BELIEF_USES)
  * belief -> knowledge: restated (>=5) AND used (>=KNOWLEDGE_USES)

Knowledge is the tier that is never auto-retired and gets the largest score
boost, so it must require corroboration from both signals rather than either
one alone.

The thresholds are deliberately conservative: ``used_count`` has no history
yet, so they can only be calibrated once real traffic has accumulated.
"""

from __future__ import annotations

import pytest


def test_thresholds_are_named_constants():
    from services import memory_service as m

    for name in ("BELIEF_USES", "KNOWLEDGE_USES"):
        assert hasattr(m, name), (
            f"{name} must be a named module constant — it is the one number "
            "in this design that cannot be calibrated until used_count has "
            "accumulated real traffic, so it has to be obvious to find."
        )


def test_compute_tier_accepts_used_count():
    import inspect

    from services.memory_service import _compute_tier

    sig = inspect.signature(_compute_tier)
    assert "used_count" in sig.parameters, (
        "_compute_tier must consider how often a memory was actually used."
    )
    assert sig.parameters["used_count"].default == 0, (
        "used_count must default to 0 so existing single-argument callers "
        "keep working unchanged."
    )


# --- the repetition path still works, unchanged ----------------------------


def test_repetition_alone_still_reaches_belief():
    from services.memory_service import _compute_tier

    assert _compute_tier(2) == "belief"
    assert _compute_tier(4) == "belief"


def test_never_reinforced_never_used_stays_an_observation():
    from services.memory_service import _compute_tier

    assert _compute_tier(0) == "observation"
    assert _compute_tier(1) == "observation"


# --- the new use path ------------------------------------------------------


def test_repeated_use_alone_reaches_belief():
    from services.memory_service import BELIEF_USES, _compute_tier

    assert _compute_tier(0, used_count=BELIEF_USES) == "belief", (
        "A memory Edward keeps reaching for has earned belief even if Ellis "
        "only ever said it once."
    )


def test_use_below_the_threshold_does_not_promote():
    from services.memory_service import BELIEF_USES, _compute_tier

    assert _compute_tier(0, used_count=BELIEF_USES - 1) == "observation"


# --- knowledge requires both -----------------------------------------------


def test_knowledge_requires_both_signals():
    from services.memory_service import KNOWLEDGE_USES, _compute_tier

    assert _compute_tier(5, used_count=KNOWLEDGE_USES) == "knowledge"


def test_repetition_alone_no_longer_reaches_knowledge():
    from services.memory_service import _compute_tier

    assert _compute_tier(50, used_count=0) == "belief", (
        "Knowledge is never auto-retired and gets the largest retrieval "
        "boost. Being restated a lot, with nothing ever reaching for it, is "
        "not enough evidence for that."
    )


def test_use_alone_no_longer_reaches_knowledge():
    from services.memory_service import KNOWLEDGE_USES, _compute_tier

    assert _compute_tier(0, used_count=KNOWLEDGE_USES * 10) == "belief"


def test_tier_is_monotonic_in_both_signals():
    """More evidence never demotes."""
    from services.memory_service import _compute_tier

    rank = {"observation": 0, "belief": 1, "knowledge": 2}

    for uses in (0, 3, 10, 40):
        tiers = [rank[_compute_tier(r, used_count=uses)] for r in range(0, 12)]
        assert tiers == sorted(tiers), f"non-monotonic in reinforcement at uses={uses}"

    for reinf in (0, 2, 5, 12):
        tiers = [rank[_compute_tier(reinf, used_count=u)] for u in range(0, 40)]
        assert tiers == sorted(tiers), f"non-monotonic in use at reinforcement={reinf}"


def test_none_inputs_are_treated_as_zero():
    from services.memory_service import _compute_tier

    assert _compute_tier(None) == "observation"
    assert _compute_tier(None, used_count=None) == "observation"


# --- a dated episode stops at belief (memory audit §2.1/§7.4, 2026-09-27) ---
#
# Knowledge is never auto-retired and carries the largest retrieval boost.
# An episode — "On July 10–12, 2026, Rowan sent Ellis…", "For Trivia Night
# reminders on July 24, July 31 and August 28…" — was true of its day, not
# forever; being reached for often (thousands of retrievals) is not a reason to
# keep it for good. Use may lift it to belief; nothing lifts it past.

from datetime import datetime, timezone  # noqa: E402

AUDIT_NOW = datetime(2026, 9, 27, 15, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("content", [
    "On July 10-12, 2026, Rowan sent Ellis photos from the lake trip",
    "On July 9, 2026, Ellis was waiting for Parker to reply",
    "For Trivia Night reminders on July 24, July 31, and August 28, message the book club group",
    "On Monday, July 6 Ellis scheduled iMessages for the morning",
    "Tool test from 2026-08-22: return raw tool output verbatim",
    "Ellis told Nate on Aug. 22 that coin credits don't incur charges",
    # A birthday in an episode does not make it a fact (review, 2026-09-27;
    # two such rows in the live store).
    "On July 29, 2026, Parker Lowe was celebrating his birthday",
    "Ellis sent Book Club a birthday Cameo on Aug. 30",
])
def test_a_past_date_marks_an_episode(content):
    from services.memory_service import names_past_date

    assert names_past_date(content, now=AUDIT_NOW) is True


@pytest.mark.parametrize("content", [
    "Ellis's birthday is March 14",
    "Parker's wedding is on October 3",
    "Ellis works at Acme Group",
    "Biscuit takes her pill at 06:00 and 18:00",
    "The dentist appointment is on 2026-10-02",
    # A date bound to what recurs or never changes is a fact, whatever the
    # calendar says today (review of batch A1, 2026-09-27: each of these
    # read as an episode, and the yearless ones flipped with the season).
    "Rowan's birthday is on April 9",
    "Ellis and Rowan's anniversary is on May 20",
    "Biscuit was adopted on June 5, 2021",
    "Ellis's niece was born March 3, 2020",
    "Maple was born on 2019-04-02",
])
def test_no_past_date_is_not_an_episode(content):
    from services.memory_service import names_past_date

    assert names_past_date(content, now=AUDIT_NOW) is False


def test_a_dated_episode_never_reaches_knowledge():
    from services.memory_service import KNOWLEDGE_USES, _compute_tier

    dated = "On July 10-12, 2026, Rowan sent Ellis photos from the lake trip"
    assert _compute_tier(50, used_count=KNOWLEDGE_USES * 10, content=dated, now=AUDIT_NOW) == "belief"
    assert _compute_tier(0, used_count=KNOWLEDGE_USES, content=dated, now=AUDIT_NOW) == "belief"


def test_use_can_still_lift_a_dated_episode_to_belief():
    from services.memory_service import BELIEF_USES, _compute_tier

    dated = "On July 9, 2026, Ellis was waiting for Parker to reply"
    assert _compute_tier(0, used_count=BELIEF_USES, content=dated, now=AUDIT_NOW) == "belief"


def test_an_undated_memory_still_reaches_knowledge():
    from services.memory_service import KNOWLEDGE_USES, _compute_tier

    assert _compute_tier(5, used_count=KNOWLEDGE_USES, content="Ellis works at Acme Group",
                         now=AUDIT_NOW) == "knowledge"
