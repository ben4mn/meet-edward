"""The Claude spend ledger and the hard daily cap (2026-10-10).

Under ``EDWARD_LLM_PROVIDER=anthropic`` there is no weekly Codex window to
pace against: every token is paid for from a prepaid balance. So every
Claude call writes one ``llm_calls`` row priced in dollars
(``services/llm_pricing.py``), and a cap on Ellis's local day decides:

* ``background`` work stops once today's spend reaches
  ``EDWARD_DAILY_SPEND_CAP_USD`` × ``EDWARD_BACKGROUND_SPEND_SHARE``
  (default $10 × 0.6) and raises the deferral the loops already absorb;
* Edward's own turns (wakes, the pulse) stop at the cap;
* Ellis's turns run on into ``EDWARD_INTERACTIVE_RESERVE_USD`` ($3) past the
  cap, so he is always answered, and stop past it with
  ``LLMSpendCapReached``, whose message Edward can say as it is.

The day's total is cached in memory for a few seconds and bumped by every
call this process records, so the check costs nothing per call; a cold
cache (a restart) reads the table, so the stop survives a bounce.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from services import llm_spend as ls
from services.llm_rate_gate import LLMRateLimitDeferred

CHI = ZoneInfo("America/Chicago")
OPUS_MTOK_IN = {"input_tokens": 1_000_000}      # $4.00 on claude-opus-5-5


@pytest.fixture
def chicago(monkeypatch):
    from services import timezone_utils

    monkeypatch.setattr(timezone_utils, "get_user_tz", lambda: CHI)
    monkeypatch.setattr(timezone_utils, "current_tz_name", lambda: "America/Chicago")


class _Clock:
    def __init__(self):
        # 1 pm Monday 12 Oct in Springfield (CDT, UTC-5).
        self.now = datetime(2026, 10, 12, 18, 0, tzinfo=timezone.utc)
        self.mono = 1000.0


@pytest.fixture
def clock(monkeypatch):
    c = _Clock()
    monkeypatch.setattr(ls, "_now_utc", lambda: c.now)
    monkeypatch.setattr(ls, "_clock", lambda: c.mono)
    return c


@pytest.fixture
def table(monkeypatch):
    """The ledger table as a dict of (start, end) → dollars; reads are counted."""

    class T:
        spent = 0.0
        reads = []
        fail = False

    async def _spent(start, end):
        T.reads.append((start, end))
        if T.fail:
            raise RuntimeError("database is down")
        return T.spent

    monkeypatch.setattr(ls, "_db_spent_between", _spent)
    return T


@pytest.fixture
def limits(monkeypatch):
    monkeypatch.setenv("EDWARD_DAILY_SPEND_CAP_USD", "10")
    monkeypatch.setenv("EDWARD_BACKGROUND_SPEND_SHARE", "0.6")
    monkeypatch.setenv("EDWARD_INTERACTIVE_RESERVE_USD", "3")


# --------------------------------------------------------------- pure --

@pytest.mark.unit
def test_provider_defaults_to_anthropic_and_reads_the_switch(monkeypatch):
    monkeypatch.delenv("EDWARD_LLM_PROVIDER", raising=False)
    assert ls.provider() == "anthropic" and ls.is_anthropic()
    for spelled in ("anthropic", "Anthropic ", "claude"):
        monkeypatch.setenv("EDWARD_LLM_PROVIDER", spelled)
        assert ls.provider() == "anthropic" and ls.is_anthropic()
    for spelled in ("codex", " Codex"):
        monkeypatch.setenv("EDWARD_LLM_PROVIDER", spelled)
        assert ls.provider() == "codex" and not ls.is_anthropic()


@pytest.mark.unit
def test_limits_come_from_the_environment_with_defaults(monkeypatch):
    for name in ("EDWARD_DAILY_SPEND_CAP_USD", "EDWARD_BACKGROUND_SPEND_SHARE", "EDWARD_INTERACTIVE_RESERVE_USD"):
        monkeypatch.delenv(name, raising=False)
    lim = ls.SpendLimits.from_env()
    # The owner's choice (2026-10-11): a $5 daily cap.
    assert ls.DEFAULT_DAILY_CAP_USD == 5.0
    assert (lim.cap, lim.share, lim.reserve) == (5.0, 0.6, 3.0)
    assert lim.background_stop == pytest.approx(3.0) and lim.hard_stop == pytest.approx(8.0)
    monkeypatch.setenv("EDWARD_DAILY_SPEND_CAP_USD", "25.5")
    monkeypatch.setenv("EDWARD_BACKGROUND_SPEND_SHARE", "0.5")
    monkeypatch.setenv("EDWARD_INTERACTIVE_RESERVE_USD", "5")
    lim = ls.SpendLimits.from_env()
    assert (lim.cap, lim.share, lim.reserve) == (25.5, 0.5, 5.0)


@pytest.mark.unit
def test_bad_limits_fall_back_to_defaults_and_the_share_is_clamped(monkeypatch):
    monkeypatch.setenv("EDWARD_DAILY_SPEND_CAP_USD", "ten dollars")
    monkeypatch.setenv("EDWARD_BACKGROUND_SPEND_SHARE", "1.7")
    monkeypatch.setenv("EDWARD_INTERACTIVE_RESERVE_USD", "-4")
    lim = ls.SpendLimits.from_env()
    assert lim.cap == 5.0 and lim.share == 1.0 and lim.reserve == 3.0


@pytest.mark.unit
def test_background_stops_at_the_share_of_the_cap():
    lim = ls.SpendLimits(cap=10.0, share=0.6, reserve=3.0)
    assert ls.spend_decision("background", 5.99, lim).allowed
    d = ls.spend_decision("background", 6.00, lim)
    assert not d.allowed and d.code == "background_share" and d.limit == pytest.approx(6.0)


@pytest.mark.unit
def test_owners_turns_run_into_the_reserve_and_stop_past_it():
    lim = ls.SpendLimits(cap=10.0, share=0.6, reserve=3.0)
    assert ls.spend_decision("interactive", 9.0, lim).allowed
    assert ls.spend_decision("interactive", 12.99, lim).allowed       # past the cap, inside the reserve
    d = ls.spend_decision("interactive", 13.0, lim)
    assert not d.allowed and d.code == "hard_stop" and d.limit == pytest.approx(13.0)


@pytest.mark.unit
def test_triage_is_treated_like_interactive():
    lim = ls.SpendLimits(cap=10.0, share=0.6, reserve=3.0)
    assert ls.spend_decision("triage", 12.0, lim).allowed
    assert ls.spend_decision("triage", 13.5, lim).code == "hard_stop"


@pytest.mark.unit
def test_edwards_own_turns_stop_at_the_cap_not_in_owners_reserve():
    lim = ls.SpendLimits(cap=10.0, share=0.6, reserve=3.0)
    assert ls.spend_decision("interactive", 9.5, lim, autonomous=True).allowed
    d = ls.spend_decision("interactive", 10.0, lim, autonomous=True)
    assert not d.allowed and d.code == "cap" and d.limit == pytest.approx(10.0)


@pytest.mark.unit
@pytest.mark.parametrize("purpose,turn_source,expected", [
    ("mind:morning", None, True),
    ("pulse", None, True),
    ("surplus:research", None, True),
    (None, "mind", True),
    ("turn:chat", "chat", False),
    ("turn:voice", "voice", False),
    ("routers.chat._astream_fresh_greeting", None, False),
    ("memory_service.extract", "chat", False),
])
def test_which_turns_are_edwards_own(purpose, turn_source, expected):
    assert ls.is_autonomous(purpose, turn_source) is expected


@pytest.mark.unit
def test_the_memory_work_of_owners_own_turn_runs_on_his_limit():
    """Review 2026-10-11: extraction of what Ellis says runs at background
    priority; past $6 it was refused and dropped (no queue, no retry), so a
    standing instruction he gave at 8 pm was never stored. Background work
    done for his own turn runs to cap + reserve, like the turn itself."""
    lim = ls.SpendLimits(cap=10.0, share=0.6, reserve=3.0)
    assert ls.spend_decision("background", 6.5, lim, owners_turn=True).allowed
    assert ls.spend_decision("background", 12.99, lim, owners_turn=True).allowed
    d = ls.spend_decision("background", 13.0, lim, owners_turn=True)
    assert not d.allowed and d.code == "hard_stop" and d.limit == pytest.approx(13.0)
    assert not ls.spend_decision("background", 6.5, lim).allowed


@pytest.mark.unit
def test_the_background_stop_says_owners_words_are_still_learned():
    lim = ls.SpendLimits(cap=10.0, share=0.6, reserve=3.0)
    bg = ls.spend_decision("background", 6.5, lim).reason
    assert "what you tell me is still remembered" in bg and "$13.00" in bg


@pytest.mark.unit
def test_owners_turn_sources_are_the_ones_memory_trusts():
    from services.memory_service import OWNER_SPEAKS_SOURCES

    assert set(ls.OWNER_TURN_SOURCES) == set(OWNER_SPEAKS_SOURCES)


@pytest.mark.unit
def test_own_work_scope_marks_edwards_own_turns():
    assert not ls.in_own_work()
    with ls.own_work_scope():
        assert ls.in_own_work()
    assert not ls.in_own_work()


@pytest.mark.unit
def test_surplus_never_runs_on_pay_per_token():
    d = ls.spend_decision("surplus", 0.0, ls.SpendLimits())
    assert not d.allowed and d.code == "pay_per_token"


@pytest.mark.unit
def test_refusals_are_sentences_owner_can_read():
    lim = ls.SpendLimits(cap=10.0, share=0.6, reserve=3.0)
    hard = ls.spend_decision("interactive", 13.25, lim).reason
    assert "$13.25" in hard and "$10.00" in hard and "$3.00" in hard and "midnight" in hard
    assert "EDWARD_DAILY_SPEND_CAP_USD" in hard
    bg = ls.spend_decision("background", 6.5, lim).reason
    assert "$6.50" in bg and "$6.00" in bg and "midnight" in bg
    own = ls.spend_decision("interactive", 10.0, lim, autonomous=True).reason
    assert "$10.00" in own and "reserve" in own
    for text in (hard, bg, own):
        assert "None" not in text and "Traceback" not in text


@pytest.mark.unit
def test_local_day_bounds_follow_owners_zone():
    # 23:30 on Sunday 11 Oct in Springfield is 04:30 UTC on Monday.
    day, start, end = ls.local_day_bounds(datetime(2026, 10, 12, 4, 30, tzinfo=timezone.utc), CHI)
    assert day == date(2026, 10, 11)
    assert start == datetime(2026, 10, 11, 5, 0) and end == datetime(2026, 10, 12, 5, 0)
    assert start.tzinfo is None and end.tzinfo is None          # naive UTC, like the column
    day, start, _ = ls.local_day_bounds(datetime(2026, 10, 12, 5, 0, tzinfo=timezone.utc), CHI)
    assert day == date(2026, 10, 12) and start == datetime(2026, 10, 12, 5, 0)


@pytest.mark.unit
def test_call_row_prices_and_bounds_the_row():
    row = ls.call_row(
        model="claude-opus-5-5",
        usage={"input_tokens": 1_000_000, "cache_read_input_tokens": 10, "cache_creation_input_tokens": 20,
               "output_tokens": 30, "output_tokens_details": {"thinking_tokens": 7},
               "server_tool_use": {"web_search_requests": 2}},
        purpose="p" * 200, priority="background", latency_ms=1234.7, ttft_ms=None, ok=False,
        error="x" * 5000, conversation_id="c1", turn_source="chat", turn_id="t1",
    )
    assert row["provider"] == "anthropic" and row["model"] == "claude-opus-5-5"
    assert len(row["purpose"]) <= 60 and len(row["error"]) <= 500
    assert (row["input_tokens"], row["cache_read_tokens"], row["cache_write_tokens"], row["output_tokens"],
            row["thinking_tokens"], row["web_searches"]) == (1_000_000, 10, 20, 30, 7, 2)
    assert float(row["cost_usd"]) == pytest.approx(4.0 + 10 * 0.2e-6 + 20 * 5e-6 + 30 * 20e-6 + 0.02)
    assert row["latency_ms"] == 1235 and row["ttft_ms"] is None and row["ok"] is False
    assert isinstance(row["created_at"], datetime) and row["created_at"].tzinfo is None


# ------------------------------------------------- the in-memory day --

async def test_a_recorded_call_counts_toward_today_at_once(chicago, clock, table, limits):
    assert await ls.spent_today() == 0.0
    cost = ls.record_claude_call("claude-opus-5-5", OPUS_MTOK_IN, purpose="memory_service.extract",
                                 priority="background")
    assert cost == pytest.approx(4.0)
    assert await ls.spent_today() == pytest.approx(4.0)
    assert len(table.reads) == 1                  # counted in memory, not re-read


async def test_background_is_deferred_with_the_class_the_loops_already_absorb(chicago, clock, table, limits):
    await ls.check_spend("background")            # $0 of $6: fine
    ls.record_claude_call("claude-opus-5-5", OPUS_MTOK_IN, priority="background")
    ls.record_claude_call("claude-opus-5-5", {"input_tokens": 500_000}, priority="background")   # $6.00
    with pytest.raises(LLMRateLimitDeferred) as err:
        await ls.check_spend("background")
    assert isinstance(err.value, ValueError)      # the loops' except handlers take it as before
    assert "$6.00" in str(err.value)
    await ls.check_spend("interactive")           # Ellis is still answered


async def test_owners_turn_is_refused_only_past_cap_plus_reserve(chicago, clock, table, limits):
    table.spent = 12.5
    await ls.check_spend("interactive", purpose="turn:chat")
    await ls.check_spend("triage", purpose="routers.chat.greeting")
    ls.record_claude_call("claude-opus-5-5", {"input_tokens": 125_000}, purpose="turn:chat")     # +$0.50
    with pytest.raises(ls.LLMSpendCapReached) as err:
        await ls.check_spend("interactive", purpose="turn:chat")
    assert not isinstance(err.value, LLMRateLimitDeferred)
    assert err.value.decision.code == "hard_stop"
    assert "$13.00" in str(err.value) and "midnight" in str(err.value)


async def test_a_wake_stops_at_the_cap(chicago, clock, table, limits):
    from services.call_purpose import purpose_scope

    table.spent = 10.0
    with purpose_scope("mind:morning"):
        with pytest.raises(ls.LLMSpendCapReached) as err:
            await ls.check_spend("interactive")
    assert err.value.decision.code == "cap"
    await ls.check_spend("interactive", purpose="turn:chat")      # Ellis's reserve is untouched


from contextlib import contextmanager


@contextmanager
def _turn(source):
    """Run inside a turn whose source is ``source`` (None: no turn)."""
    from services import turn_ledger

    class _Rec:
        id = "turn-1"
        conversation_id = "conv-1"

    rec = None
    if source is not None:
        rec = _Rec()
        rec.source = source
    token = turn_ledger._current.set(rec)
    try:
        yield
    finally:
        turn_ledger._current.reset(token)


@pytest.mark.parametrize("source", ["chat", "voice", "widget", "alexa"])
async def test_extraction_of_owners_own_words_runs_past_the_background_line(chicago, clock, table, limits, source):
    table.spent = 6.5
    with _turn(source):
        await ls.check_spend("background", purpose="memory_service.extract_and_store_memories")


async def test_extraction_of_owners_own_words_stops_past_cap_plus_reserve(chicago, clock, table, limits):
    table.spent = 13.0
    with _turn("chat"):
        with pytest.raises(LLMRateLimitDeferred):
            await ls.check_spend("background", purpose="memory_service.extract_and_store_memories")


@pytest.mark.parametrize("source", [None, "mind", "pulse", "scheduled_event", "heartbeat", "worker"])
async def test_background_work_that_is_not_owners_turn_still_stops_at_the_share(chicago, clock, table, limits,
                                                                                source):
    table.spent = 6.5
    with _turn(source):
        with pytest.raises(LLMRateLimitDeferred):
            await ls.check_spend("background", purpose="memory_service.extract_and_store_memories")


async def test_edwards_own_work_inside_a_chat_record_is_not_owners(chicago, clock, table, limits):
    """A wake's scope or the own-work scope wins over a turn source."""
    from services.call_purpose import purpose_scope

    table.spent = 6.5
    with _turn("chat"), purpose_scope("mind:morning"):
        with pytest.raises(LLMRateLimitDeferred):
            await ls.check_spend("background")
    with _turn("chat"), ls.own_work_scope():
        with pytest.raises(LLMRateLimitDeferred):
            await ls.check_spend("background")


