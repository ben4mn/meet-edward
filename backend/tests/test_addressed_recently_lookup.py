"""The recipient rule's fallback lookup (``policy._addressed_in_heartbeat``,
until AREA 3's ``ping_service`` lands) on a busy day.

It loaded the 500 newest iMessage heartbeat events across every chat and
filtered by chat in Python; production logged 547, 459 and 433 iMessage
events in single days, so an @mention in a quieter chat earlier in the
24 h window fell outside those rows and a mention addressed to Edward went
unanswered (review 2026-10-11). Test database only (:5433).
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from services.toolset import policy as P


def _event(i, *, chat, text, at, from_user=False, owner_direct=False):
    from services.database import HeartbeatEventModel

    raw = {"text": text}
    if owner_direct:
        raw["owner_direct"] = True
    return HeartbeatEventModel(id=f"ev-{i}", source="imessage", event_type="message_received", sender=f"+1201555{i:04d}",
                               chat_identifier=chat, summary=text[:200], raw_data=json.dumps(raw),
                               is_from_user=from_user, created_at=at, source_id=f"src-{i}")


async def test_an_old_mention_survives_a_busy_day(clean_db):
    from services.database import async_session

    now = datetime.utcnow()
    async with async_session() as s:
        s.add(_event(0, chat="chat-family", text="@Edward what time is dinner?", at=now - timedelta(hours=18)))
        s.add(_event(1, chat="chat-self", text="edward remind me at 5", at=now - timedelta(hours=17),
                     from_user=True, owner_direct=True))
        s.add_all([_event(10 + i, chat=f"chat-busy-{i % 7}", text=f"lol {i}", at=now - timedelta(minutes=i))
                   for i in range(600)])
        await s.commit()
    assert await P._addressed_in_heartbeat("chat-family", 24) is True
    assert await P._addressed_in_heartbeat("chat-self", 24) is True
    assert await P._addressed_in_heartbeat("chat-busy-3", 24) is False
    assert await P._addressed_in_heartbeat("chat-family", 12) is False
