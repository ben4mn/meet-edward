"""The offer grammar: every reach to Ellis from Edward's own turns has one shape.

Why (proactivity-spec §4.4, 2026-10-11): 0 of 33 pushes were opened in the
evidence window. The ones that worked named whose message and when, said
what was missing, held the irreversible step and asked one question. The
lint is pure and returns its problems in words, so ``reach_service`` can
hand them back to the model as a ``Tool error`` to fix once.
"""

import pytest

from services.offer_grammar import HELD_STEP, NO_GREETING, ONE_QUESTION, lint

pytestmark = pytest.mark.unit

SKYWAY = ("Skyway's 9:31 pm text says check-in closes at 10; you said you're en route. "
      "Want me to open online check-in? (not done)")
QUINN = ("Quinn asked at 2:10 pm for your referral link and hasn't heard back. I found it in your "
          "email — send it to him? (not sent)")


# ------------------------------------------------------------ fixtures --

def test_the_greeting_filler_offer_is_rejected():
    problems = lint("Hey! Just checking in — let me know if you need anything")
    assert any("greeting" in p for p in problems)
    assert any("whose message" in p for p in problems)
    assert any("one question" in p for p in problems)


def test_the_flight_offer_is_accepted():
    assert lint(SKYWAY, who=("Skyway",)) == []


def test_the_flight_offer_needs_no_who_when_it_has_a_time_beside_a_source():
    assert lint(SKYWAY) == []


def test_a_draft_with_no_held_step_and_no_source_time_is_rejected():
    problems = lint("I drafted a reply to Quinn. Want it?")
    assert any("held back" in p for p in problems)
    assert any("whose message" in p for p in problems)


def test_the_quinn_offer_is_accepted():
    assert lint(QUINN, who=("Quinn",)) == []


# ---------------------------------------------------- each rule alone --

def test_a_push_body_over_180_characters_is_too_long():
    text = "Skyway's 9:31 pm text says check-in closes at 10. " + "x" * 160 + " Open check-in?"
    assert len(text) > 180
    assert any("too long for a push" in p for p in lint(text, kind="push"))


def test_a_note_may_be_longer_than_a_push_but_not_past_600():
    text = "Skyway's 9:31 pm text says check-in closes at 10. " + "y" * 200 + " Open check-in?"
    assert lint(text, kind="note") == []
    long_note = "Skyway's 9:31 pm text says check-in closes at 10. " + "y" * 600 + " Open check-in?"
    assert any("too long" in p for p in lint(long_note, kind="note"))


def test_a_title_over_60_characters_is_too_long():
    assert any("too long" in p for p in lint("t" * 61, kind="title"))
    assert lint("Flight check-in", kind="title") == []


@pytest.mark.parametrize("opener", [
    "Hi Ellis,", "hello", "Good morning!", "good evening —", "Just checking: ", "Just wanted to say",
])
def test_greetings_and_filler_openers_are_rejected(opener):
    text = f"{opener} Skyway's 9:31 pm text says check-in closes at 10. Open check-in?"
    assert any("greeting" in p for p in lint(text))


@pytest.mark.parametrize("filler", ["let me know", "if you need anything", "hope you", "hope this",
                                    "happy to help", "no worries"])
def test_filler_anywhere_is_rejected(filler):
    text = f"Skyway's 9:31 pm text says check-in closes at 10 — {filler}. Open check-in?"
    assert any("greeting" in p for p in lint(text))


def test_a_name_from_who_is_a_source_reference_case_insensitive():
    assert lint("quinn still needs the referral link. Send it? (not sent)", who=("Quinn",)) == []


def test_a_time_without_a_source_noun_is_not_a_source_reference():
    problems = lint("Check-in closes at 10 pm and nothing shows you did it. Open it?")
    assert any("whose message" in p for p in problems)


@pytest.mark.parametrize("noun", ["text", "message", "email", "call", "calendar", "SMS", "voicemail",
                                  "note", "post", "emails"])
def test_any_source_noun_beside_a_time_counts(noun):
    assert lint(f"The 9:31 pm {noun} says check-in closes at 10. Open check-in?") == []


def test_the_noun_may_come_before_the_time():
    assert lint("A text at 9:31 pm says check-in closes at 10. Open check-in?") == []


def test_it_must_end_with_a_question():
    problems = lint("Skyway's 9:31 pm text says check-in closes at 10. I opened check-in.")
    assert any("one question" in p for p in problems)


def test_exactly_one_question_mark():
    problems = lint("Skyway's 9:31 pm text: did you check in? Want me to open check-in?")
    assert any("one question" in p for p in problems)


def test_a_trailing_parenthetical_is_stripped_once_before_the_question_check():
    assert lint("Skyway's 9:31 pm text says check-in closes at 10. Open check-in? (not done)") == []
    problems = lint("Skyway's 9:31 pm text says check-in closes at 10. Open check-in? (not done) (really)")
    assert any("one question" in p for p in problems)


@pytest.mark.parametrize("word", ["drafted", "draft", "prepared", "wrote", "filled", "ready", "lined up"])
def test_prepared_work_must_say_what_was_held_back(word):
    text = f"Quinn's 2:10 pm text asks for your referral link; I {word} a reply. Send it?"
    assert any("held back" in p for p in lint(text))


@pytest.mark.parametrize("held", ["not sent", "not done", "haven't sent", "nothing sent", "not booked",
                                  "not paid"])
def test_any_held_phrase_satisfies_the_held_step(held):
    text = f"Quinn's 2:10 pm text asks for your referral link; I drafted a reply. Send it? ({held})"
    assert lint(text) == []


def test_empty_text_is_a_problem_not_a_crash():
    assert lint("") != []
    assert lint(None) != []  # type: ignore[arg-type]


# ---- a note that tells is not an offer (review 2026-10-11) -----------------
#
# Ellis's answer kept the grammar for can't-wait reach. Enforced on every note
# from every model origin it refused a follow-through's report, the
# nightly's one note and a friend's relay — each a statement with nothing
# to ask. An informational note keeps length, no greeting or filler, and
# "not sent" when something was prepared; a push, and anything the speaker
# says (``offer``), keep the whole grammar.

STATEMENTS = (
    "I posted the spare pair of tickets in Trivia night at 6:58 pm, as you asked.",
    "Biscuit's evening meds were logged late three times this week.",
    "Parker says he is running 15 minutes late.",
)


@pytest.mark.parametrize("text", STATEMENTS)
def test_an_informational_note_needs_no_source_or_question(text):
    assert lint(text, kind="note") == []


@pytest.mark.parametrize("text", STATEMENTS)
def test_a_push_or_an_offer_still_does(text):
    for kind in ("push", "offer"):
        problems = lint(text, kind=kind)
        assert ONE_QUESTION in problems, (kind, problems)


def test_a_note_still_refuses_filler_length_and_an_unheld_draft():
    assert NO_GREETING in lint("Hey! Parker is running late, let me know", kind="note")
    assert any("too long" in p for p in lint("Parker is late. " + "y" * 600, kind="note"))
    assert HELD_STEP in lint("I drafted a reply to Quinn.", kind="note")
    assert lint("I drafted a reply to Quinn (not sent).", kind="note") == []


def test_an_offer_is_a_note_by_length():
    text = "Skyway's 9:31 pm text says check-in closes at 10. " + "y" * 300 + " Open check-in?"
    assert lint(text, kind="offer") == []
