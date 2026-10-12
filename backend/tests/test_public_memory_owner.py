"""The memory core names the owner from ``services.owner``, never a literal.

The extraction prompts are written with the ``{OWNER}`` token and rendered
once at import (conftest sets EDWARD_OWNER_NAME=Ellis before imports); the
mind addendum quotes the pack headings ``mind_service`` renders from the
same text; citation credit ignores the owner's configured names.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

NOW = datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc)

PROMPTS = ("MEMORY_EXTRACTION_PROMPT", "HEARTBEAT_EXTRACTION_ADDENDUM", "MIND_EXTRACTION_ADDENDUM",
           "SCHEDULED_EVENT_EXTRACTION_ADDENDUM", "ASSISTANT_TESTIMONY_RULE", "BACKFILL_PROMPT",
           "DEDUP_CLASSIFY_PROMPT")


@pytest.mark.unit
@pytest.mark.parametrize("name", PROMPTS)
def test_every_prompt_names_the_configured_owner_and_no_token_is_left(name):
    from services import memory_service

    text = getattr(memory_service, name)
    assert "{OWNER}" not in text
    assert "Ellis" in text, name
    assert not re.search(r"\bBen\b", text)


@pytest.mark.unit
@pytest.mark.parametrize("profile", ["chat", "heartbeat", "mind", "scheduled_event"])
def test_the_extraction_prompt_formats_for_every_profile(profile):
    from services.memory_service import build_extraction_prompt

    prompt = build_extraction_prompt("USER: hi", "None", profile=profile, now=NOW)
    assert "{OWNER}" not in prompt and "2026-09-05T15:00:00Z" in prompt


@pytest.mark.unit
def test_the_examples_carry_no_third_party_phone_number():
    from services.memory_service import MEMORY_EXTRACTION_PROMPT

    phone = re.compile(r"\+\d{6,}|\b\d{10,}\b|\(\d{3}\)\s*\d{3}-\d{4}|\b\d{3}-\d{3}-\d{4}\b")
    assert not phone.search(MEMORY_EXTRACTION_PROMPT)


@pytest.mark.unit
def test_the_mind_addendum_quotes_the_packs_owner_headings_byte_for_byte():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM
    from services.mind_service import STANDING_INSTRUCTIONS_HEADING, VERDICTS_HEADING

    quoted = re.findall(r'"([^"]+)"', next(l for l in MIND_EXTRACTION_ADDENDUM.splitlines()
                                            if l.startswith("- SKIP the pack's own sections")))
    standing = next(q for q in quoted if q.startswith("Standing instructions from"))
    verdicts = next(q for q in quoted if q.endswith("verdicts"))
    assert STANDING_INSTRUCTIONS_HEADING.startswith(standing)
    assert VERDICTS_HEADING.startswith(verdicts)


@pytest.mark.unit
def test_the_owners_configured_names_are_never_a_citation(monkeypatch):
    from services.memory_service import cited_memory_ids

    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    monkeypatch.setenv("EDWARD_OWNER_FULL_NAME", "Sam Rivera")
    m = SimpleNamespace(id="m-run", content="Sam Rivera and Edward agreed the training starts in October")
    assert cited_memory_ids("Sam Rivera, Edward here — October it is.", [m], prompt="hi") == []
    # Possessives too: "Sam's" tokenises as "sams".
    assert cited_memory_ids("Sam's plan, Rivera's call.", [m], prompt="hi") == []


@pytest.mark.unit
def test_with_no_owner_configured_a_name_is_an_ordinary_word(monkeypatch):
    from services.memory_service import cited_memory_ids

    monkeypatch.delenv("EDWARD_OWNER_NAME", raising=False)
    monkeypatch.delenv("EDWARD_OWNER_FULL_NAME", raising=False)
    monkeypatch.delenv("EDWARD_OWNER_ALIASES", raising=False)
    m = SimpleNamespace(id="m-run", content="Sam Rivera and Edward agreed the training starts in October")
    assert cited_memory_ids("Sam Rivera, Edward here — October it is.", [m], prompt="hi") == ["m-run"]
