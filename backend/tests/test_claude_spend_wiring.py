"""ChatClaude pays per token: every request it sends is checked against the
daily spend cap first and priced into ``llm_calls`` after (review
2026-10-11).

The spend branch built the cap (``llm_spend.check_spend`` behind
``LLMRateGate``) and the ledger (``llm_spend.record_claude_call``), and the
provider branch built ``ChatClaude`` — but nothing joined them: every Opus
and Haiku call went out ungated and was written to ``codex_calls`` instead,
so ``llm_calls`` stayed empty, the cap read $0.00 all day and the credits
never fell while the real balance drained. These tests drive ``ChatClaude``
over a fake ``AsyncAnthropic`` and pin the join:

* the cap is checked before **every** HTTP request — the first, each
  ``pause_turn`` continuation and the one retry — outside the code that
  records the request, so a refused call sends nothing and records nothing;
* each request is priced on its own usage (never ``_sum_usage``'s total,
  which kept only the first segment's ``iterations`` and added prompts
  together, pricing two 60K Haiku requests at the >100K card);
* failures are recorded too (``ok=False``); nothing goes to ``codex_calls``.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest
from langchain_core.messages import HumanMessage

from services import llm_spend as ls
from services.llm_rate_gate import LLMRateGate, LLMRateLimitDeferred
from tests.fixtures.fake_anthropic import FakeAnthropic, message, text, tool_use

CHI = ZoneInfo("America/Chicago")


async def _no_sleep(_s):
    return None


@pytest.fixture
def gate(monkeypatch):
    g = LLMRateGate(sleep=_no_sleep)
    monkeypatch.setattr("services.llm_rate_gate._gate", g)
    return g


@pytest.fixture
def anthropic(monkeypatch):
    monkeypatch.setenv("EDWARD_LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("EDWARD_DAILY_SPEND_CAP_USD", "10")
    monkeypatch.setenv("EDWARD_BACKGROUND_SPEND_SHARE", "0.6")
    monkeypatch.setenv("EDWARD_INTERACTIVE_RESERVE_USD", "3")
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "get_user_tz", lambda: CHI)
    monkeypatch.setattr(timezone_utils, "current_tz_name", lambda: "America/Chicago")


@pytest.fixture
def table(monkeypatch):
    """Today's spend in the table, and the rows written to it."""

    class T:
        spent = 0.0
        rows = []

    async def _spent(start, end):
        return T.spent

    async def _insert(row):
        T.rows.append(dict(row))
        return True

    monkeypatch.setattr(ls, "_db_spent_between", _spent)
    monkeypatch.setattr(ls, "_insert", _insert)
    monkeypatch.setattr(ls, "WRITES_ENABLED", True)
    return T


@pytest.fixture
def no_codex_rows(monkeypatch):
    from services import codex_call_ledger

    seen = []
    monkeypatch.setattr(codex_call_ledger, "schedule_call", lambda **row: seen.append(row))
    return seen


@pytest.fixture
def checks(monkeypatch):
    """Every spend check made, in order (priority, purpose)."""
    seen = []
    real = ls.check_spend

    async def _check(priority="interactive", purpose=None):
        seen.append((priority, purpose))
        return await real(priority, purpose)

    monkeypatch.setattr(ls, "check_spend", _check)
    return seen


def _llm(client, **kw):
    from services.claude_chat_model import ChatClaude

    kw.setdefault("model", "claude-opus-5-5")
    kw.setdefault("effort", "medium")
    kw.setdefault("retry_backoff", (0, 0))
    return ChatClaude(client=client, **kw)


USAGE = {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 5000,
         "cache_creation_input_tokens": 0}
OPUS_COST = 1000 * 4e-6 + 200 * 20e-6 + 5000 * 0.2e-6


# --------------------------------------------------------- the ledger --

