"""
Regression tests for the consolidation contradiction-detection bug.

Before the fix, ``consolidation_service._run_consolidation_cycle`` called
``llm.ainvoke(contradiction_prompt)`` with a bare string (line 221). LangChain's
chat models expect a list of message objects — with the real GPT/OpenAI client
this raises TypeError, and the exception handler at line 230 silently swallowed
it into ``contradiction_data = {"contradictions": []}``. Net effect:
contradiction flags were never written in production.

This test seeds two contradictory memories, scripts the LLM to cluster them and
report the contradiction, and asserts a ``MemoryFlagModel`` with
``flag_type='contradiction'`` lands in the DB. The strict stub mirrors the
real-client contract (rejects non-list input) so the bug is caught here and
not only in production.
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from sqlalchemy import select


class _StrictListLLM:
    """LLM stub that mirrors the real ChatOpenAI / ChatCodexResponses contract.

    ``ainvoke`` must be called with a list of message objects (SystemMessage,
    HumanMessage, ...). Passing a bare string — as the bug did — raises
    TypeError here, matching the real client behavior. Without this strictness
    the bug would be invisible to tests.
    """

    def __init__(self, scripted_responses):
        self._responses = list(scripted_responses)
        self.calls = []

    async def ainvoke(self, messages):
        if not isinstance(messages, list):
            raise TypeError(
                f"LLM.ainvoke requires a list of messages, got {type(messages).__name__}. "
                "This mirrors the real ChatOpenAI contract — passing a bare string "
                "is the bug the real clients raise on."
            )
        self.calls.append(messages)
        if not self._responses:
            raise AssertionError("_StrictListLLM exhausted — no more scripted responses")

        class _Response:
            def __init__(self, c):
                self.content = c

        return _Response(self._responses.pop(0))


@pytest.fixture(autouse=True)
def _merges_never_reach_the_model(monkeypatch):
    """The cycle's merge step asks ``memory_service._llm_merge_content``,
    and ``memory_service`` imported ``build_background_llm`` by name, so
    the tests' ``services.llm_utils`` stub never reached it: with two
    near-identical memories ("prefers tea / coffee in the morning") the
    merge sent two real Codex requests per run until conftest's guard
    caught it (2026-09-27). An exhausted stub raises, which the merge's
    try/except already tolerates — what the comments below assumed."""
    monkeypatch.setattr(
        "services.memory_service.build_background_llm", lambda *a, **k: _StrictListLLM([])
    )


async def _insert_memory(session, content: str, source_type: str = "user_stated") -> str:
    """Insert a memory with a real embedding so the lookback query picks it up.

    ``last_accessed`` is explicitly set to ``datetime.utcnow()`` so the
    consolidation lookback OR-branch matches regardless of the DB server's
    timezone. (The service uses naive UTC in the Python cutoff while
    ``server_default=func.now()`` stores the DB server's local wall clock —
    a separate pre-existing timezone inconsistency, tracked elsewhere.)
    """
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
        source_type=source_type,
        confidence=0.7,
        last_accessed=datetime.utcnow(),
    )
    session.add(mem)
    return mem_id


@pytest.mark.integration
async def test_consolidation_writes_contradiction_flag(clean_db, monkeypatch):
    """Seed two contradictory memories; one cycle should produce a
    memory_flags row with ``flag_type='contradiction'``.

    With the line 221 bug (bare-string ``ainvoke``), the strict stub raises,
    the exception handler swallows it, no flag is created, and this test
    fails. Once the call is wrapped in ``[SystemMessage, HumanMessage]``, the
    stub returns the scripted contradiction payload and the flag is written.
    """
    from services import database
    from services.consolidation_service import _run_consolidation_cycle

    # Seed three memories: two contradictory, one unrelated noise so we clear
    # the ``len(memories) >= 3`` guard at consolidation_service.py:124.
    async with database.async_session() as session:
        id_loves = await _insert_memory(session, "user loves coffee")
        id_hates = await _insert_memory(session, "user hates coffee")
        _id_noise = await _insert_memory(session, "user lives in San Francisco")
        await session.commit()

    # Script the LLM to return a cluster containing both coffee memories,
    # then a contradiction between them. The cycle makes exactly two LLM
    # calls that we care about (cluster + contradictions for the one cluster);
    # merging (memory_service._llm_merge_content) goes to the exhausted stub
    # of ``_merges_never_reach_the_model``, whose AssertionError the merge
    # try/except already tolerates.
    cluster_response = (
        '{"clusters": [{"label": "coffee preference", "type": "same_topic", '
        f'"memory_ids": ["{id_loves}", "{id_hates}"]}}]}}'
    )
    contradiction_response = (
        '{"contradictions": [{"memory_id_a": "' + id_loves + '", '
        '"memory_id_b": "' + id_hates + '", '
        '"description": "loves vs hates coffee"}]}'
    )
    stub = _StrictListLLM([cluster_response, contradiction_response])

    def _build(*_args, **_kwargs):
        return stub

    monkeypatch.setattr("services.llm_utils.build_background_llm", _build)
    # consolidation_service imports build_background_llm inside the function,
    # so patching the llm_utils module path is sufficient.

    await _run_consolidation_cycle()

    # Assert a contradiction flag exists linking the two memories.
    async with database.async_session() as session:
        from services.database import MemoryFlagModel

        result = await session.execute(
            select(MemoryFlagModel).where(MemoryFlagModel.flag_type == "contradiction")
        )
        flags = list(result.scalars().all())

    assert len(flags) >= 1, (
        "Expected at least one contradiction flag after one cycle. "
        "If zero flags: the bare-string ainvoke at consolidation_service.py:221 "
        "is raising into the except-swallow, so no contradiction is ever recorded."
    )
    flag = flags[0]
    assert {flag.memory_id, flag.related_memory_id} == {id_loves, id_hates}, (
        "Flag should link the two contradictory memories."
    )


@pytest.mark.integration
async def test_contradiction_call_receives_message_list(clean_db, monkeypatch):
    """The contradiction LLM call must receive a list of messages (not a bare
    string). This catches the specific shape bug at line 221.
    """
    from services import database
    from services.consolidation_service import _run_consolidation_cycle

    async with database.async_session() as session:
        id_a = await _insert_memory(session, "user prefers tea in the morning")
        id_b = await _insert_memory(session, "user prefers coffee in the morning")
        await _insert_memory(session, "user works remotely")
        await session.commit()

    cluster_response = (
        '{"clusters": [{"label": "morning beverage", "type": "same_topic", '
        f'"memory_ids": ["{id_a}", "{id_b}"]}}]}}'
    )
    contradiction_response = '{"contradictions": []}'
    stub = _StrictListLLM([cluster_response, contradiction_response])
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda *a, **k: stub)

    await _run_consolidation_cycle()

    # Two calls expected: cluster, contradiction. Both must be message lists.
    assert len(stub.calls) >= 2, (
        f"Expected at least 2 LLM calls (cluster + contradiction), got {len(stub.calls)}. "
        "If only 1: the contradiction call raised on bare-string input and the "
        "except swallowed it without recording a call."
    )
    for idx, messages in enumerate(stub.calls):
        assert isinstance(messages, list), (
            f"Call #{idx} was {type(messages).__name__}, not list. "
            "Every LLM call in consolidation must pass a list of messages."
        )
        assert len(messages) >= 1, f"Call #{idx} had an empty message list."
