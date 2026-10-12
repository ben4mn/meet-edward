"""The iMessage listener must honor heartbeat_config.imessage_poll_seconds.

The DB column existed (default 10) and was surfaced in the status API, but
the loop slept a hardcoded ``_POLL_INTERVAL = 10`` — changing the config had
no effect.
"""

import pytest
import pytest_asyncio
from sqlalchemy import select

from services.heartbeat import listener_imessage as listener


@pytest_asyncio.fixture(autouse=True)
async def _fresh_engine_pool():
    """Clear connections pooled by earlier test files' event loops — asyncpg
    connections can't be reused across pytest-asyncio's per-test loops."""
    from services import database

    await database.engine.dispose()
    yield


@pytest.mark.integration
async def test_poll_interval_reads_config(clean_db):
    from services import database

    async with database.async_session() as session:
        result = await session.execute(
            select(database.HeartbeatConfigModel).where(
                database.HeartbeatConfigModel.id == "default"
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            config = database.HeartbeatConfigModel(id="default")
            session.add(config)
        config.imessage_poll_seconds = 25
        await session.commit()

    assert await listener._get_poll_interval() == 25


@pytest.mark.integration
async def test_poll_interval_defaults_when_unset(clean_db):
    from services import database

    async with database.async_session() as session:
        result = await session.execute(
            select(database.HeartbeatConfigModel).where(
                database.HeartbeatConfigModel.id == "default"
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            config = database.HeartbeatConfigModel(id="default")
            session.add(config)
        config.imessage_poll_seconds = None
        await session.commit()

    assert await listener._get_poll_interval() == 10


@pytest.mark.integration
async def test_poll_interval_clamped_to_sane_floor(clean_db):
    from services import database

    async with database.async_session() as session:
        result = await session.execute(
            select(database.HeartbeatConfigModel).where(
                database.HeartbeatConfigModel.id == "default"
            )
        )
        config = result.scalar_one_or_none()
        if config is None:
            config = database.HeartbeatConfigModel(id="default")
            session.add(config)
        config.imessage_poll_seconds = 0
        await session.commit()

    assert await listener._get_poll_interval() >= 2


async def test_poll_interval_survives_db_failure(monkeypatch):
    """A transient DB error must fall back to the default, not kill the loop."""

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(listener, "async_session", boom)

    assert await listener._get_poll_interval() == 10
