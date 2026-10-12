"""Interjection tuning (2026-07-08): self-awareness + restraint, keep the freedom.

Two threads missed the mark: Edward answered questions addressed to the owner
AS the owner ("Can I call?" → "Yes!"), joined emotional moments, replied many
times in a row to a conversation between the owner and a friend — and skipped
the one task that was genuinely his. The diagnosis: hallucinated helpfulness
needs a grounding preflight; adjacency is not invitation.

The legacy heartbeat reply triggers that carried the shared rules block
(ACT/REPLY/MENTION, INTERJECTION_RULES, the presence line) left with the
legacy triage engine. What stays, and is under test:
- Real presence data: Edward's sends to a chat in the trailing hour, from
  the send ledger (``triage_service._count_recent_sends``), failing open.
- The send-review critic knows the rubric and receives the count.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from services.heartbeat import triage_service
from services.owner import owner_name


# ---------------------------------------------------------------------------
# Presence count from the send ledger
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_count_recent_sends_window(clean_db):
    from services.database import ImessageOutgoingSendModel, async_session

    now = datetime.now(timezone.utc)
    async with async_session() as session:
        session.add_all([
            ImessageOutgoingSendModel(chat_identifier="+15550001111", text_hash="h1",
                                      sent_at=now - timedelta(minutes=5)),
            ImessageOutgoingSendModel(chat_identifier="+15550001111", text_hash="h2",
                                      sent_at=now - timedelta(minutes=50)),
            ImessageOutgoingSendModel(chat_identifier="+15550001111", text_hash="h3",
                                      sent_at=now - timedelta(minutes=90)),  # outside window
            ImessageOutgoingSendModel(chat_identifier="+15559990000", text_hash="h4",
                                      sent_at=now - timedelta(minutes=5)),  # other chat
        ])
        await session.commit()

    assert await triage_service._count_recent_sends("+15550001111") == 2
    assert await triage_service._count_recent_sends("+15559990000") == 1
    assert await triage_service._count_recent_sends("+15551230000") == 0


async def test_count_recent_sends_fails_open(monkeypatch):
    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(triage_service, "async_session", boom)
    assert await triage_service._count_recent_sends("+15550001111") == 0


# ---------------------------------------------------------------------------
# Critic rubric + count
# ---------------------------------------------------------------------------


def test_critic_rubric_covers_addressee_grounding_burst_identity():
    from services.send_review_service import SEND_REVIEW_SYSTEM

    low = SEND_REVIEW_SYSTEM.lower()
    owner = owner_name().lower()  # conftest: EDWARD_OWNER_NAME=Ellis
    assert owner == "ellis"
    assert "{owner}" not in low, "the owner token must be rendered"
    assert f"addressed to {owner}" in low
    assert "mind-read" in low or "cannot ground" in low or "can't ground" in low
    assert "let the humans talk" in low
    assert f"as if edward is {owner}" in low


async def test_critic_prompt_includes_presence_count(monkeypatch):
    from services import send_review_service as srs

    monkeypatch.setattr(
        "services.heartbeat.triage_service._count_recent_sends",
        AsyncMock(return_value=4),
    )
    srs.enable_heartbeat_send_review()

    captured = {}

    def build_llm(**kw):
        async def ainvoke(messages):
            captured["human"] = messages[-1].content
            return SimpleNamespace(content='{"approved": true, "reason": "ok"}',
                                   response_metadata={})
        return SimpleNamespace(ainvoke=ainvoke)

    result = await srs.review_outgoing_message("draft text", "+15550001111",
                                               _build_llm=build_llm)

    assert result["approved"] is True
    assert "4" in captured["human"]
    assert "last hour" in captured["human"].lower()
