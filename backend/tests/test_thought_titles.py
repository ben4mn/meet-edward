"""Thought conversations need a title that says what the thought was.

155 of Edward's thought conversations are titled "Edward" because the
wake-up tick passes an empty title through. The sidebar then shows a
column of identical rows. ``thought_title`` derives one from the
narrative when none is given: first sentence, trimmed, never empty.
"""

from services.thought_conversation_service import thought_title


def test_explicit_title_wins():
    assert thought_title("3 pings while you were busy", "Long narrative…") == "3 pings while you were busy"


def test_falls_back_to_first_sentence_of_narrative():
    assert thought_title("", "Quiet evening. Two threads worth watching tomorrow.") == "Quiet evening"
    assert thought_title(None, "Parker confirmed 1 PM! Nothing else moved.") == "Parker confirmed 1 PM"


def test_generic_edward_title_is_treated_as_missing():
    assert thought_title("Edward", "Rowan's plans are still forming; I'll wait.") == "Rowan's plans are still forming; I'll wait"


def test_long_first_sentence_is_trimmed_on_a_word():
    long = "Ellis authorized streaming access for Parker in a direct iMessage chat this evening and asked me to recheck the shared login state tomorrow morning."
    t = thought_title("", long)
    assert len(t) <= 64
    assert t.endswith("…")
    assert not t[:-1].endswith(" ")


def test_nothing_at_all_still_gives_a_name():
    assert thought_title("", "") == "Edward's thought"
    assert thought_title(None, None) == "Edward's thought"


def test_stops_before_a_bulleted_list():
    assert thought_title("", "Three threads worth clearing this morning: • Rowan asked about… • Parker…") == "Three threads worth clearing this morning"
    assert thought_title("", "You've got a cluster: - Parker - Rowan") == "You've got a cluster"
