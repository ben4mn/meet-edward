"""R/G TDD for Task #9 — wire observation emitters that are silently dead.

`self_observations` has 2 rows in 73 days because no one is calling
`record_observation`. The synthesis judge gates on `occurrences >= 3`,
so even the two existing rows never advance.

This module ships:

* ``is_correction_signal(text)`` — heuristic pure function detecting
  user corrections in chat
* ``correction_signature(text)`` — derives a stable signature so
  repeated corrections of the same kind aggregate
* ``emit_correction_observation(...)`` — convenience wrapper that
  records via ``observe.record_observation``

Once these helpers are in place, the chat node calls them on each user
turn so the learn-loop has a steady supply of signal.
"""

from __future__ import annotations

import pytest


# ---------------------------------------------------------------------------
# is_correction_signal
# ---------------------------------------------------------------------------


def test_correction_signal_no_thats_wrong():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("no, that's wrong, X is actually Y") is True


def test_correction_signal_past_tense_was_wrong():
    """Real-speech variant — past tense is common when correcting."""
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("no, that was wrong") is True


def test_correction_signal_was_the_wrong_one():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("No that was the wrong one") is True


def test_correction_signal_thats_not_right():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("that's not right") is True
    assert is_correction_signal("that's incorrect") is True


def test_correction_signal_actually():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("Actually it's $200 not $20.") is True


def test_correction_signal_stop_doing():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("stop doing that, please") is True


def test_correction_signal_dont_do_that():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("don't do that anymore") is True


def test_correction_signal_remember_that():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("remember that Rowan is vegan") is True


def test_correction_signal_forget():
    """Forget is the symmetric pair to remember; both are memory-update
    signals worth recording so the synthesis judge can compound them."""
    from services.observation_emitters import is_correction_signal, correction_signature

    assert is_correction_signal("forget that thing about Rowan") is True
    assert is_correction_signal("forget what I said about X") is True
    # Signature distinguishes from remember
    sig_forget = correction_signature("forget that thing about Rowan")
    sig_remember = correction_signature("remember that Rowan is vegan")
    assert sig_forget != sig_remember
    assert "forget" in sig_forget


def test_correction_signal_normal_message_returns_false():
    from services.observation_emitters import is_correction_signal

    for msg in (
        "how are you?",
        "send the link to owner",
        "thanks!",
        "tell me about the tides",
    ):
        assert is_correction_signal(msg) is False, f"false positive on {msg!r}"


def test_correction_signal_empty_or_none():
    from services.observation_emitters import is_correction_signal

    assert is_correction_signal("") is False
    assert is_correction_signal(None) is False  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# correction_signature — stable across reworded variants
# ---------------------------------------------------------------------------


def test_signature_stable_across_no_variants():
    from services.observation_emitters import correction_signature

    a = correction_signature("no, that's wrong")
    b = correction_signature("No that's wrong, X is Y")
    c = correction_signature("No! That's wrong.")
    assert a == b == c, f"expected stable signature; got {a!r} {b!r} {c!r}"


def test_signature_differs_for_different_kinds():
    from services.observation_emitters import correction_signature

    a = correction_signature("no, that's wrong")
    b = correction_signature("stop doing that")
    c = correction_signature("remember that vegans don't eat meat")
    assert len({a, b, c}) == 3, (
        "different correction kinds must produce different signatures; "
        f"got {a!r}, {b!r}, {c!r}"
    )


# ---------------------------------------------------------------------------
# emit_correction_observation — integration
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_emit_correction_records_observation(clean_db):
    from services import observation_emitters
    from services.database import async_session, SelfObservationModel
    from sqlalchemy import select

    await observation_emitters.emit_correction_observation(
        "no, that's wrong — Rowan is vegan, not vegetarian"
    )

    async with async_session() as session:
        rows = (await session.execute(
            select(SelfObservationModel).where(
                SelfObservationModel.kind == "user_correction"
            )
        )).scalars().all()
    assert len(rows) == 1
    assert rows[0].occurrences == 1
    assert rows[0].source == "chat"


@pytest.mark.integration
async def test_emit_correction_aggregates_via_signature(clean_db):
    """Two corrections of the same kind → one row, occurrences=2."""
    from services import observation_emitters
    from services.database import async_session, SelfObservationModel
    from sqlalchemy import select

    await observation_emitters.emit_correction_observation("no, that's wrong about X")
    await observation_emitters.emit_correction_observation("No that's wrong about Y")

    async with async_session() as session:
        rows = (await session.execute(
            select(SelfObservationModel).where(
                SelfObservationModel.kind == "user_correction"
            )
        )).scalars().all()
    assert len(rows) == 1, f"signature should aggregate; got {len(rows)} rows"
    assert rows[0].occurrences == 2
