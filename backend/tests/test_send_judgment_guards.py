"""Non-deterministic send-quality guards (2026-07-07 "Perfect 👌" incident).

Transcript evidence: Edward correctly judged "No reply needed, the owner
already handled it in-thread" — then a required-tool nudge forced him to
send anyway, producing filler ("Perfect 👌") to someone in the owner's chat.

The owner's direction: fix by judgment, not muting. The legacy heartbeat
reply triggers (ACT/REPLY/MENTION and ``_execute_classification``) that
carried the follow-up and self-check language left with the legacy triage
engine. What stays, and is under test:
- C: an LLM draft critic (send_review_service) reviews outbound heartbeat
  sends — advisory, fail-open, at most one rejection per turn — and the
  send tool refuses a rejected draft.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock


# ---------------------------------------------------------------------------
# C — draft critic (send_review_service)
# ---------------------------------------------------------------------------


def _fake_llm(reply_json: str):
    response = SimpleNamespace(content=reply_json, response_metadata={})
    return SimpleNamespace(ainvoke=AsyncMock(return_value=response))


async def test_review_off_outside_heartbeat_context():
    from services import send_review_service as srs

    # Fresh context: review not enabled → approve without any LLM call.
    def build_must_not_be_called(**kw):
        raise AssertionError("no LLM call when review is off")

    result = await srs.review_outgoing_message("Perfect 👌", "+15551230000",
                                               _build_llm=build_must_not_be_called)
    assert result["approved"] is True


async def test_review_rejects_filler_in_heartbeat_context(monkeypatch):
    from services import send_review_service as srs

    srs.enable_heartbeat_send_review()
    llm = _fake_llm('{"approved": false, "reason": "emoji-only filler"}')

    result = await srs.review_outgoing_message(
        "Perfect 👌", "+15551230000", _build_llm=lambda **kw: llm
    )

    assert result["approved"] is False
    assert "filler" in result["reason"]


async def test_review_allows_after_one_rejection_per_turn():
    """Bounded: one rejection per turn — the revision goes through even if
    the critic would reject again (no infinite revise loops)."""
    from services import send_review_service as srs

    srs.enable_heartbeat_send_review()
    llm = _fake_llm('{"approved": false, "reason": "still filler"}')

    first = await srs.review_outgoing_message("Perfect 👌", "x", _build_llm=lambda **kw: llm)
    second = await srs.review_outgoing_message("Perfect 👌👌", "x", _build_llm=lambda **kw: llm)

    assert first["approved"] is False
    assert second["approved"] is True


async def test_review_fails_open_on_llm_error():
    from services import send_review_service as srs

    srs.enable_heartbeat_send_review()
    broken = SimpleNamespace(ainvoke=AsyncMock(side_effect=RuntimeError("gate down")))

    result = await srs.review_outgoing_message("hi", "x", _build_llm=lambda **kw: broken)

    assert result["approved"] is True


async def test_review_fails_open_on_garbage_output():
    from services import send_review_service as srs

    srs.enable_heartbeat_send_review()
    llm = _fake_llm("not json at all")

    result = await srs.review_outgoing_message("hi", "x", _build_llm=lambda **kw: llm)

    assert result["approved"] is True


# ---------------------------------------------------------------------------
# C — tool wiring: rejected drafts don't send
# ---------------------------------------------------------------------------


async def test_send_tool_blocks_rejected_draft(monkeypatch):
    from services import send_review_service as srs
    from services.graph import tools as graph_tools
    import services.imessage_service as imsg

    sent = {}
    monkeypatch.setattr(imsg, "send_imessage",
                        lambda r, m: sent.update(r=r) or {"success": True, "recipient": r})
    monkeypatch.setattr(imsg, "is_available", lambda: True)
    monkeypatch.setattr(
        srs, "review_outgoing_message",
        AsyncMock(return_value={"approved": False, "reason": "social filler"}),
    )
    import services.heartbeat.triage_service as ts
    monkeypatch.setattr(ts, "register_listening_window_for_chat", AsyncMock())

    result = await graph_tools.send_imessage.ainvoke(
        {"contact": "+15551230000", "message": "Perfect 👌"}
    )

    assert "r" not in sent, "rejected draft must not be delivered"
    assert "rejected" in result.lower()
    assert "skip" in result.lower() or "revise" in result.lower()
