"""The work rail's chip for a toolset call is what the frontend's labels
read (``frontend/lib/toolLabels.ts``: ``"{action}: {detail}"``; area 2
integration, 2026-10-11). A wake he sets himself reads "Set a time to look
again" there only when the chip says ``wake:`` — ``schedule(add,
kind=wake)`` must not show as an ordinary scheduled task."""

from services.graph.streaming import _summarize_tool_args


def test_a_self_wake_is_chipped_as_a_wake():
    assert _summarize_tool_args("schedule", {"action": "add", "kind": "wake", "what": "Check the ticket prices"}) \
        == "wake: Check the ticket prices"


def test_a_task_or_reminder_stays_add():
    assert _summarize_tool_args("schedule", {"action": "add", "what": "Call the vet"}) == "add: Call the vet"
    assert _summarize_tool_args("schedule", {"action": "add", "kind": "remind", "what": "Call the vet"}) \
        == "add: Call the vet"