async def test_edwards_own_scheduled_work_stops_at_the_cap_not_in_owners_reserve(chicago, clock, table, limits):
    """Review 2026-10-11: a follow-up the mind scheduled ran as a
    ``scheduled_event`` turn counted as Ellis's and spent his reserve."""
    table.spent = 10.2
    with _turn("scheduled_event"), ls.own_work_scope():
        with pytest.raises(ls.LLMSpendCapReached) as err:
            await ls.check_spend("interactive")
    assert err.value.decision.code == "cap"
    with _turn("scheduled_event"):                 # an event Ellis asked for keeps his reserve
        await ls.check_spend("interactive")
    with _turn("chat"):
        await ls.check_spend("interactive")


async def test_surplus_is_always_deferred(chicago, clock, table, limits):
    with pytest.raises(LLMRateLimitDeferred):
        await ls.check_spend("surplus")


async def test_the_days_total_is_cached_briefly(chicago, clock, table, limits):
    await ls.check_spend("background")
    await ls.check_spend("background")
    assert len(table.reads) == 1
    table.spent = 7.0                              # another process wrote rows
    clock.mono += ls.CACHE_TTL_SECONDS + 1
    with pytest.raises(LLMRateLimitDeferred):
        await ls.check_spend("background")
    assert len(table.reads) == 2


