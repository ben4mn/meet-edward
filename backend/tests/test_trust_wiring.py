"""The trust boundary's loose ends (plan Phase 6, 2026-09-24).

A send held for Ellis's approval did not deliver anything, so the ledger must
not count it as delivered; and the Twilio reply is sent by the webhook
router, not by a tool, so it never passed the egress secret scan.
"""
from __future__ import annotations

import pytest

from services import event_ledger
from routers import webhooks

pytestmark = pytest.mark.unit


def test_a_held_send_is_not_delivered():
    held = [{"n": "send_imessage", "ok": True, "held": True}]
    assert event_ledger.delivered(held) is False


def test_a_real_send_still_counts():
    assert event_ledger.delivered([{"n": "send_imessage", "ok": True}]) is True


def test_a_twilio_reply_carrying_a_secret_is_replaced(monkeypatch):
    recorded = []
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: recorded.append(kw))
    key = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
    out = webhooks.scrub_outbound_reply(f"here is the key {key}")
    assert key not in out
    assert "credential" in out.lower()
    assert recorded and recorded[0]["kind"] == "egress" and recorded[0]["outcome"] == "blocked"
    assert key not in (recorded[0].get("reason") or "")


def test_an_ordinary_twilio_reply_passes_untouched(monkeypatch):
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: None)
    text = "Dinner is at 7 at Lupa, table under Ellis. Call 555-123-4567 if late."
    assert webhooks.scrub_outbound_reply(text) == text


def test_the_scan_switch_turns_the_reply_check_off(monkeypatch):
    monkeypatch.setattr(event_ledger, "record_event", lambda **kw: None)
    monkeypatch.setenv("EDWARD_EGRESS_SECRET_SCAN", "off")
    key = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8S9t0"
    assert webhooks.scrub_outbound_reply(key) == key


def test_a_held_send_is_a_need_on_today():
    from datetime import datetime, timedelta, timezone
    from services.today_service import TodayInputs, compose_today

    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    row = {
        "id": 7, "status": "pending", "tool_name": "send_imessage",
        "summary": "iMessage to Parker: running 10 min late",
        "conversation_id": "c1", "created_at": (now - timedelta(minutes=5)).replace(tzinfo=None),
    }
    out = compose_today(TodayInputs(approvals=[row]), now=now)
    need = next(n for n in out["needs"] if n["kind"] == "approval")
    assert "Parker" in need["detail"]
    assert [a["id"] for a in need["actions"]][:2] == ["approve", "deny"]


def test_an_expired_approval_is_not_a_need():
    from datetime import datetime, timedelta, timezone
    from services.today_service import TodayInputs, compose_today

    now = datetime(2026, 9, 24, 12, 0, tzinfo=timezone.utc)
    row = {"id": 8, "status": "pending", "tool_name": "send_message", "summary": "x",
           "conversation_id": "c1", "created_at": (now - timedelta(hours=30)).replace(tzinfo=None)}
    out = compose_today(TodayInputs(approvals=[row]), now=now)
    assert not [n for n in out["needs"] if n["kind"] == "approval"]
