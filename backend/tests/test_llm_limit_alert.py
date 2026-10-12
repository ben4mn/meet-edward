"""When Codex rate-limits Edward, Ellis must find out — once, not 400 times.

Astra is the heaviest tier. If the subscription's rolling window trips, every
429 gets a WARNING with a stable ``CODEX_LIMIT`` tag (grep-able in
/tmp/edward-backend.log) and Ellis gets ONE static push per quiet hour. A push
per 429 would be the alert-fatigue failure the hub rules warn about.
"""

import logging

import pytest

from services.llm_limit_alert import LimitAlertPolicy, format_alert


class _Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


def test_first_limit_event_fires_push():
    clock = _Clock()
    policy = LimitAlertPolicy(clock=clock)
    assert policy.should_push(kind="429") is True


def test_second_event_within_an_hour_is_suppressed():
    clock = _Clock()
    policy = LimitAlertPolicy(clock=clock)
    policy.should_push(kind="429")
    clock.t += 600
    assert policy.should_push(kind="429") is False


def test_fires_again_after_quiet_hour():
    clock = _Clock()
    policy = LimitAlertPolicy(clock=clock)
    policy.should_push(kind="429")
    clock.t += 3601
    assert policy.should_push(kind="429") is True


def test_hard_cap_is_not_suppressed_by_a_recent_429():
    """usage_limit_reached is the plan's hard cap — a worse event than a
    cooldown, and Ellis should hear about it even inside the quiet hour."""
    clock = _Clock()
    policy = LimitAlertPolicy(clock=clock)
    policy.should_push(kind="429")
    clock.t += 60
    assert policy.should_push(kind="usage_limit_reached") is True


def test_counts_events_in_window():
    clock = _Clock()
    policy = LimitAlertPolicy(clock=clock)
    for _ in range(3):
        policy.record(kind="429")
        clock.t += 10
    assert policy.recent_count() == 3
    clock.t += 4 * 3600
    assert policy.recent_count() == 0


def test_alert_text_is_static_and_names_the_model():
    title, body = format_alert(kind="429", model="gpt-6-astra", recent=3, cooldown_s=1800)
    assert title == "Edward: Codex rate limit"
    assert "gpt-6-astra" in body
    assert "3" in body
    assert "30 min" in body


async def test_wrapper_reports_limit_without_blocking(monkeypatch):
    """The Codex wrapper schedules the alert as a task so a failing request
    never waits on push delivery."""
    import asyncio
    import services.codex_chat_model as ccm
    import services.llm_limit_alert as alert

    seen = []

    async def _fake(kind, model, cooldown_seconds):
        seen.append((kind, model, cooldown_seconds))

    monkeypatch.setattr(alert, "handle_limit_event", _fake)
    task = ccm._report_limit("429", "gpt-6-astra", 42.0)
    assert task is not None
    await asyncio.sleep(0)
    assert seen == [("429", "gpt-6-astra", 42.0)]


@pytest.mark.asyncio
async def test_handle_limit_event_logs_tag_and_pushes_once(monkeypatch, caplog):
    import services.llm_limit_alert as alert

    sent = []

    async def _fake_push(title, body, **kw):
        sent.append((title, body, kw))
        return {"sent": 1}

    monkeypatch.setattr(alert, "send_push_notification", _fake_push)
    clock = _Clock()
    alert._policy = LimitAlertPolicy(clock=clock)

    with caplog.at_level(logging.WARNING):
        await alert.handle_limit_event(kind="429", model="gpt-6-astra", cooldown_seconds=90)
        await alert.handle_limit_event(kind="429", model="gpt-6-astra", cooldown_seconds=90)

    tagged = [r for r in caplog.records if "CODEX_LIMIT" in r.getMessage()]
    assert len(tagged) == 2, "every event is logged"
    assert len(sent) == 1, "but only one push per quiet hour"
    assert sent[0][2].get("tag") == "codex-limit"