@pytest.mark.unit
async def test_a_claude_call_is_priced_into_llm_calls_and_never_into_codex_calls(
        gate, anthropic, table, no_codex_rows):
    client = FakeAnthropic([message([text("Hello Ellis.")], usage=USAGE)])
    msg = await _llm(client, purpose="tests.caller").ainvoke([HumanMessage(content="hi")])
    await ls.drain()
    assert msg.content == "Hello Ellis."
    assert len(table.rows) == 1
    row = table.rows[0]
    assert (row["provider"], row["model"], row["purpose"], row["priority"], row["ok"]) == (
        "anthropic", "claude-opus-5-5", "tests.caller", "interactive", True)
    assert (row["input_tokens"], row["cache_read_tokens"], row["output_tokens"]) == (1000, 5000, 200)
    assert row["cost_usd"] == pytest.approx(OPUS_COST)
    assert row["latency_ms"] is not None and row["ttft_ms"] is not None
    assert await ls.spent_today() == pytest.approx(OPUS_COST)
    assert msg.response_metadata["estimated_cost_usd"] == pytest.approx(OPUS_COST)
    assert no_codex_rows == []


@pytest.mark.unit
async def test_each_pause_turn_request_is_priced_on_its_own_usage(gate, anthropic, table, checks):
    """Two 60K-token Haiku requests are two prompts under the 100K card,
    not one 120K prompt — and each is checked against the cap first."""
    srv = tool_use("srvtoolu_1", "web_search", {"query": "x"}, server=True)
    usage = {"input_tokens": 60_000, "output_tokens": 1_000, "cache_read_input_tokens": 0,
             "cache_creation_input_tokens": 0}
    client = FakeAnthropic([message([srv], stop_reason="pause_turn", model="claude-haiku-5-5", usage=usage),
                            message([text("Found it.")], model="claude-haiku-5-5", usage=usage)])
    msg = await _llm(client, model="claude-haiku-5-5", purpose="tests.search").ainvoke(
        [HumanMessage(content="news?")])
    await ls.drain()
    assert len(client.calls) == 2
    one = 60_000 * 0.10e-6 + 1_000 * 0.50e-6
    assert [r["cost_usd"] for r in table.rows] == [pytest.approx(one), pytest.approx(one)]
    assert msg.response_metadata["estimated_cost_usd"] == pytest.approx(2 * one)
    assert checks == [("interactive", "tests.search")] * 2


@pytest.mark.unit
async def test_a_failed_request_is_recorded_and_the_retry_is_checked_again(gate, anthropic, table, checks):
    client = FakeAnthropic([message([], error={"type": "overloaded_error", "message": "Overloaded"}),
                            message([text("second try")], usage=USAGE)])
    msg = await _llm(client, purpose="tests.retry").ainvoke([HumanMessage(content="hi")])
    await ls.drain()
    assert msg.content == "second try"
    assert [r["ok"] for r in table.rows] == [False, True]
    assert "overloaded" in (table.rows[0]["error"] or "")
    assert len(checks) == 2


# ------------------------------------------------------------ the cap --

@pytest.mark.unit
async def test_owners_turn_past_cap_and_reserve_sends_nothing_and_records_nothing(gate, anthropic, table):
    table.spent = 13.0
    client = FakeAnthropic([message([text("never")], usage=USAGE)])
    with pytest.raises(ls.LLMSpendCapReached) as err:
        await _llm(client).ainvoke([HumanMessage(content="hi")])
    await ls.drain()
    assert client.calls == []
    assert table.rows == []
    assert "midnight" in str(err.value)


@pytest.mark.unit
async def test_background_work_defers_at_the_share(gate, anthropic, table):
    table.spent = 6.0
    client = FakeAnthropic([message([text("never")], usage=USAGE)])
    with pytest.raises(LLMRateLimitDeferred):
        await _llm(client, model="claude-haiku-5-5", priority="background",
                   purpose="memory_service.extract").ainvoke([HumanMessage(content="extract")])
    assert client.calls == []
    assert table.rows == []


