"""Operator endpoint: ask the running heartbeat loop for an immediate cycle.

Until 2026-09-05 the only ways to make triage run *now* were an inbound
message or waiting out the 15-minute interval. Re-opening a failed
@edward ping (triage_status back to 'pending') therefore meant either
cluttering the thread with another ping or waiting. The endpoint routes
through ``request_triage`` so it coalesces with listener-driven requests
and never runs a second cycle concurrently.
"""

from __future__ import annotations


async def test_run_triage_now_requests_a_cycle(monkeypatch):
    import services.heartbeat.heartbeat_service as hs
    from routers import heartbeat as router_module

    reasons = []
    monkeypatch.setattr(hs, "request_triage", lambda reason: reasons.append(reason))

    body = await router_module.run_triage_now()

    assert reasons == ["operator request"]
    assert body["requested"] is True


def test_run_triage_now_is_a_post_route():
    from routers import heartbeat as router_module

    routes = {(r.path, tuple(sorted(r.methods))) for r in router_module.router.routes}
    assert ("/heartbeat/triage/run", ("POST",)) in routes
