"""EDWARD_LLM_PROVIDER: Anthropic (the default) or Codex, per purpose.

With ``EDWARD_LLM_PROVIDER=codex`` every builder behaves exactly as before
(Codex). Unset, or ``anthropic``, every builder returns ``ChatClaude`` with
a (model, effort) from one table — chat, pings, the mind and the pulse on
Sonnet 5.5 at medium, enrichment on Haiku 5.5 at high, voice on Sonnet 5.5
at low, a judge on Opus 5.5 at high (the benchmark's routes, 2026-10-11) —
each overridable by
``EDWARD_CLAUDE_<PURPOSE>_MODEL`` / ``_EFFORT`` / ``_MAX_TOKENS``. Model ids
are fixed strings: a Codex id (settings.model, a stale checkpoint) never
reaches Anthropic, and neither does a retired Claude id.
"""

from __future__ import annotations

import os

import pytest
from langchain_core.messages import AIMessage, HumanMessage

pytestmark = pytest.mark.unit


@pytest.fixture
def clean_env(monkeypatch):
    for key in list(os.environ):
        if key.startswith("EDWARD_CLAUDE_") or key in ("EDWARD_LLM_PROVIDER", "EDWARD_SURPLUS",
                                                       "EDWARD_CHAT_REASONING_EFFORT", "EDWARD_MIND_MODEL",
                                                       "EDWARD_PULSE_MODEL"):
            monkeypatch.delenv(key, raising=False)
    return monkeypatch


@pytest.fixture
def anthropic(clean_env):
    clean_env.setenv("EDWARD_LLM_PROVIDER", "anthropic")
    return clean_env


@pytest.fixture
def codex(clean_env):
    """The Codex fallback is asked for by name (unset means Anthropic)."""
    clean_env.setenv("EDWARD_LLM_PROVIDER", "codex")
    return clean_env


class TestTheFlag:
    def test_anthropic_is_the_default(self, clean_env):
        from services import claude_routing

        assert claude_routing.llm_provider() == "anthropic"
        assert claude_routing.anthropic_enabled()

    @pytest.mark.parametrize("raw", ["codex", " Codex ", "openai", "gpt"])
    def test_codex_spellings(self, clean_env, raw):
        from services import claude_routing

        clean_env.setenv("EDWARD_LLM_PROVIDER", raw)
        assert claude_routing.llm_provider() == "codex"
        assert not claude_routing.anthropic_enabled()

    @pytest.mark.parametrize("raw", ["anthropic", " Anthropic ", "claude"])
    def test_anthropic_spellings(self, clean_env, raw):
        from services import claude_routing

        clean_env.setenv("EDWARD_LLM_PROVIDER", raw)
        assert claude_routing.llm_provider() == "anthropic"

    def test_an_unknown_value_stays_on_anthropic(self, clean_env):
        from services import claude_routing

        clean_env.setenv("EDWARD_LLM_PROVIDER", "gemini")
        assert claude_routing.llm_provider() == "anthropic"


def test_the_production_env_cannot_turn_claude_on_for_the_suite(tmp_path, monkeypatch):
    """``main.py``, ``voice_service`` and ``graph_worker`` load the repo's
    ``.env`` at import, and ``load_dotenv`` fills only unset variables. With
    the flag in the live ``.env``, the first test to import ``main`` turned
    every later builder into ChatClaude (26 routing failures, by test
    order). conftest pins the provider to codex and blanks the live key; a
    test that wants Claude says so with ``monkeypatch.setenv``."""
    from dotenv import load_dotenv

    for key in ("EDWARD_LLM_PROVIDER", "ANTHROPIC_API_KEY"):
        # Record the current state (present or absent) so teardown restores
        # it whatever load_dotenv does below.
        prior = os.environ.get(key)
        monkeypatch.setenv(key, "placeholder")
        if prior is None:
            monkeypatch.delenv(key)
        else:
            monkeypatch.setenv(key, prior)
    env = tmp_path / ".env"
    env.write_text("EDWARD_LLM_PROVIDER=anthropic\nANTHROPIC_API_KEY=sk-ant-not-a-real-key\n")
    load_dotenv(env)
    assert os.environ.get("EDWARD_LLM_PROVIDER") == "codex"
    assert os.environ.get("ANTHROPIC_API_KEY") == ""


