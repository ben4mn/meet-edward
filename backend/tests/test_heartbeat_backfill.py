"""R/G TDD red for the heartbeat attributedBody backfill.

The backfill re-decodes old heartbeat rows whose summary came out of the
pre-plistlib heuristic. It needs a ``looks_garbled`` classifier that
distinguishes real message text from leaked NSKeyedArchiver keys and
iMessage reply-frame residue.
"""

from scripts.backfill_heartbeat_attributedbody import looks_garbled


class TestLooksGarbled:
    def test_flags_plist_wrapper_keys(self):
        assert looks_garbled("X$versionY$archiverT$topX$objects") is True
        assert looks_garbled("$archiver$version") is True

    def test_flags_leading_reply_frame_punctuation(self):
        # "%Sorry…", "KIdk…", "#Now…", "'Im…" — all iMessage reply-frame artefacts.
        assert looks_garbled("%Sorry I missed the start") is True
        assert looks_garbled("#Now Nate is showing us his deck") is True
        assert looks_garbled("*ty Edward - do you have the address") is True
        assert looks_garbled("?Im talking to the landlord") is True
        assert looks_garbled("'Im so proud of you all") is True

    def test_does_not_flag_normal_text(self):
        assert looks_garbled("Hey, how's it going?") is False
        assert looks_garbled("Sorry I'm late") is False
        assert looks_garbled("I got halfway through my book") is False
        # Emoji and punctuation mid-sentence should not trigger.
        assert looks_garbled("We did great. :)") is False

    def test_does_not_flag_empty_or_none(self):
        assert looks_garbled("") is False
        assert looks_garbled(None) is False  # type: ignore[arg-type]

    def test_single_char_leading_letter_K_is_garbled(self):
        # Single capital letter immediately against another letter is the
        # iMessage frame byte, not a word like "Kidnapping".
        assert looks_garbled("KIdk maybe after lunch") is True
        # But multi-letter caps followed by space is a normal acronym.
        assert looks_garbled("BBQ at noon tomorrow") is False
