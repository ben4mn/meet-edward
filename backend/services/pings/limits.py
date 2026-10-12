"""How often Edward answers pings in one chat (AREA 3, 2026-10-11).

Two limits, both decided at claim time by the runner and nothing else:

- ``loop``: more than three friends' pings in one chat in five minutes,
  this one included; for the owner's own pings, more than twelve of theirs
  in five minutes. The owner's lines already pass the structural echo checks
  (ROWID, the in-flight claim, the ledger hash, the signature) and the
  unconfirmed-send breaker, so four quick lines from them are a
  conversation, not a loop (review 2026-10-11: "remind me at 9", "actually
  8:30", "and add milk", "thanks" was stopped); the higher count is the
  backstop if every one of those checks failed. Each party counts only its
  own pings. The owner is told.
- ``third_party_rate``: six or more friends' turns started in one chat in the
  last hour. Friends' pings (without the shell, editor,
  Chrome and screens unless ``EDWARD_FRIENDS_FULL_TOOLSET=on``) are bounded in
  cost by this per-chat rate and Edward's own spend share.

Pure: the store counts, this decides. See docs/architecture/pings.md.
"""

from __future__ import annotations

import os
from typing import Optional

LOOP_MAX_PINGS = 3          # more than this many friends' pings in one chat in LOOP_WINDOW_S is a loop
LOOP_MAX_OWNER_PINGS = 12     # the owner's own: a runaway, never a quick back-and-forth
LOOP_WINDOW_S = 300
FRIEND_TURNS_PER_HOUR = 6   # friends' turns started in one chat in RATE_WINDOW_S before the next is limited
RATE_WINDOW_S = 3600


def friend_turns_per_hour() -> int:
    """``EDWARD_PING_FRIEND_TURNS_PER_HOUR``, else 6. A junk value keeps the default."""
    raw = os.getenv("EDWARD_PING_FRIEND_TURNS_PER_HOUR", "").strip()
    try:
        value = int(raw)
    except ValueError:
        return FRIEND_TURNS_PER_HOUR
    return value if value > 0 else FRIEND_TURNS_PER_HOUR


def decide(*, pings_in_chat_5m: int, third_party: bool, third_party_turns_in_chat_1h: int) -> Optional[str]:
    """``"loop"``, ``"third_party_rate"`` or None (answer it). Pure but for the env knob.

    ``pings_in_chat_5m`` counts this ping too, and only pings of its own
    party (friends' for a friend's ping, the owner's for the owner's);
    ``third_party_turns_in_chat_1h`` counts friends' turns already started
    in the chat in the last hour.
    """
    if int(pings_in_chat_5m) > (LOOP_MAX_PINGS if third_party else LOOP_MAX_OWNER_PINGS):
        return "loop"
    if third_party and int(third_party_turns_in_chat_1h) >= friend_turns_per_hour():
        return "third_party_rate"
    return None


def owner_note(reason: str, *, chat: str, count: int) -> str:
    """What the owner is told when a limit stops Edward in a chat (once per chat per hour)."""
    where = chat or "a chat"
    if reason == "loop":
        return (f"I stopped answering in {where} — {count} pings in 5 minutes looks like a loop. "
                "Ping me again in a few minutes if it was real.")
    return (f"I've answered {count} of your friends' asks in {where} in the last hour, so I'm pausing "
            "there for now. Ask me yourself if something needs me.")
