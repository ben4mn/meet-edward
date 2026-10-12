"""Phase 3 (muse-and-beyond plan, 2026-09-24): one row per Codex call, and
the meter as a time series.

Why: the quota is one weekly window shared by every GPT model, and nothing
said who spent it. ``usage_events`` recorded Anthropic field names with
``source="chat"`` for every turn — cached and reasoning tokens invisible,
mind and pulse spend labelled chat — and ``codex exec`` threw its usage
away. ``codex_calls`` is written by ``ChatCodexResponses`` for every HTTP
request (success or failure; ``tests/test_codex_call_rows.py``) and by
``codex_cli_service`` for each ``turn.completed``; ``llm_meter_samples``
keeps each meter reading the gate adopts (≤ 1 row a minute per window) so
spend can be regressed on tokens.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def captured(monkeypatch):
    """Capture the ledger rows that get scheduled (no DB)."""
    from services import codex_call_ledger

    rows = []
    monkeypatch.setattr(codex_call_ledger, "schedule_call", lambda **fields: rows.append(fields))
    return rows


# ------------------------------------------------------------- the table --

@pytest.mark.integration
async def test_record_call_writes_a_row_and_bounds_its_strings(clean_db):
    from sqlalchemy import select
    from services import codex_call_ledger
    from services.database import async_session, CodexCallModel

    await codex_call_ledger.record_call(
        purpose="x" * 200, priority="background", turn_source="pulse", conversation_id="edward-surfaces",
        turn_id="t1", model="gpt-5.6-terra", effort="low", input_tokens=10, cached_tokens=4, output_tokens=3,
        reasoning_tokens=1, latency_ms=900, ttft_ms=400, status="ok", error_class=None, http_status=200,
        not_a_column="ignored",
    )
    async with async_session() as s:
        rows = (await s.execute(select(CodexCallModel))).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    assert row.turn_source == "pulse" and row.input_tokens == 10 and row.cached_tokens == 4
    assert len(row.purpose) <= 60
    assert row.created_at is not None


@pytest.mark.asyncio
async def test_record_call_never_raises(monkeypatch):
    from services import codex_call_ledger, database

    def _boom():
        raise RuntimeError("database is down")

    monkeypatch.setattr(database, "async_session", _boom)
    await codex_call_ledger.record_call(status="ok", model="m")  # must not raise
    await codex_call_ledger.record_meter_samples([{"window": "primary", "used_percent": 1.0}])


@pytest.mark.integration
async def test_prune_clears_old_calls_and_meter_samples(clean_db):
    from sqlalchemy import select, func
    from services import turn_ledger
    from services.database import async_session, CodexCallModel, LLMMeterSampleModel

    old = datetime.utcnow() - timedelta(days=turn_ledger.RETENTION_DAYS + 1)
    async with async_session() as s:
        s.add(CodexCallModel(created_at=old, status="ok"))
        s.add(CodexCallModel(created_at=datetime.utcnow(), status="ok"))
        s.add(LLMMeterSampleModel(sampled_at=old, window="primary", used_percent=10.0, source="headers"))
        await s.commit()
    deleted = await turn_ledger.prune()
    assert deleted["codex_calls"] == 1 and deleted["llm_meter_samples"] == 1
    async with async_session() as s:
        assert (await s.execute(select(func.count()).select_from(CodexCallModel))).scalar_one() == 1


# ---------------------------------------------------------- codex exec --

def test_exec_usage_is_read_from_turn_completed():
    from services.codex_cli_service import exec_usage

    assert exec_usage({"type": "turn.completed", "usage": {
        "input_tokens": 24763, "cached_input_tokens": 24448, "output_tokens": 122, "reasoning_output_tokens": 64,
    }}) == {"input_tokens": 24763, "cached_tokens": 24448, "output_tokens": 122, "reasoning_tokens": 64}
    assert exec_usage({"type": "turn.completed"}) == {
        "input_tokens": 0, "cached_tokens": 0, "output_tokens": 0, "reasoning_tokens": 0}


class _FakeStream:
    def __init__(self, lines):
        self._lines = [l.encode() for l in lines]

    async def readline(self):
        return self._lines.pop(0) if self._lines else b""


class _FakeProcess:
    def __init__(self, lines):
        self.stdout = _FakeStream(lines)
        self.stderr = _FakeStream([])
        self.returncode = 0

    async def wait(self):
        return 0

    def kill(self):
        pass


@pytest.mark.integration
async def test_codex_exec_turn_completed_is_recorded(clean_db, monkeypatch, captured, tmp_path):
    from services import codex_cli_service

    # No cwd = a fresh scratch directory (2026-09-27); keep it out of ~,
    # and don't make the ledger test depend on the installed CLI.
    monkeypatch.setenv("EDWARD_CODEX_SCRATCH_ROOT", str(tmp_path))
    monkeypatch.setattr(codex_cli_service, "codex_preflight", lambda: {"ok": True, "version": "test"})
    lines = [
        json.dumps({"type": "thread.started", "thread_id": "th_1"}) + "\n",
        json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "done"}}) + "\n",
        json.dumps({"type": "turn.completed", "usage": {"input_tokens": 900, "cached_input_tokens": 512,
                                                        "output_tokens": 40}}) + "\n",
    ]

    async def fake_exec(*cmd, **kwargs):
        return _FakeProcess(lines)

    monkeypatch.setattr(codex_cli_service.asyncio, "create_subprocess_exec", fake_exec)
    events = [e async for e in codex_cli_service.run_codex("fix it", conversation_id="conv-9", model="gpt-6-astra")]
    assert events[-1]["status"] == "completed"
    assert len(captured) == 1
    row = captured[0]
    assert row["turn_source"] == "codex_exec" and row["model"] == "gpt-6-astra"
    assert (row["input_tokens"], row["cached_tokens"], row["output_tokens"]) == (900, 512, 40)
    assert row["conversation_id"] == "conv-9" and row["status"] == "ok"


# ------------------------------------------------------- meter samples --

def _utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).replace(tzinfo=None)


class _Clock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


def _headers(used=12.0, reset=3600.0, secondary=None):
    h = {"x-codex-primary-used-percent": str(used), "x-codex-primary-window-minutes": "10080",
         "x-codex-primary-reset-after-seconds": str(reset)}
    if secondary is not None:
        h.update({"x-codex-secondary-used-percent": str(secondary), "x-codex-secondary-window-minutes": "300",
                  "x-codex-secondary-reset-after-seconds": "100"})
    return h


def test_the_gate_emits_a_meter_sample_at_most_once_a_minute_per_window():
    from services.llm_rate_gate import LLMRateGate

    wall = _Clock()
    gate = LLMRateGate(wall_clock=wall)
    seen = []
    gate.on_meter_sample = lambda meter, source, windows: seen.append((source, tuple(windows), meter.windows["primary"].used_percent))

    gate.note_usage_headers(_headers(12))
    wall.now += 20
    gate.note_usage_headers(_headers(13))            # throttled
    wall.now += 45
    gate.note_usage_headers(_headers(14, secondary=3))
    gate.note_usage_snapshot({"rate_limit": {"primary_window": {"used_percent": 15, "limit_window_seconds": 604800,
                                                                "reset_after_seconds": 3000}}})  # same minute
    wall.now += 61
    gate.note_usage_snapshot({"rate_limit": {"primary_window": {"used_percent": 16, "limit_window_seconds": 604800,
                                                                "reset_after_seconds": 2900}}})
    assert seen == [
        ("headers", ("primary",), 12.0),
        ("headers", ("primary", "secondary"), 14.0),
        ("wham", ("primary",), 16.0),
    ]


def test_an_older_sample_or_a_429_is_not_a_meter_reading():
    from services.llm_rate_gate import LLMRateGate, QuotaMeter

    wall = _Clock()
    gate = LLMRateGate(wall_clock=wall)
    seen = []
    gate.on_meter_sample = lambda meter, source, windows: seen.append(source)
    gate.note_usage_headers(_headers(12))
    gate.note_meter(QuotaMeter.from_headers(_headers(11), now=wall.now - 500))  # older, ignored
    wall.now += 120
    gate.note_429('{"error": {"resets_in_seconds": 30}}')  # synthetic exhaustion, not a reading
    assert seen == ["headers"]


def test_a_gate_without_the_hook_is_unchanged():
    from services.llm_rate_gate import LLMRateGate

    gate = LLMRateGate(wall_clock=_Clock())
    gate.note_usage_headers(_headers(12))
    assert gate.meter.windows["primary"].used_percent == 12.0


def test_meter_sample_rows_carry_the_window_and_its_reset():
    from services.codex_call_ledger import meter_sample_rows
    from services.llm_rate_gate import QuotaMeter

    meter = QuotaMeter.from_headers(_headers(40, reset=7200, secondary=5), now=1_700_000_000.0)
    rows = meter_sample_rows(meter, "headers", ["primary"])
    assert rows == [{
        "sampled_at": _utc(1_700_000_000.0),
        "window": "primary",
        "used_percent": 40.0,
        "window_minutes": 10080,
        "resets_at": _utc(1_700_000_000.0 + 7200),
        "source": "headers",
    }]


@pytest.mark.integration
async def test_meter_samples_land_in_the_table(clean_db, monkeypatch):
    from sqlalchemy import select
    from services import codex_call_ledger, llm_rate_gate
    from services.database import async_session, LLMMeterSampleModel

    monkeypatch.setattr(codex_call_ledger, "WRITES_ENABLED", True)
    monkeypatch.setattr(llm_rate_gate, "_gate", None)
    gate = llm_rate_gate.get_llm_rate_gate()
    gate.note_usage_headers(_headers(33, secondary=2))
    await codex_call_ledger.drain()
    async with async_session() as s:
        rows = (await s.execute(select(LLMMeterSampleModel).order_by(LLMMeterSampleModel.window))).scalars().all()
    assert [(r.window, r.used_percent, r.source) for r in rows] == [("primary", 33.0, "headers"), ("secondary", 2.0, "headers")]
    monkeypatch.setattr(llm_rate_gate, "_gate", None)
