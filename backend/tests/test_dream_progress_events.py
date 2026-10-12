"""
Dream progress events are emitted during speculative deep retrieval.

B5 makes dreaming visible in the chat UI by emitting PROGRESS events
with ``step="dream"`` around the speculative deep-retrieval call in
``streaming.py``. The frontend's ``ThinkingIndicator`` already renders
arbitrary step names, so a new ``step="dream"`` event surfaces
automatically in the thinking list.

This test is a structural guard: it asserts the emissions exist in the
streaming module so a future refactor doesn't silently drop them.
"""

from __future__ import annotations

from pathlib import Path


def test_streaming_emits_dream_progress_events():
    """Streaming code must emit at least two ``step="dream"`` PROGRESS events
    — one when the speculative retrieval starts, one when it completes.
    """
    src = (
        Path(__file__).resolve().parent.parent
        / "services"
        / "graph"
        / "streaming.py"
    ).read_text()

    # Both started and completed events use the same step key; a compiled
    # regex would be equivalent but substring-count is clearer here.
    dream_events = src.count('step="dream"')

    assert dream_events >= 2, (
        f"Expected at least 2 `step=\"dream\"` PROGRESS emissions in "
        f"streaming.py (one started, one completed). Found {dream_events}. "
        f"If this fails: the B5 dream pulse was removed from the speculative "
        f"deep-retrieval block — the chat UI will lose its dreaming indicator."
    )
