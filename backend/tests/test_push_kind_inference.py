"""Push subscriptions learn their device kind from the user agent.

Why (2026-09-02): the one live subscription (Ellis's iPhone PWA) was saved
before device identity existed, so the interrupt ladder could not target
it and 'push:iphone' fell back to broadcast. Infer kind from the UA when
the client did not say, and backfill old rows at startup.
"""

import pytest

from services.push_service import infer_kind

pytestmark = pytest.mark.unit

IPHONE = "Mozilla/5.0 (iPhone; CPU iPhone OS 18_7 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148 Safari"
MAC = "Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0) AppleWebKit/605.1.15 Version/18.0 Safari/605.1.15"


def test_infer_kind_from_user_agent():
    assert infer_kind(IPHONE) == "iphone-pwa"
    assert infer_kind(MAC) == "mac-web"
    assert infer_kind("") is None and infer_kind(None) is None


@pytest.mark.integration
async def test_save_without_kind_infers_it_and_backfill_fixes_old_rows(clean_db):
    from sqlalchemy import select, update
    from services import database, push_service
    from services.database import PushSubscriptionModel

    sub = await push_service.save_subscription("https://p/1", "k", "a", user_agent=IPHONE)
    assert sub.kind == "iphone-pwa" and sub.device_id
    async with database.async_session() as session:
        await session.execute(update(PushSubscriptionModel).values(kind=None, device_id=None))
        await session.commit()
    fixed = await push_service.backfill_device_kinds()
    assert fixed == 1
    async with database.async_session() as session:
        row = (await session.execute(select(PushSubscriptionModel))).scalar_one()
    assert row.kind == "iphone-pwa" and row.device_id
