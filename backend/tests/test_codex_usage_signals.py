"""
Codex 'try again at X' parsing.

``parse_reset_time`` turns the reset time ``codex exec`` names when the
account's window is spent into a datetime. (The evolution_history
signals that used to ride ``get_usage_status`` went with self-evolution
on 2026-09-12; the meter itself is covered in test_codex_usage_meter.)
"""

from datetime import datetime, timedelta



# -- Parser ----------------------------------------------------------------

def test_parse_reset_time_long_format():
    from services.codex_usage_service import parse_reset_time

    msg = "You've hit your usage limit ... try again at May 27th, 2026 1:15 AM."
    dt = parse_reset_time(msg)
    assert dt is not None
    assert dt.year == 2026 and dt.month == 5 and dt.day == 27
    assert dt.hour == 1 and dt.minute == 15


def test_parse_reset_time_short_format_today_when_future():
    from services.codex_usage_service import parse_reset_time

    # Pick a time clearly later today
    target_hour = (datetime.now().hour + 3) % 24
    msg = f"... try again at {target_hour % 12 or 12}:45 {'PM' if target_hour >= 12 else 'AM'}."
    dt = parse_reset_time(msg)
    assert dt is not None
    assert dt > datetime.now()
    assert dt < datetime.now() + timedelta(hours=24)


def test_parse_reset_time_short_format_tomorrow_when_past():
    from services.codex_usage_service import parse_reset_time

    # Use a time that's clearly in the past today
    past_hour = (datetime.now().hour - 3) % 24
    msg = f"... try again at {past_hour % 12 or 12}:00 {'PM' if past_hour >= 12 else 'AM'}."
    dt = parse_reset_time(msg)
    assert dt is not None
    assert dt > datetime.now()
    assert dt < datetime.now() + timedelta(hours=24)


def test_parse_reset_time_returns_none_when_unparseable():
    from services.codex_usage_service import parse_reset_time
    assert parse_reset_time("nothing relevant here") is None
    assert parse_reset_time("") is None
