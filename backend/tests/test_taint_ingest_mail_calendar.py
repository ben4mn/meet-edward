"""Mail, calendar and Reminders reads are taint signals, never blocks (2026-10-11).

Ellis: "I don't want to block and restrict much from Edward." Reading an
email, a calendar event or a reminder someone else wrote brings untrusted
text into the turn, so a successful read marks the turn tainted
(``taint_service.ingest_taint`` → ``read:<tool>``) — a ledger signal for
the health report. Nothing is refused for it: the taint gate stays off by
default, and no read gains a gate or a ``may_read_private`` field. A
failed read brings nothing in and marks nothing.
"""

from __future__ import annotations

import pytest

from services import taint_service

pytestmark = pytest.mark.unit


@pytest.mark.parametrize("name,action", [
    ("messages", "mail_search"),
    ("messages", "mail_read"),
    ("messages", "recent"),
    ("messages", "thread"),
    ("messages", "participants"),
    ("schedule", "calendar"),
    ("schedule", "reminders"),
])
def test_reading_what_others_wrote_is_marked(name, action):
    assert taint_service.ingest_taint(name, {"action": action}, "From: Parker <parker@example.com>\n---\nhi") == \
        f"read:{name}"


@pytest.mark.parametrize("name,action", [
    ("messages", "who"),
    ("messages", "stand_down"),
    ("schedule", "add"),
    ("schedule", "list"),
    ("schedule", "cancel"),
])
def test_other_actions_are_not_reads(name, action):
    assert taint_service.ingest_taint(name, {"action": action}, "ok") is None


def test_a_failed_read_marks_nothing():
    assert taint_service.ingest_taint("messages", {"action": "mail_search"},
                                      "Tool error: Gmail isn't connected — Ellis connects it in Settings → Gmail.") is None


def test_the_ingest_table():
    assert taint_service.INGEST_ACTIONS == {
        "messages": frozenset({"recent", "thread", "participants", "mail_search", "mail_read"}),
        "schedule": frozenset({"calendar", "reminders"}),
    }


def test_reads_are_never_gated():
    """A read is not a send: no gate holds it, whatever the mode."""
    for name, action in (("messages", "mail_search"), ("messages", "mail_read"), ("schedule", "calendar"),
                         ("schedule", "reminders")):
        assert not taint_service.is_gated_send(name, {"action": action})


def test_no_new_read_gate_on_the_policy():
    from services.toolset.policy import TurnPolicy

    assert "may_read_private" not in TurnPolicy.__dataclass_fields__