class TestCodexPathUnchanged:
    def test_builders_return_codex_models(self, codex):
        from services import llm_utils
        from services.codex_chat_model import ChatCodexResponses

        assert isinstance(llm_utils.build_chat_llm(model="gpt-6-astra"), ChatCodexResponses)
        assert isinstance(llm_utils.build_background_llm(), ChatCodexResponses)

    def test_provider_for_is_unchanged(self, codex):
        from services import llm_utils

        assert llm_utils.provider_for("gpt-6-astra") == "openai"
        assert llm_utils.provider_for("claude-sonnet-4-5", "anthropic") == "openai"

    def test_turn_model_is_left_alone(self, codex):
        from services import llm_utils

        assert llm_utils.turn_model_for("gpt-6-astra", "chat") == "gpt-6-astra"
        assert llm_utils.turn_model_for("gpt-5.6-luna", "voice") == "gpt-5.6-luna"

    def test_surplus_stays_on_by_default(self, codex):
        from services.llm_rate_gate import surplus_enabled

        assert surplus_enabled() is True


class TestTheTable:
    @pytest.mark.parametrize("turn_source,model,effort", [
        ("chat", "claude-sonnet-5-5", "medium"),
        ("scheduled_event", "claude-sonnet-5-5", "medium"),
        ("heartbeat", "claude-sonnet-5-5", "medium"),
        ("mind", "claude-sonnet-5-5", "medium"),
        ("pulse", "claude-sonnet-5-5", "medium"),
        ("voice", "claude-sonnet-5-5", "low"),
        ("hub_voice", "claude-sonnet-5-5", "low"),
    ])
    def test_chat_builder_routes_by_turn(self, anthropic, turn_source, model, effort):
        from services import llm_utils
        from services.claude_chat_model import ChatClaude

        llm = llm_utils.build_chat_llm(model="gpt-6-astra", turn_source=turn_source)
        assert isinstance(llm, ChatClaude)
        assert (llm.model, llm.effort) == (model, effort)
        assert llm.priority == "interactive"

    def test_chat_and_mind_get_room_to_think_and_a_short_cache(self, anthropic):
        """Turns are hours apart: a 1-hour write is rarely read again, a
        5-minute one still covers the calls inside a turn (bench §5.3)."""
        from services import llm_utils

        chat = llm_utils.build_chat_llm(model="gpt-6-astra", turn_source="chat")
        assert chat.max_tokens >= 32000
        assert chat.thinking_display == "summarized"
        assert chat.cache_ttl == "5m"
        voice = llm_utils.build_chat_llm(model="gpt-5.6-luna", turn_source="voice")
        assert voice.max_tokens < chat.max_tokens

    def test_background_is_haiku_at_high_and_names_its_caller(self, anthropic):
        from services import llm_utils
        from services.claude_chat_model import ChatClaude

        llm = llm_utils.build_background_llm(max_tokens=100)
        assert isinstance(llm, ChatClaude)
        assert (llm.model, llm.effort) == ("claude-haiku-5-5", "high")
        assert llm.priority == "background"
        assert llm.purpose
        # 100 output tokens would be eaten by thinking: the table's floor wins.
        assert llm.max_tokens >= 16000

    def test_a_larger_caller_cap_is_kept(self, anthropic):
        from services import llm_utils

        assert llm_utils.build_chat_llm(model="gpt-6-astra", max_tokens=64000).max_tokens == 64000

    def test_a_judge_route(self, anthropic):
        from services import llm_utils

        llm = llm_utils.build_background_llm(route="judge")
        assert llm.model == "claude-opus-5-5"

    def test_vision_needs_nothing_special(self, anthropic):
        from services import llm_utils

        assert llm_utils.build_background_llm(vision=True, priority="interactive").model == "claude-haiku-5-5"

    def test_env_overrides(self, anthropic):
        from services import llm_utils

        anthropic.setenv("EDWARD_CLAUDE_CHAT_MODEL", "claude-sonnet-5-5")
        anthropic.setenv("EDWARD_CLAUDE_CHAT_EFFORT", "high")
        anthropic.setenv("EDWARD_CLAUDE_BACKGROUND_MODEL", "claude-opus-5-5")
        anthropic.setenv("EDWARD_CLAUDE_BACKGROUND_MAX_TOKENS", "20000")
        chat = llm_utils.build_chat_llm(model="gpt-6-astra", turn_source="chat")
        assert (chat.model, chat.effort) == ("claude-sonnet-5-5", "high")
        bg = llm_utils.build_background_llm()
        assert bg.model == "claude-opus-5-5" and bg.max_tokens == 20000

    def test_bad_overrides_are_ignored(self, anthropic):
        from services import llm_utils

        anthropic.setenv("EDWARD_CLAUDE_CHAT_MODEL", "gpt-6-astra")
        anthropic.setenv("EDWARD_CLAUDE_CHAT_EFFORT", "turbo")
        anthropic.setenv("EDWARD_CLAUDE_CHAT_MAX_TOKENS", "lots")
        chat = llm_utils.build_chat_llm(model="gpt-6-astra", turn_source="chat")
        assert (chat.model, chat.effort) == ("claude-sonnet-5-5", "medium")
        assert chat.max_tokens == 32000

    @pytest.mark.parametrize("stale", ["gpt-5.4", "gpt-6-astra", "claude-3-opus-20240229", "claude-sonnet-4-5", "", None])
    def test_stale_ids_never_reach_anthropic(self, anthropic, stale):
        from services import llm_utils

        assert llm_utils.build_chat_llm(model=stale, turn_source="chat").model == "claude-sonnet-5-5"
        assert llm_utils.build_background_llm(model=stale).model == "claude-haiku-5-5"

    def test_a_current_claude_id_from_the_table_is_honoured(self, anthropic):
        from services import llm_utils

        assert llm_utils.build_chat_llm(model="claude-sonnet-5-5", turn_source="chat").model == "claude-sonnet-5-5"
        assert llm_utils.build_background_llm(model="claude-opus-5-5").model == "claude-opus-5-5"

    def test_codex_effort_knobs_do_not_leak_into_claude(self, anthropic):
        from services import llm_utils

        anthropic.setenv("EDWARD_CHAT_REASONING_EFFORT", "xhigh")
        assert llm_utils.build_chat_llm(model="gpt-6-astra", turn_source="chat").effort == "medium"
        assert llm_utils.build_chat_llm(model="gpt-6-astra", turn_source="mind",
                                        reasoning_effort="xhigh").effort == "medium"
        assert llm_utils.build_background_llm(effort="low").effort == "high"