async def test_near_the_cap_the_check_reads_the_table_again(chicago, clock, table, limits):
    """Review 2026-10-11 (minor): the total is cached for 30 s per process, so
    near the cap one process admitted calls against a total missing what the
    other (the graph worker) had just committed. Within $0.50 of a limit, a
    reading older than a couple of seconds is refreshed before admitting."""
    from services.call_purpose import purpose_scope

    table.spent = 9.80                              # the cap is $10 (``limits``)
    with purpose_scope("mind:free"):
        await ls.check_spend("interactive")
    assert len(table.reads) == 1                    # just read: no second read
    table.spent = 10.20                             # the graph worker committed $0.40 meanwhile
    clock.mono += 5                                 # well inside the 30 s cache
    with purpose_scope("mind:free"):
        with pytest.raises(ls.LLMSpendCapReached):
            await ls.check_spend("interactive")
    assert len(table.reads) == 2


async def test_near_the_background_line_too(chicago, clock, table, limits):
    table.spent = 5.70                              # the background line is $6
    await ls.check_spend("background")
    table.spent = 6.05
    clock.mono += 5
    with pytest.raises(LLMRateLimitDeferred):
        await ls.check_spend("background")


async def test_far_from_every_limit_the_cache_still_serves(chicago, clock, table, limits):
    table.spent = 2.0
    for _ in range(5):
        await ls.check_spend("interactive", purpose="turn:chat")
        await ls.check_spend("background")
        clock.mono += 5
    assert len(table.reads) == 1


