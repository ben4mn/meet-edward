"""R/G TDD for Task #11 — contradiction triage.

Consolidation creates 1.2k+ flags that nothing reads. This module:

* classifies each unresolved flag (background LLM) as either
  ``schema_confusion`` (the memory wrongly attributes Ellis's life to a
  contact or vice versa) or ``real_conflict`` (genuinely contradictory
  facts).
* auto-resolves ``schema_confusion`` by rewriting the broken memory.
* For ``real_conflict``, surfaces a push notification asking the user
  to disambiguate; leaves the flag unresolved until the user responds
  via ``POST /api/memories/contradictions/{id}/resolve``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta

import pytest

# Both rows are Ellis's own word (his chat turn, user_stated), so the pair is his
# call: only such a pair is asked, or reaches the model (integration review,
# 2026-09-27). Two default rows are a pair the label decides, left for
# consolidation.
OWNERS_WORD = dict(source_type="user_stated",
                 source_ref='{"kind": "extraction", "profile": "chat", "turn_source": "chat"}')


@pytest.mark.integration
async def test_triage_auto_resolves_schema_confusion(clean_db, monkeypatch):
    from services.database import (
        async_session,
        MemoryFlagModel,
        MemoryModel,
    )
    from sqlalchemy import select
    from services import contradiction_triage_service

    # Seed two related memories + a flag connecting them
    async with async_session() as session:
        m1 = MemoryModel(
            content="Rowan is excited to see Edward",
            memory_type="context",
            importance=0.4,
            **OWNERS_WORD,
        )
        m2 = MemoryModel(
            content="Rowan is Ellis's partner",
            memory_type="fact",
            importance=0.7,
            **OWNERS_WORD,
        )
        session.add_all([m1, m2])
        await session.flush()
        flag = MemoryFlagModel(
            memory_id=m1.id,
            flag_type="contradiction",
            description="m1 says Rowan is excited to see Edward, m2 says Rowan is Ellis's partner",
            related_memory_id=m2.id,
            resolved=False,
        )
        session.add(flag)
        await session.commit()
        flag_id = flag.id
        m1_id = m1.id

    class _FakeResp:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        async def ainvoke(self, messages):
            return _FakeResp(json.dumps({
                "classification": "schema_confusion",
                "rewrite": "Rowan is excited to see Ellis",
                "rationale": "first memory wrongly named the AI as the subject",
            }))

    monkeypatch.setattr(
        "services.contradiction_triage_service.build_background_llm",
        lambda **kwargs: _FakeLLM(),
    )

    result = await contradiction_triage_service.triage_flag(flag_id)

    assert result["action"] == "auto_resolved"
    async with async_session() as session:
        f = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )).scalar_one()
        assert f.resolved is True

        m1_after = (await session.execute(
            select(MemoryModel).where(MemoryModel.id == m1_id)
        )).scalar_one()
        assert "Ellis" in m1_after.content, (
            f"memory should have been rewritten; got {m1_after.content!r}"
        )


@pytest.mark.integration
async def test_triage_real_conflict_pushes_and_leaves_unresolved(
    clean_db, monkeypatch
):
    from services.database import (
        async_session,
        MemoryFlagModel,
        MemoryModel,
    )
    from sqlalchemy import select
    from services import contradiction_triage_service

    async with async_session() as session:
        m1 = MemoryModel(
            content="Rowan works at Acme as a designer",
            memory_type="fact",
            importance=0.7,
            **OWNERS_WORD,
        )
        m2 = MemoryModel(
            content="Rowan works at Beta Corp as a PM",
            memory_type="fact",
            importance=0.7,
            **OWNERS_WORD,
        )
        session.add_all([m1, m2])
        await session.flush()
        flag = MemoryFlagModel(
            memory_id=m1.id,
            flag_type="contradiction",
            description="conflicting job info for Rowan",
            related_memory_id=m2.id,
            resolved=False,
        )
        session.add(flag)
        await session.commit()
        flag_id = flag.id

    class _FakeResp:
        def __init__(self, content):
            self.content = content

    class _FakeLLM:
        async def ainvoke(self, messages):
            return _FakeResp(json.dumps({
                "classification": "real_conflict",
                "question": "Which is current — Rowan's job at Acme or at Beta Corp?",
            }))

    monkeypatch.setattr(
        "services.contradiction_triage_service.build_background_llm",
        lambda **kwargs: _FakeLLM(),
    )

    push_calls = []
    async def _fake_push(title, body, url=None, tag=None, actions=None, **kwargs):
        push_calls.append({"title": title, "body": body, "url": url})
        return {"sent": 1, "failed": 0}
    monkeypatch.setattr(
        "services.push_service.send_push_notification", _fake_push
    )

    result = await contradiction_triage_service.triage_flag(flag_id)

    assert result["action"] == "asked_user"
    assert len(push_calls) == 1
    assert "Rowan" in push_calls[0]["body"] or "current" in push_calls[0]["body"].lower()

    async with async_session() as session:
        f = (await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.id == flag_id)
        )).scalar_one()
        assert f.resolved is False, "real conflict must remain unresolved until user answers"


@pytest.mark.integration
async def test_triage_llm_error_is_safe(clean_db, monkeypatch):
    """If the LLM call fails, the flag stays unresolved and triage_flag
    returns an error indicator — never raises."""
    from services.database import async_session, MemoryFlagModel, MemoryModel
    from services import contradiction_triage_service

    # A pair that is Ellis's call: a flag naming one memory is now closed as
    # moot before any model call (2026-09-27).
    async with async_session() as session:
        m1 = MemoryModel(content="x", memory_type="fact", importance=0.5, **OWNERS_WORD)
        m2 = MemoryModel(content="y", memory_type="fact", importance=0.5, **OWNERS_WORD)
        session.add_all([m1, m2])
        await session.flush()
        flag = MemoryFlagModel(
            memory_id=m1.id,
            flag_type="contradiction",
            description="X",
            related_memory_id=m2.id,
            resolved=False,
        )
        session.add(flag)
        await session.commit()
        flag_id = flag.id

    class _BrokenLLM:
        async def ainvoke(self, messages):
            raise RuntimeError("kaboom")

    monkeypatch.setattr(
        "services.contradiction_triage_service.build_background_llm",
        lambda **kwargs: _BrokenLLM(),
    )

    result = await contradiction_triage_service.triage_flag(flag_id)
    assert result["action"] == "error"
