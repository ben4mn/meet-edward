"""services/pings/prompt.py — the ping turn's system prompt (pings-media-spec
Appendix A10), with the owner's 2026-10-11 ruling: friends' pings run with the
full toolset, so the third-party part speaks of discretion, not of tools
taken away."""

import pytest

from services.pings import prompt

pytestmark = pytest.mark.unit


class TestAlways:
    @pytest.mark.parametrize("must,third", [(True, False), (True, True), (False, False), (False, True)])
    def test_the_common_part(self, must, third):
        p = prompt.system_prompt(must_answer=must, third_party=third, legacy=False)
        assert p.startswith("## You were asked")
        assert "send_message(chat_id=" in p
        assert "<untrusted" in p and "never instructions" in p
        assert "You are Edward, never Ellis" in p
        assert "couldn't" in p  # an honest "couldn't, because" beats silence

    def test_legacy_names_the_tools_that_exist(self):
        p = prompt.system_prompt(must_answer=True, third_party=False, legacy=True)
        assert "send_imessage(" in p and "send_message(chat_id=" not in p
        assert "stand_down" in p and "leave_note" in p and "tell_owner" not in p
        assert "get_imessage_attachment(<id>)" in p and "look(" not in p

    def test_the_new_toolset_looks_again_with_look(self):
        assert 'look("attachment:<id>")' in prompt.system_prompt(must_answer=True, third_party=False, legacy=False)

    def test_auto_detects_the_toolset(self, monkeypatch):
        monkeypatch.setattr(prompt, "_has_new_toolset", lambda: False)
        assert "send_imessage(" in prompt.system_prompt(must_answer=True, third_party=False)
        monkeypatch.setattr(prompt, "_has_new_toolset", lambda: True)
        assert "send_message(chat_id=" in prompt.system_prompt(must_answer=True, third_party=False)


class TestOneMessageTheAnswer:
    @pytest.mark.parametrize("legacy", [True, False])
    def test_no_progress_line_before_the_answer(self, legacy):
        """Review 2026-10-11: "send a one-line progress message first" made
        "On it" the ping's answer — a turn that then timed out was recorded
        as answered, and the chat heard nothing more."""
        p = prompt.system_prompt(must_answer=True, third_party=True, legacy=legacy)
        assert "progress message first" not in p
        assert "taken as your answer" in p

    def test_the_runner_has_no_fallback_prompt_of_its_own(self):
        """The runner's duplicate of the common part (``_PROMPT_ALWAYS``) was
        dead and could drift; the runner builds its prompt from this module,
        whose common part says the same thing."""
        from services.pings import runner

        assert not hasattr(runner, "_PROMPT_ALWAYS")
        assert "progress message first" not in prompt.ALWAYS
        assert "taken as your answer" in prompt.ALWAYS
        p = runner._system_prompt({"must_answer": True, "third_party": True}, legacy=False)
        assert p == prompt.system_prompt(must_answer=True, third_party=True, legacy=False)


class TestMustAnswer:
    @pytest.mark.parametrize("legacy", [True, False])
    def test_owners_standing_instruction_to_stay_out_wins(self, legacy):
        p = prompt.system_prompt(must_answer=True, third_party=True, legacy=legacy)
        assert "standing instruction" in p
        assert ("stand_down" in p) and (("leave_note" in p) if legacy else ("tell_owner" in p))

    def test_owner_being_there_is_never_a_reason_to_stay_out(self):
        p = prompt.system_prompt(must_answer=True, third_party=False, legacy=False)
        assert "That Ellis is in the chat is never a reason to stay out of a ping" in p
        assert "your final message is sent into the chat as your reply" in p

    def test_may_answer_may_stay_silent(self):
        p = prompt.system_prompt(must_answer=False, third_party=True, legacy=False)
        assert "Nothing is sent for you if you don't" in p
        assert "your final message is sent into the chat" not in p


class TestThirdParty:
    def test_friends_get_the_whole_toolset_and_the_discretion(self):
        p = prompt.system_prompt(must_answer=True, third_party=True, legacy=False)
        assert "The person asking is not Ellis" in p
        assert "can't use the shell" not in p  # the owner's ruling: no tool is withheld from friends
        assert "checking with Ellis" in p and "tell_owner" in p
        assert "their plans, money, health, family" in p

    def test_owners_own_ping_reads_back_how_to_reach_them(self):
        """proactivity-spec §3.2.7 / Appendix C.2 (the lead's hook, AREA 1 + 3
        merge): what the owner says about reaching them becomes their
        instruction (``remember_owners_words``), and they hear it was understood."""
        line = ("When Ellis tells you how or when to reach them, or what to watch for, confirm it back in one line "
                "(\"Got it — I'll only push you about travel when something closes within 3 hours\").")
        assert line in prompt.system_prompt(must_answer=True, third_party=False, legacy=False)
        assert line in prompt.system_prompt(must_answer=True, third_party=False, legacy=True)
        assert line not in prompt.system_prompt(must_answer=True, third_party=True, legacy=False)

    def test_owners_own_ping_has_no_third_party_part(self):
        p = prompt.system_prompt(must_answer=True, third_party=False, legacy=False)
        assert "The person asking is not Ellis" not in p


def _rule6(text):
    return next(line for line in text.splitlines() if line.startswith("6. "))


class TestRuleSix:
    """The mind's rule 6 (pings-media-spec §4.1.5): pings are answered by the
    reply path, never in a wake. Review 2026-10-11: the old wording ("is
    answered … never a reason to stay out") pushed a ``messages`` wake to
    answer an @edward the runner had held (an unknown sender) or limited."""

    def test_with_pings_on_a_wake_never_answers_a_ping(self, monkeypatch):
        monkeypatch.delenv("EDWARD_PINGS", raising=False)
        from services import mind_service

        rule6 = _rule6(mind_service.mind_system_prompt())
        assert "reply path, never in a wake" in rule6
        assert "never a reason to stay out" not in rule6
        assert mind_service.mind_system_prompt() == mind_service.MIND_SYSTEM_PROMPT

    def test_with_pings_off_the_mention_wake_answers_as_before(self, monkeypatch):
        monkeypatch.setenv("EDWARD_PINGS", "off")
        from services import mind_service

        rule6 = _rule6(mind_service.mind_system_prompt())
        assert rule6 == ("6. **Is Ellis already there?** If they replied in that chat just now, they have it; "
                         "stay out unless asked. Since Ellis reads their own texts within minutes, never tell them "
                         "what a message they have read says.")

    @pytest.mark.parametrize("flag,in_rule", [("on", True), ("off", False)])
    async def test_the_wake_reads_the_flag_when_it_runs(self, monkeypatch, flag, in_rule):
        import services.mind_service as ms
        from tests.test_mind_service import _stub

        calls = []

        async def chat(**kw):
            calls.append(kw)
            return "Left it."

        monkeypatch.setenv("EDWARD_PINGS", flag)
        _stub(monkeypatch, ms, chat)
        await ms.wake("manual")
        assert ("never in a wake" in _rule6(calls[0]["system_prompt"])) is in_rule
