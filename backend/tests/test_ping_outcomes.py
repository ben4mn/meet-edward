"""How a ping turn ended, and what the runner does next (AREA 3, 2026-10-11).

``services/pings/outcomes.decide`` is pure: it reads the ping's shared
context (the sends the transport noted, the tools AREA 2's loop noted, the
loop's stop record), the turn's final text and its exception, and says
answered / declined / retry / deliver this text or that template. First
match wins (pings-media-spec §2.8). The rules that close every
double-reply and wrong-reply path are pinned here:

- a delivered send answers the ping whatever failed afterwards;
- a stand-down or ``tell_owner`` is a decline, and nothing is posted;
- an honest ending the loop wrote over a model failure is never posted;
- a refusal note or a ``max_tokens`` partial gets a template, not the text;
- a may-answer turn that sent nothing is declined.
"""

from __future__ import annotations

import asyncio

import pytest

from services.pings import outcomes
from services.pings.hooks import new_context


def _ctx(*, must_answer=True, third_party=False, chat="chat123", is_group=True, kind=None):
    return new_context(ping_id=7, chat_identifier=chat, chat_guid=f"iMessage;+;{chat}", is_group=is_group,
                       conversation_id="ping-7", must_answer=must_answer, third_party=third_party, kind=kind)


def _self_chat():
    """Ellis's own chat with himself: the only place the cap's sentence goes."""
    return _ctx(chat="+12015550100", is_group=False, kind="owner_direct")


def _never_transient(_exc):
    return False


def _always_transient(_exc):
    return True


def decide(ctx, text, error=None, *, attempts=1, transient=_never_transient):
    return outcomes.decide(ctx, text, error, attempts=attempts, is_transient=transient)


class LLMSpendCapReached(Exception):
    """Same class name as services.llm_spend's: the rule matches by name."""


# ------------------------------------------------------------ row 1 ----

def test_an_ok_send_answers_the_ping():
    ctx = _ctx()
    ctx["sends"].append({"ok": True, "purpose": "tool", "rowid": 41, "route": "chat_id", "error": None, "at": "t"})
    out = decide(ctx, "Here you go")
    assert out.status == "answered"
    assert out.send["rowid"] == 41


def _sent(ctx, rowid=41):
    ctx["sends"].append({"ok": True, "purpose": "tool", "rowid": rowid, "route": "chat_id", "error": None, "at": "t"})
    return ctx


def test_a_send_then_an_error_was_progress_the_unfinished_note_goes_out_and_it_is_never_rerun():
    """Review 2026-10-11: "On it" then a crash used to close the ping as
    answered, and the chat saw only "On it". A send the turn did not finish
    after is progress: the honest note follows it, and the turn never
    runs again (a second answer would be a double reply)."""
    out = decide(_sent(_ctx()), "", RuntimeError("boom after the send"), transient=_always_transient)
    assert out.status == "deliver" and out.purpose == "fallback"
    assert out.template == "unfinished" and out.detail == "RuntimeError"
    assert out.send is not None and out.send["rowid"] == 41


def test_a_progress_send_then_the_turn_bound_gets_the_unfinished_timeout_note():
    out = decide(_sent(_ctx()), None, asyncio.TimeoutError())
    assert out.status == "deliver" and out.template == "unfinished_timeout"


def test_a_send_then_an_honest_ending_is_unfinished_and_the_ending_is_never_posted():
    ctx = _sent(_ctx())
    out = decide(ctx, "I stopped before finishing: the model did not answer within 300s. Done so far: send_imessage.")
    assert out.status == "deliver" and out.template == "unfinished" and out.text is None


def test_a_send_then_the_loops_error_stop_is_unfinished():
    ctx = _sent(_ctx())
    ctx["stop"].update(outcome="error", error_class="LLMProviderError", retryable=True)
    out = decide(ctx, "", None, attempts=1)
    assert out.status == "deliver" and out.template == "unfinished"


def test_a_send_then_a_max_tokens_partial_is_unfinished():
    ctx = _sent(_ctx())
    ctx["stop"].update(incomplete_reason="max_tokens")
    out = decide(ctx, "and the cheapest is on Viv")
    assert out.template == "unfinished"


