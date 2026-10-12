"""Quiet hours come from ``EDWARD_QUIET_HOURS`` (default 22–7), and the
reach/trust seams read the host from ``host_config`` — the public build's
knobs for what used to be one household's constants."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from services import approvals_service, host_config, owner_channel, reach_service
from services import speak_gate as g

pytestmark = pytest.mark.unit

TZ = ZoneInfo("America/Chicago")


def at(h, m=0):
    return datetime(2026, 10, 3, h, m, tzinfo=TZ)


@pytest.mark.parametrize("raw,expected", [
    ("22-7", (22, 7)), (" 23 - 6 ", (23, 6)), ("1–5", (1, 5)), ("0-0", (0, 0)),
    ("", None), (None, None), ("22", None), ("25-7", None), ("ten-seven", None),
])
def test_parse_quiet_hours(raw, expected):
    assert g.parse_quiet_hours(raw) == expected


def test_the_default_is_ten_to_seven(monkeypatch):
    monkeypatch.delenv("EDWARD_QUIET_HOURS", raising=False)
    assert g.quiet_hours() == (22, 7) == g.DEFAULT_QUIET_HOURS
    monkeypatch.setenv("EDWARD_QUIET_HOURS", "not hours")
    assert g.quiet_hours() == (22, 7)


@pytest.mark.parametrize("hour,start,end,quiet", [
    (23, 22, 7, True), (3, 22, 7, True), (7, 22, 7, False), (12, 22, 7, False),
    (1, 1, 6, True), (6, 1, 6, False), (0, 1, 6, False), (5, 0, 0, False),
])
def test_in_quiet_hours(hour, start, end, quiet):
    assert g.in_quiet_hours(hour, start, end) is quiet


def _household_push(closes_at):
    src = g.Source(ref="hb:1", chat="courier", at=at(closes_at.hour - 1 if closes_at.hour else 0, 0), from_me=False)
    return g.Noticing(id="n", kind="household", category="household", claim="c", sources=(src,), chat="courier",
                      who=("Courier",), closes_at=closes_at, suggested="push", dedupe_key="k", sensitive=False)


def _ctx(now):
    return g.GateContext(now=now, push_scope="gate", quota="ok")


def test_the_gate_reads_the_configured_window(monkeypatch):
    monkeypatch.delenv("EDWARD_QUIET_HOURS", raising=False)
    n = _household_push(at(23, 55))
    assert g.decide(n, _ctx(at(23, 10))).reason == "quiet hours"
    monkeypatch.setenv("EDWARD_QUIET_HOURS", "0-6")
    assert g.decide(n, _ctx(at(23, 10))).channel == "push"


def test_reach_says_the_configured_window(monkeypatch):
    monkeypatch.delenv("EDWARD_QUIET_HOURS", raising=False)
    assert reach_service.quiet_hours_label() == "10 pm–7 am"
    monkeypatch.setenv("EDWARD_QUIET_HOURS", "23-6")
    assert reach_service.quiet_hours_label() == "11 pm–6 am"


def test_own_app_urls_follow_the_public_base_url(monkeypatch):
    monkeypatch.setenv("EDWARD_PUBLIC_BASE_URL", "https://edward.example.org")
    monkeypatch.setattr(host_config, "local_hostname", lambda: "studio-mac.local")
    assert approvals_service.is_own_app_url("https://edward.example.org/today")
    assert approvals_service.is_own_app_url("https://www.edward.example.org/today")
    assert approvals_service.is_own_app_url("http://studio-mac.local:3000/chat")
    assert approvals_service.is_own_app_url("http://localhost:3000/chat")
    assert not approvals_service.is_own_app_url("https://example.org/")
    assert not approvals_service.is_own_app_url("https://evil.local/")


def test_a_text_links_to_the_public_base_url(monkeypatch):
    monkeypatch.delenv("EDWARD_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("EDWARD_PUBLIC_SCREEN_URL", raising=False)
    assert owner_channel.public_url("/today") == "http://localhost:3000/today"
    monkeypatch.setenv("EDWARD_PUBLIC_SCREEN_URL", "https://edward.example.org/screen")
    assert owner_channel.public_url("chat?c=1") == "https://edward.example.org/chat?c=1"
