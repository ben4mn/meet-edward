"""The outgoing-send ledger must record sends made from worker threads.

The 2026-07-07 heartbeat overhaul moved osascript sends into
``asyncio.to_thread`` (they were blocking the event loop). But
``_record_outgoing_send`` scheduled its DB insert via
``asyncio.get_running_loop()`` — in a worker thread there is no running
loop, so the ledger insert silently skipped. Without a ledger row,
Edward's own iCloud-echoed reply in Ellis's self-chat is indistinguishable
from an owner ping → direct-channel triage could reply to itself in a loop.

Fix: the send tool notes the main loop before dispatching; the ledger
falls back to ``run_coroutine_threadsafe`` when called off-loop.
"""

import asyncio

import pytest
from sqlalchemy import select

from services import imessage_service as imsg


@pytest.fixture(autouse=True)
def _reset_main_loop():
    imsg._MAIN_LOOP = None
    yield
    imsg._MAIN_LOOP = None


@pytest.mark.integration
async def test_ledger_records_send_from_worker_thread(clean_db):
    from services.database import ImessageOutgoingSendModel, async_session

    imsg.note_main_loop(asyncio.get_running_loop())

    await asyncio.to_thread(imsg._record_outgoing_send, "Ellis", "hello from a thread")
    # the insert is scheduled onto the main loop — give it a beat
    for _ in range(50):
        await asyncio.sleep(0.02)
        async with async_session() as session:
            rows = (await session.execute(
                select(ImessageOutgoingSendModel)
            )).scalars().all()
        if rows:
            break

    assert len(rows) == 1
    assert rows[0].chat_identifier == "Ellis"


@pytest.mark.integration
async def test_ledger_still_records_on_loop_callers(clean_db):
    from services.database import ImessageOutgoingSendModel, async_session

    imsg._record_outgoing_send("Ellis", "hello from the loop")
    for _ in range(50):
        await asyncio.sleep(0.02)
        async with async_session() as session:
            rows = (await session.execute(
                select(ImessageOutgoingSendModel)
            )).scalars().all()
        if rows:
            break

    assert len(rows) == 1


def test_send_tool_notes_main_loop_before_dispatch():
    """The graph send_imessage tool must capture the main loop before
    handing the blocking send to a worker thread."""
    import inspect

    from services.graph import tools as graph_tools

    src = inspect.getsource(graph_tools.send_imessage.coroutine)
    assert "note_main_loop" in src
