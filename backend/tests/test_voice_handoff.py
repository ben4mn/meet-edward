"""Voice keeps working: the pure decisions behind a handed-off spoken turn.

Muse's headline voice feature is that it keeps working while you talk
(plan Phase 5, 2026-09-24). Edward's spoken turns used to be one turn per
commit: a tool-heavy answer kept Ellis waiting in silence, and a barge-in or
closing the voice view threw the work away. Now a turn that is still
running tools after ``DEFAULT_HANDOFF_SECONDS`` or at its third tool call
says so out loud, keeps going detached from the socket, and reports back —
spoken if the session is still open and quiet, a push if it is not.
"""

import random

import pytest

from services.voice.handoff import (
    DEFAULT_HANDOFF_SECONDS,
    HANDOFF_LINES,
    HANDOFF_TOOL_CALLS,
    PHASE_ANSWERING,
    PHASE_HANDED_OFF,
    PHASE_THINKING,
    PHASE_TOOLS,
    WAIT_LINES,
    delivery_for,
    is_stop_phrase,
    pick_handoff_line,
    pick_wait_line,
    push_text_for,
    result_speech,
    should_hand_off,
    summary_for_speech,
)

pytestmark = pytest.mark.unit


# --------------------------------------------------------------------------
# should_hand_off
# --------------------------------------------------------------------------

def test_the_defaults_are_the_ones_the_plan_names():
    assert DEFAULT_HANDOFF_SECONDS == 12.0
    assert HANDOFF_TOOL_CALLS == 3


def test_a_quick_turn_with_one_tool_is_not_handed_off():
    assert should_hand_off(2.0, 1, PHASE_TOOLS) is False


def test_the_third_tool_call_hands_off_however_fast_it_came():
    assert should_hand_off(0.5, 3, PHASE_TOOLS) is True
    assert should_hand_off(0.5, 2, PHASE_TOOLS) is False


def test_still_running_tools_after_the_threshold_hands_off():
    assert should_hand_off(12.0, 1, PHASE_TOOLS) is True
    assert should_hand_off(11.9, 1, PHASE_TOOLS) is False


def test_a_slow_model_with_no_tools_is_not_work_to_hand_off():
    # A late first token is what the bridge lines are for.
    assert should_hand_off(30.0, 0, PHASE_THINKING) is False


def test_an_answer_already_being_spoken_is_never_cut_for_a_handoff():
    assert should_hand_off(30.0, 5, PHASE_ANSWERING) is False


def test_a_turn_is_handed_off_once():
    assert should_hand_off(30.0, 5, PHASE_HANDED_OFF) is False


def test_thresholds_can_be_passed_in():
    assert should_hand_off(0.3, 1, PHASE_TOOLS, seconds=0.2) is True
    assert should_hand_off(0.1, 2, PHASE_TOOLS, max_tool_calls=2) is True


# --------------------------------------------------------------------------
# delivery_for
# --------------------------------------------------------------------------

def test_an_open_idle_session_hears_the_result():
    assert delivery_for(True, "listening") == "speak"


def test_a_closed_session_gets_a_push():
    assert delivery_for(False, "listening") == "push"
    assert delivery_for(False, "speaking") == "push"


@pytest.mark.parametrize("state", ["thinking", "speaking"])
def test_an_open_busy_session_holds_the_result_for_the_next_quiet_moment(state):
    # Talking over Ellis (or over Edward's own next answer) is worse than
    # waiting a few seconds; a held result that never gets a gap is pushed
    # when the session ends.
    assert delivery_for(True, state) == "hold"


# --------------------------------------------------------------------------
# summary_for_speech / result_speech / push_text_for
# --------------------------------------------------------------------------

def test_summary_is_the_first_two_sentences():
    text = "Your flight is booked. It leaves at 7:40 from gate B12. I also emailed the receipt. Anything else?"
    assert summary_for_speech(text) == "Your flight is booked. It leaves at 7:40 from gate B12."


def test_summary_flattens_markdown_and_links():
    text = "**Done.** I added [the recipe](https://example.com/r) to your notes.\n\n- item one\n- item two"
    out = summary_for_speech(text)
    assert "*" not in out and "http" not in out and "[" not in out
    assert out.startswith("Done. I added the recipe to your notes.")


def test_summary_keeps_abbreviations_whole():
    assert summary_for_speech("Dr. Smith moved you to 3pm. Bring the forms. And the card.") == (
        "Dr. Smith moved you to 3pm. Bring the forms."
    )


def test_a_run_on_first_sentence_is_cut_at_a_soft_boundary():
    text = "I checked " + ", ".join(f"store number {i}" for i in range(60)) + " and none had it."
    out = summary_for_speech(text, max_chars=120)
    assert len(out) <= 121
    assert out.endswith(".")


def test_empty_answers_summarise_to_nothing():
    assert summary_for_speech("") == ""
    assert summary_for_speech("   \n ") == ""


def test_result_speech_leads_in_so_a_sudden_voice_makes_sense():
    line = result_speech("All three are booked. Details are in the chat.")
    assert line.startswith("Okay, that's done.")
    assert "All three are booked." in line


def test_result_speech_without_an_answer_points_at_the_chat():
    assert "chat" in result_speech("")


def test_result_speech_for_a_failure_says_so():
    assert "couldn't" in result_speech("", failed=True)


def test_push_text_carries_the_summary():
    title, body = push_text_for("All three are booked. Details are in the chat. More.")
    assert title
    assert body == "All three are booked. Details are in the chat."
    assert len(body) <= 200


def test_push_text_for_an_empty_or_failed_answer():
    assert "chat" in push_text_for("")[1]
    assert "couldn't" in push_text_for("", failed=True)[1]


# --------------------------------------------------------------------------
# lines
# --------------------------------------------------------------------------

def test_handoff_lines_are_short_and_promise_to_report_back():
    assert len(HANDOFF_LINES) >= 5
    for line in HANDOFF_LINES:
        assert 10 <= len(line) <= 90, line
        assert line.strip() == line


def test_handoff_line_never_repeats_back_to_back():
    rng = random.Random(3)
    last = HANDOFF_LINES[0]
    for _ in range(100):
        chosen = pick_handoff_line(rng, last=last)
        assert chosen in HANDOFF_LINES and chosen != last
        last = chosen


def test_wait_lines_exist_and_rotate():
    assert len(WAIT_LINES) >= 2
    rng = random.Random(5)
    last = WAIT_LINES[0]
    chosen = pick_wait_line(rng, last=last)
    assert chosen in WAIT_LINES and chosen != last


# --------------------------------------------------------------------------
# is_stop_phrase
# --------------------------------------------------------------------------

@pytest.mark.parametrize("text", ["stop", "Stop.", "cancel that", "Never mind!", "nevermind", "forget it", " STOP IT "])
def test_stop_phrases(text):
    assert is_stop_phrase(text) is True


@pytest.mark.parametrize("text", ["", "stop the music in the kitchen", "don't stop", "what's the weather", "cancel my 3pm"])
def test_ordinary_requests_are_not_stop_phrases(text):
    assert is_stop_phrase(text) is False
