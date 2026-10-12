"""Unit tests for scripts/prune_dead_turns.py.

Regression origin (2026-08-25): the script was written to repair Rowan's
checkpoint (296 empty AI turns + 198 nudges) and shipped untested. Its
report and chunking are pure and cheap to pin; the graph write is not.
"""

import pytest

from langchain_core.messages import AIMessage, HumanMessage

from scripts.prune_dead_turns import chunks, plan_prune

pytestmark = pytest.mark.unit


def _nudge() -> HumanMessage:
    return HumanMessage(content="[SYSTEM] You have tools available but didn't use any. Use one.")


class TestPlanPrune:
    def test_reports_counts_and_doomed_ids(self):
        dead = AIMessage(content="", id="dead-1")
        nudge = _nudge()
        nudge.id = "nudge-1"
        keep = AIMessage(content="hello", id="ok-1")
        report = plan_prune([HumanMessage(content="hi", id="h1"), dead, nudge, keep])

        assert report.messages == 4
        assert report.doomed == ["dead-1", "nudge-1"]
        assert report.characters == len("hi") + len("hello") + len(nudge.content)

    def test_clean_thread_has_nothing_to_prune(self):
        report = plan_prune([HumanMessage(content="hi", id="h1"), AIMessage(content="yo", id="a1")])
        assert report.doomed == []


class TestChunks:
    def test_splits_into_bounded_batches(self):
        assert list(chunks(list(range(5)), 2)) == [[0, 1], [2, 3], [4]]

    def test_empty_input_yields_nothing(self):
        assert list(chunks([], 200)) == []
