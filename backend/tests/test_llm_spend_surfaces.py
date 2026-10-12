"""Where the Claude spend shows (2026-10-10).

* ``GET /api/health/spend`` gains a ``claude`` block: today's spend by
  purpose and model, the cap and the background share, what is left today,
  the credits remaining and the days they last, and a per-day series.
* ``GET /api/health/report`` gains ``spend.claude`` — the same headline
  numbers, and a warning once today's spend passes 80 % of the cap.
* ``GET /api/settings/codex-usage`` keeps its Codex payload and gains
  ``provider`` and the ``claude`` block; under the anthropic provider it
  fetches no Codex meter (the surplus queue's block went with it, 2026-10-11).
* A chat turn the cap refuses says the cap's own sentence — no model call
  to explain it (that call would be refused too).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import llm_spend as ls

CHI = ZoneInfo("America/Chicago")


@pytest.fixture(autouse=True)
def _ten_dollar_cap(monkeypatch):
    """These cases read the thresholds of a $10 cap (a $6 background stop,
    $13 with the reserve). The default became $5 on Ellis's answer
    (2026-10-11); ``test_llm_spend`` pins that."""
    monkeypatch.setenv("EDWARD_DAILY_SPEND_CAP_USD", "10")


@pytest.fixture
def chicago(monkeypatch):
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "get_user_tz", lambda: CHI)
    monkeypatch.setattr(timezone_utils, "current_tz_name", lambda: "America/Chicago")


@pytest.fixture
def at_one_pm(monkeypatch):
    now = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)      # 1 pm Monday in Springfield
    monkeypatch.setattr(ls, "_now_utc", lambda: now)
    return now


def _group(purpose, model, cost, calls=1, errors=0, **tokens):
    g = {"purpose": purpose, "model": model, "priority": "interactive", "calls": calls, "errors": errors,
         "cost_usd": cost, "input_tokens": 0, "cache_read_tokens": 0, "cache_write_tokens": 0, "output_tokens": 0,
         "thinking_tokens": 0, "web_searches": 0}
    g.update(tokens)
    return g


# --------------------------------------------------------------- pure --

@pytest.mark.unit
def test_the_block_says_where_today_stands():
    groups = [
        _group("turn:chat", "claude-opus-5-5", 1.5, calls=3, input_tokens=100),
        _group("mind:morning", "claude-opus-5-5", 2.0, calls=2, errors=1),
        _group("memory_service.extract", "claude-haiku-5-5", 0.25, calls=10),
        _group("turn:chat", "claude-haiku-5-5", 0.25, calls=1, input_tokens=50),
    ]
    credits = ls.credits_view(start_usd=100.0, start_date=date(2026, 10, 11), spent=20.0, burn_spent=12.0,
                              burn_span_days=3.0)
    block = ls.compose_claude_block(spent=4.0, limits=ls.SpendLimits(cap=10.0), day=date(2026, 10, 12),
                                    tz_name="America/Chicago", groups=groups, credits=credits, active=True)
    assert block["provider"] == "anthropic" and block["active"] is True
    assert block["day"] == "2026-10-12" and block["timezone"] == "America/Chicago"
    assert block["spent_today_usd"] == 4.0 and block["cap_usd"] == 10.0
    assert block["background_share"] == 0.6 and block["background_stop_usd"] == pytest.approx(6.0)
    assert block["interactive_reserve_usd"] == 3.0 and block["hard_stop_usd"] == 13.0
    assert block["remaining_today_usd"] == 6.0 and block["percent_of_cap"] == 40.0
    assert block["state"] == "ok" and block["background_allowed"] is True
    assert block["calls_today"] == 16 and block["errors_today"] == 1
    assert list(block["by_purpose"]) == ["mind:morning", "turn:chat", "memory_service.extract"]
    assert block["by_purpose"]["turn:chat"]["cost_usd"] == pytest.approx(1.75)
    assert block["by_purpose"]["turn:chat"]["calls"] == 4 and block["by_purpose"]["turn:chat"]["input_tokens"] == 150
    assert list(block["by_model"]) == ["claude-opus-5-5", "claude-haiku-5-5"]
    assert block["by_model"]["claude-opus-5-5"]["cost_usd"] == pytest.approx(3.5)
    assert block["credits"]["remaining_usd"] == 80.0 and block["credits"]["days_left"] == pytest.approx(20.0)
    assert "console.anthropic.com" in block["credits"]["note"]


@pytest.mark.unit
@pytest.mark.parametrize("spent,state", [(5.99, "ok"), (6.0, "background_paused"), (10.0, "own_work_paused"),
                                         (13.0, "stopped")])
def test_the_blocks_state_follows_the_thresholds(spent, state):
    block = ls.compose_claude_block(spent=spent, limits=ls.SpendLimits(cap=10.0), day=date(2026, 10, 12), tz_name="UTC",
                                    groups=[], credits=None, active=True)
    assert block["state"] == state
    assert block["remaining_today_usd"] == max(0.0, round(10.0 - spent, 4))


@pytest.mark.unit
def test_the_health_headline_warns_past_eighty_percent_of_the_cap():
    def head(spent):
        block = ls.compose_claude_block(spent=spent, limits=ls.SpendLimits(cap=10.0), day=date(2026, 10, 12), tz_name="UTC",
                                        groups=[_group("turn:chat", "claude-opus-5-5", spent)],
                                        credits=ls.credits_view(start_usd=100.0, start_date=date(2026, 10, 11),
                                                                spent=30.0, burn_spent=9.0, burn_span_days=3.0),
                                        active=True)
        return ls.headline(block)

    calm = head(8.0)
    assert calm["warning"] is None
    assert calm["spent_today_usd"] == 8.0 and calm["cap_usd"] == 10.0 and calm["background_share"] == 0.6
    assert calm["remaining_today_usd"] == 2.0
    assert calm["credits_remaining_usd"] == 70.0 and calm["credits_days_left"] == pytest.approx(70.0 / 3.0)
    assert calm["top_purposes"][0]["purpose"] == "turn:chat"
    hot = head(8.01)
    assert hot["warning"] and "$8.01" in hot["warning"] and "$10.00" in hot["warning"]


# -------------------------------------------------------- the reports --

def _row(created_at, cost, **extra):
    from services.database import LLMCallModel

    fields = dict(created_at=created_at, provider="anthropic", model="claude-opus-5-5", purpose="turn:chat",
                  priority="interactive", input_tokens=10, cache_read_tokens=0, cache_write_tokens=0,
                  output_tokens=5, thinking_tokens=0, web_searches=0, cost_usd=cost, ok=True)
    fields.update(extra)
    return LLMCallModel(**fields)


async def _add(*rows):
    from services.database import async_session

    async with async_session() as s:
        for r in rows:
            s.add(r)
        await s.commit()


async def _seed():
    await _add(
        _row(datetime(2026, 10, 11, 4, 0), 50.0),                                  # Sat 11 pm: before the credits
        _row(datetime(2026, 10, 11, 15, 0), 2.0, purpose="mind:morning"),          # Sunday
        _row(datetime(2026, 10, 12, 14, 0), 3.0, purpose="turn:chat"),             # Monday (today)
        _row(datetime(2026, 10, 12, 15, 0), 1.0, purpose="memory_service.extract",
             model="claude-haiku-5-5", priority="background", ok=False, error="overloaded"),
        _row(datetime(2026, 10, 12, 16, 0), 4.5, purpose="mind:messages"),
    )


@pytest.mark.integration
async def test_the_block_reads_todays_rows_and_the_credits(clean_db, chicago, at_one_pm, monkeypatch):
    monkeypatch.setenv("EDWARD_CREDITS_START_DATE", "2026-10-11")
    monkeypatch.setenv("EDWARD_CREDITS_START_USD", "100")
    await _seed()
    block = await ls.claude_block(days=3)
    assert block["day"] == "2026-10-12"
    assert block["spent_today_usd"] == pytest.approx(8.5)
    assert block["state"] == "background_paused" and block["background_allowed"] is False
    assert list(block["by_purpose"]) == ["mind:messages", "turn:chat", "memory_service.extract"]
    assert block["by_model"]["claude-haiku-5-5"]["errors"] == 1
    assert block["errors_today"] == 1 and block["calls_today"] == 3
    assert block["credits"]["spent_usd"] == pytest.approx(10.5)
    assert block["credits"]["remaining_usd"] == pytest.approx(89.5)
    assert block["by_day"] == [
        {"day": "2026-10-10", "calls": 1, "cost_usd": 50.0},
        {"day": "2026-10-11", "calls": 1, "cost_usd": 2.0},
        {"day": "2026-10-12", "calls": 3, "cost_usd": 8.5},
    ]


@pytest.mark.integration
async def test_the_spend_report_carries_the_claude_block(clean_db, chicago, at_one_pm):
    from services.spend_report_service import build_spend_report

    await _seed()
    out = await build_spend_report(7)
    assert "claude" in out
    assert out["claude"]["spent_today_usd"] == pytest.approx(8.5)
    assert out["claude"]["by_day"]                 # a series as long as the report's window
    assert "totals" in out and "by" in out         # the Codex report is unchanged


@pytest.mark.integration
async def test_the_health_report_carries_the_claude_headline(clean_db, chicago, at_one_pm):
    from services.health_report_service import build_report

    await _seed()
    out = await build_report(7)
    claude = out["spend"]["claude"]
    assert claude["spent_today_usd"] == pytest.approx(8.5)
    assert claude["cap_usd"] == 10.0 and claude["remaining_today_usd"] == pytest.approx(1.5)
    assert claude["warning"] and "85%" in claude["warning"]
    assert "turns" in out and "storage" in out
    assert "llm_calls" in out["storage"]


@pytest.mark.unit
async def test_a_report_section_that_fails_never_sinks_the_report(monkeypatch):
    async def _boom(*a, **k):
        raise RuntimeError("database is down")

    monkeypatch.setattr(ls, "_db_groups", _boom)
    monkeypatch.setattr(ls, "_db_spent_between", _boom)
    block = await ls.claude_block()
    assert "error" in block and block["cap_usd"] == 10.0


# --------------------------------------------------------- the card --

@pytest.fixture
def gate(monkeypatch):
    from services.llm_rate_gate import LLMRateGate

    g = LLMRateGate()
    monkeypatch.setattr("services.llm_rate_gate._gate", g)
    return g


@pytest.fixture
def fake_block(monkeypatch):
    calls = []

    async def _block(days=None):
        calls.append(days)
        return {"provider": ls.provider(), "active": ls.is_anthropic(), "spent_today_usd": 1.25, "cap_usd": 10.0,
                "credits": {"remaining_usd": 88.0, "days_left": 21.0, "note": ls.CREDITS_NOTE}}

    monkeypatch.setattr(ls, "claude_block", _block)
    return calls


async def test_the_card_under_anthropic_reads_the_spend_and_no_codex_meter(gate, fake_block, monkeypatch):
    from services import codex_usage_service as cus

    monkeypatch.setenv("EDWARD_LLM_PROVIDER", "anthropic")

    async def _no_meter():
        raise AssertionError("no Codex meter is fetched under pay-per-token")

    monkeypatch.setattr(cus, "refresh_gate_meter", _no_meter)
    out = await cus.get_usage_status(with_surplus=True)
    assert out["provider"] == "anthropic"
    assert out["claude"]["spent_today_usd"] == 1.25 and out["claude"]["credits"]["remaining_usd"] == 88.0
    assert out["meter"]["windows"] == {}
    assert "surplus" not in out, "the surplus queue is retired (2026-10-11)"
    assert out["status"] == "available"


async def test_the_card_under_codex_keeps_the_meter_and_gains_the_block(gate, fake_block, monkeypatch):
    from services import codex_usage_service as cus

    gate.note_usage_headers({"x-codex-plan-type": "pro", "x-codex-primary-used-percent": "22",
                             "x-codex-primary-window-minutes": "10080",
                             "x-codex-primary-reset-after-seconds": "300000"})

    out = await cus.get_usage_status(with_surplus=True)
    assert out["provider"] == "codex"
    assert out["meter"]["windows"]["primary"]["used_percent"] == 22
    assert "surplus" not in out and "spend_top" not in out
    assert out["claude"]["active"] is False


async def test_the_bare_meter_status_reads_no_ledger(gate, fake_block):
    from services import codex_usage_service as cus

    gate.note_usage_headers({"x-codex-primary-used-percent": "22", "x-codex-primary-window-minutes": "10080",
                             "x-codex-primary-reset-after-seconds": "300000"})
    out = await cus.get_usage_status()
    assert "claude" not in out and fake_block == []


# ----------------------------------------------------------- the chat --

async def test_a_turn_the_cap_refuses_says_the_caps_own_sentence(monkeypatch):
    from routers import chat as chat_router
    import services.llm_utils

    def _no_llm(**_kw):
        raise AssertionError("no model call to explain a spent cap")

    monkeypatch.setattr(services.llm_utils, "build_background_llm", _no_llm)
    why = ls.spend_decision("interactive", 13.4, ls.SpendLimits())

    async def _refused(*args, **kwargs):
        raise ls.LLMSpendCapReached(why.reason, why)
        yield  # pragma: no cover — an async generator that raises at once

    monkeypatch.setattr(chat_router, "stream_with_memory_events", _refused)
    events = [e async for e in chat_router._wrap_chat_stream(
        message="hi", conversation_id="conv-1", system_prompt="", model="m", temperature=0.7, graph=None,
        attachments=None, provider="anthropic")]
    contents = [e["content"] for e in events if isinstance(e, dict) and e.get("type") == "content"]
    assert contents == [why.reason]
    assert any(isinstance(e, dict) and e.get("type") == "done" for e in events)


@pytest.mark.integration
async def test_a_series_that_cannot_be_read_leaves_the_rest_of_the_block(clean_db, chicago, at_one_pm, monkeypatch):
    await _seed()

    async def _boom(*a, **k):
        raise RuntimeError("unknown time zone")

    monkeypatch.setattr(ls, "_db_by_day", _boom)
    block = await ls.claude_block(days=7)
    assert "error" not in block
    assert block["spent_today_usd"] == pytest.approx(8.5)
    assert block["by_day"] is None
