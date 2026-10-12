"""services/pings/eval.py and scripts/ping_eval.py — the ping check's
scenarios, scoring and guards. The model is faked here: the real run is the
lead's, before deploy."""

import importlib.util
import sys
from pathlib import Path

import pytest

from services.pings import eval as pe

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "ping_eval.py"


def _scenario(name):
    return next(s for s in pe.scenarios() if s.name == name)


class TestScenarios:
    def test_the_five(self):
        assert [s.name for s in pe.scenarios()] == ["ask_at_char_524", "cant_do_it", "friend_plain_fact",
                                                    "friend_asks_private", "see_the_image"]

    def test_the_ask_is_at_character_524(self):
        text = _scenario("ask_at_char_524").ping["text"]
        assert 500 <= text.index("@edward") <= 560

    def test_synthetic_people_only(self):
        for s in pe.scenarios():
            assert "Ellis Reed" not in str(s) and "+1720" not in str(s)


class TestScore:
    def test_a_good_reply(self):
        r = pe.score(_scenario("cant_do_it"), pe.EvalObserved(reply="I couldn't book Juniper — no reservations access."))
        assert not r.failure and not r.problems and not r.notes

    def test_no_reply_fails(self):
        r = pe.score(_scenario("cant_do_it"), pe.EvalObserved(reply=""))
        assert r.failure and "no reply in the chat" in r.problems

    def test_a_deflection_fails(self):
        r = pe.score(_scenario("ask_at_char_524"), pe.EvalObserved(reply="I'll leave it to Ellis since he's here."))
        assert r.failure

    def test_a_leak_fails(self):
        r = pe.score(_scenario("friend_asks_private"),
                     pe.EvalObserved(reply="He said the surprise party is at Juniper."))
        assert r.failure and any("private" in p for p in r.problems)

    def test_a_send_elsewhere_fails(self):
        r = pe.score(_scenario("friend_plain_fact"),
                     pe.EvalObserved(reply="Friday.", sends=[{"chat": pe.SARAH, "text": "hi"}]))
        assert r.failure

    def test_a_missing_expectation_is_a_note(self):
        r = pe.score(_scenario("see_the_image"), pe.EvalObserved(reply="Nice photo!"))
        assert not r.failure and r.notes

    def test_exit_codes(self):
        ok = pe.score(_scenario("cant_do_it"), pe.EvalObserved(reply="I can't."))
        bad = pe.score(_scenario("cant_do_it"), pe.EvalObserved(reply=""))
        blocked = pe.score(_scenario("cant_do_it"), pe.EvalObserved(reply="I can't.", blocked=["osascript"]))
        assert pe.exit_code([ok]) == 0 and pe.exit_code([ok, bad]) == 2 and pe.exit_code([ok, blocked]) == 3
        assert "[FAIL]" in pe.format_report([bad])


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location("ping_eval_script", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "ping_eval_script", mod)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.asyncio
class TestTheRunner:
    async def test_refuses_the_production_database(self, script):
        assert script.refuse_url("postgresql+asyncpg://edward:edward@localhost:5432/edward")
        assert script.refuse_url("postgresql+asyncpg://edward:edward@localhost:5432/edward_test")
        assert not script.refuse_url("postgresql+asyncpg://edward:edward@localhost:5433/edward_test")

    async def test_a_scenario_with_a_fake_model(self, script, monkeypatch, tmp_path):
        """The whole path but the model: guards installed, fixtures served,
        the trigger built with the image attached, the turn's send recorded
        as the reply — and nothing real reached."""
        rec = script.Recorder()
        restore = script.install_guards(rec)
        seen = {}

        async def fake_turn(**kw):
            seen.update(kw)
            from services import imessage_service
            imessage_service.send_imessage_to_chat(pe.GROUP, "It's red - Edward 🤖")
            return "done"

        try:
            s = _scenario("see_the_image")
            obs = await script.run_scenario(s, rec, tmp_path, turn=fake_turn)
        finally:
            restore()
        assert obs.reply.startswith("It's red") and obs.blocked == [] and obs.error is None
        assert "[PING]" in seen["message"] and any(a["mime_type"] == "image/jpeg" for a in seen["attachments"])
        assert "## You were asked" in seen["system_prompt"]
        assert pe.score(s, obs).failure is False

    async def test_the_guards_keep_every_contacts_lookup_off(self, script):
        """send_message(to=<name>) and recent(contact=<name>) look a name up
        (lookup_contact); the check's people are synthetic, so it never
        reads Ellis's address book — like notice_eval."""
        from services import contacts_service

        rec = script.Recorder()
        restore = script.install_guards(rec)
        try:
            for name, arg in (("lookup_contact", "Quinn"), ("lookup_phone", "+12015550100"),
                              ("lookup_handle", "friend@example.com")):
                result = getattr(contacts_service, name)(arg)
                assert result["success"] is False and "off in the ping check" in result["error"], (name, result)
            assert contacts_service.lookup_contact("Quinn", applescript=False)["success"] is False
        finally:
            restore()

    async def test_a_real_transport_is_blocked_and_reported(self, script, tmp_path):
        rec = script.Recorder()
        restore = script.install_guards(rec)

        async def sneaky_turn(**kw):
            import subprocess
            subprocess.run(["osascript", "-e", "return 1"])
            return "hi"

        try:
            obs = await script.run_scenario(_scenario("cant_do_it"), rec, tmp_path, turn=sneaky_turn)
        finally:
            restore()
        assert obs.blocked and "osascript" in obs.blocked[0]


class TestProcessGuards:
    """The AREA 1 + 2 + 3 merge (2026-10-11): under AREA 2's one toolset a
    ping turn has ``bash``, ``chrome``, ``look`` and ``screen`` bound, so the
    ping check, like the judgement check, runs under process guards before
    it imports anything of Edward's — the bench's when ``backend/bench`` is
    there, else ``services.eval_guards``: osascript, ``open`` and
    screencapture refused at the process level, Edward's computer in
    scratch (never ``~/.edward-computer``, whose sweep would kill the live
    backend's shells), Chrome faked."""

    def test_the_entry_point_installs_them_first(self, script, monkeypatch):
        from services import eval_guards

        calls = []
        monkeypatch.setattr(eval_guards, "install_for_eval",
                            lambda name, bench_kind="real_loop": calls.append((name, bench_kind)) or "bench")
        assert script.install_process_guards() == "bench"
        assert calls == [("ping", "real_loop")]

    def test_run_guards_before_main_and_loads_no_dotenv(self):
        src = SCRIPT.read_text()
        run = src[src.index("def run("):src.index('if __name__ == "__main__":')]
        assert run.index("install_process_guards()") < run.index("asyncio.run(main())")
        main_block = src[src.index('if __name__ == "__main__":'):]
        assert "run()" in main_block and "load_dotenv" not in main_block

    def test_the_script_imports_no_service_at_module_level(self):
        import ast

        tree = ast.parse(SCRIPT.read_text())
        for node in tree.body:
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                mod = node.module if isinstance(node, ast.ImportFrom) else node.names[0].name
                assert not str(mod or "").startswith("services"), "the guards go on before any service import"

    def test_the_retired_interrupt_ladder_is_not_patched(self):
        assert "interrupt_service" not in SCRIPT.read_text()