async def test_a_new_local_day_starts_from_the_table_again(chicago, clock, table, limits):
    ls.record_claude_call("claude-opus-5-5", {"input_tokens": 2_000_000}, priority="background")   # $8
    with pytest.raises(LLMRateLimitDeferred):
        await ls.check_spend("background")
    clock.now = datetime(2026, 10, 13, 5, 1, tzinfo=timezone.utc)   # 00:01 Tuesday in Springfield
    table.spent = 0.0
    await ls.check_spend("background")
    start, end = table.reads[-1]
    assert start == datetime(2026, 10, 13, 5, 0) and end == datetime(2026, 10, 14, 5, 0)


async def test_a_row_that_never_lands_still_counts_after_a_reread(chicago, clock, table, limits, monkeypatch):
    monkeypatch.setattr(ls, "WRITES_ENABLED", True)

    async def _insert_fails(row):
        return False

    monkeypatch.setattr(ls, "_insert", _insert_fails)
    ls.record_claude_call("claude-opus-5-5", {"input_tokens": 2_000_000}, priority="background")   # $8
    await ls.drain()
    clock.mono += ls.CACHE_TTL_SECONDS + 1          # the next check re-reads an empty table
    with pytest.raises(LLMRateLimitDeferred):
        await ls.check_spend("background")


