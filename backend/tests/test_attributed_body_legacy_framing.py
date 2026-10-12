"""
Regression: the legacy attributedBody decoder surfaced NSKeyedArchiver framing
as if it were the message.

Found 2026-08-22, immediately after get_recent_messages started returning real
text (it previously returned only chat metadata, so this was invisible):

    Aug 22 6:41 AM — You: X$versionY$archiverT$topX$objects

That is the bplist wrapper, not something Ellis typed. imessage_decoder's docstring
calls out this exact artefact as the reason the plist-based decoder was written —
but the legacy regex fallback still runs when plistlib rejects a blob, and its
skip-list only covered Cocoa CLASS names (NSString, NSObject, ...). It never
covered the "$"-prefixed archive keys, so the framing run survived, and being the
longest printable run it was then chosen as "the message".

Better to return nothing than to put archive scaffolding in Ellis's message history.
"""

from __future__ import annotations

import plistlib

import pytest

from services.heartbeat.listener_imessage import (
    _extract_text_from_attributed_body_legacy as legacy,
)


class TestFramingIsNeverTheMessage:
    @pytest.mark.parametrize(
        "blob",
        [
            b"X$versionY$archiverT$topX$objects",
            b"bplist00\xd4\x01\x02\x03\x04X$versionY$archiverT$topX$objects",
            b"$archiver\x10\x00NSKeyedArchiver$null",
            b"X$objectsX$versionT$top",
        ],
    )
    def test_pure_framing_yields_nothing(self, blob):
        assert legacy(blob) is None

    def test_the_exact_live_regression(self):
        assert legacy(b"X$versionY$archiverT$topX$objects") is None

    def test_nskeyedarchiver_name_is_not_the_message(self):
        assert legacy(b"\x04\x0bstreamtyped\x81NSKeyedArchiver\x00") is None


class TestRealTextStillSurvives:
    def test_plain_message_is_returned(self):
        assert "See you at the station" in (
            legacy(b"\x04\x0bstreamtyped\x81\x01See you at the station\x86") or ""
        )

    def test_message_wins_over_surrounding_framing(self):
        blob = b"X$versionY$archiverT$topX$objects\x00Running ten minutes late!!\x00NSString"
        assert legacy(blob) == "Running ten minutes late!!"

    def test_empty_blob_is_none(self):
        assert legacy(b"") is None
        assert legacy(None) is None


class TestAttributeNamesAreNeverTheMessage:
    """The skip-list enumerated exactly one __kIM* attribute name.

    Found live on 2026-08-22, in the same blob that produced the "$version"
    leak — once the archive keys were skipped, the next-longest printable run
    was another attribute name:

        Aug 22 6:41 AM — You: __kIMCalendarEventAttributeName

    Enumerating these one at a time is a losing game; Apple adds them freely.
    Any token starting with "__kIM" is framing by construction.
    """

    @pytest.mark.parametrize(
        "name",
        [
            "__kIMCalendarEventAttributeName",
            "__kIMMessagePartAttributeName",
            "__kIMFileTransferGUIDAttributeName",
            "__kIMDataDetectedAttributeName",
            "__kIMLinkAttributeName",
            "__kIMSomeFutureAttributeNameAppleHasNotInventedYet",
        ],
    )
    def test_attribute_names_are_skipped(self, name):
        assert legacy(name.encode()) is None

    def test_the_exact_second_live_regression(self):
        blob = b"X$versionY$archiverT$topX$objects__kIMCalendarEventAttributeName"
        assert legacy(blob) is None

    def test_real_text_still_wins_over_attribute_names(self):
        blob = b"__kIMCalendarEventAttributeName\x00Running ten minutes late!!\x00"
        assert legacy(blob) == "Running ten minutes late!!"

    def test_a_message_merely_mentioning_kim_is_not_dropped(self):
        """Only the __kIM prefix is framing — don't eat ordinary words."""
        # \x01 splits the printable run, as in test_plain_message_is_returned —
        # without it the text merges with "streamtyped" and is skipped for that
        # reason rather than the one under test.
        assert "Kim said hi" in (
            legacy(b"\x04\x0bstreamtyped\x81\x01Kim said hi\x86") or ""
        )


class TestLongestIsNotTheRightHeuristic:
    """The decoder picked the longest printable run, and junk can be longer.

    A live case (2026-08-22). The blob's candidate runs included
    both the real message and a longer Foundation range descriptor:

        'Grab milk on the way home'          <- what Ellis actually sent
        'NS.rangeval.locationZNS.special'    <- longer, so it won

    In typedstream layout the message text is the first non-structural run,
    sitting right after the NSString marker and ahead of the attribute names.
    Position is a far better signal than length.
    """

    def test_real_text_beats_a_longer_junk_run(self):
        blob = (
            b"\x04\x0bstreamtyped\x81NSString\x01Grab milk on the way home\x86"
            b"\x01NS.rangeval.locationZNS.special\x86"
        )
        assert legacy(blob) == "Grab milk on the way home"

    def test_ns_dotted_keys_are_skipped(self):
        assert legacy(b"\x01NS.rangeval.locationZNS.special\x86") is None
        assert legacy(b"\x01NS.string\x86") is None
        assert legacy(b"\x01NS.objects\x86") is None

    def test_a_short_message_is_not_beaten_by_class_names(self):
        blob = b"\x04\x0bstreamtyped\x81NSMutableAttributedString\x01Checking\x86"
        assert legacy(blob) == "Checking"

    def test_message_with_a_dollar_amount_survives(self):
        """The archive-key filters must not eat ordinary text about money."""
        blob = b"\x04\x0bstreamtyped\x81\x01A good used bike would be $50-$500.\x86"
        assert legacy(blob) == "A good used bike would be $50-$500."

    def test_message_that_looks_like_a_domain_survives(self):
        """People do send a bare hostname as a whole message — 'Bounce.example.org'."""
        blob = b"\x04\x0bstreamtyped\x81\x01Bounce.example.org\x86"
        assert legacy(blob) == "Bounce.example.org"
