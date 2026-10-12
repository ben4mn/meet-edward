"""Dream query generation timed out 1,615 times in one day (5 s ceiling;
the background model needs 4–10 s on a real window) and every timeout
*cancelled* the request — a Codex call spent for nothing, and neither deep
retrieval (before the turn) nor reflection (after it) ever got queries.

Now the pre-turn caller still waits at most 5 s (first-token latency is
Ellis's), but a slow answer keeps running and is cached, so reflection — which
asks for the same key seconds later and can afford to wait — gets it free.
"""

from __future__ import annotations

import asyncio
import json

import pytest

MESSAGES = [{"role": "user", "content": "remind me about cat food and Rowan's ETA"}]
QUERIES = ["rowan eta", "cat food", "animals dinner"]


class _SlowLLM:
    def __init__(self, delay: float):
        self.delay = delay
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        await asyncio.sleep(self.delay)

        class _R:
            content = json.dumps(QUERIES)

        return _R()


@pytest.fixture
def slow_llm(monkeypatch):
    from services import llm_utils

    llm = _SlowLLM(0.2)
    monkeypatch.setattr(llm_utils, "build_background_llm", lambda **kw: llm)
    return llm


async def test_a_slow_answer_is_kept_and_cached_for_the_next_caller(slow_llm):
    from services.dream.context import generate_search_queries

    first = await generate_search_queries("conv-1", MESSAGES, n=3, timeout=0.05)
    assert first == []  # the caller did not wait around
    await asyncio.sleep(0.3)  # ...but the answer lands anyway
    second = await generate_search_queries("conv-1", MESSAGES, n=3, timeout=0.05)
    assert second == QUERIES
    assert slow_llm.calls == 1, "one Codex call must serve both callers"


async def test_a_patient_second_caller_waits_on_the_call_already_in_flight(slow_llm):
    from services.dream.context import generate_search_queries

    assert await generate_search_queries("conv-2", MESSAGES, n=3, timeout=0.05) == []
    # Reflection arrives right after and can afford to wait.
    assert await generate_search_queries("conv-2", MESSAGES, n=3, timeout=2.0) == QUERIES
    assert slow_llm.calls == 1


async def test_the_callers_have_the_right_patience(monkeypatch):
    from services import deep_retrieval_service, reflection_service
    from services.dream import context

    assert context.DEEP_RETRIEVAL_TIMEOUT_SECONDS == 5.0  # before the turn: Ellis is waiting
    assert context.REFLECTION_TIMEOUT_SECONDS >= 15.0  # after the turn: nobody is

    seen = {}

    async def recorder(conversation_id, messages, n=5, window=10, timeout=None):
        seen[conversation_id] = timeout
        return []

    monkeypatch.setattr(context, "generate_search_queries", recorder)
    await reflection_service.generate_reflection_queries(MESSAGES, conversation_id="r")
    await deep_retrieval_service.generate_search_queries(MESSAGES, conversation_id="d")
    assert seen == {"r": context.REFLECTION_TIMEOUT_SECONDS, "d": context.DEEP_RETRIEVAL_TIMEOUT_SECONDS}
