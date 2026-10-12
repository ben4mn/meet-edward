"""
Regression: iMessage tap-mentions like ``@Edward …`` must decode with
the ``@`` intact so ``MENTION_PATTERN`` matches and heartbeat fires the
fast-path triage.

Context: the owner typed ``@Edward …`` in two iMessage threads, and
iMessage's address-book autocomplete silently converted the typed name
into a formal tap-mention — the ``text`` column went NULL and the content
moved to ``attributedBody``. Edward's attributed-body decoder *was*
extracting the right text, but ``_strip_leading_control`` was peeling the
leading ``@`` because ``@`` sat in the "stray frame byte" strip set. The
reconstructed text began ``Edward …``, the mention regex missed it, the
listener never called ``trigger_immediate_triage``, and Edward went
silent until the owner manually scheduled a replay.

The fixture is a ``streamtyped`` attributedBody built with the suite's own
encoder (``tests.fixtures.fake_chatdb.attributed_body``) around an invented
message of the same shape: a leading tap-mention, a second sentence, a
curly apostrophe (multi-byte UTF-8) and a one-byte length prefix.
"""

from services.heartbeat.imessage_decoder import decode_attributed_body
from tests.fixtures.fake_chatdb import attributed_body

_MENTION = "@Edward you there? Can you answer Jordan’s question above about Sunday"


def test_the_fixture_has_the_shape_of_a_tap_mention():
    utf = _MENTION.encode("utf-8")
    assert len(utf) < 0x80 and len(utf) != len(_MENTION)  # one-byte length, multi-byte text
    assert attributed_body(_MENTION).count(bytes([len(utf)]) + utf) == 1


def test_tap_mention_preserves_leading_at_sign():
    decoded = decode_attributed_body(attributed_body(_MENTION))

    assert decoded is not None
    assert decoded.startswith("@Edward"), (
        f"Tap-mention must decode with the '@' intact so MENTION_PATTERN "
        f"matches. Got: {decoded!r}"
    )
    # Sanity-check the rest of the body survived too.
    assert decoded == _MENTION
    assert "question above" in decoded
