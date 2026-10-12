"""R/G TDD: scheduled-event execution must leave a substantive chat
message in the conversation, not just a tool-execution log.

When Edward fires a scheduled event that pushes Ellis, Ellis taps the push
and lands in the conversation. Today his only assistant turn is
"Sent Ellis the push notification: ...". That's a tool log, not a chat.

Standing rule from Ellis: "I like to be able to tap into his thoughts
and start talking to him." The system prompt must direct Edward to
write substance — the actual reminder context — rather than narrating
his own action.
"""

from __future__ import annotations

from types import SimpleNamespace


def _fake_event(description: str, channel: str | None = None):
    return SimpleNamespace(
        description=description,
        scheduled_at=None,
        delivery_channel=channel,
    )


def test_scheduler_system_prompt_instructs_substantive_message():
    from services.scheduler_service import _build_scheduler_system_prompt

    prompt = _build_scheduler_system_prompt(
        "You are Edward.",
        _fake_event("Send Ellis a push notification: 'Change the Britta filter.'"),
    )

    lowered = prompt.lower()
    # The old "brief confirmation of what you did" wording produces a tool
    # log — replace it with guidance that points at the conversation as
    # Edward's thought-thread.
    assert "tap" in lowered or "open" in lowered or "land" in lowered, (
        "prompt should reference Ellis tapping/opening the push to find this "
        f"conversation; got:\n{prompt}"
    )
    # Must NOT instruct him to merely confirm the action.
    assert "brief confirmation of what you did" not in lowered, (
        "old self-narrating instruction still present — Edward will keep "
        "writing 'Sent push notification...' instead of speaking to Ellis"
    )


def test_scheduler_system_prompt_works_for_chat_channel():
    """Chat-only events should still get the substantive-message guidance."""
    from services.scheduler_service import _build_scheduler_system_prompt

    prompt = _build_scheduler_system_prompt(
        "You are Edward.",
        _fake_event("Remind Ellis to drink water", channel="chat"),
    )
    lowered = prompt.lower()
    assert "tap" in lowered or "open" in lowered or "land" in lowered


def test_scheduler_system_prompt_works_for_imessage_channel():
    """iMessage-channel events still need a meaningful chat surface
    because Ellis can tap the push that confirms the iMessage was sent."""
    from services.scheduler_service import _build_scheduler_system_prompt

    prompt = _build_scheduler_system_prompt(
        "You are Edward.",
        _fake_event("Text Rowan: 'on my way'", channel="imessage"),
    )
    lowered = prompt.lower()
    # iMessage delivery rules must still be present
    assert "imessage" in lowered or "send_imessage" in lowered
    # AND the substance-not-narration guidance
    assert "tap" in lowered or "open" in lowered or "land" in lowered
