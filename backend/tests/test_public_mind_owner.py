"""The mind names the owner from the environment, never a literal.

conftest sets ``EDWARD_OWNER_NAME=Ellis`` before any service imports, so
text rendered at import (``owner_text``) says "Ellis"; runtime text reads
``owner_name()`` on each call. The held-send sentinel is one constant the
prompt quotes and the journal matches, and the evals take only the model's
provider and key from ``.env``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 18, 14, 30, tzinfo=timezone.utc)


def _pack(**kw):
    from services.mind_service import MindPack

    base = dict(
        now=NOW, reason="manual", last_wake_at=None,
        attention={"owner_present": True, "active_surface": "iphone"},
        messages=[
            {"sender": "Parker", "chat_identifier": "chat1", "text": "dinner at 7?", "is_from_me": False,
             "timestamp": "2026-09-18T13:02:00"},
            {"sender": None, "chat_identifier": "chat1", "text": "works for me", "is_from_me": True,
             "timestamp": "2026-09-18T13:05:00"},
        ],
        calendar=[], board="", notes="", pushes="", verdicts=[], thoughts=[], actions=[],
        surfaces={}, escalations=[], state_document=None, extra=None,
        instructions=["  - Stay out of the Family chat. (said Sep 10)"],
    )
    base.update(kw)
    return MindPack(**base)


def test_the_mind_prompt_names_the_owner_and_holds_no_token():
    from services import mind_service as ms

    assert "{OWNER}" not in ms.MIND_SYSTEM_PROMPT
    assert "{OWNER}" not in ms.RULE_6_PINGS_OFF
    assert "Ellis" in ms.MIND_SYSTEM_PROMPT
    assert ms.RULE_6_PINGS_ON in ms.MIND_SYSTEM_PROMPT
    assert "Mac mini" not in ms.MIND_SYSTEM_PROMPT
    for pronoun in (" he ", " his ", " him "):
        assert pronoun not in ms.RULE_6_PINGS_OFF


def test_the_held_sentinel_is_one_text_the_prompt_quotes_and_the_journal_matches():
    from services import mind_service as ms
    from services import thought_journal as tj

    assert tj.HELD_PREFIX == "Waiting for Ellis's approval"
    assert f'"{tj.HELD_PREFIX} (#id)"' in ms.MIND_SYSTEM_PROMPT
    assert tj.action_ok("send_message", f"{tj.HELD_PREFIX} (#3): hi. Don't retry.") == (True, True)


def test_the_owners_own_lines_and_headings_use_their_name():
    from services import mind_service as ms

    assert ms.speaker({"is_from_me": True, "text": "hi"}) == "Ellis"
    assert ms.speaker({"is_from_me": True, "by_edward": True, "text": "hi"}) == "Edward"
    text = ms.render_pack(_pack())
    assert " Ellis: works for me" in text          # the owner's line carries no chat
    assert "Parker in chat1: dinner at 7?" in text and "Ellis in chat1" not in text
    assert ms.STANDING_INSTRUCTIONS_HEADING in text
    assert ms.STANDING_INSTRUCTIONS_HEADING.startswith("Standing instructions from Ellis — ")
    assert "Ellis's verdicts since your last wake:" in text
    assert "Pushed to Ellis's phone in the last 12h (every sender):" in text
    assert "Ellis is at iphone" in text
    assert "{OWNER}" not in text


def test_free_time_and_the_nightly_name_the_owner():
    from services import mind_service as ms

    free = "\n".join(ms.free_lines())
    assert "Ellis" in free and "{OWNER}" not in free and "Mac mini" not in free
    nightly = "\n".join(ms.nightly_lines(_pack(reason="nightly", now=datetime(2026, 9, 18, 8, 0,
                                                                               tzinfo=timezone.utc))))
    assert "about Ellis" in nightly and "until they confirm it" in nightly


def test_the_speak_line_names_nobody():
    from services import wake_state

    assert wake_state.SPEAK_LINE == "Something came up — I left a note."


def test_judgement_scenarios_name_the_configured_owner(monkeypatch):
    from services import judgement_eval as je

    monkeypatch.setenv("EDWARD_OWNER_NAME", "Sam")
    by_name = {s.name: s for s in je.scenarios()}
    assert "Sam" in by_name["mention_answerable"].calendar[0]
    assert by_name["nightly_silent"].messages[0]["text"].startswith("Sam are you coming")
    monkeypatch.delenv("EDWARD_OWNER_NAME")
    nightly = {s.name: s for s in je.scenarios()}["nightly_silent"]
    assert nightly.messages[0]["text"].startswith("hey are you coming")


# ---- the evals take only the model's settings from .env --------------------------------

def test_model_env_fills_only_what_the_shell_left_unset(tmp_path, capsys):
    from services import eval_guards as eg

    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport EDWARD_LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY='sk-ant-x'\n"
                        "DATABASE_URL=postgresql://nope\n")
    environ = {}
    assert eg.productions_model_env([env_file], environ) == "anthropic"
    assert environ == {"EDWARD_LLM_PROVIDER": "anthropic", "ANTHROPIC_API_KEY": "sk-ant-x"}
    assert str(env_file) in capsys.readouterr().out


def test_a_value_the_shell_set_wins_even_empty(tmp_path):
    from services import eval_guards as eg

    env_file = tmp_path / ".env"
    env_file.write_text("EDWARD_LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-ant-x\n")
    environ = {"EDWARD_LLM_PROVIDER": "codex", "ANTHROPIC_API_KEY": ""}
    assert eg.productions_model_env([env_file], environ) == "codex"
    assert environ == {"EDWARD_LLM_PROVIDER": "codex", "ANTHROPIC_API_KEY": ""}


def test_no_env_file_leaves_the_code_default(tmp_path, capsys):
    from services import eval_guards as eg

    environ = {}
    assert eg.productions_model_env([tmp_path / "missing.env"], environ) is None
    assert environ == {}
    assert "the code default" in capsys.readouterr().out


def test_install_for_eval_installs_the_guards_and_accepts_older_keywords(monkeypatch):
    from services import eval_guards as eg

    calls = []
    monkeypatch.setattr(eg, "productions_model_env", lambda *a, **k: calls.append("env"))
    monkeypatch.setattr(eg, "install", lambda **kw: calls.append(kw["run"]))
    assert eg.install_for_eval("ping", bench_kind="real_loop") == "in-repo"
    assert calls[0] == "env" and calls[1].startswith("ping-")