async def test_an_unreadable_table_never_blocks_owner(chicago, clock, table, limits):
    table.fail = True
    await ls.check_spend("interactive")           # fail open, with a warning in the log
    assert await ls.spent_today() == 0.0


async def test_recording_never_raises():
    assert ls.record_claude_call("claude-opus-5-5", object()) == 0.0
    assert ls.record_claude_call(None, None, purpose=None, priority=None) == 0.0


# --------------------------------------------------------- the table --

def _row(created_at: datetime, cost: float, **extra):
    from services.database import LLMCallModel

    fields = dict(created_at=created_at, provider="anthropic", model="claude-opus-5-5", purpose="turn:chat",
                  priority="interactive", input_tokens=1, cache_read_tokens=0, cache_write_tokens=0,
                  output_tokens=1, thinking_tokens=0, web_searches=0, cost_usd=cost, ok=True)
    fields.update(extra)
    return LLMCallModel(**fields)


async def _add(*rows):
    from services.database import async_session

    async with async_session() as s:
        for r in rows:
            s.add(r)
        await s.commit()


@pytest.mark.integration
async def test_record_claude_call_now_writes_a_row(clean_db, chicago):
    from sqlalchemy import select
    from services.database import async_session, LLMCallModel

    cost = await ls.record_claude_call_now(
        "claude-sonnet-5-5", {"input_tokens": 1000, "output_tokens": 200, "cache_read_input_tokens": 5000,
                              "server_tool_use": {"web_search_requests": 1}},
        purpose="mind:morning", priority="interactive", latency_ms=2500, ttft_ms=900, ok=True, error=None,
        conversation_id="edward-mind",
    )
    async with async_session() as s:
        rows = (await s.execute(select(LLMCallModel))).scalars().all()
    assert len(rows) == 1
    r = rows[0]
    assert (r.provider, r.model, r.purpose, r.priority, r.conversation_id) == (
        "anthropic", "claude-sonnet-5-5", "mind:morning", "interactive", "edward-mind")
    assert (r.input_tokens, r.cache_read_tokens, r.output_tokens, r.web_searches) == (1000, 5000, 200, 1)
    assert float(r.cost_usd) == pytest.approx(cost)
    assert cost == pytest.approx(1000 * 2e-6 + 5000 * 0.2e-6 + 200 * 10e-6 + 0.01)
    assert (r.latency_ms, r.ttft_ms, r.ok, r.error) == (2500, 900, True, None)
    assert abs((datetime.utcnow() - r.created_at).total_seconds()) < 60     # UTC, not the host's zone


