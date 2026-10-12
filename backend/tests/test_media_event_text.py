"""services/media/present.event_text — a heartbeat event's text with what its
photos show (review 2026-10-11, minor, verified).

Both ``mind_service.event_text`` (the mind pack's "Waiting on you") and
``noticing_service.event_text`` (the noticer's batch) first ask
``media.present.event_text``. It did not exist: the AttributeError was
swallowed every time and both fell back to ``raw_data.full_text``, which the
listener freezes with bare placeholders (``[image: IMG_0042.heic
attachment:449321]``). The describer writes what a photo shows into
``raw_data.attachment_descriptions`` and the in-memory ``media.hooks`` cache
only, so a friend's photo of a parking citation never reached the noticer.
Pure: no database, no model, no chat.db.
"""

import json
from types import SimpleNamespace

import pytest

from services.media import hooks, present

pytestmark = pytest.mark.unit

PLACEHOLDER = "[image: IMG_0042.heic attachment:449321]"
CITATION = "a parking citation from the City of Springfield, $65, due Oct 16"


@pytest.fixture(autouse=True)
def _fresh_descriptions(monkeypatch):
    """The describer's in-memory cache is process-wide; other tests fill it."""
    from collections import OrderedDict

    monkeypatch.setattr(hooks, "_descriptions", OrderedDict())
    monkeypatch.delenv("EDWARD_MEDIA", raising=False)


def _row(raw=None, summary="photo"):
    return SimpleNamespace(raw_data=json.dumps(raw) if raw is not None else None, summary=summary)


def _described(ref="attachment:449321", desc=CITATION, kind="image", name="IMG_0042.heic"):
    # The describer's line: f"[{kind} {name} {ref}]: {desc}"
    return f"[{kind} {name} {ref}]: {desc}"


def test_the_presenter_exists():
    assert callable(getattr(present, "event_text", None))


def test_a_stored_description_fills_its_placeholder():
    row = _row({"full_text": PLACEHOLDER, "attachment_descriptions": [_described()]})
    text = present.event_text(row)
    assert text == f'[image: IMG_0042.heic attachment:449321 - "{CITATION}"]'


def test_the_caption_is_kept_around_the_filled_placeholder():
    row = _row({"full_text": f"look what I got {PLACEHOLDER}", "attachment_descriptions": [_described()]})
    assert present.event_text(row).startswith("look what I got [image: IMG_0042.heic attachment:449321 - ")


def test_the_hooks_cache_answers_before_the_event_is_rewritten():
    hooks.note_description("attachment:7001", "a boarding pass, UA958, gate closes 21:40")
    row = _row({"full_text": "[image: IMG_7001.heic attachment:7001]"})
    assert "boarding pass, UA958" in present.event_text(row)


def test_a_placeholder_already_described_is_left_alone():
    already = '[image: IMG_1.heic attachment:1 - "a dog"]'
    row = _row({"full_text": already, "attachment_descriptions": [_described("attachment:1", "a cat")]})
    assert present.event_text(row) == already


def test_a_description_with_no_placeholder_in_the_text_is_added():
    row = _row({"full_text": "see attached", "attachment_descriptions": [_described("attachment:12")]})
    text = present.event_text(row)
    assert text.startswith("see attached") and CITATION in text and "attachment:12" in text


def test_a_longer_ref_is_not_taken_for_a_shorter_one():
    row = _row({"full_text": "[image: a.heic attachment:123]",
                "attachment_descriptions": [_described("attachment:12", "not this one")]})
    text = present.event_text(row)
    assert "attachment:123]" in text and "not this one" in text  # added, never spliced into :123


def test_an_undescribed_line_adds_nothing():
    row = _row({"full_text": PLACEHOLDER, "attachment_descriptions": ["[image IMG_0042.heic attachment:449321]"]})
    assert present.event_text(row) == PLACEHOLDER


def test_no_text_is_empty_so_callers_fall_back():
    assert present.event_text(_row(None)) == ""
    assert present.event_text(_row({"rowid": 3})) == ""
    assert present.event_text(SimpleNamespace(raw_data="not json", summary="x")) == ""


def test_a_quote_or_bracket_in_a_description_cannot_break_the_placeholder():
    row = _row({"full_text": PLACEHOLDER, "attachment_descriptions": [_described(desc='sign says "STOP] now"')]})
    text = present.event_text(row)
    assert text.count("[") == 1 and text.count("]") == 1 and text.endswith('"]')


def test_the_mind_pack_and_the_noticer_both_see_what_the_photo_says():
    from services import mind_service, noticing_service

    row = _row({"full_text": PLACEHOLDER, "attachment_descriptions": [_described()]}, summary="photo")
    assert CITATION in mind_service.event_text(row)
    assert CITATION in noticing_service.event_text(row)
