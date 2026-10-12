"""R/G TDD red for Bucket G (iMessage attributedBody parsing).

The existing heuristic at listener_imessage.py:_extract_text_from_attributed_body
treats the NSKeyedArchiver blob as a UTF-8 string and regex-finds the
longest printable run. That leaks:

  * ``X$versionY$archiverT$topX$objects`` when the wrapping plist keys
    happen to be the longest printable stretch.
  * Single-leading-control-char artifacts like ``%Sorry…``, ``KIdk…``,
    ``#Now Nate…`` from tapback/reply framing bytes.

The replacement parses the blob as a binary plist, walks ``$objects``
to find the real NSString payload, and strips leading control bytes.
"""

import plistlib
from services.heartbeat.imessage_decoder import decode_attributed_body


def _make_attributed_body(text: str) -> bytes:
    """Build a minimal NSKeyedArchiver-shaped bplist carrying ``text``.

    Real iMessage blobs include an attribute dict; we keep them simple so
    the decoder is tested on structure rather than exact Apple output.
    """
    data = {
        "$version": 100000,
        "$archiver": "NSKeyedArchiver",
        "$top": {"root": plistlib.UID(1)},
        "$objects": [
            "$null",
            {
                "NS.string": plistlib.UID(2),
                "NSAttributes": plistlib.UID(3),
                "$class": plistlib.UID(4),
            },
            text,
            {"NSDictionary": plistlib.UID(0)},
            {"$classname": "NSMutableAttributedString",
             "$classes": ["NSMutableAttributedString", "NSAttributedString", "NSObject"]},
        ],
    }
    return plistlib.dumps(data, fmt=plistlib.FMT_BINARY)


class TestDecodeAttributedBody:
    def test_decodes_attributedbody_to_plain_text(self):
        blob = _make_attributed_body("Hey! How's it going?")
        assert decode_attributed_body(blob) == "Hey! How's it going?"

    def test_strips_leading_control_chars(self):
        # Real iMessage tapback frames leave a single control byte in front of
        # the user-visible text. Our decoder should not hand that back.
        blob = _make_attributed_body("\x03Sorry I missed your call earlier")
        assert decode_attributed_body(blob) == "Sorry I missed your call earlier"

    def test_does_not_return_nskeyedarchiver_wrapper_keys(self):
        # The old heuristic occasionally returned things like
        # "X$versionY$archiverT$topX$objects" — never happen again.
        blob = _make_attributed_body("the only real message here")
        result = decode_attributed_body(blob)
        assert result is not None
        for wrapper in ("$version", "$archiver", "$objects", "$top"):
            assert wrapper not in result, f"leaked {wrapper!r} into decoded text"

    def test_returns_none_for_empty_blob(self):
        assert decode_attributed_body(b"") is None
        assert decode_attributed_body(None) is None  # type: ignore[arg-type]

    def test_returns_none_for_non_plist_bytes(self):
        # Random bytes that don't parse as a bplist should not crash and
        # should not return garbled content.
        assert decode_attributed_body(b"not a plist at all") is None

    def test_decodes_unicode_message(self):
        blob = _make_attributed_body("¯\\_(ツ)_/¯ 🎉")
        assert decode_attributed_body(blob) == "¯\\_(ツ)_/¯ 🎉"


def _make_typedstream_short(text: str) -> bytes:
    """Build a minimal NSArchiver ``streamtyped`` blob carrying ``text``.

    The on-disk shape of an iMessage attributedBody starts with:

        \\x04\\x0bstreamtyped\\x81\\xe8\\x03\\x84\\x01@
          \\x84\\x84\\x84\\x12NSAttributedString\\x00
          \\x84\\x84\\x08NSObject\\x00
          \\x85\\x92\\x84\\x84\\x84\\x08NSString\\x01\\x94\\x84\\x01+
          <length byte><utf-8 text><trailer>

    For ``text`` shorter than 128 bytes, ``<length byte>`` is a single
    byte equal to len(text.encode('utf-8'))."""
    utf = text.encode("utf-8")
    assert len(utf) < 128, "use _make_typedstream_long for >=128 chars"
    header = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@"
        b"\x84\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSObject\x00"
        b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
    )
    trailer = b"\x86\x84\x02iI\x01\x01\x86"  # any bytes — decoder reads length
    return header + bytes([len(utf)]) + utf + trailer


def _make_typedstream_long(text: str) -> bytes:
    """Same shape but with the 0x81 uint16 length prefix used when the
    string is 128 bytes or longer. Messages writes it **little-endian**:
    this fixture used to encode big-endian, which is why the decoder's
    big-endian bug passed its own test (R9, 2026-10-11)."""
    utf = text.encode("utf-8")
    assert 128 <= len(utf) < 65536
    header = (
        b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@"
        b"\x84\x84\x84\x12NSAttributedString\x00"
        b"\x84\x84\x08NSObject\x00"
        b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
    )
    length_block = b"\x81" + len(utf).to_bytes(2, "little")
    trailer = b"\x86\x84\x02iI\x01\x01\x86"
    return header + length_block + utf + trailer


