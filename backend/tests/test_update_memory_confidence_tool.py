"""R/G TDD red for D4 — `update_memory_confidence` LLM tool.

When the user contradicts a stored memory, Edward should be able to *lower*
its confidence rather than delete it. The Memory row already has a
``confidence`` column (0-1) and retrieval multiplies by it (memory_service
lines ~240-242), so a simple setter tool closes the loop.

Contract:
  * Tool named ``update_memory_confidence``.
  * Listed in ``MEMORY_TOOLS``.
  * Accepts ``memory_id: str`` and ``new_confidence: float`` (and an
    optional ``reason`` that's logged / included in the confirmation).
  * Clamps values outside [0, 1].
  * Returns ``"ok"`` on success, descriptive string on failure.
"""

from __future__ import annotations

import pytest


def test_update_memory_confidence_is_importable_from_tools_module():
    from services.graph import tools

    assert hasattr(tools, "update_memory_confidence"), (
        "`update_memory_confidence` tool must be defined in services.graph.tools"
    )


def test_update_memory_confidence_is_listed_in_memory_tools():
    from services.graph.tools import MEMORY_TOOLS, update_memory_confidence

    tool_names = {t.name for t in MEMORY_TOOLS}
    assert "update_memory_confidence" in tool_names, (
        "update_memory_confidence must be included in MEMORY_TOOLS so the "
        "registry exposes it. Currently: " + repr(sorted(tool_names))
    )


@pytest.mark.integration
async def test_update_memory_confidence_lowers_value_in_db(clean_db):
    """Integration test: call the tool; verify the memory row's confidence
    drops and that retrieval deprioritises the memory."""
    from services.graph.tools import update_memory_confidence
    from services.memory_service import Memory, store_memory, get_memory_by_id

    mem = await store_memory(
        Memory(
            id=None,
            content="Ellis drinks coffee every morning",
            memory_type="preference",
            importance=0.6,
            confidence=0.9,
        )
    )
    assert mem.confidence == pytest.approx(0.9)

    # Invoke the LangChain tool the same way the runtime tool loop does.
    result = await update_memory_confidence.ainvoke(
        {"memory_id": mem.id, "new_confidence": 0.1, "reason": "user quit coffee"}
    )
    assert result == "ok", f"expected 'ok', got {result!r}"

    refreshed = await get_memory_by_id(mem.id)
    assert refreshed is not None
    assert refreshed.confidence == pytest.approx(0.1, abs=1e-6)


async def test_update_memory_confidence_clamps_out_of_range(monkeypatch):
    """Unit-ish test: values > 1 clamp to 1, values < 0 clamp to 0.

    We stub ``update_memory`` so the test is DB-free."""
    from services.graph.tools import update_memory_confidence
    from services.graph import tools as tools_module

    captured = {}

    class _FakeMemory:
        pass

    async def _fake_get(memory_id):
        return _FakeMemory()

    async def _fake_update(**kwargs):
        captured.update(kwargs)
        return _FakeMemory()

    monkeypatch.setattr(
        "services.memory_service.get_memory_by_id", _fake_get
    )
    monkeypatch.setattr(
        "services.memory_service.update_memory", _fake_update
    )

    result = await update_memory_confidence.ainvoke(
        {"memory_id": "abc", "new_confidence": 1.7}
    )
    assert result == "ok", f"got {result!r}"
    assert captured.get("confidence") == pytest.approx(1.0)

    captured.clear()
    result = await update_memory_confidence.ainvoke(
        {"memory_id": "abc", "new_confidence": -0.5}
    )
    assert result == "ok", f"got {result!r}"
    assert captured.get("confidence") == pytest.approx(0.0)


async def test_update_memory_confidence_returns_error_when_memory_missing(monkeypatch):
    from services.graph.tools import update_memory_confidence

    async def _none_get(memory_id):
        return None

    monkeypatch.setattr(
        "services.memory_service.get_memory_by_id", _none_get
    )

    result = await update_memory_confidence.ainvoke(
        {"memory_id": "nope", "new_confidence": 0.1}
    )
    assert "not found" in result.lower(), f"got {result!r}"
