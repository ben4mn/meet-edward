"""What counts as a ping (pings-media-spec §2.1). Pure.

A ping is someone asking Edward something where he must (or may) answer in
that chat: an ``@edward`` from anyone in any chat (the owner's own included —
R1: the identity filter used to drop it), the owner in their self-chat, and an
inline reply to one of Edward's own bubbles.
"""

from __future__ import annotations

import pytest

from services.pings import detect

pytestmark = pytest.mark.unit

OWNER_HANDLE = "+12015550000"
OWNER = frozenset({OWNER_HANDLE, "owner@example.com"})
EDWARD_GUID = "EDWARD-SENT-GUID-1"


def _msg(text, *, from_me=False, group=False, chat="+12015550101", thread=None, assoc=0):
    return {"text": text, "is_from_me": from_me, "is_group": group, "chat_identifier": chat,
            "thread_originator_guid": thread, "associated_message_type": assoc}


def _classify(msg, *, echo=False, unconfirmed=False, guids=frozenset({EDWARD_GUID})):
    return detect.classify(msg, is_echo=echo, owner_handles=OWNER, edward_guids=guids, unconfirmed_send=unconfirmed)


def test_owners_own_at_edward_in_a_friends_one_to_one_is_a_must_answer_mention():
    v = _classify(_msg("@Edward what's the score?", from_me=True, chat="+12015550101"))
    assert v == detect.PingVerdict(kind="mention", from_owner=True, must_answer=True)


def test_owners_at_edward_in_a_group_is_a_mention_from_owner():
    v = _classify(_msg("@edward look at used tickets", from_me=True, group=True, chat="chat123"))
    assert v.kind == "mention" and v.from_owner and v.must_answer


def test_a_friends_at_edward_in_a_group_is_a_must_answer_mention():
    v = _classify(_msg("hey @EDWARD when's dinner", group=True, chat="chat123"))
    assert v == detect.PingVerdict(kind="mention", from_owner=False, must_answer=True)


def test_owners_self_chat_needs_no_at():
    v = _classify(_msg("remind me to call the vet", from_me=True, chat=OWNER_HANDLE))
    assert v == detect.PingVerdict(kind="owner_direct", from_owner=True, must_answer=True)


def test_self_chat_by_email_handle():
    assert _classify(_msg("what's on today", from_me=True, chat="owner@example.com")).kind == "owner_direct"


def test_self_chat_spelled_differently_is_still_the_self_chat():
    v = _classify(_msg("what's on today", from_me=True, chat="12015550000"))
    assert v is not None and v.kind == "owner_direct"


def test_self_chat_breaker_without_at_is_not_a_ping():
    assert _classify(_msg("Done. The vet is booked", from_me=True, chat=OWNER_HANDLE), unconfirmed=True) is None


def test_self_chat_breaker_with_at_edward_is_a_mention():
    v = _classify(_msg("@edward and the groomer too", from_me=True, chat=OWNER_HANDLE), unconfirmed=True)
    assert v.kind == "mention" and v.from_owner and v.must_answer


def test_a_friend_writing_in_their_own_one_to_one_is_not_owner_direct():
    assert _classify(_msg("hey are you around", from_me=False, chat=OWNER_HANDLE)) is None


def test_a_group_with_owners_handle_as_its_identifier_is_not_the_self_chat():
    assert _classify(_msg("on my way", from_me=True, group=True, chat=OWNER_HANDLE)) is None


def test_an_echo_carrying_at_edward_is_never_a_ping():
    assert _classify(_msg("Sure — @edward here. - Edward 🤖", from_me=True, chat="chat123", group=True),
                     echo=True) is None
    assert _classify(_msg("Booked.", from_me=True, chat=OWNER_HANDLE), echo=True) is None


def test_owners_inline_reply_to_edward_must_be_answered():
    v = _classify(_msg("and the other one?", from_me=True, group=True, chat="chat123", thread=EDWARD_GUID))
    assert v == detect.PingVerdict(kind="reply_to_edward", from_owner=True, must_answer=True)


