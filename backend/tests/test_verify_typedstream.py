"""scripts/verify_typedstream.py counts how chat.db's attributedBody blobs
decode, and prints counts only — never a message (pings-media-spec §2.4)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.fixtures.fake_chatdb import FakeChatDB, attributed_body

pytestmark = pytest.mark.unit

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "verify_typedstream.py"


def _big_endian_body(text: str) -> bytes:
    good = attributed_body(text)
    utf = text.encode()
    le = b"\x81" + len(utf).to_bytes(2, "little")
    return good.replace(le, b"\x81" + len(utf).to_bytes(2, "big"), 1)


def test_counts_only(tmp_path):
    db = FakeChatDB(tmp_path / "chat.db")
    secret = "@edward the gate code is 4417 and the long tail of this message keeps going " * 3
    db.add_message("chat1", secret, body_only=True)                       # 0x81, decodes LE
    db.add_message("chat1", "short and sweet", body_only=True)            # one-byte length
    db.add_message("chat1", "plain text column")                          # no body
    db.add_message("chat1", None, attributed=b"\x04\x0bstreamtyped junk")  # no NSString
    db.add_message("chat1", None, attributed=_big_endian_body(secret))    # a BE length: fails
    out = subprocess.run([sys.executable, str(SCRIPT), "--db", str(db.path)], capture_output=True, text=True,
                         timeout=60)
    assert out.returncode == 0, out.stderr
    lines = dict(line.split(": ", 1) for line in out.stdout.strip().splitlines() if ": " in line)
    assert lines["bodies"] == "4"
    assert lines["prefix_0x81"] == "2"
    assert lines["prefix_0x81_le_fits"] == "1"
    assert lines["prefix_0x81_be_fits"] == "1"
    assert lines["decoded"] == "2"
    assert lines["undecoded"] == "2"
    assert "4417" not in out.stdout and "gate" not in out.stdout and "sweet" not in out.stdout


def test_refuses_a_missing_db(tmp_path):
    out = subprocess.run([sys.executable, str(SCRIPT), "--db", str(tmp_path / "nope.db")], capture_output=True,
                         text=True, timeout=60)
    assert out.returncode == 2
