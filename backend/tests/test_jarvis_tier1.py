"""Tier 1 "feel like Jarvis" changes.

Four character/cadence shifts that close the gap between Edward's
plumbing and the felt experience:

  Change 1 — Base system prompt rewritten as a character (dry+anchored
            Jarvis voice), not a CRM tagline.
  Change 2 — Active-chat gate flipped from suppress to interleave for
            escalated heartbeat events.
  Change 3 — Heartbeat briefing rendered as narrative via background LLM
            instead of a bullet list.
  Change 4 — schedule_event tool description encourages self-scheduling
            (Edward books his own follow-ups / recaps / nudges).

Each test is a focused contract guard — small enough that it stays green
as the surrounding code evolves, specific enough that a regression of the
"feel" change shows up here first.
"""

from __future__ import annotations


# ---------------------------------------------------------------------------
# Change 4 — schedule_event docstring encourages self-scheduling
# ---------------------------------------------------------------------------


def test_schedule_event_description_encourages_self_scheduling():
    """The schedule_event tool description must invite Edward to book
    events for himself (follow-ups, recaps, nudges) without being asked.

    The plumbing has always worked — the only thing missing was a prompt
    that tells the model self-scheduling is welcome. Guard against the
    description silently regressing to "use when the user asks".
    """
    from services.graph.tools import schedule_event

    description = (schedule_event.description or "").lower()

    # The character-defining phrase: Edward schedules things "for yourself"
    # (i.e. unprompted, anticipating need). If this disappears, Edward
    # quietly reverts to a reactive scheduler.
    assert "for yourself" in description or "yourself" in description, (
        "schedule_event description should invite self-scheduling"
    )

    # At least one of the canonical self-scheduling cues should appear.
    self_scheduling_cues = ["check-in", "follow-up", "follow up", "recap", "nudge", "anticipate"]
    assert any(cue in description for cue in self_scheduling_cues), (
        "schedule_event description should mention at least one self-scheduling pattern"
    )


# ---------------------------------------------------------------------------
# Change 1 — base persona is a character, not "helpful AI assistant"
# ---------------------------------------------------------------------------


def test_default_system_prompt_is_a_character_not_a_tagline():
    """The schema default for system_prompt must read as a persona
    addressed to the owner, not a generic helpful-assistant tagline.

    This is the single highest-leverage Tier 1 change. If the default
    reverts to the CRM tagline, Edward's voice flattens across every
    surface (chat, voice, scheduled events, heartbeat triage).
    """
    from models.schemas import Settings
    from services.owner import OWNER_TOKEN, owner_text

    default_prompt = Settings.model_fields["system_prompt"].default

    # Negative guards — the old tagline should be gone.
    forbidden = ["helpful AI assistant", "a tad cheeky"]
    for phrase in forbidden:
        assert phrase not in default_prompt, (
            f"default system prompt should no longer contain {phrase!r}"
        )

    # Positive guards — the persona is anchored to the owner and has a stance.
    # The default names the owner with the {OWNER} token, rendered at compose
    # time (so a name change needs no database edit); conftest names them Ellis.
    assert OWNER_TOKEN in default_prompt, "persona should name the principal user ({OWNER})"
    assert "Ellis" in owner_text(default_prompt), "the token renders as the owner's name"
    # Stance signals: at least one of these phrases that mark a Jarvis-like
    # posture should appear.
    stance_signals = ["act first", "anticipate", "colleague", "ambient", "without needing permission"]
    assert any(s.lower() in default_prompt.lower() for s in stance_signals), (
        "persona should include a stance signal (e.g. 'act first', 'anticipate')"
    )


# ---------------------------------------------------------------------------
# Change 3 — briefing is a synthesis, not a bulleted log
# ---------------------------------------------------------------------------


