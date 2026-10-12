"""
Pin the default for ``ConsolidationConfigModel.enabled``.

Consolidation was originally disabled by default; the Track A5 redesign
flips that so new installs exercise the full dreaming pipeline without an
extra manual toggle. Locking that choice in with a test prevents an
accidental flip-back.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select


def test_model_default_is_enabled_true():
    """Unit-only: the SQLAlchemy column default must be True."""
    from services.database import ConsolidationConfigModel

    default = ConsolidationConfigModel.__table__.c.enabled.default.arg
    assert default is True, (
        "ConsolidationConfigModel.enabled default must be True so new "
        "installs don't need a manual toggle to get dreaming working."
    )


@pytest.mark.integration
async def test_fresh_row_is_enabled(clean_db):
    """Integration: inserting a fresh row with no explicit ``enabled`` value
    must yield ``enabled=True`` after commit.
    """
    from services import database
    from services.database import ConsolidationConfigModel

    async with database.async_session() as session:
        cfg = ConsolidationConfigModel(id="default")
        session.add(cfg)
        await session.commit()

    async with database.async_session() as session:
        result = await session.execute(
            select(ConsolidationConfigModel).where(ConsolidationConfigModel.id == "default")
        )
        cfg = result.scalar_one()

    assert cfg.enabled is True, (
        "A fresh consolidation_config row should be enabled=True so the "
        "hourly loop runs out of the box. Got enabled=False."
    )
