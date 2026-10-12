"""Tests for the triage decision trail and the owner-preempt metric.

Why this exists (2026-09-02): Edward acted on 391 iMessage events in 14 days
and Ellis's voice memo says many of those landed while he was already replying.
Nothing recorded *why* an event was acted on or *when* relative to Ellis's own
reply, so the settle-before-act change would have been unmeasurable.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.heartbeat.decision_trail import (
    PREEMPT_GRACE_SECONDS,
    ActionRow,
    OwnerReplyRow,
    compute_preempt_metric,
    record_decision,
)

pytestmark = pytest.mark.unit

T0 = datetime(2026, 9, 2, 20, 0, tzinfo=timezone.utc)


def _action(chat="c1", inbound=T0, acted=T0 + timedelta(seconds=90), contact="Rowan"):
    return ActionRow(event_id="e", chat_identifier=chat, inbound_at=inbound, acted_at=acted, contact=contact)


def _reply(chat="c1", at=T0 + timedelta(seconds=60)):
    return OwnerReplyRow(chat_identifier=chat, at=at)


class TestComputePreemptMetric:
    def test_owner_reply_between_inbound_and_action_is_preempted(self):
        m = compute_preempt_metric([_action()], [_reply()])
        assert m.actions == 1 and m.preempted == 1 and m.preempt_rate == 1.0

    def test_owner_reply_shortly_after_action_still_counts(self):
        m = compute_preempt_metric([_action()], [_reply(at=T0 + timedelta(seconds=91))])
        assert m.preempted == 1

    def test_owner_reply_before_inbound_is_ignored(self):
        m = compute_preempt_metric([_action()], [_reply(at=T0 - timedelta(seconds=5))])
        assert m.preempted == 0 and m.preempt_rate == 0.0

    def test_owner_reply_after_grace_is_ignored(self):
        late = T0 + timedelta(seconds=90 + PREEMPT_GRACE_SECONDS + 1)
        m = compute_preempt_metric([_action()], [_reply(at=late)])
        assert m.preempted == 0

    def test_reply_in_another_chat_does_not_count(self):
        m = compute_preempt_metric([_action()], [_reply(chat="other")])
        assert m.preempted == 0

    def test_per_contact_breakdown(self):
        rows = [_action(contact="Rowan"), _action(chat="c2", contact="Parker")]
        m = compute_preempt_metric(rows, [_reply()])
        assert m.by_contact["Rowan"] == (1, 1)
        assert m.by_contact["Parker"] == (1, 0)

    def test_no_actions_gives_zero_rate_not_division_error(self):
        m = compute_preempt_metric([], [])
        assert m.actions == 0 and m.preempt_rate == 0.0


class TestRecordDecision:
    async def test_never_raises_when_db_is_down(self, monkeypatch):
        from services.heartbeat import decision_trail

        class Boom:
            async def __aenter__(self):
                raise RuntimeError("db down")

            async def __aexit__(self, *a):
                return False

        monkeypatch.setattr(decision_trail, "async_session", lambda: Boom())
        await record_decision("evt", stage="settle", decision="hold", reason_code="settle_base")


@pytest.mark.integration
async def test_record_decision_persists_and_metrics_endpoint_reads_it(clean_db):
    from httpx import ASGITransport, AsyncClient
    from services import database
    from services.database import HeartbeatEventModel, TriageDecisionModel
    from sqlalchemy import select

    now = datetime.utcnow()
    async with database.async_session() as session:
        session.add(HeartbeatEventModel(id="in1", source="imessage", event_type="message_received",
                                        chat_identifier="c1", contact_name="Rowan", summary="hi",
                                        is_from_user=False, triage_status="acted",
                                        created_at=now - timedelta(minutes=5)))
        session.add(HeartbeatEventModel(id="out1", source="imessage", event_type="message_sent",
                                        chat_identifier="c1", summary="yo", is_from_user=True,
                                        triage_status="dismissed", created_at=now - timedelta(minutes=4)))
        await session.commit()

    await record_decision("in1", stage="layer3", decision="acted", reason_code="act",
                          attention={"owner_present": True}, owner_replied_since=False, latency_ms=12)

    async with database.async_session() as session:
        rows = (await session.execute(select(TriageDecisionModel))).scalars().all()
    assert len(rows) == 1 and rows[0].reason_code == "act"

    from main import app
    from services.auth_service import create_access_token
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as client:
        r = await client.get("/api/heartbeat/jarvis-metrics?days=1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["actions"] == 1 and body["preempted"] == 1
