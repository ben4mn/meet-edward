"""R/G TDD red for P4 — stop the default sink.

690 of 1710 live rows sit at exactly ``context`` + ``timeless`` +
``importance 0.5``: the value every validator falls back to. Among them,
"Parker said he is at the gym right now" — filed as timeless, so under P2 it
would outlive most facts about people.

Two causes, both in the validation block of ``extract_and_store_memories``:

  * ``temporal_nature`` defaults to ``"timeless"`` when the extractor omits
    it or returns something unrecognized. That is the worst possible default:
    timeless is the longest-lived class, so every extractor slip produces a
    memory that never fades.
  * The coercion is silent. An extractor that stopped emitting the field
    would look identical to one working perfectly.

The fix is to make the default depend on what kind of memory it is, and to
make the miss visible:

  * ``context`` is situational by definition — a note about what someone is
    doing right now. Its safe default is ``temporary``.
  * ``fact`` and ``instruction`` describe things that persist. Their safe
    default stays ``timeless``. (A ``preference`` defaults to ``evolving``
    since 2026-09-27 — tastes change, and a one-off is ``temporary``; see
    ``test_a_preference_defaults_to_evolving`` below.)
  * Every fallback is counted so the extractor's health is observable.

Defaults should fail toward forgetting. A wrongly-forgotten context note
costs one re-ask; a wrongly-immortal one pollutes retrieval forever.
"""

from __future__ import annotations

import pytest


def test_default_temporal_nature_helper_exists():
    from services import memory_service

    assert hasattr(memory_service, "default_temporal_nature")


@pytest.mark.parametrize(
    "memory_type,expected",
    [
        ("context", "temporary"),
        ("fact", "timeless"),
        ("preference", "evolving"),
        ("instruction", "timeless"),
    ],
)
def test_default_depends_on_memory_type(memory_type, expected):
    from services.memory_service import default_temporal_nature

    assert default_temporal_nature(memory_type) == expected


def test_a_preference_defaults_to_evolving():
    """A preference with no stated nature was ``timeless`` (memory audit,
    preferences, 2026-09-27). ``remember`` has no ``temporal_nature``
    argument, so every preference Ellis stated through it was user_stated +
    timeless — the one pair the decay pass holds forever
    (``memory_decay_service.is_protected``). Tastes change; timeless is
    only for one held for years, and only when the extractor says so."""
    from services.memory_service import coerce_extracted_fields, default_temporal_nature

    assert default_temporal_nature("preference") == "evolving"
    assert default_temporal_nature("preference", origin="heard") == "evolving"
    assert default_temporal_nature("preference", origin="read") == "evolving"
    assert default_temporal_nature("preference", origin="thought") == "temporary", "a thought still fades"

    fields, misses = coerce_extracted_fields({"content": "Ellis likes the window seat", "memory_type": "preference",
                                              "source_type": "user_stated", "origin": "heard", "about": "owner"})
    assert fields["temporal_nature"] == "evolving"
    assert "temporal_nature" in misses
    fields, _ = coerce_extracted_fields({"content": "Ellis dislikes bananas", "memory_type": "preference",
                                         "temporal_nature": "timeless", "origin": "heard", "about": "owner"})
    assert fields["temporal_nature"] == "timeless", "the extractor's own word stands"


def test_context_never_defaults_to_timeless():
    """The single most damaging default in the system."""
    from services.memory_service import default_temporal_nature

    assert default_temporal_nature("context") != "timeless", (
        '"Parker said he is at the gym right now" is a context memory. Filed as '
        "timeless it outlives real facts about people."
    )


def test_unknown_memory_type_defaults_to_temporary():
    from services.memory_service import default_temporal_nature

    assert default_temporal_nature("wat") == "temporary", (
        "An unrecognized type means the extractor is confused. Fail toward "
        "forgetting."
    )
    assert default_temporal_nature(None) == "temporary"


# ---------------------------------------------------------------------------
# Coercion must be visible
# ---------------------------------------------------------------------------


def test_coerce_reports_what_it_changed():
    from services.memory_service import coerce_extracted_fields

    item = {"content": "Parker is at the gym", "memory_type": "context"}
    result, misses = coerce_extracted_fields(item)

    assert result["temporal_nature"] == "temporary"
    assert "temporal_nature" in misses, (
        "A silent coercion makes a broken extractor indistinguishable from a "
        "working one. The miss has to be reported."
    )


def test_valid_values_pass_through_with_no_misses():
    from services.memory_service import coerce_extracted_fields

    item = {
        "content": "Ellis's dog is named Cricket",
        "memory_type": "fact",
        "temporal_nature": "timeless",
        "source_type": "user_stated",
        "origin": "heard",
        "about": "owner",
    }
    result, misses = coerce_extracted_fields(item)

    assert result["memory_type"] == "fact"
    assert result["temporal_nature"] == "timeless"
    assert result["about_owner"] is True
    assert misses == []


def test_invalid_temporal_nature_is_coerced_and_reported():
    from services.memory_service import coerce_extracted_fields

    item = {
        "content": "Ellis went to Paris",
        "memory_type": "fact",
        "temporal_nature": "permanent",  # not a valid value
    }
    result, misses = coerce_extracted_fields(item)

    assert result["temporal_nature"] == "timeless"
    assert "temporal_nature" in misses


def test_invalid_memory_type_is_coerced_and_reported():
    from services.memory_service import coerce_extracted_fields

    item = {"content": "something", "memory_type": "trivia"}
    result, misses = coerce_extracted_fields(item)

    assert result["memory_type"] == "context"
    assert "memory_type" in misses
    assert result["temporal_nature"] == "temporary", (
        "Having fallen back to context, the temporal default must follow it."
    )


def test_an_explicit_valid_nature_survives_an_invalid_type():
    from services.memory_service import coerce_extracted_fields

    item = {
        "content": "something",
        "memory_type": "trivia",
        "temporal_nature": "evolving",
    }
    result, misses = coerce_extracted_fields(item)

    assert result["temporal_nature"] == "evolving", (
        "Only fall back for the field that was actually wrong."
    )
    assert "temporal_nature" not in misses
