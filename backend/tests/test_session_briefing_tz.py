"""
Regression test for the session briefing tz-mismatch.

The Postgres schema stores `conversations.updated_at` (and every other
DateTime in this project) as `TIMESTAMP WITHOUT TIME ZONE`. Adding a
`tzinfo=UTC` attribute to the value before feeding it back into another
WHERE clause breaks asyncpg with:

  invalid input for query argument $1: ... (can't subtract
  offset-naive and offset-aware datetimes)

`get_last_conversation_end()` must therefore return a tz-NAIVE datetime
so the downstream `gather_briefing_data(since=...)` comparisons work.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services import session_briefing_service


@pytest.mark.asyncio
async def test_get_last_conversation_end_returns_tz_naive_datetime():
    """The row comes back tz-naive; the helper must not attach tzinfo."""
    naive_dt = datetime(2026, 4, 20, 17, 18, 0)  # no tzinfo

    fake_result = MagicMock()
    fake_result.scalar_one_or_none = MagicMock(return_value=naive_dt)

    fake_session = MagicMock()
    fake_session.execute = AsyncMock(return_value=fake_result)

    class _SessionCM:
        async def __aenter__(self):
            return fake_session

        async def __aexit__(self, *a):
            return False

    with patch.object(session_briefing_service, "async_session", lambda: _SessionCM()):
        result = await session_briefing_service.get_last_conversation_end("some-conv-id")

    assert result == naive_dt
    assert result.tzinfo is None, (
        "get_last_conversation_end must return tz-naive datetimes — the DB "
        "columns are TIMESTAMP WITHOUT TIME ZONE and asyncpg rejects "
        "tz-aware values in subsequent WHERE clauses."
    )
