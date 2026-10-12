"""Unit tests for services/presence_logic.py — is Ellis here, and where?

Why (2026-09-02): nothing in Edward knew whether Ellis was at a keyboard.
Triage acted within 60s of a text while Ellis was typing his own reply
(46% of actions were preempted by Ellis's reply). Presence is the fact that
lets the settle window stretch when he is clearly on it, and lets an
interrupt land on the screen he is actually looking at.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services.presence_logic import (
    ACTIVE_IDLE_MAX_SECONDS,
    BEACON_TTL_SECONDS,
    THREAD_WINDOW_SECONDS,
    AttentionState,
    Beacon,
    DerivedSignals,
    compute_attention,
    is_active,
    is_fresh,
    normalize_beacon,
    parse_hid_idle_seconds,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 2, 22, 0, tzinfo=timezone.utc)


def _beacon(kind="mac", state="active", idle=5, age=0, device_id=None, **kw) -> Beacon:
    return Beacon(
        device_id=device_id or f"{kind}-1",
        device_name=kw.pop("device_name", device_id or kind),
        kind=kind,
        state=state,
        idle_seconds=idle,
        last_seen_at=NOW - timedelta(seconds=age),
        **kw,
    )


def _signals(**kw) -> DerivedSignals:
    base = dict(active_chat_ids=frozenset(), voice_state="idle", widget_last_used_at=None,
                push_last_used_at=None, last_owner_outbound=None)
    base.update(kw)
    return DerivedSignals(**base)


class TestParsing:
    def test_parse_hid_idle_seconds_converts_ns(self):
        out = '  |   "HIDIdleTime" = 45000000000\n  |   "HIDParameters" = {}\n'
        assert parse_hid_idle_seconds(out) == 45

    def test_parse_hid_idle_seconds_returns_none_on_garbage(self):
        assert parse_hid_idle_seconds("nothing here") is None

    def test_normalize_beacon_rejects_unknown_kind(self):
        with pytest.raises(ValueError):
            normalize_beacon({"device_id": "x", "kind": "toaster", "state": "active"}, NOW)

    def test_normalize_beacon_rejects_unknown_state(self):
        with pytest.raises(ValueError):
            normalize_beacon({"device_id": "x", "kind": "mac", "state": "asleep"}, NOW)

    def test_normalize_beacon_clamps_negative_idle_and_defaults(self):
        b = normalize_beacon({"device_id": "x", "kind": "mac", "state": "active", "idle_seconds": -4}, NOW)
        assert b.idle_seconds == 0 and b.last_seen_at == NOW and b.device_name == "x"


class TestFreshness:
    def test_stale_beacon_is_not_fresh(self):
        assert is_fresh(_beacon(age=BEACON_TTL_SECONDS + 10), NOW) is False
        assert is_fresh(_beacon(age=BEACON_TTL_SECONDS - 10), NOW) is True

    def test_active_requires_fresh_state_active_and_low_idle(self):
        assert is_active(_beacon(), NOW) is True
        assert is_active(_beacon(state="idle"), NOW) is False
        assert is_active(_beacon(idle=ACTIVE_IDLE_MAX_SECONDS + 1), NOW) is False
        assert is_active(_beacon(age=BEACON_TTL_SECONDS + 1), NOW) is False


class TestComputeAttention:
    def test_active_mac_marks_owner_present_and_active_surface(self):
        st = compute_attention([_beacon()], _signals(), NOW)
        assert st.owner_present is True
        assert st.active_surface == "mac:mac-1"
        assert st.seconds_since_activity == 5

    def test_idle_mac_and_no_signals_is_absent(self):
        st = compute_attention([_beacon(state="idle", idle=900)], _signals(), NOW)
        assert st.owner_present is False
        assert st.active_surface is None
        assert st.best_surface_for_interrupt == "push:all"

    def test_display_beacon_alone_does_not_mark_present(self):
        """The kiosk is always 'on'; that says the screen is lit, not that Ellis is there."""
        st = compute_attention([_beacon(kind="display")], _signals(), NOW)
        assert st.owner_present is False
        assert st.best_surface_for_interrupt == "display:display-1"

    def test_owner_outbound_in_window_sets_in_imessage_thread(self):
        sig = _signals(last_owner_outbound=("+1555", NOW - timedelta(seconds=90)))
        st = compute_attention([], sig, NOW)
        assert st.in_imessage_thread == "+1555"
        assert st.owner_present is True

    def test_owner_outbound_outside_window_is_ignored(self):
        sig = _signals(last_owner_outbound=("+1555", NOW - timedelta(seconds=THREAD_WINDOW_SECONDS + 1)))
        st = compute_attention([], sig, NOW)
        assert st.in_imessage_thread is None and st.owner_present is False

    def test_active_chat_stream_marks_present_without_beacons(self):
        st = compute_attention([], _signals(active_chat_ids=frozenset({"conv-1"})), NOW)
        assert st.owner_present is True

    def test_voice_session_marks_present(self):
        st = compute_attention([], _signals(voice_state="listening"), NOW)
        assert st.owner_present is True

    def test_menubar_beats_web_beats_display_for_interrupt_surface(self):
        beacons = [_beacon(kind="display"), _beacon(kind="mac-web"), _beacon(kind="menubar")]
        assert compute_attention(beacons, _signals(), NOW).best_surface_for_interrupt == "menubar:menubar-1"
        assert compute_attention(beacons[:2], _signals(), NOW).best_surface_for_interrupt == "mac-web:mac-web-1"

    def test_iphone_seen_recently_targets_that_device_push(self):
        phone = _beacon(kind="iphone-pwa", state="hidden", idle=0, age=600)
        st = compute_attention([phone], _signals(), NOW)
        assert st.best_surface_for_interrupt == "push:iphone-pwa-1"

    def test_iphone_stale_falls_back_to_push_all(self):
        phone = _beacon(kind="iphone-pwa", state="hidden", age=3600)
        st = compute_attention([phone], _signals(), NOW)
        assert st.best_surface_for_interrupt == "push:all"

    def test_lowest_idle_wins_active_surface_tie_breaks_by_priority(self):
        a = _beacon(kind="mac", idle=30, device_id="mac-a")
        b = _beacon(kind="mac-web", idle=3, device_id="web-b")
        c = _beacon(kind="menubar", idle=3, device_id="bar-c")
        st = compute_attention([a, b, c], _signals(), NOW)
        assert st.active_surface == "menubar:bar-c"

    def test_attention_state_reason_names_the_deciding_signal(self):
        st = compute_attention([_beacon(kind="menubar")], _signals(), NOW)
        assert "menubar" in st.reason
        absent = compute_attention([], _signals(), NOW)
        assert "no" in absent.reason.lower()

    def test_as_dict_is_json_friendly(self):
        d = compute_attention([_beacon()], _signals(), NOW).as_dict()
        assert d["owner_present"] is True and isinstance(d["fresh_beacons"], list)


class TestAtDevice:
    """The stage (2026-09-28): /screen needs to know whether Ellis is at the
    Mac mini itself — its own probe beacon, not whichever surface has the
    lowest idle (a phone in his hand would win that)."""

    def test_an_active_probe_beacon_means_owner_is_at_that_device(self):
        from services.presence_logic import at_device
        mini = _beacon(kind="mac", device_id="mac-mini-backend", idle=10)
        phone = _beacon(kind="iphone-pwa", device_id="phone", idle=0)
        assert at_device([phone, mini], "mac-mini-backend", NOW) is True

    def test_idle_stale_or_missing_is_not_at_the_device(self):
        from services.presence_logic import at_device
        idle = _beacon(kind="mac", device_id="mac-mini-backend", state="idle", idle=400)
        stale = _beacon(kind="mac", device_id="mac-mini-backend", age=BEACON_TTL_SECONDS + 1)
        long_idle = _beacon(kind="mac", device_id="mac-mini-backend", idle=ACTIVE_IDLE_MAX_SECONDS)
        assert at_device([idle], "mac-mini-backend", NOW) is False
        assert at_device([stale], "mac-mini-backend", NOW) is False
        assert at_device([long_idle], "mac-mini-backend", NOW) is False
        assert at_device([], "mac-mini-backend", NOW) is False

    def test_a_display_beacon_never_counts(self):
        """The /screen kiosk is always 'active': it means the screen is lit."""
        from services.presence_logic import at_device
        kiosk = _beacon(kind="display", device_id="mac-mini-backend", idle=0)
        assert at_device([kiosk], "mac-mini-backend", NOW) is False
