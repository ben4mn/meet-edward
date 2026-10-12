"""``usage_events`` read Anthropic field names (``cache_read_input_tokens``)
off a Codex response and labelled every turn ``chat`` (muse-and-beyond plan
C1, 2026-09-24): cached tokens were always 0 and mind and pulse spend was
chat. ``usage_counts`` reads LangChain's ``usage_metadata`` first (what
``ChatCodexResponses`` now fills), then the raw usage in either shape, and
``record_message_usage`` takes the real ``source``.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessage

from services.usage_service import usage_counts

pytestmark = pytest.mark.unit


def test_counts_from_usage_metadata():
    msg = AIMessage(content="x", usage_metadata={
        "input_tokens": 5000, "output_tokens": 300, "total_tokens": 5300,
        "input_token_details": {"cache_read": 4096}, "output_token_details": {"reasoning": 200},
    })
    assert usage_counts(msg) == {
        "input_tokens": 5000, "output_tokens": 300, "cache_read_tokens": 4096,
        "cache_creation_tokens": 0, "reasoning_tokens": 200,
    }


def test_counts_from_raw_codex_usage():
    msg = AIMessage(content="x", response_metadata={"usage": {
        "input_tokens": 10, "output_tokens": 2,
        "input_tokens_details": {"cached_tokens": 8}, "output_tokens_details": {"reasoning_tokens": 1},
    }})
    counts = usage_counts(msg)
    assert (counts["cache_read_tokens"], counts["reasoning_tokens"]) == (8, 1)


def test_counts_from_raw_anthropic_usage():
    msg = AIMessage(content="x", response_metadata={"usage": {
        "input_tokens": 10, "output_tokens": 2, "cache_read_input_tokens": 6, "cache_creation_input_tokens": 3,
    }})
    counts = usage_counts(msg)
    assert (counts["cache_read_tokens"], counts["cache_creation_tokens"]) == (6, 3)


def test_no_usage_is_none():
    assert usage_counts(AIMessage(content="x")) is None
    assert usage_counts(None) is None


@pytest.mark.asyncio
async def test_record_message_usage_passes_the_source(monkeypatch):
    from services import usage_service

    seen = {}

    async def fake_record_usage(**kwargs):
        seen.update(kwargs)

    monkeypatch.setattr(usage_service, "record_usage", fake_record_usage)
    msg = AIMessage(content="x", usage_metadata={
        "input_tokens": 9, "output_tokens": 1, "total_tokens": 10, "input_token_details": {"cache_read": 4},
    })
    await usage_service.record_message_usage(msg, provider="openai", model="gpt-6-astra",
                                             conversation_id="edward-mind", source="mind")
    assert seen == {
        "provider": "openai", "model": "gpt-6-astra", "input_tokens": 9, "output_tokens": 1,
        "cache_creation_tokens": 0, "cache_read_tokens": 4, "conversation_id": "edward-mind", "source": "mind",
    }
    seen.clear()
    await usage_service.record_message_usage(AIMessage(content="x"), provider="openai", model="m")
    assert seen == {}, "nothing to record without usage"
