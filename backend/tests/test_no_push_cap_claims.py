"""Nothing the model reads names a push cap Ellis ruled out.

Ellis, 2026-10-11 (answer #2): "push, with no limit" — no daily push count
(``EDWARD_PUSH_BUDGET`` is unset by default; being ignored is judgment
input, never a hard block). A count in a tool description or the manual is
a guard that lives only in the prompt: a model trusting "the system caps
pushes (two a day)" leaves a note where a can't-wait push was needed. The
toolset's ``tell_owner`` lost the line in 2c711f3; the tool's module notes
still said it.
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.unit

COUNT = re.compile(r"\b(one|two|three|four|five|\d+)\s+(a|per|each)\s+day\b", re.I)


def _no_cap(text: str, where: str) -> None:
    assert not COUNT.search(text), f"{where} names a push count: {COUNT.search(text).group(0)!r}"
    assert "caps pushes" not in text.lower(), where
    assert "daily push budget" not in text.lower(), where


def test_the_toolsets_tell_owner_and_its_notes_name_no_cap(monkeypatch):
    from services.toolset import tell_owner

    monkeypatch.delenv("EDWARD_PUSH_BUDGET", raising=False)
    _no_cap(tell_owner.tell_owner.description, "toolset tell_owner")
    assert "urgent" in tell_owner.tell_owner.description
    _no_cap(tell_owner.__doc__ or "", "services/toolset/tell_owner.py notes")


def test_the_manual_and_the_mind_prompt_name_no_cap(monkeypatch):
    from services import mind_service
    from services.toolset.manual import TOOL_MANUAL

    monkeypatch.delenv("EDWARD_PUSH_BUDGET", raising=False)
    _no_cap(TOOL_MANUAL, "the tool manual")
    _no_cap(mind_service.mind_system_prompt(), "the mind prompt")
