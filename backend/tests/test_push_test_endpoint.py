"""POST /api/push/test with an empty body must work — the PWA button sends none.

Regression (2026-09-03): the Settings 'Send test push' button POSTs with no
body; FastAPI answered 422 and the panel rendered the detail object as
'[object Object]' in red.
"""

import pytest


@pytest.mark.integration
async def test_push_test_accepts_empty_body(clean_db, monkeypatch):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from routers import push as push_router
    from services.auth_service import create_access_token

    async def fake_send(**kw):
        return {"sent": 1, "failed": 0, "total": 1}

    monkeypatch.setattr(push_router, "send_push_notification", fake_send)
    monkeypatch.setattr(push_router, "is_configured", lambda: True)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as client:
        r = await client.post("/api/push/test")
        assert r.status_code == 200, r.text
        assert r.json()["sent"] == 1
        r = await client.post("/api/push/test", json={"title": "Hi"})
        assert r.status_code == 200
