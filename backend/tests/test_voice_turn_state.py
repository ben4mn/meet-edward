"""Server-side voice session reducer.

States: listening -> thinking -> speaking -> listening. Pure so the
barge-in rules can be pinned down without a socket in sight.
"""

from services.voice.turn_state import decide


def test_committed_while_listening_starts_a_turn():
    state, action = decide("listening", "committed", "What time is it?")
    assert state == "thinking"
    assert action == "start_turn"


def test_empty_commit_is_ignored():
    assert decide("listening", "committed", "   ") == ("listening", "ignore")


def test_partial_while_listening_is_forwarded_only():
    assert decide("listening", "partial", "What") == ("listening", "forward")


def test_first_audio_moves_to_speaking():
    assert decide("thinking", "first_audio") == ("speaking", "none")


def test_response_done_returns_to_listening():
    assert decide("speaking", "response_done") == ("listening", "none")
    assert decide("thinking", "response_done") == ("listening", "none")


def test_short_partial_while_speaking_is_not_a_barge_in():
    # Echo of Edward's own voice or a cough shows up as one stray word.
    assert decide("speaking", "partial", "Uh") == ("speaking", "forward")


def test_two_word_partial_while_speaking_barges_in():
    assert decide("speaking", "partial", "wait stop") == ("listening", "barge_in")


def test_committed_while_thinking_restarts_with_new_text():
    assert decide("thinking", "committed", "actually never mind") == ("thinking", "restart_turn")


def test_committed_while_speaking_restarts():
    assert decide("speaking", "committed", "one more thing please") == ("thinking", "restart_turn")


def test_explicit_interrupt_always_lands_in_listening():
    for s in ("listening", "thinking", "speaking"):
        assert decide(s, "interrupt") == ("listening", "cancel")


# --- barge-in threshold follows the room ------------------------------------

from services.voice.turn_state import barge_in_min_words


def test_barge_in_threshold_is_configurable():
    assert decide("speaking", "partial", "stop now", barge_in_min_words=3) == ("speaking", "forward")
    assert decide("speaking", "partial", "no stop now", barge_in_min_words=3) == ("listening", "barge_in")


def test_noisy_room_needs_three_words_to_interrupt():
    assert barge_in_min_words("low") == 3
    assert barge_in_min_words("normal") == 2
    assert barge_in_min_words("high") == 2
    assert barge_in_min_words(None) == 2
