"""
Structured-logging tests for the dreaming subsystems.

``reflection_service``, ``deep_retrieval_service``, and
``consolidation_service`` all use ``print()`` for status/error output.
Silent failures are impossible to triage: exceptions hit ``except: print``
and vanish into stdout with no severity and no fields ops can filter on.

These tests drive the conversion to module-level ``logging.Logger``:
  - No ``print()`` lines with service-prefix strings may appear on stdout.
  - At least one ``logger.info`` (or warning) record is emitted per cycle,
    tagged with the module's logger name.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime

import pytest
from sqlalchemy import select


class _EmptyCycleLLM:
    """Returns an empty cluster response; cycle does one LLM call and exits."""

    def __init__(self):
        self.calls = []

    async def ainvoke(self, messages):
        assert isinstance(messages, list)
        self.calls.append(messages)

        class _R:
            content = '{"clusters": []}'

        return _R()


async def _insert_memory(session, content: str) -> str:
    from services import memory_service
    from services.database import MemoryModel

    mem_id = str(uuid.uuid4())
    mem = MemoryModel(
        id=mem_id,
        content=content,
        embedding=memory_service.get_embedding(content),
        memory_type="fact",
        importance=0.6,
        temporal_nature="timeless",
        source_type="user_stated",
        confidence=0.8,
        last_accessed=datetime.utcnow(),
    )
    session.add(mem)
    return mem_id


@pytest.mark.integration
async def test_consolidation_uses_logger_not_print(clean_db, monkeypatch, capsys, caplog):
    """A consolidation cycle must route status output through ``logging``,
    not ``print``.
    """
    from services import database
    from services.consolidation_service import _run_consolidation_cycle

    async with database.async_session() as session:
        for i in range(3):
            await _insert_memory(session, f"filler memory {i}")
        await session.commit()

    stub = _EmptyCycleLLM()
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)

    with caplog.at_level(logging.INFO, logger="services.consolidation_service"):
        await _run_consolidation_cycle()

    captured = capsys.readouterr()
    offending = [
        line
        for line in captured.out.splitlines()
        if line.startswith("Consolidation:") or line.startswith("Consolidation cycle")
    ]
    assert not offending, (
        "consolidation_service must log through `logger`, not print. "
        f"Found {len(offending)} print lines on stdout: {offending[:3]}"
    )

    records = [r for r in caplog.records if r.name == "services.consolidation_service"]
    assert records, (
        "consolidation_service should emit at least one log record per cycle via "
        "`logging.getLogger(__name__)`."
    )


@pytest.mark.integration
async def test_reflection_uses_logger_not_print(clean_db, monkeypatch, capsys, caplog):
    """``run_reflection_safe`` prints on success and on every failure path.
    Route through a module logger instead. Seed a memory so the happy path
    actually stores enrichments and hits the ``Stored N enrichments`` line.
    """
    from services import database
    from services.reflection_service import run_reflection_safe

    # Seed a conversation + memory so retrieve_memories returns something.
    conversation_id = str(uuid.uuid4())
    async with database.async_session() as session:
        await _insert_memory(session, "user enjoys hiking on weekends in Colorado")
        await session.commit()

    class _ReflectionLLM:
        async def ainvoke(self, messages):
            assert isinstance(messages, list)

            class _R:
                content = '["hiking weekends Colorado"]'

            return _R()

    monkeypatch.setattr(
        "services.llm_utils.build_background_llm",
        lambda *a, **k: _ReflectionLLM(),
    )

    messages = [
        {"role": "user", "content": "I love hiking on weekends"},
        {"role": "assistant", "content": "Noted — hiking on weekends."},
    ]

    with caplog.at_level(logging.INFO, logger="services.reflection_service"):
        await run_reflection_safe(conversation_id, messages, already_retrieved_ids=[])

    captured = capsys.readouterr()
    offending = [
        line for line in captured.out.splitlines() if line.startswith("[REFLECTION]")
    ]
    assert not offending, (
        "reflection_service must log through `logger`, not print. "
        f"Found print lines: {offending[:3]}"
    )


@pytest.mark.integration
async def test_deep_retrieval_uses_logger_not_print(clean_db, monkeypatch, capsys, caplog):
    """``deep_retrieve_memories`` prints on success (found N memories).
    Route through a module logger instead.
    """
    from services.deep_retrieval_service import deep_retrieve_memories

    # Seed one memory so there's something to retrieve — otherwise the
    # "found 0 unique memories" print line still fires.
    from services import database

    async with database.async_session() as session:
        await _insert_memory(session, "user enjoys python programming")
        await session.commit()

    class _DeepLLM:
        async def ainvoke(self, messages):
            assert isinstance(messages, list)

            class _R:
                content = '["python", "programming language", "coding hobbies"]'

            return _R()

    monkeypatch.setattr(
        "services.llm_utils.build_background_llm",
        lambda *a, **k: _DeepLLM(),
    )

    recent_messages = [
        {"role": "user", "content": "what do you remember about my coding?"},
    ]

    with caplog.at_level(logging.INFO, logger="services.deep_retrieval_service"):
        await deep_retrieve_memories(
            "what do you remember about my coding?",
            recent_messages,
        )

    captured = capsys.readouterr()
    offending = [
        line for line in captured.out.splitlines() if line.startswith("[DEEP RETRIEVAL]")
    ]
    assert not offending, (
        "deep_retrieval_service must log through `logger`, not print. "
        f"Found print lines: {offending[:3]}"
    )