@pytest.mark.integration
async def test_the_stop_holds_across_a_restart(clean_db, chicago, clock, limits):
    today = datetime(2026, 10, 12, 15, 0)          # 10 am in Springfield, naive UTC
    await _add(_row(today, 4.0), _row(today + timedelta(hours=1), 2.5))
    ls.reset_cache()                               # a fresh process knows nothing
    with pytest.raises(LLMRateLimitDeferred):
        await ls.check_spend("background")
    await ls.check_spend("interactive")
    assert await ls.spent_today() == pytest.approx(6.5)


@pytest.mark.integration
async def test_the_day_is_owners_local_day(clean_db, chicago, clock, limits):
    # Springfield's Monday starts at 05:00 UTC.
    await _add(_row(datetime(2026, 10, 12, 4, 59), 9.0),      # Sunday 23:59 local
               _row(datetime(2026, 10, 12, 5, 1), 1.25))       # Monday 00:01 local
    ls.reset_cache()
    assert await ls.spent_today() == pytest.approx(1.25)
    await ls.check_spend("background")


@pytest.mark.integration
async def test_fire_and_forget_writes_land_and_count(clean_db, chicago, monkeypatch):
    from sqlalchemy import func, select
    from services.database import async_session, LLMCallModel

    monkeypatch.setattr(ls, "WRITES_ENABLED", True)
    ls.record_claude_call("claude-haiku-5-5", {"input_tokens": 1000, "output_tokens": 100}, priority="background")
    ls.record_claude_call("claude-haiku-5-5", {"input_tokens": 2000, "output_tokens": 100}, priority="background")
    in_memory = await ls.spent_today()
    await ls.drain()
    async with async_session() as s:
        n, total = (await s.execute(select(func.count(), func.sum(LLMCallModel.cost_usd)))).one()
    assert n == 2
    assert float(total) == pytest.approx(in_memory)
    ls.reset_cache()
    assert await ls.spent_today() == pytest.approx(float(total))