def test_a_send_then_the_cap_says_the_cap():
    out = decide(_sent(_self_chat()), "", LLMSpendCapReached("I've reached today's budget of $5."))
    assert out.status == "deliver" and out.template == "spend_cap"


def test_a_send_then_the_cap_in_a_group_never_posts_the_dollars():
    """Review 2026-10-11: the cap's sentence carries Ellis's spend and an env
    var; in a group the plain line goes, and Ellis gets the sentence."""
    sentence = "I've spent $8.12 on Claude today — raising EDWARD_DAILY_SPEND_CAP_USD lets me back in sooner."
    out = decide(_sent(_ctx()), "", LLMSpendCapReached(sentence))
    assert out.status == "deliver" and out.template == "spend_cap_group"
    assert out.detail == sentence


def test_a_send_then_a_graceful_deadline_posts_what_the_turn_said():
    ctx = _sent(_ctx())
    ctx["stop"].update(outcome="deadline")
    out = decide(ctx, "Here's what I found so far: Saturday from $142.")
    assert out.status == "deliver" and out.purpose == "reply"
    assert out.text == "Here's what I found so far: Saturday from $142."


def test_a_send_on_a_may_answer_ping_then_a_crash_still_gets_the_note():
    out = decide(_sent(_ctx(must_answer=False)), "", KeyError("x"))
    assert out.status == "deliver" and out.template == "unfinished"


def test_a_send_still_landing_when_the_turn_was_cut_is_the_answer():
    """Path (d): the turn's last act was a send still inside osascript when
    the bound came; the runner waited for it. That send is the answer, and
    nothing follows it."""
    ctx = _ctx()
    ctx["ended_at"] = "2026-10-11 10:00:00"
    ctx["sends"].append({"ok": True, "purpose": "tool", "rowid": 41, "route": "chat_id", "error": None,
                         "at": "2026-10-11 10:00:01"})
    out = decide(ctx, None, asyncio.TimeoutError())
    assert out.status == "answered" and out.send["rowid"] == 41


def test_a_send_and_a_clean_end_is_answered_whatever_the_final_text():
    out = decide(_sent(_ctx()), "Sent it.")
    assert out.status == "answered" and out.send["rowid"] == 41


def test_an_ack_send_does_not_answer():
    ctx = _ctx()
    ctx["sends"].append({"ok": True, "purpose": "ack", "rowid": 41, "route": "chat_id", "error": None, "at": "t"})
    out = decide(ctx, "The answer is 4")
    assert out.status == "deliver" and out.purpose == "reply"


def test_a_failed_send_does_not_answer():
    ctx = _ctx()
    ctx["sends"].append({"ok": False, "purpose": "tool", "rowid": None, "route": None, "error": "-1728", "at": "t"})
    out = decide(ctx, "The answer is 4")
    assert out.status == "deliver" and out.text == "The answer is 4"


# ------------------------------------------------------------ row 2 ----

def test_a_stand_down_for_this_chat_declines():
    ctx = _ctx()
    ctx["tools"].append({"name": "messages", "action": "stand_down", "chat": "any;+;chat123", "urgent": False,
                         "ok": True})
    out = decide(ctx, "I'll stay out of this one.")
    assert out.status == "declined"


def test_a_stand_down_for_another_chat_does_not_decline():
    ctx = _ctx()
    ctx["tools"].append({"name": "messages", "action": "stand_down", "chat": "chat999", "urgent": False,
                         "ok": True})
    out = decide(ctx, "Answer")
    assert out.status == "deliver"


def _told_owner(ctx):
    ctx["tools"].append({"name": "tell_owner", "action": None, "chat": None, "urgent": False, "ok": True})
    return ctx


def test_tell_owner_then_a_reply_delivers_the_reply():
    """Review 2026-10-11 (verified e2e): the prompt says "say you're checking
    with Ellis, and tell_owner" and promises the final message is posted; a
    successful tell_owner used to decline the ping and leave Parker on read."""
    out = decide(_told_owner(_ctx(third_party=True)), "I'm checking with Ellis and will let you know.")
    assert out.status == "deliver" and out.purpose == "reply"
    assert out.text == "I'm checking with Ellis and will let you know."