class TestTheBenchmarkRoutes:
    """The defaults are AREA 4's measured routes (docs/research-2026-10-10/
    benchmark-results.md §1, 2026-10-11): Sonnet 5.5 at medium ties Opus
    5.5 on ping turns at under half the cost and twice the speed, so chat,
    pings, wakes, the speaker, the nightly and the pulse run on it; Haiku
    5.5 at high beats Sonnet low on extraction, entities and consolidation
    and caught 4 of 4 must-notice cases with 0 false noticings, so the
    background (the noticer and photo descriptions ride it) stays there;
    voice is Sonnet at low; the judge Opus at high. Turns are hours apart,
    so a 1-hour cache write is rarely read again: every route but voice
    caches for 5 minutes (§5.3). Env still overrides every field."""

    @pytest.mark.parametrize("purpose,model,effort,ttl", [
        ("chat", "claude-sonnet-5-5", "medium", "5m"),
        ("mind", "claude-sonnet-5-5", "medium", "5m"),
        ("pulse", "claude-sonnet-5-5", "medium", "5m"),
        ("background", "claude-haiku-5-5", "high", "5m"),
        ("voice", "claude-sonnet-5-5", "low", "1h"),
        ("judge", "claude-opus-5-5", "high", "5m"),
    ])
    def test_the_table(self, clean_env, purpose, model, effort, ttl):
        from services.claude_routing import claude_route

        route = claude_route(purpose)
        assert (route.model, route.effort, route.cache_ttl) == (model, effort, ttl)

    @pytest.mark.parametrize("turn_source", ["chat", "ping", "ping_owner", "scheduled_event", "heartbeat"])
    def test_chat_and_ping_replies_are_sonnet_medium(self, anthropic, turn_source):
        from services import llm_utils

        llm = llm_utils.build_chat_llm(model="gpt-6-astra", turn_source=turn_source)
        assert (llm.model, llm.effort, llm.cache_ttl) == ("claude-sonnet-5-5", "medium", "5m")

    @pytest.mark.parametrize("reason", ["self", "mention", "speak", "nightly", "free", "manual"])
    def test_every_wake_and_the_speaker_follow_the_mind_route(self, anthropic, reason):
        from services import mind_service

        model, _ = mind_service.route_for_wake(reason, "mind", "gpt-6-astra")
        assert model == "claude-sonnet-5-5"

    def test_the_speaker_still_takes_its_own_knob(self, anthropic):
        from services import mind_service

        anthropic.setenv("EDWARD_SPEAK_MODEL", "claude-opus-5-5")
        assert mind_service.route_for_wake("speak", "mind", "gpt-6-astra")[0] == "claude-opus-5-5"
        assert mind_service.route_for_wake("self", "mind", "gpt-6-astra")[0] == "claude-sonnet-5-5"

    def test_the_pulse_is_sonnet_medium(self, anthropic):
        from services import mind_service

        assert mind_service.route_for_wake("surfaces", "pulse", "gpt-6-astra")[0] == "claude-sonnet-5-5"
        llm = __import__("services.llm_utils", fromlist=["x"]).build_chat_llm(model="gpt-6-astra", turn_source="pulse")
        assert (llm.model, llm.effort) == ("claude-sonnet-5-5", "medium")

    def test_the_noticer_and_photo_descriptions_ride_the_background(self, anthropic, monkeypatch):
        """No media route exists: the describer and the noticer build the
        background route (Haiku 5.5 at high)."""
        from services import llm_utils

        monkeypatch.delenv("EDWARD_NOTICE_MODEL", raising=False)
        noticer = llm_utils.build_background_llm(route="background", priority="interactive", model=None,
                                                 effort="medium")
        assert (noticer.model, noticer.effort) == ("claude-haiku-5-5", "high")
        seeing = llm_utils.build_background_llm(max_tokens=200, priority="interactive", vision=True)
        assert (seeing.model, seeing.effort) == ("claude-haiku-5-5", "high")

    def test_voice_is_sonnet_low(self, anthropic):
        from services import llm_utils

        llm = llm_utils.build_chat_llm(model="gpt-5.6-luna", turn_source="voice")
        assert (llm.model, llm.effort, llm.cache_ttl) == ("claude-sonnet-5-5", "low", "1h")
        assert llm_utils.voice_model() == "claude-sonnet-5-5"

    def test_the_hubs_voice_takes_the_voice_route_at_interactive_priority(self, anthropic):
        """The household hub's live voice (``hub_voice``, 2026-10-11) is not
        Ellis, but someone is standing there waiting to hear the answer: the
        voice route, and never background priority."""
        from services import claude_routing, llm_utils

        assert claude_routing.purpose_for_turn("hub_voice") == "voice"
        assert claude_routing.purpose_for_turn("hub_voice", "gpt-5.6-luna") == "voice"
        llm = llm_utils.build_chat_llm(model="gpt-5.6-luna", turn_source="hub_voice")
        owners = llm_utils.build_chat_llm(model="gpt-5.6-luna", turn_source="voice")
        knobs = lambda m: (m.model, m.effort, m.cache_ttl, m.max_tokens, m.thinking_display)  # noqa: E731
        assert knobs(llm) == knobs(owners)
        assert (llm.effort, llm.cache_ttl) == ("low", "1h")
        assert llm.priority == "interactive"

    def test_the_judge_is_opus_high(self, anthropic):
        from services import llm_utils

        llm = llm_utils.build_background_llm(route="judge")
        assert (llm.model, llm.effort) == ("claude-opus-5-5", "high")

    def test_env_still_overrides_the_new_defaults(self, anthropic):
        from services.claude_routing import claude_route

        anthropic.setenv("EDWARD_CLAUDE_MIND_MODEL", "claude-opus-5-5")
        anthropic.setenv("EDWARD_CLAUDE_MIND_EFFORT", "low")
        anthropic.setenv("EDWARD_CLAUDE_CHAT_CACHE_TTL", "1h")
        anthropic.setenv("EDWARD_CLAUDE_VOICE_CACHE_TTL", "off")
        assert (claude_route("mind").model, claude_route("mind").effort) == ("claude-opus-5-5", "low")
        assert claude_route("chat").cache_ttl == "1h"
        assert claude_route("voice").cache_ttl is None