@pytest.mark.integration
async def test_credits_remaining_and_days_left(clean_db, chicago, clock, monkeypatch):
    monkeypatch.setenv("EDWARD_CREDITS_START_USD", "100")
    monkeypatch.setenv("EDWARD_CREDITS_START_DATE", "2026-10-11")
    await _add(_row(datetime(2026, 10, 10, 20, 0), 50.0),     # before the credits were bought
               _row(datetime(2026, 10, 11, 12, 0), 3.0),
               _row(datetime(2026, 10, 12, 12, 0), 6.0))
    out = await ls.credits_status()
    assert out["start_usd"] == 100.0 and out["start_date"] == "2026-10-11"
    assert out["spent_usd"] == pytest.approx(9.0)
    assert out["remaining_usd"] == pytest.approx(91.0)
    # The burn reads the trailing three days, but no further back than the
    # start: 05:00 UTC on 11 Oct (Springfield midnight) to now is 37 hours.
    span_days = 37 / 24
    assert out["burn_per_day_usd"] == pytest.approx(9.0 / span_days)
    assert out["days_left"] == pytest.approx(91.0 / (9.0 / span_days))
    assert "console.anthropic.com" in out["note"] and "tracked locally" in out["note"]


@pytest.mark.unit
def test_credits_view_without_spend_has_no_days_left():
    out = ls.credits_view(start_usd=100.0, start_date=date(2026, 10, 11), spent=0.0, burn_spent=0.0,
                          burn_span_days=1.0)
    assert out["remaining_usd"] == 100.0 and out["burn_per_day_usd"] == 0.0 and out["days_left"] is None
    spent_out = ls.credits_view(start_usd=100.0, start_date=date(2026, 10, 11), spent=104.0, burn_spent=12.0,
                                burn_span_days=3.0)
    assert spent_out["remaining_usd"] == pytest.approx(-4.0) and spent_out["days_left"] == 0.0


@pytest.mark.integration
async def test_prune_keeps_the_claude_ledger_long_enough_for_the_credits(clean_db):
    from sqlalchemy import select
    from services import turn_ledger
    from services.database import async_session, LLMCallModel

    now = datetime.utcnow()
    await _add(_row(now - timedelta(days=40), 1.0, purpose="kept"),
               _row(now - timedelta(days=ls.RETENTION_DAYS + 1), 1.0, purpose="old"))
    deleted = await turn_ledger.prune()
    async with async_session() as s:
        left = (await s.execute(select(LLMCallModel.purpose))).scalars().all()
    assert left == ["kept"]
    assert deleted["llm_calls"] == 1
