"""Triage decision trail + the owner-preempt metric.

Why (2026-09-02): over two weeks Edward acted on hundreds of inbound
iMessage events, and many landed while the owner was already replying to
the same thread. Nothing recorded *why* an event was acted on, nor *when*
that happened relative to the owner's own reply — so any change to the act
policy was unmeasurable. This module is the measurement.

Two halves:

* ``record_decision`` — one durable row per decision stage (settle, layer3,
  dispatch, delivery, ...). Never raises: a logging failure must not take
  down a triage cycle. (Its writer, the legacy engine's settle-before-act
  queue, is gone; the function stays for any stage that wants a row.)
* ``compute_preempt_metric`` — pure. An action is *preempted* when the
  owner's own outbound message in the same chat lands after the inbound
  event and no later than ``PREEMPT_GRACE_SECONDS`` after Edward acted.
  That is the "Edward jumped in while I was answering" number
  (``GET /api/heartbeat/jarvis-metrics``).
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, Iterable, Optional, Sequence, Tuple

from services.database import async_session, TriageDecisionModel

PREEMPT_GRACE_SECONDS = 300


@dataclass(frozen=True)
class ActionRow:
    event_id: str
    chat_identifier: Optional[str]
    inbound_at: datetime
    acted_at: datetime
    contact: str = "?"


@dataclass(frozen=True)
class OwnerReplyRow:
    chat_identifier: Optional[str]
    at: datetime


@dataclass
class PreemptMetric:
    actions: int = 0
    preempted: int = 0
    by_contact: Dict[str, Tuple[int, int]] = field(default_factory=dict)  # contact → (actions, preempted)

    @property
    def preempt_rate(self) -> float:
        return (self.preempted / self.actions) if self.actions else 0.0

    def as_dict(self) -> Dict[str, Any]:
        return {
            "actions": self.actions,
            "preempted": self.preempted,
            "preempt_rate": round(self.preempt_rate, 4),
            "grace_seconds": PREEMPT_GRACE_SECONDS,
            "by_contact": {
                c: {"actions": a, "preempted": p, "preempt_rate": round(p / a, 4) if a else 0.0}
                for c, (a, p) in sorted(self.by_contact.items(), key=lambda kv: -kv[1][0])
            },
        }


def _is_preempted(action: ActionRow, replies: Iterable[OwnerReplyRow]) -> bool:
    deadline = action.acted_at + timedelta(seconds=PREEMPT_GRACE_SECONDS)
    for r in replies:
        if r.chat_identifier != action.chat_identifier:
            continue
        if action.inbound_at < r.at <= deadline:
            return True
    return False


def compute_preempt_metric(actions: Sequence[ActionRow], replies: Sequence[OwnerReplyRow]) -> PreemptMetric:
    """Pure: preempted / actions, overall and per contact."""
    m = PreemptMetric()
    for a in actions:
        hit = _is_preempted(a, replies)
        m.actions += 1
        m.preempted += int(hit)
        prev_a, prev_p = m.by_contact.get(a.contact, (0, 0))
        m.by_contact[a.contact] = (prev_a + 1, prev_p + int(hit))
    return m


async def record_decision(
    event_id: str,
    *,
    stage: str,
    decision: str,
    reason_code: str,
    reason: str = "",
    cycle_id: Optional[str] = None,
    attention: Optional[Dict[str, Any]] = None,
    owner_replied_since: Optional[bool] = None,
    latency_ms: Optional[int] = None,
) -> None:
    """Persist one decision row. Swallows every error (logs it)."""
    try:
        async with async_session() as session:
            session.add(
                TriageDecisionModel(
                    id=str(uuid.uuid4()),
                    event_id=event_id,
                    cycle_id=cycle_id,
                    stage=stage[:20],
                    decision=decision[:30],
                    reason_code=reason_code[:60],
                    reason=reason or None,
                    attention=json.dumps(attention, default=str) if attention is not None else None,
                    owner_replied_since=owner_replied_since,
                    latency_ms=latency_ms,
                )
            )
            await session.commit()
    except Exception as exc:  # noqa: BLE001 — must never break a triage cycle
        print(f"[decision-trail] record failed for {event_id} ({stage}/{decision}): {exc}")


async def load_preempt_inputs(days: int) -> Tuple[list[ActionRow], list[OwnerReplyRow]]:
    """Read the rows the metric needs from ``heartbeat_events``.

    Until the settle work stamps ``inbound_at``/``acted_at`` on events, the
    row's ``created_at`` is used for both: the inbound time is when the
    listener stored it, and today Edward acts within the same cycle, so the
    baseline is a lower bound on the real preempt rate.
    """
    from sqlalchemy import select
    from services.database import HeartbeatEventModel

    since = datetime.utcnow() - timedelta(days=days)
    actions: list[ActionRow] = []
    replies: list[OwnerReplyRow] = []
    async with async_session() as session:
        rows = (
            await session.execute(
                select(HeartbeatEventModel).where(
                    HeartbeatEventModel.created_at >= since,
                    HeartbeatEventModel.source == "imessage",
                )
            )
        ).scalars().all()
    for r in rows:
        if r.is_from_user:
            replies.append(OwnerReplyRow(chat_identifier=r.chat_identifier, at=r.created_at))
        elif r.triage_status in ("acted", "escalated"):
            inbound = getattr(r, "inbound_at", None) or r.created_at
            acted = getattr(r, "acted_at", None) or r.notified_at or r.created_at
            actions.append(
                ActionRow(
                    event_id=r.id,
                    chat_identifier=r.chat_identifier,
                    inbound_at=inbound,
                    acted_at=acted,
                    contact=r.contact_name or r.sender or "?",
                )
            )
    return actions, replies