def test_tell_owner_then_a_reply_on_owners_own_ping_delivers_it_too():
    out = decide(_told_owner(_ctx()), "Only you can say — I've put it on your list.")
    assert out.status == "deliver" and out.purpose == "reply"


def test_tell_owner_with_no_final_text_declines():
    out = decide(_told_owner(_ctx()), "   ")
    assert out.status == "declined" and out.why == "tell_owner"


def test_tell_owner_with_only_a_written_tool_call_declines():
    out = decide(_told_owner(_ctx()), _LEAK)
    assert out.status == "declined" and out.why == "tell_owner"


def test_tell_owner_on_a_may_answer_ping_declines():
    out = decide(_told_owner(_ctx(must_answer=False)), "Checking with Ellis.")
    assert out.status == "declined" and out.why == "tell_owner"


def test_a_stand_down_and_tell_owner_declines_whatever_the_final_text():
    """MUST_ANSWER: stand_down for this chat, then tell_owner — nothing is posted."""
    ctx = _told_owner(_ctx())
    ctx["tools"].insert(0, {"name": "stand_down", "action": None, "chat": "chat123", "urgent": False, "ok": True})
    out = decide(ctx, "Told Ellis what Parker asked.")
    assert out.status == "declined" and out.why == "stand_down"


def test_tell_owner_then_a_stand_down_declines_too():
    ctx = _told_owner(_ctx())
    ctx["tools"].append({"name": "stand_down", "action": None, "chat": "chat123", "urgent": False, "ok": True})
    out = decide(ctx, "Told Ellis what Parker asked.")
    assert out.status == "declined" and out.why == "stand_down"


def test_tell_owner_then_a_failure_declines_and_never_retries():
    """Ellis was told; a retry would tell him twice."""
    out = decide(_told_owner(_ctx()), "", RuntimeError("overloaded"), transient=_always_transient)
    assert out.status == "declined" and out.why == "tell_owner"


def test_a_failed_tell_owner_does_not_decline():
    ctx = _ctx()
    ctx["tools"].append({"name": "tell_owner", "action": None, "chat": None, "urgent": False, "ok": False})
    out = decide(ctx, "Answer")
    assert out.status == "deliver"


# ------------------------------------------------------------ row 3 ----

def test_the_cap_on_owners_ping_in_his_own_chat_delivers_area4s_sentence():
    out = decide(_self_chat(), "", LLMSpendCapReached("I've reached today's budget of $20."))
    assert out.status == "deliver" and out.purpose == "fallback"
    assert out.template == "spend_cap"
    assert out.detail == "I've reached today's budget of $20."


@pytest.mark.parametrize("ctx_kw", [{}, {"is_group": False, "kind": "mention"}, {"is_group": False}])
def test_the_cap_on_owners_ping_anywhere_else_posts_the_plain_line(ctx_kw):
    """Review 2026-10-11: friends read a group (or a 1:1 with Ellis's friend):
    they get the plain line; the sentence — Ellis's dollars, an env var — goes
    to Ellis (the runner's ``spend_cap_group`` note)."""
    sentence = "I've spent $8.12 on Claude today — past my $5.00 daily cap and the $3.00 I keep for you."
    out = decide(_ctx(**ctx_kw), "", LLMSpendCapReached(sentence))
    assert out.status == "deliver" and out.template == "spend_cap_group"
    assert out.detail == sentence


def test_the_cap_on_a_friends_ping_uses_the_friend_template():
    out = decide(_ctx(third_party=True), "", LLMSpendCapReached("I've reached today's budget."))
    assert out.template == "spend_cap_friend"


def test_the_cap_from_the_loops_stop_record():
    ctx = _ctx(third_party=True)
    ctx["stop"].update(outcome="cap")
    out = decide(ctx, "partial words")
    assert out.status == "deliver" and out.template == "spend_cap_friend"


def test_the_cap_on_a_may_answer_ping_declines_and_never_retries():
    out = decide(_ctx(must_answer=False), "", LLMSpendCapReached("cap"), transient=_always_transient)
    assert out.status == "declined"


