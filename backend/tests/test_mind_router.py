"""``/api/mind`` — hand-triggered wakes and the mind's ledger."""

from __future__ import annotations

import pytest


async def _client():
    from httpx import ASGITransport, AsyncClient
    from main import app
    from services.auth_service import create_access_token

    token = create_access_token()
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://t", cookies={"edward_token": token})


@pytest.mark.integration
async def test_router_wake_and_recent(clean_db, monkeypatch):
    """``Wake now`` answers at once (2026-09-25): a wake runs for minutes and
    the PWA reaches the backend through the Cloudflare tunnel, which gives up
    on a request after 100 s (a 524). The wake runs as a tracked task; the
    ledger shows it when it is done."""
    import asyncio

    import services.mind_service as ms

    release = asyncio.Event()
    calls = []

    async def fake_wake(reason, **kw):
        calls.append((reason, kw.get("extra")))
        await release.wait()
        await ms._record_now(source=reason, outcome="left_alone", note="quiet", tools=[])
        return {"ok": True, "note": "quiet", "skipped": False}

    monkeypatch.setattr(ms, "wake", fake_wake)
    ms.reset_state()
    try:
        async with await _client() as c:
            r = await asyncio.wait_for(c.post("/api/mind/wake", json={"reason": "manual", "extra": "look at the vet"}),
                                       timeout=5)
            assert r.status_code == 202, r.text
            assert r.json() == {"accepted": True, "reason": "manual", "joined": False}
            await asyncio.sleep(0.05)
            assert calls == [("manual", "look at the vet")], "the wake runs in the background"
            assert "manual" in ms._state.pending, "tracked like a requested wake"

            # A second press while it runs joins it rather than starting another.
            r = await c.post("/api/mind/wake", json={"reason": "manual"})
            assert r.status_code == 202 and r.json() == {"accepted": True, "reason": "manual", "joined": True}

            release.set()
            for _ in range(50):
                await asyncio.sleep(0.02)
                if "manual" not in ms._state.pending:
                    break
            assert len(calls) == 1
            r = await c.get("/api/mind/recent?limit=5")
            assert r.status_code == 200
            rows = r.json()["wakes"]
            assert rows and rows[0]["source"] == "manual" and rows[0]["outcome"] == "left_alone"
    finally:
        release.set()
        ms.reset_state()


@pytest.mark.integration
async def test_router_rejects_an_unknown_reason(clean_db):
    async with await _client() as c:
        r = await c.post("/api/mind/wake", json={"reason": "banana"})
        assert r.status_code == 400
