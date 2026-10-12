"""Screensaver settings for the hub.

Stored as a single JSON row so shape changes never need a migration
(reusing DisplayConfigModel with id="hub"). Everything reaching this
module comes off a touchscreen or an HTTP body, so it is all treated as
untrusted: clamp, coerce, fall back — never trust a number to be in range
and never let a bad value blank the screensaver.
"""

import pytest

from services import hub_settings_service as svc


def test_defaults_are_returned_for_an_empty_store():
    s = svc.normalize({})
    assert s["slide_seconds"] == svc.DEFAULT_SLIDE_SECONDS
    assert s["transition"] in svc.TRANSITIONS
    assert isinstance(s["edward_presents"], bool)


def test_defaults_survive_a_none():
    assert svc.normalize(None)["slide_seconds"] == svc.DEFAULT_SLIDE_SECONDS


def test_known_values_pass_through():
    s = svc.normalize({"slide_seconds": 45, "transition": "slide", "edward_presents": False})
    assert s["slide_seconds"] == 45
    assert s["transition"] == "slide"
    assert s["edward_presents"] is False


# ---------------------------------------------------------------------------
# Clamping — a slider on a touchscreen, and an HTTP body, can send anything
# ---------------------------------------------------------------------------

def test_slide_seconds_is_clamped_to_the_allowed_band():
    assert svc.normalize({"slide_seconds": 1})["slide_seconds"] == svc.MIN_SLIDE_SECONDS
    assert svc.normalize({"slide_seconds": 99999})["slide_seconds"] == svc.MAX_SLIDE_SECONDS


def test_a_zero_interval_cannot_be_set():
    """A 0s slide would spin the album as fast as the event loop allows."""
    assert svc.normalize({"slide_seconds": 0})["slide_seconds"] == svc.MIN_SLIDE_SECONDS


def test_negative_interval_cannot_be_set():
    assert svc.normalize({"slide_seconds": -30})["slide_seconds"] == svc.MIN_SLIDE_SECONDS


@pytest.mark.parametrize("bad", ["abc", None, [], {}, float("nan")])
def test_unparseable_interval_falls_back_to_the_default(bad):
    assert svc.normalize({"slide_seconds": bad})["slide_seconds"] == svc.DEFAULT_SLIDE_SECONDS


def test_numeric_strings_are_accepted_since_forms_send_them():
    assert svc.normalize({"slide_seconds": "45"})["slide_seconds"] == 45


def test_unknown_transition_falls_back_rather_than_blanking_the_screen():
    assert svc.normalize({"transition": "explode"})["transition"] == svc.DEFAULT_TRANSITION


@pytest.mark.parametrize("name", ["crossfade", "kenburns", "slide"])
def test_every_advertised_transition_is_accepted(name):
    assert svc.normalize({"transition": name})["transition"] == name


def test_edward_presents_coerces_truthy_values():
    assert svc.normalize({"edward_presents": "true"})["edward_presents"] is True
    assert svc.normalize({"edward_presents": 0})["edward_presents"] is False


def test_unknown_keys_are_dropped_not_stored():
    s = svc.normalize({"slide_seconds": 30, "rm": "-rf"})
    assert "rm" not in s


def test_normalize_is_idempotent():
    once = svc.normalize({"slide_seconds": 45, "transition": "slide"})
    assert svc.normalize(once) == once
