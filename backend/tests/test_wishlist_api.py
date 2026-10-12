"""``/api/wishlist`` — Ellis reads and manages Edward's wishlist.

GET lists (optionally by status), POST adds one as Ellis, PATCH edits any
field, DELETE removes. A bad status is a 400 that names the allowed list.
"""

from __future__ import annotations

import pytest


@pytest.fixture
async def client(clean_db):
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t",
                           cookies={"edward_token": create_access_token()}) as c:
        yield c


@pytest.mark.integration
async def test_wishlist_crud_over_http(client):
    from services import wishlist_service as ws

    edwards = await ws.add_wish(title="Read PDFs", why="Ellis sends PDFs.", conversation_id="conv-9")

    r = await client.post("/api/wishlist", json={"title": "Hear the doorbell", "why": "Ring events.", "priority": 1})
    assert r.status_code == 200, r.text
    owners = r.json()
    assert owners["source"] == "owner" and owners["priority"] == 1 and owners["status"] == "open"

    r = await client.get("/api/wishlist")
    assert r.status_code == 200
    items = r.json()["items"]
    assert {i["id"] for i in items} == {edwards["id"], owners["id"]}
    assert next(i for i in items if i["id"] == edwards["id"])["conversation_id"] == "conv-9"

    r = await client.patch(f"/api/wishlist/{edwards['id']}", json={"status": "planned", "detail": "Next weekend."})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "planned" and r.json()["detail"] == "Next weekend."

    r = await client.get("/api/wishlist", params={"status": "planned"})
    assert [i["id"] for i in r.json()["items"]] == [edwards["id"]]

    r = await client.patch(f"/api/wishlist/{edwards['id']}", json={"status": "someday"})
    assert r.status_code == 400
    assert "declined" in r.json()["detail"]

    r = await client.get("/api/wishlist", params={"status": "someday"})
    assert r.status_code == 400

    r = await client.patch("/api/wishlist/nope", json={"status": "done"})
    assert r.status_code == 404

    r = await client.delete(f"/api/wishlist/{owners['id']}")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    r = await client.delete(f"/api/wishlist/{owners['id']}")
    assert r.json() == {"deleted": False}


@pytest.mark.integration
async def test_post_requires_a_title(client):
    r = await client.post("/api/wishlist", json={"why": "no title"})
    assert r.status_code == 422
    r = await client.post("/api/wishlist", json={"title": "   ", "why": "blank"})
    assert r.status_code == 400
