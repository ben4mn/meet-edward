"""Lightweight flags for push-like heartbeat events."""

from __future__ import annotations

import json
import re
from typing import Any


KNOWN_ONE_WAY_RBM_LABELS = {
    "united airlines",
}

AIRLINE_RBM_BRAND_TERMS = {
    "airline",
    "airlines",
    "alaska airlines",
    "american airlines",
    "delta",
    "frontier",
    "jetblue",
    "southwest",
    "spirit airlines",
    "united",
    "united airlines",
}

RBM_BUSINESS_DOMAIN = "rbm.goog"
REPLY_AFFORDANCE_RE = re.compile(
    r"\b(reply|confirm|choose|tap|let\s+us\s+know)\b",
    re.IGNORECASE,
)
INPUT_REQUEST_RE = re.compile(
    r"\bplease\s+(send|select|respond|provide|enter|pick|choose|confirm|reply|tap)\b"
    r"|\b(select|pick|choose|confirm)\s+(an?|your|one)\b",
    re.IGNORECASE,
)
RBM_CONVERSATIONAL_CUE_RE = re.compile(
    r"\b("
    r"reply|respond|confirm|choose|select|pick|tap|send|provide|enter|call|text|"
    r"need\s+you\s+to|we\s+need\s+you\s+to|let\s+us\s+know|"
    r"can\s+you|could\s+you|would\s+you|do\s+you|are\s+you|will\s+you"
    r")\b",
    re.IGNORECASE,
)
QUICK_REPLY_KEYS = {
    "quickreplies",
    "quickreplychips",
    "suggestedactions",
    "suggestedreplies",
    "chips",
}


def _normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def _has_rbm_business_domain(value: str | None) -> bool:
    text = _normalize(value)
    if not text:
        return False
    match = re.search(r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})", text)
    domain = match.group(1).lower() if match else text
    return domain == RBM_BUSINESS_DOMAIN or domain.endswith("." + RBM_BUSINESS_DOMAIN)


def is_known_one_way_branded_rbm_sender(
    *,
    sender: str | None,
    label: str | None,
) -> bool:
    """Known branded RBM senders that should not train reply expectations."""
    if not (_has_rbm_business_domain(sender) or _has_rbm_business_domain(label)):
        return False

    normalized_sender = _normalize(sender)
    normalized_label = _normalize(label)
    return any(
        known in normalized_label or known in normalized_sender
        for known in KNOWN_ONE_WAY_RBM_LABELS
    )


def _contains_reply_affordance(value: Any) -> bool:
    if isinstance(value, str):
        return bool(REPLY_AFFORDANCE_RE.search(value))
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized_key = re.sub(r"[^a-z]", "", _normalize(str(key)))
            if normalized_key in QUICK_REPLY_KEYS and nested:
                return True
            if _contains_reply_affordance(nested):
                return True
    if isinstance(value, list):
        return any(_contains_reply_affordance(item) for item in value)
    return False


def has_clear_reply_affordance(*, text: str | None, raw_data: str | None) -> bool:
    """Return True when a push gives the user an obvious way/reason to reply."""
    if "?" in (text or "") or INPUT_REQUEST_RE.search(text or "") or _contains_reply_affordance(text):
        return True

    try:
        parsed = json.loads(raw_data) if raw_data else {}
    except (TypeError, json.JSONDecodeError):
        return False
    return _contains_reply_affordance(parsed)


def rbm_conversational_cue_score(*, text: str | None, raw_data: str | None = None) -> int:
    """Score lightweight cues that an RBM update expects user action."""
    body = text or ""
    score = body.count("?")
    score += len(RBM_CONVERSATIONAL_CUE_RE.findall(body))
    if INPUT_REQUEST_RE.search(body):
        score += 1
    if raw_data and has_clear_reply_affordance(text=text, raw_data=raw_data):
        score += 1
    return score


def is_transactional_rbm_update_without_action_request(
    *,
    sender: str | None,
    text: str | None,
    raw_data: str | None = None,
) -> bool:
    """Generic RBM business update suppression, scoped to RBM sender suffix."""
    if not _normalize(sender).endswith("@rbm.goog"):
        return False
    if not (text or "").strip():
        return False
    return rbm_conversational_cue_score(text=text, raw_data=raw_data) == 0


def is_airline_rbm_sender(
    *,
    sender: str | None,
    label: str | None,
    chat_id: str | None = None,
) -> bool:
    if not (
        _has_rbm_business_domain(sender)
        or _has_rbm_business_domain(label)
        or _has_rbm_business_domain(chat_id)
    ):
        return False

    haystack = " ".join(
        part
        for part in (_normalize(sender), _normalize(label), _normalize(chat_id))
        if part
    )
    if not any(term in haystack for term in AIRLINE_RBM_BRAND_TERMS):
        return False

    return True


def is_informational_airline_rbm_push(
    *,
    sender: str | None,
    label: str | None,
    chat_id: str | None = None,
    text: str | None = None,
    raw_data: str | None = None,
) -> bool:
    """Airline RBM senders are informational unless they ask for user action."""
    if not is_airline_rbm_sender(sender=sender, label=label, chat_id=chat_id):
        return False

    return not has_clear_reply_affordance(text=text, raw_data=raw_data)


def mark_no_reply_expected(raw_data: str | None, *, reason: str) -> str:
    payload: dict[str, Any]
    try:
        parsed = json.loads(raw_data) if raw_data else {}
        payload = parsed if isinstance(parsed, dict) else {}
    except (TypeError, json.JSONDecodeError):
        payload = {}

    payload["informational_push"] = True
    payload["no_reply_expected"] = True
    payload["no_reply_expected_reason"] = reason
    return json.dumps(payload)


def no_reply_expected_from_raw_data(raw_data: str | None) -> bool:
    try:
        parsed = json.loads(raw_data) if raw_data else {}
    except (TypeError, json.JSONDecodeError):
        return False
    if not isinstance(parsed, dict):
        return False
    return bool(parsed.get("no_reply_expected") or parsed.get("informational_push"))