def test_a_friends_inline_reply_to_edward_may_be_answered():
    v = _classify(_msg("lol thanks", group=True, chat="chat123", thread=EDWARD_GUID))
    assert v == detect.PingVerdict(kind="reply_to_edward", from_owner=False, must_answer=False)


def test_an_inline_reply_to_someone_else_is_not_a_ping():
    assert _classify(_msg("lol", group=True, chat="chat123", thread="SOMEONE-ELSE")) is None


def test_a_mention_beats_a_reply():
    v = _classify(_msg("@edward and this?", group=True, chat="chat123", thread=EDWARD_GUID))
    assert v.kind == "mention" and v.must_answer


@pytest.mark.parametrize("text", [
    "Edward Jones called about the account",
    "CL's Edward is sick today",
    "mail it to foo@edward.com",
    "send x@edward.com the deck",
    "Thanks Edward!",
    "edward, are you there",          # vocative is P1 and off by default
    "@edwardo is a different guy",
    # Review 2026-10-11: a link is not an address to Edward.
    "look https://www.youtube.com/@edward/videos",
    "https://medium.com/@edward/my-post",
    "youtube.com/@edward has the clip",
    'check this [link: "Edward (@edward) / X" (x.com) attachment:449330]',
    '[image: IMG_1.heic attachment:449331 — "a screenshot of a post by @edward"]',
])
def test_not_pings(text):
    assert _classify(_msg(text, group=True, chat="chat123")) is None


@pytest.mark.parametrize("text", [
    "@edward",
    "(@Edward) what time",
    "ok @Edward's turn",
    "hey\n@edward on line two",
])
def test_mention_spellings(text):
    assert _classify(_msg(text, group=True, chat="chat123")).kind == "mention"


def test_the_listeners_caption_is_what_is_read():
    msg = _msg('[link: "Edward (@edward) / X" (x.com) attachment:449330]', group=True, chat="chat123")
    msg["caption"] = ""
    assert _classify(msg) is None
    msg = _msg('@edward what is this [link: "Some page" attachment:449330]', group=True, chat="chat123")
    msg["caption"] = "@edward what is this"
    assert _classify(msg).kind == "mention"


def test_a_mention_beside_a_link_is_still_a_mention():
    assert _classify(_msg("@edward is https://example.com/deal legit?", group=True, chat="chat123")).kind == "mention"


def test_an_ask_at_character_524_of_572_is_still_a_ping():
    head = ("we were going back and forth about the weekend and nobody can decide what to do "
            * 8)[:523] + " "
    text = (head + "@edward can you check the used ticket prices for us?")[:572]
    assert text.index("@edward") == 524
    assert _classify(_msg(text, group=True, chat="chat123")).kind == "mention"


def test_tapbacks_and_other_associated_messages_are_never_pings():
    """A tapback on a message that said @edward reads 'Loved “@edward …”'.
    The listener never ingests them; classify refuses them too."""
    assert _classify(_msg("Loved “@edward look at this”", group=True, chat="chat123", assoc=2000)) is None
    assert _classify(_msg("Emphasized “and the other one?”", from_me=True, chat=OWNER_HANDLE, assoc=2004)) is None
    assert _classify(_msg("Liked “x”", group=True, chat="chat123", thread=EDWARD_GUID, assoc=2001)) is None


def test_missing_fields_are_ordinary():
    assert detect.classify({}, is_echo=False, owner_handles=frozenset(), edward_guids=frozenset(),
                           unconfirmed_send=False) is None
    assert detect.classify({"text": None, "is_from_me": True}, is_echo=False, owner_handles=OWNER,
                           edward_guids=frozenset(), unconfirmed_send=False) is None


def test_must_answer_matches_the_hooks_rule():
    from services.pings import hooks

    for kind in ("mention", "owner_direct", "reply_to_edward"):
        for from_owner in (True, False):
            assert hooks.must_answer(kind, from_owner) == (kind != "reply_to_edward" or from_owner)
