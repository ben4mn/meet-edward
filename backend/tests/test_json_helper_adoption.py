"""
Integration tests: each background subsystem uses the json_parsing helper.

These tests verify that the core subsystems previously using the fragile
``re.search(r'\\[[\\s\\S]*?\\]', ...)`` pattern now call ``extract_json_payload``
so the plan's Fix #1 is wired end-to-end, not just theoretically available.

Each test feeds a dict-wrapped GPT-5.4-style response (which the old regex
mangled) and asserts the subsystem recovers the intended payload.
"""

from __future__ import annotations


class _Resp:
    """Minimal stand-in for a langchain response object."""

    def __init__(self, content, usage_metadata=None):
        self.content = content
        # Some callers read usage_metadata; most don't
        self.usage_metadata = usage_metadata or {"input_tokens": 10, "output_tokens": 5}


class _FakeLLM:
    def __init__(self, content: str, usage_metadata=None):
        self._content = content
        self._usage = usage_metadata
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return _Resp(self._content, self._usage)


# ---------------------------------------------------------------------------
# Reflection service — generate_reflection_queries
# ---------------------------------------------------------------------------


async def test_reflection_extracts_queries_from_dict_wrapped_payload(monkeypatch):
    from services import reflection_service

    wrapped = '{"queries": ["what did owner eat", "where does owner work", "what is owner working on"]}'
    fake = _FakeLLM(wrapped)
    # Reflection now routes through services.dream.context which imports
    # build_background_llm at call time from services.llm_utils.
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda **kw: fake)

    queries = await reflection_service.generate_reflection_queries(
        [{"role": "user", "content": "food talk dict wrapped"}]
    )
    # Exact-list check relaxed: DreamContext may request 5 and trim, so the
    # first three items should match the stubbed payload.
    assert queries[:3] == [
        "what did owner eat",
        "where does owner work",
        "what is owner working on",
    ]


async def test_reflection_handles_preamble_before_array(monkeypatch):
    from services import reflection_service

    msg = 'Here are the queries:\n\n["food preferences", "cooking style", "dietary restrictions"]'
    fake = _FakeLLM(msg)
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda **kw: fake)

    queries = await reflection_service.generate_reflection_queries(
        [{"role": "user", "content": "food talk preamble case"}]
    )
    assert "food preferences" in queries


# ---------------------------------------------------------------------------
# Deep retrieval service — generate_search_queries
# ---------------------------------------------------------------------------


async def test_deep_retrieval_queries_handle_dict_wrapped(monkeypatch):
    from services import deep_retrieval_service

    wrapped = '{"queries": ["query1", "query2", "query3", "query4", "query5"]}'
    fake = _FakeLLM(wrapped)
    monkeypatch.setattr("services.llm_utils.build_background_llm", lambda **kw: fake)

    queries = await deep_retrieval_service.generate_search_queries(
        [{"role": "user", "content": "a unique message for deep retrieval dict-wrap test"}, {"role": "assistant", "content": "a response"}]
    )
    assert len(queries) == 5
    assert queries[0] == "query1"


# ---------------------------------------------------------------------------
# Entity extraction
# ---------------------------------------------------------------------------


async def test_entity_extraction_handles_dict_wrapped(monkeypatch):
    from services import entity_service

    wrapped = (
        '{"items": [{"name": "Maple", "entity_type": "person", '
        '"aliases": ["the cat"], "relationship": "pet"}]}'
    )
    fake = _FakeLLM(wrapped)
    monkeypatch.setattr(entity_service, "build_background_llm", lambda **kw: fake)

    entities = await entity_service.extract_entities([
        {"role": "user", "content": "Maple is doing great today"}
    ])
    assert len(entities) == 1
    assert entities[0]["name"] == "Maple"
    # entity_type is coerced to "topic" if not in the whitelist; "person"
    # is accepted unchanged.
    assert entities[0]["entity_type"] == "person"


# ---------------------------------------------------------------------------
# Memory extraction — the marquee bug
# ---------------------------------------------------------------------------


async def test_memory_extraction_recovers_dict_wrapped_payload(monkeypatch):
    """This test is THE one — the primary subsystem that was silently failing
    for weeks. Feeds a dict-wrapped payload (which GPT-5.4 commonly produces)
    and asserts a memory is actually stored."""
    from services import memory_service

    # Capture stored memories without touching the DB
    stored_captured = []

    async def _fake_store(memories):
        stored_captured.extend(memories)
        for m in memories:
            if not m.id:
                import uuid as _u
                m.id = str(_u.uuid4())
        return memories

    monkeypatch.setattr(
        memory_service,
        "store_memories_with_conflict_detection",
        _fake_store,
    )

    # Avoid entity extraction side effects (it's imported inside the function)
    async def _noop_entities(*a, **kw):
        return []
    monkeypatch.setattr(
        "services.entity_service.extract_entities",
        _noop_entities,
    )

    # Patch the already-imported build_background_llm name on memory_service
    dict_wrapped = (
        '{"memories": [{"content": "Ellis\'s favourite colour is teal", '
        '"memory_type": "fact", "importance": 0.9, "temporal_nature": "timeless", '
        '"source_type": "user_stated", "confidence": 0.95}]}'
    )
    fake = _FakeLLM(dict_wrapped)
    monkeypatch.setattr(memory_service, "build_background_llm", lambda **kw: fake)

    # The function reads messages list; feed a minimal conversation
    await memory_service.extract_and_store_memories(
        messages=[
            {"role": "user", "content": "My favourite colour is teal."},
            {"role": "assistant", "content": "Got it."},
        ],
        conversation_id="test-conv-1",
    )

    assert len(stored_captured) == 1
    assert stored_captured[0].content == "Ellis's favourite colour is teal"
    assert stored_captured[0].confidence == 0.95
    assert stored_captured[0].source_type == "user_stated"