# ------------------------------------------------------------ row 4 ----

def test_a_transient_error_retries_once():
    out = decide(_ctx(), "", RuntimeError("overloaded"), attempts=1, transient=_always_transient)
    assert out.status == "retry"


def test_the_loops_retryable_stop_retries():
    ctx = _ctx()
    ctx["stop"].update(error_class="LLMProviderError", retryable=True, outcome="honest_ending")
    out = decide(ctx, "Sorry, my AI service failed.", None, attempts=1)
    assert out.status == "retry"


def test_after_two_attempts_a_transient_error_gets_the_provider_template():
    out = decide(_ctx(), "", RuntimeError("overloaded"), attempts=2, transient=_always_transient)
    assert out.status == "deliver" and out.purpose == "fallback"
    assert out.template == "provider"
    assert out.detail == "RuntimeError"


# ------------------------------------------------------------ row 5 ----

def test_a_timeout_on_a_must_answer_ping_gets_the_timeout_template():
    out = decide(_ctx(), "", asyncio.TimeoutError())
    assert out.template == "timeout" and out.purpose == "fallback"


def test_a_timeout_on_a_may_answer_ping_declines():
    out = decide(_ctx(must_answer=False), "", asyncio.TimeoutError())
    assert out.status == "declined"


# ------------------------------------------------------------ row 6 ----

def test_any_other_error_gets_the_error_template_with_the_class_name_only():
    out = decide(_ctx(), "", ValueError("secret detail with someone's message"))
    assert out.template == "error"
    assert out.detail == "ValueError"


def test_an_honest_ending_over_a_model_failure_is_never_posted():
    ctx = _ctx()
    ctx["stop"].update(error_class="LLMProviderError", retryable=False, outcome="honest_ending")
    out = decide(ctx, "Sorry — my model call failed (LLMProviderError).", None, attempts=2)
    assert out.status == "deliver" and out.purpose == "fallback"
    assert out.template == "error"
    assert out.text is None


LEGACY_HONEST_ENDING = "I stopped before finishing: the model did not answer within 300s. No tools ran."


def test_the_legacy_loops_honest_ending_is_never_posted_it_retries():
    """Review 2026-10-11: on this branch the legacy loop returns its honest
    ending as ordinary text on a model-call deadline, and rule 9 posted it
    into the group, signed."""
    out = decide(_ctx(), LEGACY_HONEST_ENDING, None, attempts=1)
    assert out.status == "retry"


def test_the_legacy_honest_ending_on_the_last_attempt_gets_the_provider_template():
    out = decide(_ctx(third_party=True), LEGACY_HONEST_ENDING, None, attempts=2)
    assert out.status == "deliver" and out.purpose == "fallback" and out.text is None
    assert out.template == "provider" and out.detail == "LLMCallTimeout"


def test_a_summary_call_failure_names_its_class():
    text = ("I stopped before finishing: it ran out of tool iterations (30), and the summary call failed "
            "(ReadTimeout). Done so far: web_search.")
    out = decide(_ctx(), text, None, attempts=2)
    assert out.template == "provider" and out.detail == "ReadTimeout"


def test_area2s_honest_ending_outcome_is_never_posted():
    ctx = _ctx()
    ctx["stop"].update(outcome="honest_ending", stop_reason="the model gave no answer after its tools")
    out = decide(ctx, "I stopped before finishing: the model gave no answer after its tools.", None, attempts=2)
    assert out.status == "deliver" and out.purpose == "fallback" and out.text is None


def test_an_honest_ending_on_a_may_answer_ping_declines():
    out = decide(_ctx(must_answer=False), LEGACY_HONEST_ENDING, None, attempts=2)
    assert out.status == "declined"


def test_another_error_on_a_may_answer_ping_declines():
    out = decide(_ctx(must_answer=False), "", KeyError("x"))
    assert out.status == "declined"


# ------------------------------------------------------------ row 7 ----

def test_a_may_answer_turn_with_no_send_declines_even_with_text():
    out = decide(_ctx(must_answer=False), "Probably meant for Ellis, staying out.")
    assert out.status == "declined"


