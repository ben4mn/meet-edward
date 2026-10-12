"""The push ledger: one record per notification that leaves for Ellis's phone,
whatever code path sent it, plus the hard quiet-hours hold for the
deterministic senders.

Origin (2026-09-12 audit): twelve independent senders, eight of which
bypassed every dedup / quiet-hours rule the dispatcher has; the morning
briefing, the memory report, a dispatcher push and an interrupt escalation
all landed within two hours of each other and none of the judgment paths
could see what the others had already said.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from services.push_ledger import (
    JUDGMENT_SOURCES,
    format_recent,
    hold_reason,
    opened_url_matches_conversation,
)

pytestmark = pytest.mark.unit


class TestHoldReason:
    def test_deterministic_sender_is_held_inside_quiet_hours(self):
        assert hold_reason(local_hour=23, respect_quiet_hours=True) == "quiet_hours"
        assert hold_reason(local_hour=3, respect_quiet_hours=True) == "quiet_hours"

    def test_deterministic_sender_goes_out_in_the_day(self):
        assert hold_reason(local_hour=9, respect_quiet_hours=True) is None
        assert hold_reason(local_hour=21, respect_quiet_hours=True) is None

    def test_quiet_hours_boundary_matches_user_context(self):
        # user_context_service treats 22:00-07:00 as quiet: 22 is in, 7 is out.
        assert hold_reason(local_hour=22, respect_quiet_hours=True) == "quiet_hours"
        assert hold_reason(local_hour=7, respect_quiet_hours=True) is None

    def test_judgment_senders_are_never_held(self):
        # The dispatcher, the interrupt ladder, the LLM tool and the wakeup
        # ticks already weigh quiet hours themselves; a door that needs
        # unlocking at 23:30 must still ring.
        assert hold_reason(local_hour=3, respect_quiet_hours=False) is None

    def test_judgment_source_set_names_the_paths_that_think(self):
        assert {"dispatcher", "interrupt", "tool", "wakeup"} <= JUDGMENT_SOURCES


def _row(**kw):
    base = dict(
        sent_at=datetime(2026, 9, 12, 14, 0, tzinfo=timezone.utc),
        source="dispatcher",
        title="Rowan needs the door unlocked",
        body="She is outside with groceries.",
        url="/chat?c=abc",
        held_reason=None,
        opened_at=None,
        open_source=None,
        sent=1,
    )
    base.update(kw)
    return SimpleNamespace(**base)


class TestFormatRecent:
    NOW = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)

    def test_empty_ledger_says_so(self):
        assert format_recent([], now=self.NOW) == "(nothing pushed to Ellis recently)"

    def test_line_carries_age_source_title_and_outcome(self):
        text = format_recent([_row()], now=self.NOW)
        assert "2h ago" in text
        assert "dispatcher" in text
        assert "Rowan needs the door unlocked" in text
        assert "not opened" in text

    def test_opened_and_held_rows_are_labelled(self):
        opened = _row(opened_at=self.NOW - timedelta(hours=1), open_source="sw-click")
        held = _row(source="morning_briefing", title="Good morning", held_reason="quiet_hours", sent=0)
        text = format_recent([opened, held], now=self.NOW)
        assert "opened" in text.splitlines()[0]
        assert "held (quiet_hours)" in text.splitlines()[1]

    def test_body_is_clipped_so_the_prompt_stays_small(self):
        text = format_recent([_row(body="x" * 500)], now=self.NOW)
        assert len(text) < 260


class TestOpenedUrlMatchesConversation:
    def test_chat_url_with_the_conversation_id(self):
        assert opened_url_matches_conversation("/chat?c=abc-123", "abc-123")

    def test_other_urls_do_not_match(self):
        assert not opened_url_matches_conversation("/today", "abc-123")
        assert not opened_url_matches_conversation("/chat?c=other", "abc-123")
        assert not opened_url_matches_conversation(None, "abc-123")


class TestSpokeRecently:
    NOW = datetime(2026, 9, 12, 13, 0, tzinfo=timezone.utc)  # 08:00 Central

    def test_a_wakeup_push_an_hour_ago_means_the_briefing_stays_quiet(self):
        from services.push_ledger import spoke_recently

        rows = [_row(source="wakeup", sent_at=self.NOW - timedelta(hours=1))]
        assert spoke_recently(rows, sources={"wakeup"}, within_hours=3, now=self.NOW)

    def test_held_or_undelivered_pushes_do_not_count(self):
        from services.push_ledger import spoke_recently

        rows = [_row(source="wakeup", sent=0, held_reason="quiet_hours", sent_at=self.NOW - timedelta(hours=1))]
        assert not spoke_recently(rows, sources={"wakeup"}, within_hours=3, now=self.NOW)

    def test_old_or_other_source_pushes_do_not_count(self):
        from services.push_ledger import spoke_recently

        rows = [
            _row(source="wakeup", sent_at=self.NOW - timedelta(hours=5)),
            _row(source="dispatcher", sent_at=self.NOW - timedelta(minutes=10)),
        ]
        assert not spoke_recently(rows, sources={"wakeup"}, within_hours=3, now=self.NOW)


class TestSnooze:
    NOW = datetime(2026, 9, 12, 16, 0, tzinfo=timezone.utc)

    def test_only_rows_whose_snooze_has_passed_are_due(self):
        from services.push_ledger import due_for_resend

        due = _row(id="a", snoozed_until=self.NOW - timedelta(minutes=1), resent_at=None)
        later = _row(id="b", snoozed_until=self.NOW + timedelta(minutes=30), resent_at=None)
        done = _row(id="c", snoozed_until=self.NOW - timedelta(hours=1), resent_at=self.NOW - timedelta(minutes=5))
        assert [r.id for r in due_for_resend([due, later, done], now=self.NOW)] == ["a"]


class TestPayload:
    def test_payload_carries_kind_ledger_id_and_the_three_actions(self):
        from services.push_service import build_payload

        p = build_payload(title="T", body="B", url="/today", tag="x", icon=None,
                          delivery_id="d1", ledger_id="l1", source="wakeup")
        assert p["kind"] == "wakeup" and p["ledger_id"] == "l1" and p["delivery_id"] == "d1"
        assert [a["action"] for a in p["actions"]] == ["open", "done", "snooze"]
        assert p["url"] == "/today"


# --------------------------------------------- counting today (2026-10-11) --
# reach_service's push budget counts Edward's own pushes in Ellis's local day
# (proactivity-spec §4.1.6): rows under a source prefix, minus the exempt
# sources, that actually reached a device.

async def _push_row(source, *, sent=1, sent_at=None, held=None):
    from services.database import PushLogModel, async_session

    async with async_session() as s:
        s.add(PushLogModel(source=source, title="t", body="b", sent=sent, failed=0, held_reason=held,
                           sent_at=sent_at))
        await s.commit()


@pytest.mark.integration
async def test_count_sent_today_counts_the_prefix_minus_the_exempt_sources(clean_db, monkeypatch):
    from datetime import datetime, timezone
    from services import push_ledger, timezone_utils

    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    now = datetime(2026, 10, 11, 20, 0, tzinfo=timezone.utc)  # 15:00 in Springfield
    today = datetime(2026, 10, 11, 14, 0)                     # naive UTC, 09:00 local
    for src in ("reach:speak", "reach:self", "reach:care", "reach:owner", "tool", "reach:ping:owner"):
        await _push_row(src, sent_at=today)
    await _push_row("reach:self", sent=0, sent_at=today)            # reached no device
    await _push_row("reach:self", sent=0, sent_at=today, held="quiet_hours")
    await _push_row("reach:fallback", sent_at=datetime(2026, 10, 11, 4, 0))  # 23:00 local yesterday
    n = await push_ledger.count_sent_today("reach:", exclude=("reach:care", "reach:owner", "reach:ping:owner"),
                                           now=now)
    assert n == 2


@pytest.mark.integration
async def test_count_sent_today_uses_owner_local_day_not_utc(clean_db, monkeypatch):
    from datetime import datetime, timezone
    from services import push_ledger, timezone_utils

    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
    # 01:30 UTC on the 12th is 20:30 on the 11th in Springfield: a push at 23:00
    # UTC on the 11th (18:00 local) is still today.
    now = datetime(2026, 10, 12, 1, 30, tzinfo=timezone.utc)
    await _push_row("reach:self", sent_at=datetime(2026, 10, 11, 23, 0))
    await _push_row("reach:self", sent_at=datetime(2026, 10, 11, 4, 59))   # 23:59 on the 10th
    assert await push_ledger.count_sent_today("reach:", now=now) == 1


@pytest.mark.integration
async def test_count_sent_today_is_none_when_the_ledger_cannot_be_read(monkeypatch):
    from services import push_ledger

    class _Broken:
        def __call__(self, *a, **k):
            raise RuntimeError("no database")

    import services.database as database

    monkeypatch.setattr(database, "async_session", _Broken())
    assert await push_ledger.count_sent_today("reach:") is None
