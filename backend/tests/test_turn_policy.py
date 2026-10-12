"""``TurnPolicy`` (tools-spec §5.2): what a turn may do, enforced by the
executor instead of by hiding tools.

The toolset is the same 14 tools on every surface; a wake that may not
reach people gets an honest refusal, never a missing tool. A policy that
nobody set is fail-closed for every turn Ellis is not part of.
"""

from __future__ import annotations

import pytest

from services.toolset import policy as P

pytestmark = pytest.mark.unit


class TestDefaults:
    @pytest.mark.parametrize("source", ["chat", "voice", "widget", "alexa", "ping_owner"])
    def test_owners_own_turns_may_do_everything(self, source):
        p = P.default_policy(source)
        assert (p.owner_asked, p.may_reach_people, p.may_push) == (True, True, True)

    @pytest.mark.parametrize("source", ["mind", "pulse", "heartbeat", "worker", "background", "ping", None, "",
                                        "something-new"])
    def test_everything_else_is_fail_closed(self, source):
        p = P.default_policy(source)
        assert (p.owner_asked, p.may_reach_people, p.may_push) == (False, False, False)

    def test_twilio_replies_go_out_from_the_router(self):
        p = P.default_policy("twilio")
        assert (p.owner_asked, p.may_reach_people, p.may_push) == (False, False, False)

    def test_a_scheduled_event_owner_made_is_his(self):
        p = P.default_policy("scheduled_event")
        assert (p.owner_asked, p.may_reach_people, p.may_push) == (True, True, True)

    def test_a_scheduled_event_edward_made_is_his_own_work(self):
        """``scheduler_service`` runs an event Ellis did not make under
        ``llm_spend.own_work_scope`` (``event_is_owners``) — the stamp."""
        from services import llm_spend

        with llm_spend.own_work_scope():
            p = P.default_policy("scheduled_event")
        assert (p.owner_asked, p.may_reach_people, p.may_push) == (False, True, True)

    def test_reply_to_is_a_tuple_of_strings(self):
        p = P.TurnPolicy(source="ping", reply_to=["chat1", None, "chat2"])
        assert p.reply_to == ("chat1", "chat2")

    def test_a_ping_reply_turn_posts_its_final_text(self):
        """AREA 3's runner posts the final text of a must-answer ping, so the
        reply check never forces an extra round there (critique #23)."""
        assert P.TurnPolicy(source="ping", reply_to=("c",)).final_text_answers()
        assert P.TurnPolicy(source="ping_owner", reply_to=("c",)).final_text_answers()
        assert not P.TurnPolicy(source="mind", reply_to=("c",)).final_text_answers()
        assert not P.TurnPolicy(source="ping", reply_to=("c",), final_text_is_reply=False).final_text_answers()

    def test_third_party_grants_the_full_toolset(self):
        """Ellis, 2026-10-11 06:30: friends' pings run with the full toolset.
        The field exists so AREA 3's runner runs them; it narrows nothing."""
        p = P.TurnPolicy(source="ping", third_party=True)
        assert p.third_party is True
        assert P.scope_refusal(p, "bash", {"command": "ls"}) is None
        assert P.scope_refusal(p, "chrome", {"action": "open"}) is None


class TestTheTurnDollarCap:
    def test_defaults_by_whose_turn(self, monkeypatch):
        monkeypatch.delenv("EDWARD_TURN_MAX_USD", raising=False)
        monkeypatch.delenv("EDWARD_AUTONOMOUS_TURN_MAX_USD", raising=False)
        assert P.TurnPolicy(source="chat", owner_asked=True).turn_cap_usd() == 3.0
        assert P.TurnPolicy(source="mind").turn_cap_usd() == 1.0

    def test_env_and_explicit(self, monkeypatch):
        monkeypatch.setenv("EDWARD_TURN_MAX_USD", "5")
        monkeypatch.setenv("EDWARD_AUTONOMOUS_TURN_MAX_USD", "0.5")
        assert P.TurnPolicy(source="chat", owner_asked=True).turn_cap_usd() == 5.0
        assert P.TurnPolicy(source="mind").turn_cap_usd() == 0.5
        assert P.TurnPolicy(source="mind", max_turn_usd=2.0).turn_cap_usd() == 2.0

    def test_a_bad_env_value_falls_back(self, monkeypatch):
        monkeypatch.setenv("EDWARD_TURN_MAX_USD", "lots")
        assert P.TurnPolicy(source="chat", owner_asked=True).turn_cap_usd() == 3.0


