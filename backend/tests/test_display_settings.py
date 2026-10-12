"""services/display_settings.py — the /screen page's own settings.

The stage (2026-09-28): two on/off settings Ellis controls from the screen's
panel — whether Edward's thinking shows on the screen, and whether his
lines are spoken aloud. Both default on; only a real bool changes them.
"""

import pytest

from services import display_settings as ds

pytestmark = pytest.mark.unit


def test_thinking_and_speaking_default_on():
    d = ds.default_settings()
    assert d["show_thinking"] is True and d["speak_aloud"] is True


def test_only_a_real_bool_changes_them():
    assert ds._validate({"show_thinking": False, "speak_aloud": False}) == {"show_thinking": False,
                                                                           "speak_aloud": False}
    assert ds._validate({"show_thinking": "no", "speak_aloud": 0}) == {}


def test_an_old_stored_blob_gets_them_back():
    merged = ds._merge_with_defaults({"accent": "blue"})
    assert merged["accent"] == "blue" and merged["show_thinking"] is True and merged["speak_aloud"] is True


def test_the_patch_route_accepts_them():
    from routers.display import DisplaySettingsPatch
    patch = DisplaySettingsPatch(show_thinking=False, speak_aloud=True)
    assert patch.model_dump(exclude_none=True) == {"show_thinking": False, "speak_aloud": True}
