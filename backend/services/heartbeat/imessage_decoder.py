"""Decode iMessage ``attributedBody`` blobs to plain text.

iMessage stores some messages (mentions, replies, tapbacks, pasted links)
only in the ``attributedBody`` column of ``chat.db``, as a binary plist
serialised via ``NSKeyedArchiver``. The prior heuristic regex-scanned the
raw bytes for printable runs and often surfaced framing artefacts such as
``X$versionY$archiverT$topX$objects`` or messages with a leading control
byte like ``%Sorry I missed your call``.

This module does it properly:

1. Parse the blob as a binary plist (``plistlib.loads``).
2. Walk ``$objects`` collecting every non-class string.
3. Ignore structural keys (``$version``, ``$archiver``, ``NSMutable…``).
4. Strip leading ASCII/Unicode control bytes from the chosen string.
5. Fall back to ``None`` rather than guessing when the blob is not a plist.
"""

from __future__ import annotations

import plistlib
from typing import Optional

# NSKeyedArchiver wrapper keys and Foundation class names that must never
# be surfaced as "the message".
_WRAPPER_STRINGS = {
    "$null",
    "$version",
    "$archiver",
    "$objects",
    "$top",
    "NSKeyedArchiver",
    "NSString",
    "NSMutableString",
    "NSAttributedString",
    "NSMutableAttributedString",
    "NSObject",
    "NSDictionary",
    "NSMutableDictionary",
    "NSArray",
    "NSMutableArray",
    "NSNumber",
    "NSValue",
    "NSData",
    "NSMutableData",
    "NSDate",
    "NSParagraphStyle",
    "NSFont",
    "NSColor",
    "__kIMMessagePartAttributeName",
    "__kIMBaseWritingDirectionAttributeName",
    "__kIMDataDetectedAttributeName",
    "__kIMFileTransferGUIDAttributeName",
    "__kIMTextItalicAttributeName",
    "__kIMTextBoldAttributeName",
    "__kIMTextUnderlineAttributeName",
    "__kIMTextStrikethroughAttributeName",
    "__kIMMentionConfirmedMention",
    "NS.string",
    "NSAttributes",
    "NS.keys",
    "NS.objects",
}


def _strip_leading_control(text: str) -> str:
    """Drop leading ASCII/Unicode control bytes left over from iMessage
    reply/tapback framing — e.g. ``%Sorry…`` → ``Sorry…``. A single
    printable-but-nonsensical prefix byte is also stripped when followed
    by a space or a capital letter, since that is the canonical iMessage
    reply-framing shape (seen as ``KIdk…``, ``#Now Nate…``, ``'Im…``).

    ``@`` is excluded from the strip set — it's the leading character of
    a tap-mention payload (``@Edward …``). Stripping it broke
    ``MENTION_PATTERN`` so heartbeat couldn't detect tap-mentions of
    Edward and the fast-path triage never fired (2026-05-28 incident)."""
    out = text.lstrip("".join(chr(c) for c in range(32)) + "\x7f")
    # A single stray prefix byte (one non-alphanumeric ASCII char) followed
    # by an uppercase/lower letter is the iMessage reply frame. Peel it.
    # Exclude '@' so tap-mentions like "@Edward …" survive intact.
    if len(out) >= 2 and out[0] in "!\"#$%&'()*+,-./:;<=>?[\\]^_`{|}~" and out[1].isalpha():
        out = out[1:]
    # Preserve trailing whitespace — real iMessage text often ends with a
    # space (e.g. "Correct "). Only trim leading whitespace we missed.
    return out.lstrip()


_TYPEDSTREAM_MAGIC = b"\x04\x0bstreamtyped"
_NSSTRING_PAYLOAD_MARKER = b"NSString\x01\x94\x84\x01+"


def _read_typedstream_length(blob: bytes, pos: int) -> tuple[int, int] | tuple[None, None]:
    """Read a typedstream length prefix starting at ``pos``. Returns
    ``(length, new_pos)`` or ``(None, None)`` when the blob is too short.

    Encoding:
      * 0x00–0x7f: single-byte length (direct value).
      * 0x81: next 2 bytes are a **little-endian** uint16.
      * 0x82: next 4 bytes are a **little-endian** uint32.

    Little-endian (2026-10-11): 1,859 of 1,859 real ``0x81`` blobs on the
    host Mac decode read LE; read big-endian, 9 fit
    (``scripts/verify_typedstream.py`` counts them). Until then every
    message of 128 bytes or more that lived only in ``attributedBody``
    (mentions, inline replies, rich text) failed here and fell through to
    the legacy regex, which kept its first line — an ask on line two of a
    long ``@edward`` message never reached Edward.
    """
    if pos >= len(blob):
        return None, None
    sentinel = blob[pos]
    pos += 1
    if sentinel == 0x81:
        if pos + 2 > len(blob):
            return None, None
        return int.from_bytes(blob[pos : pos + 2], "little"), pos + 2
    if sentinel == 0x82:
        if pos + 4 > len(blob):
            return None, None
        return int.from_bytes(blob[pos : pos + 4], "little"), pos + 4
    return sentinel, pos


def _decode_typedstream(blob: bytes) -> Optional[str]:
    """Pull the first NSString payload out of an NSArchiver typedstream.

    The on-disk shape is ``…NSString\\x01\\x94\\x84\\x01+<len><utf-8>…``.
    For strings shorter than 128 bytes the length is a single byte; for
    longer ones it is the standard 0x81/0x82 multi-byte prefix. Subsequent
    NSStrings in the same stream use backreferences (``\\x92…``) which we
    ignore — the primary message text is always first.
    """
    idx = blob.find(_NSSTRING_PAYLOAD_MARKER)
    if idx < 0:
        return None
    pos = idx + len(_NSSTRING_PAYLOAD_MARKER)
    length, pos = _read_typedstream_length(blob, pos)
    if length is None or pos is None:
        return None
    if pos + length > len(blob) or length <= 0:
        return None
    try:
        return blob[pos : pos + length].decode("utf-8", errors="replace")
    except Exception:
        return None


def _decode_bplist(blob: bytes) -> Optional[str]:
    """Fallback for the newer NSKeyedArchiver format (rare on iMessage,
    but seen on some macOS versions and on mentions)."""
    try:
        parsed = plistlib.loads(blob, fmt=plistlib.FMT_BINARY)
    except Exception:
        return None

    objects = parsed.get("$objects") if isinstance(parsed, dict) else None
    if not isinstance(objects, list):
        return None

    candidates: list[str] = []
    for obj in objects:
        if not isinstance(obj, str):
            continue
        stripped = obj.strip()
        if not stripped or stripped in _WRAPPER_STRINGS:
            continue
        candidates.append(stripped)

    if not candidates:
        return None
    return max(candidates, key=len)


def decode_attributed_body(blob: Optional[bytes]) -> Optional[str]:
    """Return the user-visible text carried by an iMessage ``attributedBody``
    blob, or ``None`` if the blob is empty / unparsable / contains no text.

    Tries the typedstream (NSArchiver) format first — that's what iMessage
    writes on macOS today — then falls back to NSKeyedArchiver bplist."""
    if not blob:
        return None

    text: Optional[str]
    if blob[: len(_TYPEDSTREAM_MAGIC)] == _TYPEDSTREAM_MAGIC:
        text = _decode_typedstream(blob)
    else:
        text = _decode_bplist(blob)

    if text is None:
        return None
    cleaned = _strip_leading_control(text)
    return cleaned or None