class TestDerivedAnswers:
    def test_provider_for_says_anthropic(self, anthropic):
        from services import llm_utils

        assert llm_utils.provider_for("claude-opus-5-5") == "anthropic"
        assert llm_utils.provider_for("gpt-6-astra") == "anthropic"
        assert llm_utils.provider_for(None) == "anthropic"

    def test_turn_model_for(self, anthropic):
        from services import llm_utils

        assert llm_utils.turn_model_for("gpt-6-astra", "chat") == "claude-sonnet-5-5"
        assert llm_utils.turn_model_for("gpt-5.6-luna", "voice") == "claude-sonnet-5-5"
        assert llm_utils.turn_model_for("gpt-6-astra", "pulse") == "claude-sonnet-5-5"
        assert llm_utils.turn_model_for("claude-haiku-5-5", "mind") == "claude-haiku-5-5"

    def test_the_named_tiers(self, anthropic):
        from services import llm_utils

        assert llm_utils.mind_model_for("gpt-6-astra") == "claude-sonnet-5-5"
        assert llm_utils.pulse_model_for("gpt-6-astra") == "claude-sonnet-5-5"
        assert llm_utils.background_model() == "claude-haiku-5-5"
        assert llm_utils.voice_model() == "claude-sonnet-5-5"

    def test_a_wake_routes_to_claude(self, anthropic):
        from services import mind_service

        model, _ = mind_service.route_for_wake("self", "mind", "gpt-6-astra")
        assert model == "claude-sonnet-5-5"
        model, _ = mind_service.route_for_wake("surfaces", "pulse", "gpt-6-astra")
        assert model == "claude-sonnet-5-5"

    def test_the_routing_summary_shows_claude(self, anthropic, monkeypatch):
        from services import mind_service

        out = mind_service.routing_summary("gpt-6-astra")
        assert out["chat_model"] == "claude-sonnet-5-5" and out["chat_effort"] == "medium"
        assert out["mind_model"] == "claude-sonnet-5-5" and out["mind_effort"] == "medium"
        assert out["pulse_model"] == "claude-sonnet-5-5"
        assert set(out["mind_efforts"].values()) == {"medium"}

    def test_surplus_is_off_on_pay_per_token_unless_asked(self, anthropic):
        from services.llm_rate_gate import surplus_enabled

        assert surplus_enabled() is False
        anthropic.setenv("EDWARD_SURPLUS", "on")
        assert surplus_enabled() is True


