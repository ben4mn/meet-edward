"""The noticer's category and channel words reach the gate (review 2026-10-11,
major, verified with the real ``validate_items`` and ``speak_gate``).

Whether trace (c) — a vendor's "check-in closes in 2 h" — can push depends on
the noticer writing exactly ``travel`` (or ``household``): ``is_floor_push``
reads only those, and ``validate_items`` nulled any other word. The
production ``NOTICER_PROMPT`` asked for a ``category`` but never listed the
words, and its own "never … logistics" primed ``logistics``. The benchmark's
4/4 used a different prompt and scored only kind and source. Pure.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import noticing_service, speak_gate

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 3, 19, 0, tzinfo=timezone.utc)
REFS = {"hb:e1": {"ref": "hb:e1", "chat": "chat-ba", "at": NOW.isoformat(), "from_me": False}}


def _item(**kw):
    base = {"kind": "deadline", "category": "travel", "claim": "BA: online check-in for UA958 closes at 9 pm.",
            "sources": ["hb:e1"], "chat_identifier": "chat-ba", "who": ["BA"],
            "closes_at": (NOW + timedelta(hours=2)).isoformat(), "suggested": "push", "dedupe_key": "UA958"}
    base.update(kw)
    return base


def _validated(**kw):
    valid, invalid = noticing_service.validate_items({"noticings": [_item(**kw)]}, REFS)
    assert invalid == 0 and len(valid) == 1
    return valid[0]


def test_the_prompt_lists_every_category_the_gate_reads():
    prompt = noticing_service.NOTICER_PROMPT
    for cat in speak_gate.CATEGORIES:
        assert f"- {cat}:" in prompt or f"{cat} —" in prompt or f"{cat}:" in prompt, cat
    assert "check-in" in prompt.split("travel", 1)[1][:200], "travel's meaning names check-in"


def test_the_prompt_lists_the_suggested_words():
    prompt = noticing_service.NOTICER_PROMPT
    for word in noticing_service.SUGGESTIONS:
        assert word in prompt


@pytest.mark.parametrize("word", ["flight", "Flights", "airline", "trip", "check-in", "checkin", "boarding",
                                  "hotel", "train", "airport"])
def test_travel_synonyms_become_travel(word):
    assert _validated(category=word)["category"] == "travel"


@pytest.mark.parametrize("word", ["home", "house", "delivery", "pets", "pet", "yard"])
def test_household_synonyms_become_household(word):
    assert _validated(category=word)["category"] == "household"


@pytest.mark.parametrize("word,expected", [("finance", "money"), ("bill", "money"), ("job", "work"),
                                           ("medical", "health"), ("friends", "social"), ("account", "security"),
                                           ("logistics", "logistics"), ("Travel", "travel"),
                                           ("gibberish", None), ("", None)])
def test_other_words(word, expected):
    assert _validated(category=word)["category"] == expected


@pytest.mark.parametrize("word,expected", [("notify", "push"), ("alert", "push"), ("buzz", "push"),
                                           ("Push", "push"), ("nonsense", "none")])
def test_suggested_synonyms(word, expected):
    assert _validated(suggested=word)["suggested"] == expected


@pytest.mark.parametrize("word", ["reply", "chat"])
def test_a_word_the_gate_could_turn_into_a_push_is_not_guessed(word):
    """The gate turns a ``thread`` outside an open ping chat into a push: a
    guess must never raise a channel."""
    assert _validated(suggested=word)["suggested"] == "none"


@pytest.mark.parametrize("word", ["service", "services", "air"])
def test_an_ambiguous_word_is_not_made_a_floor_category(word):
    """"customer service" or "air conditioning" is not household or travel:
    only those can push, so an ambiguous word stays unknown."""
    assert _validated(category=word)["category"] is None


@pytest.mark.parametrize("word", ["travel", "flight", "airline", "check-in"])
def test_a_check_in_closing_in_two_hours_is_a_floor_push_whatever_the_travel_word(word):
    item = _validated(category=word)
    n = speak_gate.Noticing(id="n1", kind=item["kind"], category=item["category"], claim=item["claim"],
                            sources=(), chat=item["chat_identifier"], who=tuple(item["who"]),
                            closes_at=item["closes_at"], suggested=item["suggested"],
                            dedupe_key=item["dedupe_key"], sensitive=False, origin="noticer")
    assert speak_gate.is_floor_push(n, NOW)
