"""media.hooks.describe_ref — the description the describer noted for an
attachment reaches the placeholder text and saved history (pings-media-spec
§3.5, Appendix A5)."""

import pytest

from services.media import hooks

pytestmark = pytest.mark.unit


def test_a_noted_description_reaches_describe_ref_and_the_placeholder():
    hooks.note_description("attachment:449321", "screenshot of a Ticketmaster page, Riverside Fest, sold out")
    assert hooks.describe_ref("attachment:449321").startswith("screenshot of a Ticketmaster page")
    text = hooks.placeholder({"rowid": 449321}, "[image: IMG_3533.heic attachment:449321]")
    assert text == '[image: IMG_3533.heic attachment:449321 - "screenshot of a Ticketmaster page, Riverside Fest, sold out"]'


def test_unknown_refs_have_no_description():
    assert hooks.describe_ref("attachment:1") is None
    assert hooks.placeholder({"rowid": 1}, "[image: a.heic attachment:1]") == "[image: a.heic attachment:1]"


def test_media_off_describes_nothing(monkeypatch):
    hooks.note_description("attachment:2", "a dog")
    monkeypatch.setenv("EDWARD_MEDIA", "off")
    assert hooks.describe_ref("attachment:2") is None
    assert hooks.placeholder({"rowid": 2}, "[image: b.heic attachment:2]") == "[image: b.heic attachment:2]"


def test_the_present_fallback_answers_unknown_refs():
    from services.media import present
    assert present.describe_ref("media:12") is None