WEEK = 7 * 24 * 3600.0


def _codex_meter(monkeypatch, used_percent: float, *, limit_reached: bool = False, reset_days: float = 3.0):
    """The real rate gate holding a Codex meter reading (no mock of the check)."""
    import time

    from services import llm_rate_gate
    from services.llm_rate_gate import LLMRateGate, QuotaMeter, QuotaWindow

    gate = LLMRateGate()
    gate.note_meter(QuotaMeter(
        windows={"primary": QuotaWindow(used_percent=used_percent, window_seconds=WEEK,
                                        reset_after_seconds=reset_days * 86400)},
        limit_reached=limit_reached, sampled_at=time.time(),
    ))
    monkeypatch.setattr(llm_rate_gate, "_gate", gate)
    return gate


class TestTheCodexMeterDoesNotGateClaude:
    """The mind and the pulse skip while the Codex week is spent
    (``expression_service.quota_state``). On Claude no call spends that
    week, nothing refreshes its meter, and a spent window keeps reading
    "exhausted" until its own reset — up to seven days with no mentions
    answered, no ticks and the widget on its fallback card, though Claude
    is configured and working (review 2026-10-11)."""

    def test_codex_still_skips_on_a_spent_week(self, codex, monkeypatch):
        from services import expression_service as es

        _codex_meter(monkeypatch, 100.0, limit_reached=True)
        assert es.quota_state() == "exhausted"

    def test_a_spent_codex_week_does_not_stop_claude(self, anthropic, monkeypatch):
        from services import expression_service as es

        _codex_meter(monkeypatch, 100.0, limit_reached=True)
        assert es._quota_state() == "ok"
        assert es.quota_state() == "ok"

    def test_the_codex_pace_line_does_not_slow_the_pulse_on_claude(self, anthropic, monkeypatch):
        from services import expression_service as es

        # A day into the week at 60 %: "paced" on Codex.
        _codex_meter(monkeypatch, 60.0, reset_days=6.0)
        assert es._quota_state() == "ok"
        anthropic.setenv("EDWARD_LLM_PROVIDER", "codex")
        assert es._quota_state() == "paced"

    async def test_a_wake_runs_on_claude_with_the_codex_week_spent(self, anthropic, monkeypatch):
        import services.mind_service as ms
        from services import expression_service
        from tests.test_mind_service import _stub

        _codex_meter(monkeypatch, 100.0, limit_reached=True)
        calls = []

        async def chat(**kw):
            calls.append(kw)
            return "Answered Parker."

        recorded = _stub(monkeypatch, ms, chat)
        # The real check, not the stub's "ok".
        monkeypatch.setattr(ms, "quota_state", expression_service.quota_state)
        out = await ms.wake("morning")
        assert not out.get("skipped"), out
        assert len(calls) == 1
        assert recorded and recorded[0]["outcome"] != "skipped"

    def test_edward_is_not_shown_resting_on_claude(self, anthropic, monkeypatch):
        """The animation is a claim: "resting" (the week is spent) is not
        true while Claude answers. On Claude his quota is the day's spend
        cap (``llm_spend.quota_now``, ``test_llm_spend_gate.py``), never the
        Codex week's reading."""
        from services import wake_state

        _codex_meter(monkeypatch, 100.0, limit_reached=True)
        view = wake_state._quota_now()
        assert view["exhausted"] is False
        assert view["period"] == "day"
        assert view["used_percent"] != 100.0

    def test_codex_still_shows_the_spent_week(self, codex, monkeypatch):
        from services import wake_state

        _codex_meter(monkeypatch, 100.0, limit_reached=True)
        assert wake_state._quota_now()["exhausted"] is True


