"""A tool call written as text never reaches a chat (benchmark-results §6.1).

With no tool definitions in the request, 44 of 47 ping replies wrote
``send_message`` as XML in their text, and the ping runner — which posts a
turn's final text when the turn did not send — would have posted that into
a friend's chat. ``services.tool_markup.clean_reply`` strips every shape of
a tool call written as text (the XML the bench saw, antml-style tags, a
call rendered as JSON, a ``send_message(...)`` call in prose) and says
whether anything worth posting is left.
"""

from __future__ import annotations

import pytest

from services import tool_markup

pytestmark = pytest.mark.unit

A = "antml:"  # built up so this file never holds a live-looking tag


def _invoke(name="send_message", chat="chat100000000000000001", message="Happy to, Parker. Saturday works."):
    return (f'<invoke name="{name}">\n<parameter name="chat_id">{chat}</parameter>\n'
            f'<parameter name="message">{message}</parameter>\n</invoke>')


# ------------------------------------------------ what the bench saw ----

@pytest.mark.parametrize("text", [
    _invoke(),
    "<function_calls>\n" + _invoke() + "\n</function_calls>",
    f"<{A}function_calls>\n<{A}invoke name=\"send_message\">\n<{A}parameter name=\"message\">Hi</{A}parameter>\n"
    f"</{A}invoke>\n</{A}function_calls>",
    '<invoke_tool>\n<parameter name="name">send_message</parameter>\n<parameter name="arguments">'
    '{"chat_id": "chat1", "message": "Hi"}</parameter>\n</invoke_tool>',
    '<invoke_tool name="send_message">\n<parameter name="message">Rewrites below</parameter>\n</invoke_tool>',
    '<invoke name="send_message">\n<arg name="chat_id">chat1</arg>\n<arg name="message">Happy to</arg>\n</invoke>',
    '<send_message chat_id="chat1">Hi Parker</send_message>',
    '<tool_call>{"name": "send_message", "arguments": {"chat_id": "chat1", "message": "Hi"}}</tool_call>',
])
def test_a_reply_that_is_only_a_tool_call_leaves_nothing_to_post(text):
    out = tool_markup.clean_reply(text)
    assert out.found
    assert not out.meaningful
    assert "<" not in out.text and "send_message" not in out.text


def test_a_lead_in_before_the_call_is_not_an_answer():
    out = tool_markup.clean_reply("Sending it to the group now:\n\n" + _invoke())
    assert out.found and not out.meaningful


def test_an_unterminated_call_is_cut_to_the_end():
    out = tool_markup.clean_reply('Sure!\n<invoke name="send_message">\n<parameter name="message">Hi the')
    assert out.found and "invoke" not in out.text and "Hi the" not in out.text
    assert not out.meaningful


def test_the_answer_around_a_call_is_kept():
    out = tool_markup.clean_reply("The cheapest is $85 at the box office, row 12.\n\n" + _invoke())
    assert out.found and out.meaningful
    assert out.text == "The cheapest is $85 at the box office, row 12."


# ------------------------------------------------------ JSON and prose ----

@pytest.mark.parametrize("text", [
    '{"name": "send_message", "arguments": {"chat_id": "chat1", "message": "On my way"}}',
    '```json\n{"tool": "tell_owner", "input": {"message": "Parker asked about Saturday"}}\n```',
    '{"type": "tool_use", "name": "memory", "input": {"action": "search", "query": "Saturday"}}',
    'send_message(chat_id="chat1", message="On my way, 10 minutes")',
    "I'll text her: `send_message(to=\"Ana\", message=\"See you at 7 (not 8)\")`",
    'functions.tell_owner({"message": "Parker asked"})',
])
def test_a_call_written_as_json_or_code_leaves_nothing_to_post(text):
    out = tool_markup.clean_reply(text)
    assert out.found, text
    assert not out.meaningful, out.text


def test_prose_around_a_json_call_is_kept():
    out = tool_markup.clean_reply('Saturday works — 10 am at the field.\n'
                                  '{"name": "send_message", "arguments": {"message": "Saturday works"}}')
    assert out.found and out.meaningful
    assert out.text == "Saturday works — 10 am at the field."


# ---------------------------------------------------- left alone ----

@pytest.mark.parametrize("text", [
    "The cheapest is $85 at the box office.",
    "Wrap it in a <b> tag and it shows bold.",
    "I'll send a message (soon) once I hear back.",
    "send_message is how I text people; I didn't use it here.",
    'The config is {"retries": 3, "name": "backoff"}.',
    "Parameters: two eggs, one cup of flour.",
    "Use 5 < 7 and 9 > 3 to check.",
])
def test_ordinary_text_is_left_alone(text):
    out = tool_markup.clean_reply(text)
    assert not out.found
    assert out.text == text.strip()
    assert out.meaningful


def test_empty_is_not_meaningful_and_found_nothing():
    for text in ("", "   ", None):
        out = tool_markup.clean_reply(text)
        assert not out.found and not out.meaningful and out.text == ""


def test_safe_reply_falls_back_only_when_nothing_is_left():
    fallback = "Sorry — my answer came out garbled."
    assert tool_markup.safe_reply(_invoke(), fallback) == fallback
    assert tool_markup.safe_reply("Here you go: 7 pm.\n" + _invoke(), fallback) == "Here you go: 7 pm."
    assert tool_markup.safe_reply("Plain answer.", fallback) == "Plain answer."
    assert tool_markup.has_tool_markup(_invoke()) and not tool_markup.has_tool_markup("Plain answer.")


def test_a_call_removed_mid_sentence_leaves_no_double_space():
    out = tool_markup.clean_reply('Saturday works — `send_message(message="yes")` see you at the field, 10 am.')
    assert out.found and out.meaningful
    assert out.text == "Saturday works — see you at the field, 10 am."