@pytest.mark.unit
async def test_remembering_what_owner_said_runs_past_the_background_line(gate, anthropic, table):
    """The extraction of Ellis's own chat turn is background work done for
    him: at $6.50 it still runs (review 2026-10-11); a wake's does not."""
    from types import SimpleNamespace
    from services import turn_ledger
    from services.call_purpose import purpose_scope

    table.spent = 6.5
    client = FakeAnthropic([message([text("[]")], model="claude-haiku-5-5", usage=USAGE)])
    token = turn_ledger._current.set(SimpleNamespace(source="chat", id="t1", conversation_id="c1"))
    try:
        await _llm(client, model="claude-haiku-5-5", priority="background",
                   purpose="memory_service").ainvoke([HumanMessage(content="never text my mom after 9 pm")])
        with purpose_scope("mind:evening"):
            with pytest.raises(LLMRateLimitDeferred):
                await _llm(client, model="claude-haiku-5-5", priority="background",
                           purpose="memory_service").ainvoke([HumanMessage(content="extract")])
    finally:
        turn_ledger._current.reset(token)
    assert len(client.calls) == 1


@pytest.mark.unit
async def test_a_continuation_that_would_pass_the_hard_stop_is_not_sent(gate, anthropic, table):
    """The first request lands past the line; its ``pause_turn``
    continuation is refused before it goes out."""
    table.spent = 12.99
    srv = tool_use("srvtoolu_1", "web_search", {"query": "x"}, server=True)
    first = message([srv], stop_reason="pause_turn", usage={"input_tokens": 10_000, "output_tokens": 100,
                                                            "cache_read_input_tokens": 0,
                                                            "cache_creation_input_tokens": 0})
    client = FakeAnthropic([first, message([text("never")], usage=USAGE)])
    with pytest.raises(ls.LLMSpendCapReached):
        await _llm(client).ainvoke([HumanMessage(content="news?")])
    await ls.drain()
    assert len(client.calls) == 1
    assert len(table.rows) == 1 and table.rows[0]["ok"] is True


@pytest.mark.unit
async def test_chatclaude_pays_per_token_whatever_the_switch_says(gate, table, monkeypatch):
    """A ChatClaude built directly (the smoke script) is still Claude: the
    cap and the ledger apply with the provider switch unset."""
    monkeypatch.delenv("EDWARD_LLM_PROVIDER", raising=False)
    table.spent = 20.0
    client = FakeAnthropic([message([text("never")], usage=USAGE)])
    with pytest.raises(ls.LLMSpendCapReached):
        await _llm(client).ainvoke([HumanMessage(content="hi")])
    assert client.calls == []


# ------------------------------------------------------- end to end --

@pytest.mark.integration
async def test_the_row_lands_in_the_table_and_the_next_call_sees_it(clean_db, gate, anthropic, monkeypatch):
    from sqlalchemy import select
    from services.database import async_session, LLMCallModel

    monkeypatch.setattr(ls, "WRITES_ENABLED", True)
    big = {"input_tokens": 3_250_000, "output_tokens": 0, "cache_read_input_tokens": 0,
           "cache_creation_input_tokens": 0}                  # $13.00 on Opus 5.5
    client = FakeAnthropic([message([text("one")], usage=big), message([text("two")], usage=USAGE)])
    await _llm(client, purpose="tests.e2e").ainvoke([HumanMessage(content="hi")])
    await ls.drain()
    async with async_session() as s:
        rows = (await s.execute(select(LLMCallModel))).scalars().all()
    assert len(rows) == 1 and float(rows[0].cost_usd) == pytest.approx(13.0)
    ls.reset_cache()                                          # a restart: the table is all it knows
    with pytest.raises(ls.LLMSpendCapReached):
        await _llm(client).ainvoke([HumanMessage(content="again")])
    assert len(client.calls) == 1
