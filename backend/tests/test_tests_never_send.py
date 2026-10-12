"""No test may send a real message (critique #1, 2026-10-11).

The toolset removed the taint approval hold from the send path, so the
suite itself refuses the programs that reach Ellis's Messages: ``osascript``
and ``shortcuts`` raise in every test (``conftest._never_run_osascript``),
through ``subprocess`` and asyncio alike. And the one send, driven end to
end with no transport patched, sends nothing.
"""

from __future__ import annotations

import asyncio
import subprocess

import pytest

pytestmark = pytest.mark.unit


def test_subprocess_refuses_osascript():
    with pytest.raises(RuntimeError, match="never run osascript"):
        subprocess.run(["/usr/bin/osascript", "-e", "return 1"], capture_output=True)
    with pytest.raises(RuntimeError, match="never run shortcuts"):
        subprocess.run("shortcuts run 'Send Message'", shell=False, capture_output=True)


async def test_asyncio_refuses_it_too():
    with pytest.raises(RuntimeError, match="never run osascript"):
        await asyncio.create_subprocess_exec("osascript", "-e", "return 1")


def test_other_programs_still_run():
    out = subprocess.run(["/bin/echo", "ok"], capture_output=True, text=True)
    assert out.stdout.strip() == "ok"


async def test_send_message_with_nothing_patched_sends_nothing(monkeypatch):
    """Ellis asking, a real handle, the real imessage_service: the transport
    reaches osascript and is refused, and the tool says it failed."""
    from services import imessage_service
    from services.toolset import messaging, policy

    monkeypatch.setattr(imessage_service, "is_available", lambda: True)
    with policy.policy_scope(policy.TurnPolicy(source="chat", owner_asked=True, may_reach_people=True)):
        with pytest.raises(Exception):
            await messaging.send_message_impl(message="test", to="+12015550199")


class TestDryRun:
    """The judgement check runs the real model on real scenarios (critique
    #1): ``EDWARD_DRY_RUN_OUTWARD=1`` (the eval sets it) or a policy's
    ``dry_run_outward`` makes the executor record every outward,
    surface-changing or committing call — and anything in Ellis's Chrome or
    the shell — without running it, whoever built the tool."""

    async def test_nothing_outward_runs(self, monkeypatch):
        from tests.fixtures.tool_loop import FakeGraph, FakeTool, ai, base_kwargs, call, patch_loop_io
        from services.graph import streaming

        monkeypatch.setenv("EDWARD_DRY_RUN_OUTWARD", "1")
        tools = [FakeTool(n, "ran") for n in ("send_message", "chrome", "screen", "bash", "tell_owner", "schedule",
                                              "memory")]
        patch_loop_io(monkeypatch, tools=tools, script=[
            ai("", call("send_message", chat_id="c", message="hi"), call("chrome", action="open", url="https://x"),
               call("screen", action="say", line="hi"), call("bash", command="osascript -e 'beep'"),
               call("tell_owner", message="m", urgent=True), call("schedule", action="add", when="w", what="x"),
               call("memory", action="search", query="q")),
            ai("done")])
        await streaming.chat_with_memory(message="go", turn_source="mind", skip_extraction=True,
                                         **base_kwargs(FakeGraph()))
        ran = {t.name: len(t.calls) for t in tools}
        assert ran == {"send_message": 0, "chrome": 0, "screen": 0, "bash": 0, "tell_owner": 0, "schedule": 0,
                       "memory": 1}

    def test_the_eval_sets_it(self):
        from pathlib import Path

        src = (Path(__file__).resolve().parents[1] / "scripts" / "judgement_eval.py").read_text()
        assert 'os.environ["EDWARD_DRY_RUN_OUTWARD"] = "1"' in src