class TestTheTurnUnderTheFlag:
    async def test_the_stream_runs_on_claude_with_the_split_prompt(self, anthropic, monkeypatch):
        from services.claude_chat_model import STATIC_DYNAMIC_SPLIT
        from tests.fixtures.tool_loop import FakeGraph, ai, base_kwargs, patch_loop_io, run_stream

        usage = {"input_tokens": 10, "output_tokens": 2, "total_tokens": 12}
        h = patch_loop_io(monkeypatch, tools=[], script=[ai("hello", usage_metadata=usage)])
        await run_stream(message="hi", **base_kwargs(FakeGraph()))
        assert h.builds[0]["model"] == "claude-sonnet-5-5"
        assert h.builds[0]["provider"] == "anthropic"
        system = h.calls[0]["messages"][0].content
        static, dynamic = system.split(STATIC_DYNAMIC_SPLIT, 1)
        assert static.startswith("be edward")
        assert "Current date and time" in dynamic
        assert h.usage[0]["provider"] == "anthropic" and h.usage[0]["model"] == "claude-sonnet-5-5"

    async def test_the_codex_prompt_has_no_marker(self, codex, monkeypatch):
        from services.claude_chat_model import STATIC_DYNAMIC_SPLIT
        from tests.fixtures.tool_loop import FakeGraph, ai, base_kwargs, patch_loop_io, run_stream

        h = patch_loop_io(monkeypatch, tools=[], script=[ai("hello")])
        await run_stream(message="hi", **base_kwargs(FakeGraph()))
        assert STATIC_DYNAMIC_SPLIT not in h.calls[0]["messages"][0].content
        assert h.builds[0]["model"] == "gpt-6-astra"

    async def test_a_pulse_turn_builds_the_pulse_route(self, anthropic, monkeypatch):
        from services.graph import streaming
        from tests.fixtures.tool_loop import FakeGraph, ai, base_kwargs, patch_loop_io

        h = patch_loop_io(monkeypatch, tools=[], script=[ai("ok")])
        await streaming.chat_with_memory(message="pack", turn_source="pulse", skip_extraction=True,
                                         **base_kwargs(FakeGraph()))
        assert h.builds[0]["model"] == "claude-sonnet-5-5"

    async def test_a_tool_round_replays_the_thinking_and_the_checkpoint_drops_it(self, anthropic, monkeypatch):
        """End to end through the streaming loop with the real adapter and a
        fake Anthropic client: the second request carries the first
        response's thinking block verbatim (signature and all) and the
        dynamic context right after Ellis's message; the checkpoint keeps no
        signature from a finished turn's final answer."""
        from services.claude_chat_model import ChatClaude
        from services.graph import streaming
        from tests.fixtures.fake_anthropic import FakeAnthropic, message, text, thinking, tool_use
        from tests.fixtures.tool_loop import FakeGraph, FakeTool, base_kwargs, patch_loop_io, run_stream

        client = FakeAnthropic([
            message([thinking("Look it up.", "sig-round-1"), tool_use("toolu_1", "web_search", {"query": "tide"})],
                    stop_reason="tool_use"),
            message([thinking("Got it.", "sig-round-2"), text("High tide is at 6.")]),
        ])
        tool = FakeTool("web_search", result="high tide 6pm")
        h = patch_loop_io(monkeypatch, tools=[tool], script=[])

        def _build(model, temperature, max_tokens=16384, provider="openai", **kw):
            h.builds.append({"model": model, "provider": provider, **kw})
            return ChatClaude(model=model, effort="medium", client=client)

        monkeypatch.setattr(streaming, "_build_llm", _build)
        graph = FakeGraph()
        events = await run_stream(message="when is high tide?", **base_kwargs(graph))
        assert any(e.get("type") == "reasoning" for e in events)
        assert tool.calls == [{"query": "tide"}]
        second = client.calls[1]["messages"]
        roles = [m["role"] for m in second]
        assert roles == ["user", "system", "assistant", "user"]
        assert second[2]["content"][0] == {"type": "thinking", "thinking": "Look it up.", "signature": "sig-round-1"}
        assert second[3]["content"][0]["tool_use_id"] == "toolu_1"
        saved = graph.messages
        assert "sig-round-2" not in repr(saved)
        assert isinstance(saved[-1], AIMessage) and saved[-1].content == "High tide is at 6."
        assert any(isinstance(m, HumanMessage) for m in saved)


