"""A fake-chat.db iMessage listener for ping and echo tests (AREA 3, 2026-10-11).

Everything that could leave the machine is replaced: the chat.db is a
throwaway file under ``tmp_path``, the contact lookups never reach Contacts
(or its AddressBook SQLite), the runner's ``kick``, the triage request and
the attachment describer are recorders. Nothing here sends, pushes or calls
a model.

    env = await ListenerEnv.create(tmp_path, monkeypatch, contacts={"+12015550101": "Parker"})
    env.db.add_chat("chat123", display_name="Trivia crew", is_group=True, participants=["+12015550101"])
    rowid = env.db.add_message("chat123", "@edward look", sender="+12015550101")
    await env.poll()
    [ping] = await env.pings()
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from tests.fixtures.fake_chatdb import FakeChatDB

OWNER = "+12015550000"


class ListenerEnv:
    def __init__(self, tmp_path, monkeypatch, *, owner_handles=(OWNER,), contacts: Optional[Dict[str, str]] = None):
        from services import imessage_sent
        from services.heartbeat import heartbeat_service
        from services.heartbeat import listener_imessage as listener
        from services.pings import hooks as pings_hooks

        self.listener = listener
        self.monkeypatch = monkeypatch
        self.owner_handles = list(owner_handles)
        self.contacts = dict(contacts or {})
        self.db = FakeChatDB(tmp_path / "chat.db").point_listener(monkeypatch)
        self.kicks: List[str] = []
        self.triage: List[str] = []
        self.described: List[tuple] = []
        self.logs: List[str] = []

        imessage_sent.reset_for_tests()
        listener._last_seen_rowid = 0
        listener._last_event_triggered_triage = 0.0
        listener._store_failures.clear()
        listener._contact_name_cache.clear()

        async def _contact(handle):
            return self.contacts.get(handle)

        monkeypatch.setattr(listener, "_resolve_contact_name", _contact)
        try:
            from services import contacts_service

            monkeypatch.setattr(contacts_service, "lookup_phone", lambda *_a, **_k: {"success": True, "matches": []})
        except Exception:  # noqa: BLE001
            pass
        monkeypatch.setattr(pings_hooks, "kick", lambda: self.kicks.append("kick"))
        monkeypatch.setattr(heartbeat_service, "request_triage", lambda reason: self.triage.append(reason))

        async def _describe(event_id, attachments):
            self.described.append((event_id, list(attachments)))

        monkeypatch.setattr(listener, "_describe_event_attachments", _describe)

    @classmethod
    async def create(cls, tmp_path, monkeypatch, **kwargs) -> "ListenerEnv":
        env = cls(tmp_path, monkeypatch, **kwargs)
        await env._config()
        return env

    async def _config(self) -> None:
        from sqlalchemy import select

        from services import database

        async with database.async_session() as session:
            row = (await session.execute(select(database.HeartbeatConfigModel).where(
                database.HeartbeatConfigModel.id == "default"))).scalar_one_or_none()
            if row is None:
                row = database.HeartbeatConfigModel(id="default")
                session.add(row)
            row.imessage_owner_handles = json.dumps(self.owner_handles)
            await session.commit()

    # ---------------------------------------------------------- driving ----

    async def poll(self) -> None:
        await self.listener._poll_once()

    async def store(self, *, degraded: bool = False):
        messages = self.listener._query_chat_db(self.listener._last_seen_rowid)
        return await self.listener._store_events(messages, owner_handles=self.owner_handles, degraded=degraded)

    async def settle(self) -> None:
        """Let tasks scheduled after the commit (the describer) run — to the
        end: a describer that reads the database needs more than a few loop
        turns on a loaded machine (it failed in the full suite at the
        overnight merge, 2026-10-11)."""
        import asyncio

        for _ in range(5):
            await asyncio.sleep(0)
        pending = [t for t in list(getattr(self.listener, "_describe_tasks", ())) if not t.done()]
        if pending:
            await asyncio.wait(pending, timeout=10)

    # ---------------------------------------------------------- reading ----

    async def events(self) -> List[Any]:
        from sqlalchemy import select

        from services import database

        async with database.async_session() as session:
            rows = (await session.execute(select(database.HeartbeatEventModel))).scalars().all()
        return sorted(rows, key=lambda e: int(json.loads(e.raw_data)["rowid"]))

    async def event(self, rowid: int) -> Any:
        for e in await self.events():
            if e.source_id == f"imessage:{rowid}":
                return e
        return None

    async def pings(self) -> List[Dict[str, Any]]:
        from sqlalchemy import text

        from services import database

        async with database.async_session() as session:
            rows = (await session.execute(text("SELECT * FROM pings ORDER BY id"))).mappings().all()
        return [dict(r) for r in rows]


def raw(event) -> Dict[str, Any]:
    return json.loads(event.raw_data or "{}")
