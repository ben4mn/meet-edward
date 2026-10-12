"""Unit tests for heartbeat push event flags."""

import json

from services.push_event_flags import (
    has_clear_reply_affordance,
    is_informational_airline_rbm_push,
    is_known_one_way_branded_rbm_sender,
    is_transactional_rbm_update_without_action_request,
    mark_no_reply_expected,
    no_reply_expected_from_raw_data,
    rbm_conversational_cue_score,
)


def test_united_rbm_sender_is_no_reply_expected():
    assert is_known_one_way_branded_rbm_sender(
        sender="notifications@rbm.goog",
        label="United Airlines",
    )

    raw = mark_no_reply_expected(
        json.dumps({"service": "RCS"}),
        reason="known_one_way_branded_rbm_sender",
    )

    parsed = json.loads(raw)
    assert parsed["service"] == "RCS"
    assert parsed["informational_push"] is True
    assert parsed["no_reply_expected"] is True
    assert no_reply_expected_from_raw_data(raw) is True


def test_normal_conversational_sender_is_reply_tracked():
    assert not is_known_one_way_branded_rbm_sender(
        sender="+15550006666",
        label="Rowan",
    )
    assert no_reply_expected_from_raw_data(json.dumps({"service": "iMessage"})) is False


def test_airline_rbm_agent_defaults_to_informational_without_reply_affordance():
    assert is_informational_airline_rbm_push(
        sender="united_airlines_example_agent@rbm.goog",
        label="United Airlines",
        chat_id="iMessage;-;united_airlines_example_agent@rbm.goog",
        text="Your United flight has arrived at gate B12.",
        raw_data=json.dumps({"service": "RCS"}),
    )


def test_airline_rbm_agent_with_choose_or_confirm_is_reply_tracked():
    assert not is_informational_airline_rbm_push(
        sender="united_airlines_example_agent@rbm.goog",
        label="United Airlines",
        chat_id="iMessage;-;united_airlines_example_agent@rbm.goog",
        text="Please choose a rebooking option or confirm your current flight.",
        raw_data=json.dumps({"service": "RCS"}),
    )


def test_reply_affordance_detects_question_and_quick_reply_chips():
    assert has_clear_reply_affordance(
        text="Would you like to confirm this flight?",
        raw_data=None,
    )
    assert has_clear_reply_affordance(
        text="Your flight changed.",
        raw_data=json.dumps({"quick_replies": [{"title": "Confirm"}]}),
    )


def test_transactional_rbm_update_without_action_request_is_suppressed():
    assert is_transactional_rbm_update_without_action_request(
        sender="brandsender@rbm.goog",
        text="Your package has been delivered.",
        raw_data=json.dumps({"service": "RCS"}),
    )
    assert rbm_conversational_cue_score(
        text="Your package has been delivered.",
        raw_data=json.dumps({"service": "RCS"}),
    ) == 0


def test_transactional_rbm_update_keeps_direct_questions_and_requests():
    assert not is_transactional_rbm_update_without_action_request(
        sender="brandsender@rbm.goog",
        text="Do you want to reschedule delivery?",
        raw_data=None,
    )
    assert not is_transactional_rbm_update_without_action_request(
        sender="brandsender@rbm.goog",
        text="Please confirm your delivery window.",
        raw_data=None,
    )
    assert not is_transactional_rbm_update_without_action_request(
        sender="+15550006666",
        text="Your package has been delivered.",
        raw_data=None,
    )
