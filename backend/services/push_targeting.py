"""Which push subscriptions a targeted push goes to — pure.

Moved verbatim out of ``interrupt_logic`` when the interrupt ladder was
retired (proactivity rebuild, 2026-10-11): ``push_service`` still targets
a push at one device (its ``device_id``) or one kind of device
(``kind:<kind>``, e.g. ``kind:iphone-pwa``); a target that matches nothing
falls back to broadcast there.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass(frozen=True)
class SubscriptionInfo:
    id: str
    device_id: Optional[str] = None
    kind: Optional[str] = None
    last_seen_at: Optional[datetime] = None


def push_target_matches(sub: SubscriptionInfo, target: Optional[str]) -> bool:
    if target is None:
        return True
    if target.startswith("kind:"):
        return sub.kind == target[5:]
    return sub.device_id == target
