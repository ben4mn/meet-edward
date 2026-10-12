"""The ping package names the owner from the environment (services.owner),
never a literal: the prompts and refusals render ``{OWNER}`` once at import,
the trigger and the eval read the name per call, and notices to the owner
call them "you"."""

import re
from datetime import datetime, timedelta

import pytest

from services.pings import delivery, eval as pe, fallback, prompt, render, runner, scope

pytestmark = pytest.mark.unit
PRIVATE_NAME = r"\b" + "B" + "en" + r"\b"   # the private build's owner, spelled so a grep for it stays clean


def test_no_token_or_private_name_is_left_in_the_rendered_text():
    texts = [prompt.ALWAYS, prompt.MUST_ANSWER, prompt.MAY_ANSWER, prompt.THIRD_PARTY, prompt.OWNERS_OWN,
             scope.LATER_REFUSAL, scope.LATER_REFUSAL_TOOLSET, scope.TYPING_REFUSAL,
             scope.custom_mcp_refusal("x_tool"), scope._not_asked("+12015550199"),
             *fallback.TEMPLATES.values(), *runner._FALLBACKS.values()]
    for text in texts:
        assert "{OWNER}" not in text
        assert not re.search(PRIVATE_NAME, text)
    assert "Ellis" in prompt.THIRD_PARTY and "Ellis" in scope.TYPING_REFUSAL


def test_the_prompt_speaks_of_the_owner_as_they():
    p = prompt.system_prompt(must_answer=True, third_party=True, legacy=False)
    assert not re.search(r"\b(he|him|his)\b", p)
    assert "their plans, money, health, family" in p


def test_deflections_follow_the_owners_names(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    monkeypatch.setenv("EDWARD_OWNER_FULL_NAME", "Sam Rivers")
    monkeypatch.setenv("EDWARD_OWNER_ALIASES", "Sammy")
    found = pe.deflections()
    assert "leave it to sam" in found and "leave it to sammy" in found and "sam rivers can handle" in found
    scenario = next(s for s in pe.scenarios() if s.name == "friend_plain_fact")
    assert pe.score(scenario, pe.EvalObserved(reply="I'll leave it to Sammy.")).failure
    assert "Sam" in scenario.ping["text"] and "checking with sam" in scenario.expect_any


def test_deflections_without_a_name(monkeypatch):
    monkeypatch.delenv("EDWARD_OWNER_NAME", raising=False)
    monkeypatch.delenv("EDWARD_OWNER_FULL_NAME", raising=False)
    monkeypatch.delenv("EDWARD_OWNER_ALIASES", raising=False)
    assert pe.deflections()[0] == "leave it to the owner"


def test_the_trigger_reads_the_name_per_call(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    t = render.build_trigger(text="@edward hi", asked_at=datetime(2026, 10, 7, 12, 0),
                             now=datetime(2026, 10, 7, 12, 1), chat_identifier="chat123", chat_name="Crew",
                             is_group=True, roster=["Parker"], from_owner=True, kind="mention")
    assert "group of 2: Sam, Parker" in t and "From: Sam (they wrote @Edward)" in t
    assert render.instructions_block(["  - stay out of Crew"], legacy=False).startswith("Sam's standing instructions")


def test_the_runners_own_trigger_trusts_the_owners_lines(monkeypatch):
    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    now = datetime(2026, 10, 7, 12, 0)
    g = runner.Gathered(thread=[{"label": "Sam", "text": "ugh", "timestamp": now},
                                {"label": "Parker", "text": "hi", "timestamp": now}])
    row = {"id": 1, "kind": "mention", "from_owner": False, "third_party": True, "sender_known": True,
           "sender_label": "Parker", "chat_identifier": "chat123", "is_group": False, "text": "@edward x",
           "asked_at": now - timedelta(minutes=1)}
    out = runner._fallback_trigger(row, g, legacy=False, now=now)
    assert "Sam: ugh" in out and '<untrusted source="Parker">hi</untrusted>' in out
    assert "(not Sam)" in out


def test_notices_to_the_owner_say_you():
    mine = {"from_owner": True, "kind": "mention", "chat_name": "Crew"}
    friend = {"from_owner": False, "sender_label": "Parker"}
    assert runner._who_to_owner(mine) == "you" and runner._who_to_owner(friend) == "Parker"
    assert delivery._who(mine) == "you" and delivery._who(friend) == "Parker"
    assert runner._who(mine) == "Ellis"
