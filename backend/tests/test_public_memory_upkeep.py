"""Memory upkeep reads the household from the environment, never a literal.

The public build has no owner, pets or family baked in: the decay pass holds
rows that name the household's pets and people (``EDWARD_SENSITIVE_NAMES``
plus ``EDWARD_HOUSEHOLD`` other than the owner), the reminder-echo rule and
the forget refusal know the owner by ``EDWARD_OWNER_NAME``, and the
instruction horizon covers every instruction unless
``EDWARD_INSTRUCTION_HORIZON_SINCE`` exempts a migrated install's backlog.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest


@pytest.mark.unit
def test_generic_sensitive_words_hold_without_any_names(monkeypatch):
    from services.memory_decay_service import is_sensitive

    monkeypatch.delenv("EDWARD_SENSITIVE_NAMES", raising=False)
    monkeypatch.delenv("EDWARD_HOUSEHOLD", raising=False)
    assert is_sensitive("The dog was given carprofen after the vet visit") is True
    assert is_sensitive("Rowan said her mom is visiting on Friday") is True
    assert is_sensitive("Biscuit chased a squirrel") is False, "no pet name is built in"


@pytest.mark.unit
def test_sensitive_names_come_from_the_environment(monkeypatch):
    from services.memory_decay_service import is_sensitive, sensitive_names

    monkeypatch.setenv("EDWARD_SENSITIVE_NAMES", "Biscuit, Mr. Whiskers")
    monkeypatch.delenv("EDWARD_HOUSEHOLD", raising=False)
    assert sensitive_names() == ("Biscuit", "Mr. Whiskers")
    assert is_sensitive("Biscuit chased a squirrel") is True
    assert is_sensitive("biscuit's walk was short") is True
    assert is_sensitive("Mr. Whiskers slept all day") is True
    assert is_sensitive("Ellis baked biscuits") is False, "whole names only"


@pytest.mark.unit
def test_the_household_is_held_but_never_the_owner(monkeypatch):
    from services.memory_decay_service import is_sensitive, sensitive_names

    monkeypatch.delenv("EDWARD_SENSITIVE_NAMES", raising=False)
    monkeypatch.setenv("EDWARD_HOUSEHOLD", "Ellis,Rowan")
    assert sensitive_names() == ("Rowan",)
    assert is_sensitive("Rowan went for a run") is True
    assert is_sensitive("Ellis went for a run") is False, "nearly every row names the owner"


@pytest.mark.unit
def test_an_unset_household_adds_no_names(monkeypatch):
    from services.memory_decay_service import is_sensitive, sensitive_names

    monkeypatch.delenv("EDWARD_SENSITIVE_NAMES", raising=False)
    monkeypatch.delenv("EDWARD_HOUSEHOLD", raising=False)
    assert sensitive_names() == ()
    assert is_sensitive("Remind me to buy milk") is False, "the household fallback ('Me') is never a name"


@pytest.mark.unit
def test_a_reminder_echo_names_the_configured_owner(monkeypatch):
    from services.memory_decay_service import is_reminder_echo

    monkeypatch.setenv("EDWARD_OWNER_NAME", "Ellis")
    assert is_reminder_echo("Edward should remind Ellis about the dentist") is True
    assert is_reminder_echo("Edward reminded the owner about the dentist") is True
    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    assert is_reminder_echo("Edward should remind Sam about the dentist") is True
    assert is_reminder_echo("Edward should remind Ellis about the dentist") is False


@pytest.mark.unit
@pytest.mark.parametrize("raw,expected", [
    (None, datetime(1970, 1, 1, tzinfo=timezone.utc)),
    ("", datetime(1970, 1, 1, tzinfo=timezone.utc)),
    ("2026-09-28", datetime(2026, 9, 28, tzinfo=timezone.utc)),
    ("2026-09-28T06:00:00Z", datetime(2026, 9, 28, 6, tzinfo=timezone.utc)),
    ("not a date", datetime(1970, 1, 1, tzinfo=timezone.utc)),
])
def test_the_instruction_horizon_start_is_configurable(monkeypatch, raw, expected):
    from services.memory_decay_service import _instruction_horizon_since

    if raw is None:
        monkeypatch.delenv("EDWARD_INSTRUCTION_HORIZON_SINCE", raising=False)
    else:
        monkeypatch.setenv("EDWARD_INSTRUCTION_HORIZON_SINCE", raw)
    assert _instruction_horizon_since() == expected


@pytest.mark.unit
def test_forget_refuses_the_configured_owners_names(monkeypatch):
    from services import forget_service as fs

    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    monkeypatch.setenv("EDWARD_OWNER_FULL_NAME", "Sam Rivera")
    for subject in ("Sam", "Sam Rivera", "everything about Sam", "owner"):
        with pytest.raises(ValueError):
            fs.subject_terms(subject)
    assert fs.subject_terms("Sam's sister") == ["sister"]
    assert fs.subject_terms("Ellis") == ["ellis"], "another name is just a subject"


@pytest.mark.unit
def test_the_forget_preview_names_the_owner_neutrally(monkeypatch):
    from services import forget_service as fs

    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    plan = fs.ForgetPlan(id="p1", subject="Alex", terms=["alex"], created_at=0.0,
                         memories=[{"id": "m1", "content": "Alex loves bouldering", "why": "names it"}])
    text = fs.format_plan_preview(plan)
    assert "Show Sam this list and ask them to confirm." in text
    assert " him " not in text and " his " not in text and " he " not in text