class TestTheCurrentPolicy:
    def test_scope_sets_and_resets(self):
        p = P.TurnPolicy(source="mind", may_push=True)
        assert P.policy_is_set() is False
        with P.policy_scope(p):
            assert P.current_policy() is p
            assert P.policy_is_set() is True
        assert P.policy_is_set() is False

    def test_outside_a_scope_it_follows_the_turn_ledger(self):
        from services import turn_ledger

        rec = turn_ledger.begin_turn(conversation_id="c", source="mind", model="m", provider="p", message="x")
        try:
            assert P.current_policy().source == "mind"
            assert P.current_policy().may_reach_people is False
        finally:
            turn_ledger.end_turn(rec, "completed")

    def test_with_no_turn_at_all_it_is_fail_closed(self):
        assert P.current_policy().may_reach_people is False


class TestLegacyCallers:
    """A caller that still passes ``allowed_tools`` / ``required_tools`` (AREA
    1 and 3 before they merge) keeps working under v2."""

    def test_a_mind_wake_with_send_tools_may_reach_people(self):
        p = P.legacy_policy("mind", allowed_tools=["send_imessage", "send_push_notification", "remember"])
        assert (p.owner_asked, p.may_reach_people, p.may_push) == (False, True, True)

    def test_a_pulse_with_no_send_tools_may_not(self):
        p = P.legacy_policy("pulse", allowed_tools=["compose_screen", "update_widget"])
        assert (p.may_reach_people, p.may_push) == (False, False)

    def test_required_send_turns_on_the_reply_check(self):
        p = P.legacy_policy("mind", allowed_tools=["send_imessage"], required_tools=["send_imessage"])
        assert p.wants_reply() and p.any_reply

    def test_a_heartbeat_mention_reply_may_answer(self):
        p = P.legacy_policy("heartbeat", required_tools=["send_imessage"])
        assert p.may_reach_people and p.any_reply and not p.owner_asked
        assert P.legacy_policy("heartbeat").may_reach_people is False

    def test_no_legacy_arguments_is_the_default(self):
        assert P.legacy_policy("chat") == P.default_policy("chat")

    def test_a_free_time_list_without_schedule_tools_may_not_schedule(self):
        """A free-time run's event would fire later with a fired event's
        reach (mind_service, 2026-09-26; the surplus lists went with AREA 1,
        free time kept as one run a day). Its list carries neither
        schedule_event nor wake_me."""
        from services import mind_service

        free = P.legacy_policy("mind", allowed_tools=list(mind_service.LEGACY_FREE_TOOLS))
        assert (free.may_schedule, free.may_wake_later) == (False, False)
        pulse = P.legacy_policy("pulse", allowed_tools=["compose_screen", "update_widget"])
        assert (pulse.may_schedule, pulse.may_wake_later) == (False, False)

    def test_the_nightly_keeps_its_self_wake(self):
        """The silent nightly (AREA 1; it replaced reflect_lite): no
        ``schedule_event``, but ``wake_me`` stays."""
        from services import mind_service

        p = P.legacy_policy("mind", allowed_tools=list(mind_service.LEGACY_QUIET_TOOLS))
        assert (p.may_schedule, p.may_wake_later) == (False, True)

    def test_a_wake_with_schedule_event_may_schedule(self):
        from services import mind_service

        p = P.legacy_policy("mind", allowed_tools=list(mind_service.LEGACY_WAKE_TOOLS))
        assert (p.may_schedule, p.may_wake_later) == (True, True)
        assert (P.default_policy("chat").may_schedule, P.default_policy("mind").may_schedule) == (True, True)


