"""Label-scoped safety gate for proactive push eligibility.

This is intentionally not a conversation safety block. It only makes a
candidate label temporarily ineligible for proactive pushes when recent user
messages tied to that label/chat contain high-risk self-harm or extreme
violence phrases.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import func, select

from services.database import HeartbeatEventModel, async_session

logger = logging.getLogger(__name__)

SELF_HARM_RISK_LABEL_COOLDOWN_REASON = "self_harm_risk_label_cooldown"
DEFAULT_LOOKBACK_DAYS = 14
DEFAULT_COOLDOWN_DAYS = 7

_HIGH_RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("self_harm.kill_myself", re.compile(r"\b(?:i\s+)?(?:want\s+to\s+|might\s+|may\s+|gonna\s+|going\s+to\s+)?kill\s+myself\b", re.I)),
    ("self_harm.suicide", re.compile(r"\b(?:suicide|suicidal)\b", re.I)),
    ("self_harm.end_it_all", re.compile(r"\bend\s+it\s+all\b", re.I)),
    ("self_harm.take_my_life", re.compile(r"\btake\s+my\s+own\s+life\b", re.I)),
    ("self_harm.want_to_die", re.compile(r"\b(?:i\s+)?(?:want|wanna|wish)\s+(?:to\s+)?die\b", re.I)),
    ("self_harm.hurt_myself", re.compile(r"\b(?:hurt|harm)\s+myself\b", re.I)),
    ("self_harm.overdose", re.compile(r"\boverdose\b", re.I)),
    ("extreme_violence.mass_shooting", re.compile(r"\bmass\s+shoot(?:ing)?\b", re.I)),
    ("extreme_violence.shoot_up", re.compile(r"\bshoot\s+up\s+(?:the\s+)?(?:school|office|work|workplace|building|place|party|church|store|mall)\b", re.I)),
    ("extreme_violence.kill_everyone", re.compile(r"\bkill\s+(?:everyone|everybody|them\s+all|all\s+of\s+them)\b", re.I)),
    ("extreme_violence.bomb_threat", re.compile(r"\b(?:build|plant|set\s+off|detonate)\s+(?:a\s+)?bomb\b", re.I)),
)


@dataclass(frozen=True)
class PushLabelSafetyDecision:
    eligible: bool
    reason: str | None = None
    label: str | None = None
    chat_identifier: str | None = None
    matched_at: datetime | None = None
    cooldown_until: datetime | None = None
    matched_pattern: str | None = None
    lookback_days: int = DEFAULT_LOOKBACK_DAYS
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS

    def metadata(self) -> dict[str, Any]:
        return {
            "push_eligibility": {
                "rule": "recent_label_safety_cooldown",
                "decision": "suppress" if not self.eligible else "allow",
                "reason": self.reason,
                "label": self.label,
                "chat_identifier": self.chat_identifier,
                "matched_at": self.matched_at.isoformat() if self.matched_at else None,
                "cooldown_until": (
                    self.cooldown_until.isoformat() if self.cooldown_until else None
                ),
                "matched_pattern": self.matched_pattern,
                "lookback_days": self.lookback_days,
                "cooldown_days": self.cooldown_days,
            }
        }


def _normalize(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).casefold()


def classify_high_risk_push_label_text(text: str | None) -> str | None:
    """Return a narrow high-risk category for push cooldown gating."""
    normalized = _normalize(text)
    if not normalized:
        return None
    for name, pattern in _HIGH_RISK_PATTERNS:
        if pattern.search(normalized):
            return name
    return None


def raw_data_with_push_suppression(
    raw_data: str | None,
    *,
    reason: str,
    extra: dict[str, Any] | None = None,
) -> str:
    try:
        parsed = json.loads(raw_data) if raw_data else {}
        payload = parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}

    payload["do_not_push"] = True
    payload["do_not_push_reason"] = reason
    payload["push_learning_excluded"] = True
    payload["push_learning_exclusion_reason"] = reason
    if extra:
        payload.update(extra)
    return json.dumps(payload, default=str)


def raw_data_excludes_push_learning(raw_data: str | None) -> bool:
    try:
        parsed = json.loads(raw_data) if raw_data else {}
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    return (
        parsed.get("push_learning_excluded") is True
        or parsed.get("push_learning_exclusion_reason")
        == SELF_HARM_RISK_LABEL_COOLDOWN_REASON
        or parsed.get("do_not_push_reason") == SELF_HARM_RISK_LABEL_COOLDOWN_REASON
    )


async def check_recent_label_safety_cooldown(
    *,
    label: str | None,
    chat_identifier: str | None = None,
    now: datetime | None = None,
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    cooldown_days: int = DEFAULT_COOLDOWN_DAYS,
    max_messages: int = 80,
) -> PushLabelSafetyDecision:
    """Check recent user messages tied to a candidate label/chat.

    The lookup is deliberately scoped to the candidate chat when available.
    If no chat id is available, it falls back to user messages mentioning the
    label text.
    """
    label_text = (label or "").strip() or None
    chat_id = (chat_identifier or "").strip() or None
    if not label_text and not chat_id:
        return PushLabelSafetyDecision(eligible=True)

    now = now or datetime.utcnow()
    lookback_days = max(1, int(lookback_days))
    cooldown_days = max(1, int(cooldown_days))
    cutoff = now - timedelta(days=lookback_days)

    if chat_id:
        association_predicate = HeartbeatEventModel.chat_identifier == chat_id
    elif label_text:
        label_like = f"%{label_text.casefold()}%"
        association_predicate = func.lower(HeartbeatEventModel.summary).like(label_like)
    else:
        return PushLabelSafetyDecision(eligible=True)

    async with async_session() as session:
        rows = (await session.execute(
            select(HeartbeatEventModel.summary, HeartbeatEventModel.created_at)
            .where(
                HeartbeatEventModel.is_from_user.is_(True),
                HeartbeatEventModel.created_at >= cutoff,
                association_predicate,
            )
            .order_by(HeartbeatEventModel.created_at.desc())
            .limit(max_messages)
        )).all()

    for summary, created_at in rows:
        matched_pattern = classify_high_risk_push_label_text(summary)
        if not matched_pattern or created_at is None:
            continue
        cooldown_until = created_at + timedelta(days=cooldown_days)
        if cooldown_until <= now:
            continue

        decision = PushLabelSafetyDecision(
            eligible=False,
            reason=SELF_HARM_RISK_LABEL_COOLDOWN_REASON,
            label=label_text,
            chat_identifier=chat_id,
            matched_at=created_at,
            cooldown_until=cooldown_until,
            matched_pattern=matched_pattern,
            lookback_days=lookback_days,
            cooldown_days=cooldown_days,
        )
        logger.info(
            "self_harm_risk_label_cooldown label=%r chat=%r pattern=%s "
            "matched_at=%s cooldown_until=%s",
            label_text,
            chat_id,
            matched_pattern,
            created_at,
            cooldown_until,
        )
        return decision

    return PushLabelSafetyDecision(
        eligible=True,
        label=label_text,
        chat_identifier=chat_id,
        lookback_days=lookback_days,
        cooldown_days=cooldown_days,
    )
