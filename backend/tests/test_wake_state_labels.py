"""``/api/mind/now`` for the new reasons (proactivity spec §5.2, 2026-10-11).

A ``speak`` wake's closing line is what he told Ellis — the claim of a
noticing, which never reaches a glanceable surface (spec §2.3(d), §6). The
presence sheet, Today's hero and the Mac's stage read these rows, so a
speak row's line is the fixed one the stage lane uses. The nightly is
labelled as the nightly reflection.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from services import wake_state

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 11, 14, 0, tzinfo=timezone.utc)


def _now(recent):
    return wake_state.compose_now(now=NOW, waking=None, recent=recent, next_wake_at=None, approvals_waiting=0,
                                  unread_thoughts=0, quota=None, hands=None)


def test_a_speak_rows_claim_never_reaches_a_glanceable_surface():
    body = _now([{"at": "2026-10-11T13:59:00Z", "source": "speak", "outcome": "acted",
                  "note": "Told Ellis Rowan's text about the surprise party needs a call back."}])
    assert body["last"]["line"] == wake_state.SPEAK_LINE == "Something came up — I left a note."
    assert "Rowan" not in str(body) and "party" not in str(body)


def test_a_speak_row_with_no_line_stays_empty_and_others_keep_theirs():
    body = _now([{"at": "2026-10-11T13:59:00Z", "source": "speak", "outcome": "left_alone", "note": ""},
                 {"at": "2026-10-11T03:00:00Z", "source": "nightly", "outcome": "acted",
                  "note": "Closed two intentions."}])
    assert body["recent"][0]["line"] is None
    assert body["recent"][1]["line"] == "Closed two intentions."


def test_the_new_reasons_have_labels():
    assert wake_state.reason_label("speak") == "Something I noticed"
    assert wake_state.reason_label("nightly") == "Nightly reflection"
    assert wake_state.reason_label("mention") == "A mention"
    assert wake_state.reason_label("whatever") == "whatever"
