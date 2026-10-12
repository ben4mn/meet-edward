"""Every journal entry is visible in the sidebar (2026-09-25).

The default listing hides Edward's own conversations unless Ellis engaged
(> 2 messages) or Edward reached out (``notified_user`` and > 1 message).
A fresh journal entry has one message, and a commitment-only entry (he
scheduled something, set a goal, stood down) has no unread dot either —
so entries vanished from every default listing, which is exactly the
"it must be obvious when he is choosing to take action" Ellis asked for.

Journal entries (``context_pack`` kind ``mind_action`` on an
``edward_thought``) are now always visible; other thoughts keep the old
hidden-noise rule. In Edward's group the entries get their own per-source
page, so years of older thoughts and the long-lived threads cannot push
them off the first page.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from services import conversation_service as svc
from services import thought_journal as tj

ENTRY_PACK = json.dumps({"kind": "mind_action", "ledger_id": 3, "reason": "messages"})


def _conv(source="edward_thought", message_count=1, notified_user=False, context_pack=None):
    return SimpleNamespace(source=source, message_count=message_count, notified_user=notified_user,
                           context_pack=context_pack)


class TestThePredicate:
    pytestmark = pytest.mark.unit

    def test_a_journal_entry_is_always_visible(self):
        assert svc.is_conversation_visible(_conv(context_pack=ENTRY_PACK)) is True
        compact = json.dumps({"kind": "mind_action"}, separators=(",", ":"))
        assert svc.is_conversation_visible(_conv(context_pack=compact)) is True

    def test_other_thoughts_keep_the_hidden_noise_rule(self):
        assert svc.is_conversation_visible(_conv(context_pack=json.dumps({"unread": []}))) is False
        assert svc.is_conversation_visible(_conv()) is False
        assert svc.is_conversation_visible(_conv(message_count=3, context_pack="{}")) is True

    def test_only_a_thought_can_be_an_entry(self):
        """The mark on another source is not an entry (the SQL is guarded
        to ``edward_thought`` the same way)."""
        assert svc.is_conversation_visible(_conv(source="heartbeat", context_pack=ENTRY_PACK)) is False
        assert svc.is_journal_entry(_conv(source="heartbeat", context_pack=ENTRY_PACK)) is False
        assert svc.is_journal_entry(_conv(context_pack=ENTRY_PACK)) is True

    def test_the_pack_mark(self):
        assert tj.is_entry_pack(ENTRY_PACK)
        assert not tj.is_entry_pack(None) and not tj.is_entry_pack("") and not tj.is_entry_pack(3)
        assert not tj.is_entry_pack(json.dumps({"kind": "whisper", "note": "mind_action"}))


# ------------------------------------------------------------------- SQL --

SCHEDULE = {"name": "schedule_event", "ok": True, "held": False,
            "args": {"description": "Check Biscuit's pill", "scheduled_at": "2026-09-26T18:00:00"},
            "result": "Event scheduled for 2026-09-26 18:00 (id ev-1)"}


async def _entry(title_hint="Biscuit") -> str:
    tid = await tj.record_entry(reason="evening", woke_for="", reasoning=[],
                                closing_note=f"{title_hint}'s evening dose is easy to miss; I'll look at 18:00.",
                                actions=[dict(SCHEDULE, args=dict(SCHEDULE["args"],
                                                                  description=f"Check {title_hint}'s pill"))],
                                ledger_id=None, wake_thread="edward-mind", turn_id="t-1")
    assert tid
    return tid


async def _touch(ids, when):
    from sqlalchemy import update

    from services.database import ConversationModel, async_session

    async with async_session() as s:
        await s.execute(update(ConversationModel).where(ConversationModel.id.in_(list(ids)))
                        .values(updated_at=when))
        await s.commit()


@pytest.mark.integration
async def test_default_listing_shows_entries_and_hides_other_thoughts(clean_db):
    from services.conversation_service import create_conversation, get_conversation
    from services.thought_conversation_service import create_thought_conversation

    entry = await _entry()
    row = await get_conversation(entry)
    # Commitment-only: no unread dot, one message — hidden until now.
    assert row.notified_user is False and row.message_count == 1
    old = await create_thought_conversation("A few unreads", "You've got 3 unreads.", context_pack={"unread": []})
    await create_conversation("chat-1", title="Dinner", source="user")

    rows, _ = await svc.get_conversations(include_scheduled=False)
    ids = {c.id for c in rows}
    assert entry in ids and "chat-1" in ids and old not in ids

    found, total = await svc.search_conversations("Biscuit", include_scheduled=False)
    assert entry in {c.id for c in found} and total >= 1


@pytest.mark.integration
async def test_the_yours_group_still_holds_only_owners(clean_db):
    entry = await _entry()
    rows, _ = await svc.get_conversations(include_scheduled=False, group="yours")
    assert entry not in {c.id for c in rows}


@pytest.mark.integration
async def test_edwards_group_gives_entries_their_own_page(clean_db):
    from services.conversation_service import create_conversation
    from services.thought_conversation_service import create_thought_conversation

    entries = [await _entry(n) for n in ("Biscuit", "Maple", "Pepper")]
    await create_conversation("edward-mind", title="Edward's mind", source="edward_thought")
    older = [await create_thought_conversation(f"Old thought {i}", f"Narrative {i}.") for i in range(20)]
    # The older thoughts and the mind's thread were all touched after the
    # entries: newest-first, one shared page of 15 would hold none of them.
    now = datetime.utcnow()
    await _touch(entries, now - timedelta(hours=2))
    await _touch(older + ["edward-mind"], now)

    rows, has_more = await svc.get_conversations(limit=15, include_scheduled=True, group="edwards")
    ids = [c.id for c in rows]
    assert set(entries) <= set(ids)
    assert "edward-mind" in ids
    assert has_more is True
    assert len(ids) == len(set(ids))

    page2, _ = await svc.get_conversations(limit=15, offset=15, include_scheduled=True, group="edwards")
    ids2 = [c.id for c in page2]
    assert not set(entries) & set(ids2)
    assert set(older) <= set(ids) | set(ids2)


@pytest.mark.integration
async def test_the_api_lists_an_entry_by_default(clean_db):
    from httpx import ASGITransport, AsyncClient

    from main import app
    from services.auth_service import create_access_token

    entry = await _entry()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        r = await c.get("/api/conversations")
        assert r.status_code == 200, r.text
        by_id = {x["id"]: x for x in r.json()["conversations"]}
        assert entry in by_id and by_id[entry]["source"] == "edward_thought"
        r = await c.get("/api/conversations?group=edwards&include_scheduled=true&limit=15")
        assert entry in {x["id"] for x in r.json()["conversations"]}