async def test_briefing_renders_as_narrative_not_bullet_list(monkeypatch):
    """get_pending_briefing should pipe its raw events through the
    background LLM and return narrative prose. The bracket tags
    ([CALENDAR], [EMAIL], [GMAIL]) that mark the raw bullet format
    should not appear in the returned briefing string.

    Strategy: monkeypatch the DB fetch to return two synthetic events,
    monkeypatch build_background_llm to capture the call and return a
    narrative string, then assert the output is the narrative — not the
    bullet list.
    """
    from datetime import datetime, timezone
    from services.heartbeat import heartbeat_service

    # Synthetic events — enough to be worth synthesizing.
    class _Event:
        def __init__(self, source, sender, summary, contact_name=None, status="noted"):
            self.id = f"evt_{sender}"
            self.source = source
            self.sender = sender
            self.contact_name = contact_name
            self.summary = summary
            self.triage_status = status
            self.created_at = datetime.now(timezone.utc)

    fake_events = [
        _Event("email", "tom@example.com", "Re: invoice — needs reply", contact_name="Tom"),
        _Event("calendar", None, "Lunch with Sara — 12:30"),
    ]

    # Bypass the DB by short-circuiting the function's session work via
    # the helper hook (will be added in the implementation).
    async def _fake_fetch():
        return fake_events

    monkeypatch.setattr(heartbeat_service, "_fetch_briefing_events", _fake_fetch, raising=False)

    # Stub out the marker-update step so we don't touch a real DB.
    async def _fake_mark(event_ids):
        return None

    monkeypatch.setattr(heartbeat_service, "_mark_events_briefed", _fake_mark, raising=False)

    # The narrative the background LLM returns.
    narrative = "Tom's circling back on the invoice and your 12:30 with Sara is set."

    class _FakeLLM:
        async def ainvoke(self, messages):  # noqa: ANN001
            class _R:
                content = narrative
            return _R()

    def _build_background_llm(*_args, **_kwargs):
        return _FakeLLM()

    monkeypatch.setattr(
        "services.llm_utils.build_background_llm",
        _build_background_llm,
        raising=False,
    )

    briefing = await heartbeat_service.get_pending_briefing()

    assert briefing is not None, "briefing should not be empty when events exist"
    assert "[CALENDAR]" not in briefing, "narrative briefing should not contain raw bracket tags"
    assert "[EMAIL]" not in briefing, "narrative briefing should not contain raw bracket tags"
    assert "[GMAIL]" not in briefing, "narrative briefing should not contain raw bracket tags"
    assert briefing.strip() == narrative.strip(), "briefing should be the LLM-synthesized narrative"


# ---------------------------------------------------------------------------
# Change 2 — escalated events interleave into active chats instead of being
# suppressed
# ---------------------------------------------------------------------------


async def test_escalated_event_during_active_chat_triggers_ambient_injection(monkeypatch):
    """When a chat is active AND a heartbeat event is classified as
    'escalated', the event should be routed to an ambient-injection sink
    for the active conversation — not silently deferred.

    Strategy: register an active conversation, capture calls to the
    ambient-inject helper, fire a fake escalated event through the
    routing function, assert the helper was called with the conversation
    id and a summary string.
    """
    from services.heartbeat import heartbeat_service

    # Reset active-chat state between tests.
    heartbeat_service._active_chats.clear()
    heartbeat_service.register_active_chat("conv-123")

    captured: list[tuple[str, str]] = []

    async def _fake_inject(conversation_id: str, summary: str) -> None:
        captured.append((conversation_id, summary))

    monkeypatch.setattr(
        heartbeat_service,
        "inject_ambient_event",
        _fake_inject,
        raising=False,
    )

    # Reset the per-conversation rate limiter so the test isn't flaky.
    if hasattr(heartbeat_service, "_ambient_last_injected_at"):
        heartbeat_service._ambient_last_injected_at.clear()

    # Fire a synthetic escalated event through the routing entry point.
    await heartbeat_service.route_triaged_event(
        event_id="evt_test",
        triage_status="escalated",
        summary="Tom: urgent — invoice is past due",
    )

    heartbeat_service.unregister_active_chat("conv-123")

    assert captured, "escalated event during active chat should inject ambient message"
    conv_id, summary = captured[0]
    assert conv_id == "conv-123"
    assert "Tom" in summary or "invoice" in summary.lower()


async def test_noted_event_during_active_chat_does_not_inject(monkeypatch):
    """Low-urgency ('noted') events should still defer to the briefing —
    only escalated events interleave. Otherwise we'd interrupt focused
    chats with low-value pings.
    """
    from services.heartbeat import heartbeat_service

    heartbeat_service._active_chats.clear()
    heartbeat_service.register_active_chat("conv-456")

    captured: list[tuple[str, str]] = []

    async def _fake_inject(conversation_id: str, summary: str) -> None:
        captured.append((conversation_id, summary))

    monkeypatch.setattr(
        heartbeat_service,
        "inject_ambient_event",
        _fake_inject,
        raising=False,
    )

    if hasattr(heartbeat_service, "_ambient_last_injected_at"):
        heartbeat_service._ambient_last_injected_at.clear()

    await heartbeat_service.route_triaged_event(
        event_id="evt_lownote",
        triage_status="noted",
        summary="Newsletter from substack",
    )

    heartbeat_service.unregister_active_chat("conv-456")

    assert not captured, "noted events should not interleave into active chats"
