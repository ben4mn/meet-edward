"""services/pings/limits.py — the loop breaker and the per-chat rate limit
for friends' pings (pings-media-spec §2.11; Ellis's 2026-10-11 ruling keeps
only these for friends: no allowlist)."""

import pytest

from services.pings import limits

pytestmark = pytest.mark.unit


class TestLoop:
    """``pings_in_chat_5m`` counts the pings of this ping's own party (friends'
    for a friend's, Ellis's for Ellis's), this one included (review 2026-10-11)."""

    def test_three_friends_pings_in_five_minutes_is_fine(self):
        assert limits.decide(pings_in_chat_5m=3, third_party=True, third_party_turns_in_chat_1h=0) is None

    def test_a_fourth_friends_ping_in_five_minutes_is_a_loop(self):
        assert limits.decide(pings_in_chat_5m=4, third_party=True, third_party_turns_in_chat_1h=0) == "loop"

    def test_owner_talking_fast_to_edward_is_not_a_loop(self):
        """"remind me at 9", "actually 8:30", "and add milk", "thanks": four
        lines in five minutes is a conversation. His lines have the
        structural echo checks and the unconfirmed-send breaker."""
        assert limits.decide(pings_in_chat_5m=4, third_party=False, third_party_turns_in_chat_1h=0) is None
        assert limits.decide(pings_in_chat_5m=limits.LOOP_MAX_OWNER_PINGS, third_party=False,
                             third_party_turns_in_chat_1h=0) is None

    def test_a_runaway_in_owners_chat_is_still_a_loop(self):
        assert limits.decide(pings_in_chat_5m=limits.LOOP_MAX_OWNER_PINGS + 1, third_party=False,
                             third_party_turns_in_chat_1h=0) == "loop"

    def test_the_loop_wins_over_the_rate(self):
        assert limits.decide(pings_in_chat_5m=9, third_party=True, third_party_turns_in_chat_1h=9) == "loop"


class TestThirdPartyRate:
    def test_five_friend_turns_in_an_hour_is_fine(self):
        assert limits.decide(pings_in_chat_5m=1, third_party=True, third_party_turns_in_chat_1h=5) is None

    def test_six_friend_turns_in_an_hour_limits_the_next(self):
        assert limits.decide(pings_in_chat_5m=1, third_party=True,
                             third_party_turns_in_chat_1h=6) == "third_party_rate"

    def test_owners_own_pings_are_never_rate_limited(self):
        assert limits.decide(pings_in_chat_5m=1, third_party=False, third_party_turns_in_chat_1h=50) is None

    def test_the_hourly_rate_can_be_tuned(self, monkeypatch):
        monkeypatch.setenv("EDWARD_PING_FRIEND_TURNS_PER_HOUR", "2")
        assert limits.decide(pings_in_chat_5m=1, third_party=True,
                             third_party_turns_in_chat_1h=2) == "third_party_rate"
        monkeypatch.setenv("EDWARD_PING_FRIEND_TURNS_PER_HOUR", "junk")
        assert limits.decide(pings_in_chat_5m=1, third_party=True, third_party_turns_in_chat_1h=5) is None


class TestOwnerNote:
    def test_the_loop_note_names_the_chat_and_the_count(self):
        note = limits.owner_note("loop", chat="Trivia crew", count=4)
        assert "Trivia crew" in note and "4 pings in 5 minutes" in note and "loop" in note

    def test_the_rate_note_names_the_chat(self):
        note = limits.owner_note("third_party_rate", chat="Trivia crew", count=6)
        assert "Trivia crew" in note and "6" in note

    def test_windows_are_exported_for_the_store(self):
        assert limits.LOOP_WINDOW_S == 300 and limits.RATE_WINDOW_S == 3600