class TestDecodeTypedStream:
    """Real iMessage blobs use NSArchiver typedstream, not NSKeyedArchiver."""

    def test_decodes_short_typedstream_body(self):
        blob = _make_typedstream_short("Ok see you soon ")
        assert decode_attributed_body(blob) == "Ok see you soon "

    def test_decodes_short_typedstream_three_capitals(self):
        blob = _make_typedstream_short("LOL")
        assert decode_attributed_body(blob) == "LOL"

    def test_decodes_long_typedstream_body(self):
        payload = "The train is running late again, sorry, " * 4  # >128 chars
        blob = _make_typedstream_long(payload)
        assert decode_attributed_body(blob) == payload

    def test_decodes_unicode_typedstream_body(self):
        blob = _make_typedstream_short("🎉 party 🎉")
        assert decode_attributed_body(blob) == "🎉 party 🎉"

    def test_malformed_typedstream_returns_none(self):
        # Magic present, but the NSString marker is absent.
        assert decode_attributed_body(b"\x04\x0bstreamtyped\x81\xe8\x03junk") is None


class TestTypedStreamLengthIsLittleEndian:
    """R9 (2026-10-11): 1,859 of 1,859 real 0x81-prefixed blobs on the mini
    decode read little-endian; read big-endian, 9 fit. Every message of 128
    bytes or more that lived only in attributedBody (mentions, replies, rich
    text) fell through to the legacy regex, which kept its first line."""

    def test_header_81_e8_03_is_one_thousand(self):
        from services.heartbeat.imessage_decoder import _read_typedstream_length

        assert _read_typedstream_length(b"\x81\xe8\x03", 0) == (1000, 3)

    def test_82_reads_four_bytes_little_endian(self):
        from services.heartbeat.imessage_decoder import _read_typedstream_length

        assert _read_typedstream_length(b"\x82\xa0\x86\x01\x00", 0) == (100_000, 5)

    def test_a_300_byte_body_behind_81_decodes_whole(self):
        payload = ("@edward look at the used tickets for Riverside Fest on Saturday and tell me which ones "
                   "are real; the screenshots are above. ") * 3
        payload = payload[:300]
        assert len(payload.encode()) == 300
        blob = _make_typedstream_long(payload)
        assert blob.count(b"\x81\x2c\x01") == 1, "300 = 0x012c, written low byte first"
        assert decode_attributed_body(blob) == payload

    def test_a_body_behind_82_decodes_whole(self):
        payload = "x" * 70_000
        utf = payload.encode()
        header = (
            b"\x04\x0bstreamtyped\x81\xe8\x03\x84\x01@"
            b"\x84\x84\x84\x12NSAttributedString\x00"
            b"\x84\x84\x08NSObject\x00"
            b"\x85\x92\x84\x84\x84\x08NSString\x01\x94\x84\x01+"
        )
        blob = header + b"\x82" + len(utf).to_bytes(4, "little") + utf + b"\x86\x84\x02iI\x01\x01\x86"
        assert decode_attributed_body(blob) == payload

    def test_the_ask_at_character_524_of_572_survives(self):
        head = "so we were thinking about the weekend plans and " * 11
        ask = "@edward can you check the used ticket prices?"
        text = (head[:524] + ask)[:572]
        blob = _make_typedstream_long(text)
        out = decode_attributed_body(blob)
        assert out == text and out.index("@edward") == 524


class TestLegacyFallbackJoinsLines:
    """The legacy regex split a multi-line message at each newline and kept
    only the first run, so an ask on line two was lost."""

    def test_multi_line_body_is_joined(self):
        from services.heartbeat.listener_imessage import _extract_text_from_attributed_body_legacy

        blob = b"\x01+\x35hey all\nfirst line here\r\n@edward what time is dinner?\x86\x84\x02iI"
        out = _extract_text_from_attributed_body_legacy(blob)
        assert out is not None
        assert "hey all" in out and "@edward what time is dinner?" in out
        assert out.index("hey all") < out.index("first line here") < out.index("@edward")

    def test_single_line_unchanged(self):
        from services.heartbeat.listener_imessage import _extract_text_from_attributed_body_legacy

        blob = b"\x01+\x19Grab milk on the way home\x86\x84\x02iI\x01\x01\x86\x92\x84NS.rangeval.locationZNS.special"
        assert _extract_text_from_attributed_body_legacy(blob) == "Grab milk on the way home"