# ------------------------------------------------------------ row 8 ----

def test_a_refusal_note_gets_the_refused_template():
    from services.claude_chat_model import REFUSAL_NOTE

    out = decide(_ctx(), "Some words.\n\n" + REFUSAL_NOTE)
    assert out.template == "refused"
    assert out.text is None


def test_a_max_tokens_partial_gets_the_incomplete_template():
    ctx = _ctx()
    ctx["stop"].update(incomplete_reason="max_tokens")
    out = decide(ctx, "The cheapest tickets are on Viv")
    assert out.template == "incomplete"


# ------------------------------------------------------------ rows 9, 10 ----

def test_final_text_with_no_send_is_delivered_as_the_reply():
    out = decide(_ctx(), "  The cheapest is $142 on TicketHub.  ")
    assert out.status == "deliver" and out.purpose == "reply"
    assert out.text == "The cheapest is $142 on TicketHub."


def test_no_text_and_no_send_gets_the_empty_template():
    out = decide(_ctx(), "   ")
    assert out.status == "deliver" and out.template == "empty"


def test_none_text_is_empty():
    out = decide(_ctx(), None)
    assert out.template == "empty"


@pytest.mark.parametrize("stop_outcome", ["deadline", "answered", "turn_budget"])
def test_a_graceful_stop_with_text_delivers_the_text(stop_outcome):
    ctx = _ctx()
    ctx["stop"].update(outcome=stop_outcome)
    out = decide(ctx, "Here's what I found so far.")
    assert out.status == "deliver" and out.purpose == "reply"


# ------------------------------------------- a tool call written as text ----
# benchmark-results §6.1: with no tools bound, 44 of 47 ping replies wrote
# send_message as XML in their text. The runner posts final text, so the
# markup must never reach the chat.

_LEAK = ('<invoke name="send_message">\n<parameter name="chat_id">chat123</parameter>\n'
         '<parameter name="message">Happy to, Jordan.</parameter>\n</invoke>')


def test_a_final_text_that_is_only_a_tool_call_gets_the_honest_template():
    out = decide(_ctx(), _LEAK)
    assert out.status == "deliver" and out.purpose == "fallback"
    assert out.template == "garbled"
    assert out.text is None


def test_the_answer_around_a_written_call_is_posted_without_it():
    out = decide(_ctx(), "Saturday works, 10 am at the field.\n\n" + _LEAK)
    assert out.status == "deliver" and out.purpose == "reply"
    assert out.text == "Saturday works, 10 am at the field."


def test_the_turns_own_budget_is_not_the_daily_cap():
    """Review 2026-10-11 (verified e2e): the loop's per-turn dollar cap ($1
    for a friend) recorded ``cap``, which read as the daily spend cap: the
    closing call's real answer was dropped for "I've hit my limit for today"
    and Ellis was told something went wrong."""
    ctx = _ctx(third_party=True)
    ctx["stop"].update(outcome="turn_budget")
    assert not outcomes.is_cap(None, ctx["stop"])
    out = decide(ctx, "Saturday tickets start at $142 on TicketHub; StubHub blocked me.")
    assert out.status == "deliver" and out.purpose == "reply"
    assert out.text.startswith("Saturday tickets start at $142")


def test_a_progress_send_then_the_turns_budget_posts_the_closing_words():
    ctx = _sent(_ctx(third_party=True))
    ctx["stop"].update(outcome="turn_budget")
    out = decide(ctx, "Got to two sites: Saturday from $142.")
    assert out.status == "deliver" and out.purpose == "reply" and out.text == "Got to two sites: Saturday from $142."


def test_the_turns_budget_with_no_closing_words_is_unfinished_without_a_class():
    ctx = _sent(_ctx())
    ctx["stop"].update(outcome="turn_budget")
    out = decide(ctx, _LEAK)
    assert out.template == "unfinished" and out.detail == ""


def test_a_graceful_deadline_after_progress_never_posts_a_written_call():
    ctx = _sent(_ctx())
    ctx["stop"].update(outcome="deadline")
    out = decide(ctx, _LEAK)
    assert out.purpose == "fallback" and out.template == "unfinished"
    assert out.text is None