class TestTheClosingSummaryCall:
    """When a turn runs out of rounds (or time) the loop makes one last call
    with no tools to say what it did. The thread holds tool_use and
    tool_result blocks by then, and the Messages API refuses those in a
    request that defines no tools — so on Claude the summary call keeps the
    same tools (and the cached prefix) with ``tool_choice="none"``."""

    def _endless(self, monkeypatch):
        from tests.fixtures.tool_loop import FakeTool, ai, call, patch_loop_io

        tool = FakeTool("web_search", lambda a: f"results for {a.get('query')}")
        script = [ai("", call("web_search", query=f"q{i}")) for i in range(3)] + [ai("Here is what I found.")]
        return patch_loop_io(monkeypatch, tools=[tool], script=script)

    async def test_streaming(self, anthropic, monkeypatch):
        from tests.fixtures.tool_loop import FakeGraph, base_kwargs, run_stream

        h = self._endless(monkeypatch)
        await run_stream(message="look it up", max_tool_iterations=3, **base_kwargs(FakeGraph()))
        last = h.calls[-1]
        assert last["tool_choice"] == "none" and last["tools"] == ["web_search"]

    async def test_non_streaming(self, anthropic, monkeypatch):
        from services.graph import streaming
        from tests.fixtures.tool_loop import FakeGraph, base_kwargs

        h = self._endless(monkeypatch)
        await streaming.chat_with_memory(message="look it up", turn_source="mind", skip_extraction=True,
                                         max_tool_iterations=3, **base_kwargs(FakeGraph()))
        last = h.calls[-1]
        assert last["tool_choice"] == "none" and last["tools"] == ["web_search"]

    async def test_codex_keeps_its_bare_summary_call(self, codex, monkeypatch):
        from tests.fixtures.tool_loop import FakeGraph, base_kwargs, run_stream

        h = self._endless(monkeypatch)
        await run_stream(message="look it up", max_tool_iterations=3, **base_kwargs(FakeGraph()))
        last = h.calls[-1]
        assert last["tool_choice"] is None and last["tools"] == []
