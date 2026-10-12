"""R/G TDD for Bug #17 — heartbeat trigger detection misses variants.

The conversations router today flags `[HEARTBEAT EVENT]` and
`[SCHEDULED EVENT]` as triggers. But triage_service emits five
heartbeat-trigger prefix variants:

* `[HEARTBEAT EVENT]`            (ACT_TRIGGER)
* `[HEARTBEAT — @mention]`       (MENTION_TRIGGER)
* `[HEARTBEAT — follow-up reply]` (REPLY_TRIGGER)
* `[HEARTBEAT - CALENDAR]`        (calendar urgent path)
* `[HEARTBEAT - EMAIL]` / `[HEARTBEAT - GMAIL]` (email triage path)

The undetected four render as raw user-message text via
``MessageBubble``'s ``whitespace-pre-wrap break-words`` branch, which
preserves newlines but doesn't break unbroken tokens (chat GUIDs, URLs
in summaries). Result: visible overflow on mobile and ugly layout on
desktop.

Fix: extract trigger detection to a single helper and broaden it.
"""

from __future__ import annotations


def test_detect_act_trigger():
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger("[HEARTBEAT EVENT]\nFrom: Foo")
    assert is_trigger is True
    assert trigger_type == "heartbeat"


def test_detect_mention_trigger():
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger("[HEARTBEAT — @mention]\nFrom: Foo")
    assert is_trigger is True
    assert trigger_type == "heartbeat"


def test_detect_reply_trigger():
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger(
        "[HEARTBEAT — follow-up reply]\nFrom: Foo"
    )
    assert is_trigger is True
    assert trigger_type == "heartbeat"


def test_detect_calendar_trigger():
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger("[HEARTBEAT - CALENDAR]\nEvent: X")
    assert is_trigger is True
    assert trigger_type == "heartbeat"


def test_detect_email_trigger():
    from services.trigger_detection import detect_trigger

    for prefix in ("[HEARTBEAT - EMAIL]", "[HEARTBEAT - GMAIL]"):
        is_trigger, trigger_type = detect_trigger(f"{prefix}\nFrom: x@y.com")
        assert is_trigger is True, f"{prefix} should be detected"
        assert trigger_type == "heartbeat"


def test_detect_scheduled_event_trigger():
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger("[SCHEDULED EVENT]\nTime: 7am")
    assert is_trigger is True
    assert trigger_type == "scheduled_event"


def test_detect_plain_user_message_is_not_trigger():
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger("Hey, what's up?")
    assert is_trigger is False
    assert trigger_type is None


def test_detect_trigger_handles_leading_whitespace():
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger("  \n[HEARTBEAT EVENT]\nbody")
    assert is_trigger is True
    assert trigger_type == "heartbeat"


def test_detect_trigger_case_insensitive_on_prefix():
    """The triggers are emitted with uppercase HEARTBEAT but the detector
    should not be fragile to casing changes."""
    from services.trigger_detection import detect_trigger

    is_trigger, trigger_type = detect_trigger("[heartbeat - calendar]\nevent: x")
    assert is_trigger is True
    assert trigger_type == "heartbeat"


def test_conversations_router_uses_shared_detector():
    """Regression — the router should call detect_trigger, not maintain
    its own inline if/elif chain. This guards against future trigger
    variants being missed."""
    import services.trigger_detection as td
    from routers import conversations as conv_mod
    import inspect

    src = inspect.getsource(conv_mod)
    assert "detect_trigger" in src, (
        "conversations router must import and use detect_trigger so all "
        "trigger variants are recognized uniformly"
    )
