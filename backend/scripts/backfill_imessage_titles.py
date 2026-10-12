"""One-off: retitle iMessage conversations that still show a bare number.

Titles were minted from the chat identifier before the contact name was
known and never upgraded. Uses the same ``retitled_for`` rule as the live
path, so only auto-minted titles change; human edits are untouched.

Upgrade helper: only an install that has been running since the March
2026 public release (its schema and rows) needs this; a fresh install
has nothing to backfill.

Run from backend/ with the venv:  python scripts/backfill_imessage_titles.py [--dry-run]
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(Path(__file__).resolve().parents[2] / ".env")

from sqlalchemy import select  # noqa: E402

from services.database import (  # noqa: E402
    ConversationModel,
    HeartbeatEventModel,
    IMessageChatConversationModel,
    async_session,
)
from services.imessage_conversation_service import retitled_for  # noqa: E402


async def main(dry_run: bool) -> None:
    changed = 0
    async with async_session() as session:
        rows = (await session.execute(select(IMessageChatConversationModel))).scalars().all()
        for m in rows:
            conv = (
                await session.execute(select(ConversationModel).where(ConversationModel.id == m.conversation_id))
            ).scalar_one_or_none()
            if conv is None:
                continue
            name = m.chat_name
            if not name and not m.is_group and not str(m.chat_identifier).startswith("chat"):
                # 1:1 chats carry no chat_name; take the most recent
                # resolved contact name the listener recorded.
                name = (
                    await session.execute(
                        select(HeartbeatEventModel.contact_name)
                        .where(
                            HeartbeatEventModel.chat_identifier == m.chat_identifier,
                            HeartbeatEventModel.contact_name.isnot(None),
                            HeartbeatEventModel.contact_name != "",
                            HeartbeatEventModel.is_from_user == False,  # noqa: E712
                        )
                        .order_by(HeartbeatEventModel.created_at.desc())
                        .limit(1)
                    )
                ).scalar_one_or_none()
            better = retitled_for(m.chat_identifier, name, conv.title)
            if better is None:
                continue
            print(f"{conv.title!r} -> {better!r}")
            changed += 1
            if not dry_run:
                conv.title = better
        if not dry_run:
            await session.commit()
    print(f"{'would change' if dry_run else 'changed'} {changed} of {len(rows)} mapped conversations")


if __name__ == "__main__":
    asyncio.run(main("--dry-run" in sys.argv))