class TestChromeRails:
    """tools-spec §3.6 and critique #17. Typing outside a search box, and
    clicking Send/Post/Delete in Ellis's logged-in sessions, act as Ellis: only
    when he asked in this turn. Payments are his in every turn."""

    OWNER = P.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True, may_push=True)
    WAKE = P.TurnPolicy(source="mind")

    def test_typing_into_a_search_site_is_fine_from_a_wake(self):
        assert P.chrome_action_allowed("type", {"text": "lofi piano"}, self.WAKE,
                                       current_url="https://www.youtube.com/") is None

    def test_typing_on_edwards_own_screen_is_fine(self, monkeypatch):
        # His own hosts come from host_config (the public URL, localhost, this Mac).
        monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.com")
        monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
        assert P.chrome_action_allowed("type", {"text": "hi"}, self.WAKE,
                                       current_url="https://edward.example.com/screen") is None
        assert P.chrome_action_allowed("type", {"text": "hi"}, self.WAKE,
                                       current_url="http://localhost:3000/screen") is None

    def test_an_unconfigured_public_host_is_not_his_screen(self, monkeypatch):
        monkeypatch.delenv("EDWARD_PUBLIC_BASE_URL", raising=False)
        monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
        assert P.chrome_action_allowed("type", {"text": "hi"}, self.WAKE,
                                       current_url="https://edward.example.com/screen") is not None

    def test_typing_elsewhere_needs_owner(self):
        out = P.chrome_action_allowed("type", {"text": "hello"}, self.WAKE, current_url="https://mail.google.com/")
        assert out.startswith("Tool error:") and "mail.google.com" in out
        assert P.chrome_action_allowed("type", {"text": "hello"}, self.OWNER,
                                       current_url="https://mail.google.com/") is None

    def test_typing_where_the_page_is_unknown_needs_owner(self):
        assert P.chrome_action_allowed("type", {"text": "hello"}, self.WAKE) is not None

    def test_steps_are_inspected(self):
        steps = [{"action": "open", "url": "https://www.google.com"}, {"action": "type", "text": "weather"},
                 {"action": "click", "ref": 3}, {"action": "type", "text": "hi bob"}]
        out = P.chrome_action_allowed("steps", {"steps": steps}, self.WAKE)
        assert out is not None
        assert P.chrome_action_allowed("steps", {"steps": steps[:2]}, self.WAKE) is None

    @pytest.mark.parametrize("label", ["Place order", "Buy now", "Pay", "Checkout", "Confirm payment", "Add card",
                                       "Submit order", "Payment"])
    def test_payment_steps_are_refused_in_every_turn(self, label):
        for policy in (self.OWNER, self.WAKE):
            out = P.chrome_action_allowed("click", {"ref": 4}, policy, element_text=label)
            assert out is not None and "payment" in out.lower()

    @pytest.mark.parametrize("label", ["Paywall FAQ", "payload", "Paypal-free help", "Display settings"])
    def test_the_payment_words_are_word_bounded(self, label):
        assert P.chrome_action_allowed("click", {"ref": 4}, self.OWNER, element_text=label) is None

    def test_a_card_number_is_refused_in_every_turn(self):
        out = P.chrome_action_allowed("type", {"text": "4111 1111 1111 1111"}, self.OWNER,
                                      current_url="https://shop.example.com/")
        assert out is not None and "payment" in out.lower()
        # Not a Luhn number: a phone number is typed fine.
        assert P.chrome_action_allowed("type", {"text": "201 555 0100"}, self.OWNER,
                                       current_url="https://shop.example.com/") is None

    @pytest.mark.parametrize("label", ["Send", "Post", "Reply all", "Delete", "Unsubscribe", "Sign out", "Accept"])
    def test_acting_clicks_need_owner(self, label):
        assert P.chrome_action_allowed("click", {"ref": 2}, self.WAKE, element_text=label) is not None
        assert P.chrome_action_allowed("click", {"ref": 2}, self.OWNER, element_text=label) is None

    def test_reading_and_opening_are_always_fine(self):
        for action in ("tabs", "open", "read", "find", "scroll", "back", "wait", "done", "use"):
            assert P.chrome_action_allowed(action, {"url": "https://example.com"}, self.WAKE) is None
