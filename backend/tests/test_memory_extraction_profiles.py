"""Heartbeat threads extract durable facts only.

Of the 53 memories stored on 2026-09-04, 23 came from heartbeat contact
threads, and most were other people's chatter (of the shape "Parker says
the tickets cost $40", "the store has lamps at $25", "Jordan says the
pizza is 20 minutes out"). With retrieval capped at 5 per turn, that crowds
out what matters. 62% of live rows had never been used.

Two levers, both scoped to an ``extraction_profile="heartbeat"`` (the legacy
triage responders that passed it were removed with the triage engine; the
profile stays for any contact-thread caller):
  * a prompt addendum saying what to keep (facts about Ellis; durable facts
    about the contact) and what to skip (prices, ETAs, reactions, greetings,
    what a screen showed at a moment), plus a new ``about_owner`` field;
  * a code-side floor: under the heartbeat profile a ``context`` row that is
    not about Ellis needs importance >= 0.6 to be stored.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest


def test_prompt_addendum_only_for_heartbeat_profile():
    from services.memory_service import build_extraction_prompt, HEARTBEAT_EXTRACTION_ADDENDUM

    chat = build_extraction_prompt("USER: hi", "None", profile="chat")
    hb = build_extraction_prompt("USER: hi", "None", profile="heartbeat")
    assert HEARTBEAT_EXTRACTION_ADDENDUM not in chat
    assert HEARTBEAT_EXTRACTION_ADDENDUM in hb
    assert '"about"' in hb and '"about"' in chat, "the field is part of the schema for every profile"


def test_about_owner_defaults_false_and_is_reported():
    """Since the mind rebuild ``about`` is the column; ``about_owner`` is derived
    from it and the legacy boolean is still accepted from the extractor."""
    from services.memory_service import coerce_extracted_fields

    fields, misses = coerce_extracted_fields({"memory_type": "fact", "temporal_nature": "timeless",
                                              "source_type": "user_stated", "confidence": 0.9, "importance": 0.8})
    assert fields["about"] == "owner" and fields["about_owner"] is True
    assert "about" in misses

    fields, misses = coerce_extracted_fields({"memory_type": "fact", "temporal_nature": "timeless",
                                              "source_type": "user_stated", "confidence": 0.9, "importance": 0.8,
                                              "about_owner": True})
    assert fields["about_owner"] is True
    assert "about_owner" not in misses


@pytest.mark.parametrize(
    "profile,memory_type,about_owner,importance,expected",
    [
        ("heartbeat", "context", False, 0.5, False),   # a friend's ticket price
        ("heartbeat", "context", False, 0.6, True),    # at the floor
        ("heartbeat", "context", True, 0.5, True),     # about Ellis: no floor
        ("heartbeat", "fact", False, 0.5, True),       # durable types are not floored
        ("heartbeat", "preference", False, 0.3, True),
        ("chat", "context", False, 0.2, True),         # chat profile unchanged
    ],
)
def test_store_under_profile(profile, memory_type, about_owner, importance, expected):
    from services.memory_service import should_store_under_profile

    fields = {"memory_type": memory_type, "about_owner": about_owner, "importance": importance}
    assert should_store_under_profile(fields, profile) is expected


_PAYLOAD = (
    '[{"content": "Parker says the tickets cost $40", "memory_type": "context", "importance": 0.5, '
    '"temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9, "about_owner": false},'
    '{"content": "Rowan is Ellis\'s partner", "memory_type": "fact", "importance": 0.8, '
    '"temporal_nature": "timeless", "source_type": "user_stated", "confidence": 0.9, "about_owner": false},'
    '{"content": "Ellis leaves for Parker\'s at 5:45 on Friday", "memory_type": "context", "importance": 0.7, '
    '"temporal_nature": "temporary", "source_type": "user_stated", "confidence": 0.9, "about_owner": true}]'
)


def _stub_pipeline(monkeypatch):
    from services import memory_service

    class _L:
        async def ainvoke(self, messages):
            class R:
                content = _PAYLOAD
            return R()

    stored = []

    async def _store(memories):
        stored.extend(memories)
        return memories

    async def _noop(*a, **k):
        return []

    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: _L())
    monkeypatch.setattr(memory_service, "store_memories_with_conflict_detection", _store)
    monkeypatch.setattr("services.entity_service.extract_entities", _noop)
    return stored


async def test_heartbeat_profile_drops_low_value_third_party_context(monkeypatch):
    from services import memory_service

    stored = _stub_pipeline(monkeypatch)
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "tickets are $40 lol"}],
        conversation_id="hb-1",
        extraction_profile="heartbeat",
    )
    assert [m.content for m in stored] == ["Rowan is Ellis's partner", "Ellis leaves for Parker's at 5:45 on Friday"]


async def test_chat_profile_stores_all_three(monkeypatch):
    from services import memory_service

    stored = _stub_pipeline(monkeypatch)
    await memory_service.extract_and_store_memories(
        messages=[{"role": "user", "content": "tickets are $40 lol"}],
        conversation_id="chat-1",
    )
    assert len(stored) == 3


def test_chat_with_memory_threads_the_profile_through():
    src = (Path(__file__).resolve().parents[1] / "services/graph/streaming.py").read_text()
    sig_start = src.index("async def chat_with_memory(")
    sig = src[sig_start: src.index(") -> str:", sig_start)]
    assert 'extraction_profile: str = "chat"' in sig
    block = src.index("# ===== MEMORY EXTRACTION =====", sig_start)
    assert "extraction_profile=extraction_profile" in src[block: block + 900]


# ---------------------------------------------------------------------------
# 2026-09-27: a one-off preference is temporary (memory audit, preferences)
# ---------------------------------------------------------------------------
#
# 68 of the 332 live preferences were one-offs — an order, a weekend, a
# trip, "that night" — and 57 of them were stored evolving or timeless.
# The prompt had no temporary class for preferences: "fixed preferences"
# were timeless, "favorite restaurant" evolving, and "temporary" was only
# "current projects, upcoming events, temporary situations". Its one
# preference example was evolving. 54 more were someone else's preference
# stored about Ellis.

_OCCASIONS = ("occasion", "order", "meal", "trip", "weekend", "purchase", "event", "right now", "lately",
              "this week")


def _nature_line(prompt: str, nature: str) -> str:
    return next(l for l in prompt.splitlines() if l.lstrip().startswith(f'- "{nature}"'))


@pytest.mark.parametrize("name", ["MEMORY_EXTRACTION_PROMPT", "BACKFILL_PROMPT", "DEDUP_CLASSIFY_PROMPT"])
def test_every_prompt_that_defines_temporal_nature_has_a_temporary_preference(name):
    from services import memory_service

    prompt = getattr(memory_service, name)
    temporary = _nature_line(prompt, "temporary").lower()
    assert "preference" in temporary, temporary
    for word in _OCCASIONS:
        assert word in temporary, (word, temporary)
    timeless = _nature_line(prompt, "timeless").lower()
    assert "fixed preferences" not in timeless, "timeless is only for a taste held for years"
    assert "years" in timeless and "bananas" in timeless, timeless
    evolving = _nature_line(prompt, "evolving").lower()
    assert "taste" in evolving, evolving


def test_the_extraction_prompt_says_someone_elses_preference_is_about_them():
    from services.memory_service import MEMORY_EXTRACTION_PROMPT

    line = next(l for l in MEMORY_EXTRACTION_PROMPT.splitlines()
                if "preference" in l.lower() and '"about": "contact"' in l)
    assert "someone else" in line.lower()


def test_the_extraction_prompt_shows_a_temporary_preference():
    from services.memory_service import MEMORY_EXTRACTION_PROMPT

    examples = [json.loads(l.strip().rstrip(",").rstrip("]").lstrip("[").replace("{{", "{").replace("}}", "}"))
                for l in MEMORY_EXTRACTION_PROMPT.split("Example output:")[1].split("IMPORTANT:")[0].splitlines()
                if l.strip().startswith(("[{{", "{{"))]
    assert any((e["memory_type"], e["temporal_nature"]) == ("preference", "temporary") for e in examples)


def test_the_mind_addendum_says_a_one_off_preference_is_temporary():
    from services.memory_service import MIND_EXTRACTION_ADDENDUM

    line = next(l for l in MIND_EXTRACTION_ADDENDUM.splitlines() if "preference" in l.lower())
    assert '"temporal_nature": "temporary"' in line
    for word in ("order", "trip", "weekend", "right now"):
        assert word in line.lower(), word
    assert '"about": "contact"' in line
