"""main.py starts the ping runner after the heartbeat (the listener kicks
it) and stops it before the heartbeat (AREA 3, pings-media-spec §4.4).
``pings.hooks.start`` honours EDWARD_PINGS; a failure there never stops
the app from starting."""

from pathlib import Path

MAIN = (Path(__file__).resolve().parents[1] / "main.py").read_text()


def test_the_runner_starts_after_the_heartbeat():
    assert MAIN.index("await start_heartbeat()") < MAIN.index("await pings_hooks.start()")


def test_the_runner_stops_before_the_heartbeat():
    assert MAIN.index("await pings_hooks.stop()") < MAIN.index("await stop_heartbeat()")
