"""Why the noticer's items are dropped, named per rule and never with the
message (2026-10-11).

``notice_eval --only family_unanswered_4h`` failed about 40 % of runs with
"noticed 0, invalid 1": the background-route noticer (Haiku 5.5) wrote an item
``validate_items`` dropped — an unknown kind, a source not in the pack, a
one-source conflict or an unreadable ``closes_at`` — and nothing said which.
Each drop now names its rule with only the shape of what was wrong (a kind
word, an id, a date's format), never the claim or a line of anyone's
message: the drop reaches the log and the ledger. Pure.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import noticing_service

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 11, 20, 30, tzinfo=timezone.utc)
REF = "hb:family_unanswered_4h:mo-1"
REFS = {REF: {"ref": REF, "chat": "chat-mom", "at": NOW.isoformat(), "from_me": False},
        "hb:e2": {"ref": "hb:e2", "chat": "chat-mom", "at": NOW.isoformat(), "from_me": False}}
CLAIM = "Mom texted that Dad's test results came back and asked Ellis to call her."


def _item(**kw):
    base = {"kind": "unanswered", "category": "family", "claim": CLAIM, "sources": [REF],
            "chat_identifier": "chat-mom", "who": ["Mom"], "closes_at": None, "suggested": "note",
            "dedupe_key": "mom-call", "sensitive": True}
    base.update(kw)
    return base


def _drops(*items):
    drops = []
    valid, invalid = noticing_service.validate_items({"noticings": list(items)}, REFS, drops=drops)
    assert invalid == len(drops)
    return valid, drops


@pytest.mark.parametrize("item, rule", [
    ("not a dict", "not_an_object"),
    (_item(kind="reminder"), "unknown_kind"),
    (_item(sources=[]), "no_sources"),
    (_item(sources=["hb:nope"]), "source_not_in_pack"),
    (_item(kind="conflict"), "conflict_needs_two"),
    (_item(closes_at="whenever Ellis can"), "unreadable_closes_at"),
])
def test_each_drop_names_its_rule(item, rule):
    valid, drops = _drops(item)
    assert valid == [] and [d["rule"] for d in drops] == [rule]


def test_a_kept_item_is_not_a_drop():
    valid, drops = _drops(_item())
    assert len(valid) == 1 and drops == []


def test_the_drop_says_what_was_wrong_in_shape_only():
    _, drops = _drops(_item(kind="reminder", dedupe_key="a"),
                      _item(sources=["hb:mo-1", REF], dedupe_key="b"),
                      _item(closes_at="2026-10-11 tonight", dedupe_key="c"))
    by_rule = {d["rule"]: d for d in drops}
    assert by_rule["unknown_kind"]["kind"] == "reminder"
    assert by_rule["source_not_in_pack"]["missing"] == ["hb:mo-1"]
    assert by_rule["unreadable_closes_at"]["closes_at"] == "9999-99-99 aaaaaaa"


def test_a_drop_never_carries_the_claim_or_a_message_line():
    _, drops = _drops(
        _item(kind="call mom about dad's test results"),
        _item(sources=["Mom: Dad's test results came back. Can you call me when you can?"], dedupe_key="b"),
        _item(sources=[{"quote": "Dad's test results came back"}], dedupe_key="c"),
        _item(closes_at="when Dad's test results are discussed", dedupe_key="d"),
        _item(kind="conflict", dedupe_key="e"),
    )
    assert len(drops) == 5
    for d in drops:
        line = noticing_service.drop_line(d)
        for word in ("Dad", "test results", "call me", "Mom"):
            assert word.lower() not in line.lower() and word.lower() not in str(d).lower(), (word, d)


def test_the_drop_line_names_the_rule():
    _, drops = _drops(_item(sources=["hb:mo-1"]))
    line = noticing_service.drop_line(drops[0])
    assert "rule=source_not_in_pack" in line and "hb:mo-1" in line


def test_the_counts_are_unchanged_without_a_drops_list():
    valid, invalid = noticing_service.validate_items(
        {"noticings": [_item(kind="reminder"), _item(closes_at=(NOW + timedelta(hours=2)).isoformat())]}, REFS)
    assert invalid == 1 and len(valid) == 1


# ------------------------------------------------- what may be a source --
# Every drop in ~100 real noticer passes (2026-10-11: three full evals and
# 55 family runs on Haiku 5.5) was one cause: a noticing about a "Coming up"
# line — the calendar's "Toll Bill—Ellis pays" at noon Monday — cited as
# "calendar:2026-10-12T12:00:00-05:00" or not at all. The pack listed the
# calendar under the same kind of header as the messages and the prompt never
# said only ``hb:`` lines are evidence; a one-item answer that is that note is
# the family case's "noticed 0, invalid 1".

def test_the_prompt_says_only_message_lines_are_sources():
    prompt = noticing_service.NOTICER_PROMPT
    rules = prompt.split("Rules:", 1)[1]
    assert "only" in rules.split('"sources"', 1)[1][:200] and "hb:" in rules.split('"sources"', 1)[1][:200]
    coming = prompt.split("Coming up", 1)
    assert len(coming) == 2, "the prompt names the Coming up section"
    assert "never a source" in coming[1][:300] and "never a noticing" in coming[1][:300]


def test_the_pack_header_says_coming_up_is_background():
    header = noticing_service.COMING_UP_HEADER
    assert header.startswith("## Coming up")
    assert "never a source" in header and "never a noticing" in header


# --------------------------------------------------- the chat and who --
# The pack names a chat ("· Mom ·"), never its identifier, so the model's
# chat_identifier was a guess — "Mom", "M." — and it won: the gate's "Ellis is
# on it", live-chat silence, stand-downs and open pings all look a chat up by
# it, and found nothing (2026-10-11: m_twenty held where a live chat is
# silent). The chat is the sources'.

OWNER_K = {"ref": "hb:b1", "chat": "chat-k", "at": NOW.isoformat(), "from_me": True}


def test_the_chat_is_the_sources_not_the_models_guess():
    valid, _ = _drops(_item(chat_identifier="Mom"))
    assert valid[0]["chat_identifier"] == "chat-mom"


def test_the_chat_is_the_first_inbound_sources():
    refs = {**REFS, "hb:b1": OWNER_K}
    valid, _ = noticing_service.validate_items(
        {"noticings": [_item(kind="conflict", sources=["hb:b1", REF], chat_identifier="K.")]}, refs)
    assert valid[0]["chat_identifier"] == "chat-mom"


def test_with_only_owners_lines_the_chat_is_his():
    refs = {**REFS, "hb:b1": OWNER_K}
    valid, _ = noticing_service.validate_items(
        {"noticings": [_item(kind="follow_through", sources=["hb:b1"], chat_identifier="")]}, refs)
    assert valid[0]["chat_identifier"] == "chat-k"


def test_a_name_written_as_one_string_is_one_name():
    valid, _ = _drops(_item(who="Mom"))
    assert valid[0]["who"] == ["Mom"]


def test_the_prompt_no_longer_asks_for_a_chat_identifier():
    assert "chat_identifier" not in noticing_service.NOTICER_PROMPT
