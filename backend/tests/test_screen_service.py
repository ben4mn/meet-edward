"""services/screen_service.py — Edward's screen on the Mac mini.

Why (2026-09-07, Ellis): the desk monitor is Edward's when Ellis is not at it.
Two layers: a tile grid Edward composes (his default view) and whatever
he puts on top — a website he navigates to (YouTube, Twitch), a picture,
a movie, an alert. All of it is his call; nothing here schedules content.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import screen_service as ss

pytestmark = pytest.mark.unit

NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)


class TestTiles:
    def test_every_tile_type_has_a_default_size_that_exists(self):
        for t in ss.TILE_TYPES:
            assert ss.DEFAULT_SIZE[t] in ss.SIZES

    def test_normalize_keeps_known_tiles_and_drops_unknown_with_a_warning(self):
        tiles, warnings = ss.normalize_layout([
            {"type": "clock"},
            {"type": "weather"},
            {"type": "text", "body": "hello", "size": "m"},
        ])
        assert [t["type"] for t in tiles] == ["clock", "text"]
        assert tiles[0]["size"] == ss.DEFAULT_SIZE["clock"]
        assert tiles[1]["size"] == "m" and tiles[1]["body"] == "hello"
        assert any("weather" in w for w in warnings)

    def test_authored_tiles_need_their_content(self):
        tiles, warnings = ss.normalize_layout([
            {"type": "text"},
            {"type": "image", "source": "https://x/y.jpg"},
            {"type": "image", "source": "ftp://nope"},
            {"type": "list", "items": ["a", {"label": "b", "detail": "c"}]},
            {"type": "stat", "value": "42", "label": "memories"},
            {"type": "countdown", "target": "2026-12-25T00:00:00Z", "label": "Christmas"},
        ])
        assert [t["type"] for t in tiles] == ["image", "list", "stat", "countdown"]
        assert tiles[1]["items"] == [{"label": "a", "detail": None}, {"label": "b", "detail": "c"}]
        assert len(warnings) == 2

    def test_unknown_size_falls_back_to_the_default_size(self):
        tiles, warnings = ss.normalize_layout([{"type": "calendar", "size": "enormous"}])
        assert tiles[0]["size"] == ss.DEFAULT_SIZE["calendar"] and warnings

    def test_layout_caps_the_tile_count(self):
        tiles, warnings = ss.normalize_layout([{"type": "clock"}] * (ss.MAX_TILES + 3))
        assert len(tiles) == ss.MAX_TILES and any("most" in w for w in warnings)

    def test_html_tile_body_is_capped(self):
        tiles, warnings = ss.normalize_layout([{"type": "html", "body": "x" * (ss.MAX_TILE_HTML_CHARS + 1)}])
        assert tiles == [] and warnings


class TestPacking:
    def test_pack_places_tiles_first_fit_left_to_right_top_to_bottom(self):
        placed, warnings = ss.pack_layout([
            {"type": "clock", "size": "s"},        # 2x1
            {"type": "alerts", "size": "l"},       # 4x1
            {"type": "calendar", "size": "tall"},  # 2x2
            {"type": "edward", "size": "hero"},    # 4x2
        ])
        assert warnings == []
        assert [(p["col"], p["row"], p["w"], p["h"]) for p in placed] == [
            (0, 0, 2, 1), (2, 0, 4, 1), (0, 1, 2, 2), (2, 1, 4, 2),
        ]

    def test_pack_drops_what_does_not_fit_and_says_so(self):
        placed, warnings = ss.pack_layout([{"type": "html", "body": "<b>x</b>", "size": "full"}] * 3)
        assert len(placed) == 1
        assert warnings and "fit" in warnings[0]

    def test_pack_skips_a_gap_a_later_small_tile_can_still_fill(self):
        placed, _ = ss.pack_layout([
            {"type": "calendar", "size": "tall"},  # 2x2 at 0,0
            {"type": "edward", "size": "hero"},    # 4x2 at 2,0
            {"type": "vitals", "size": "s"},       # 2x1 at 0,2
        ])
        assert (placed[2]["col"], placed[2]["row"]) == (0, 2)

    def test_default_layout_fills_the_grid_without_warnings(self):
        placed, warnings = ss.pack_layout(ss.default_layout())
        assert warnings == []
        types = {p["type"] for p in placed}
        assert {"clock", "edward", "calendar", "alerts"} <= types
        assert sum(p["w"] * p["h"] for p in placed) == ss.GRID_COLS * ss.GRID_ROWS


class TestShow:
    def test_web_is_not_a_show_kind_any_more(self):
        """Websites open in Ellis's Chrome (browser_hands), not in the page."""
        assert "web" not in ss.SHOW_KINDS
        with pytest.raises(ValueError):
            ss.make_show_content(kind="web", source="https://www.twitch.tv/examplestreamer", now=NOW)

    def test_other_kinds_go_through_the_household_validation(self):
        c = ss.make_show_content(kind="video", source="https://youtu.be/dQw4w9WgXcQ", seconds=60, now=NOW)
        assert c["embed_url"].startswith("https://www.youtube.com/embed/dQw4w9WgXcQ")
        assert c["expires_at"] == (NOW + timedelta(seconds=60)).isoformat()
        with pytest.raises(ValueError):
            ss.make_show_content(kind="alert", body="", now=NOW)
        with pytest.raises(ValueError):
            ss.make_show_content(kind="movie", source="https://x", now=NOW)

    def test_show_expiry(self):
        c = ss.make_show_content(kind="text", body="hi", seconds=30, now=NOW)
        assert not ss.show_is_expired(c, NOW + timedelta(seconds=29))
        assert ss.show_is_expired(c, NOW + timedelta(seconds=30))
        assert not ss.show_is_expired(ss.make_show_content(kind="alert", body="hi", now=NOW), NOW + timedelta(days=9))


class TestSnapshot:
    def test_snapshot_uses_the_default_layout_when_edward_has_not_composed(self):
        snap = ss.build_snapshot(layout=None, layout_note=None, layout_updated_at=None, layout_updated_by=None, show=None, now=NOW)
        assert snap["layout_source"] == "default" and snap["show"] is None
        assert snap["grid"] == {"cols": ss.GRID_COLS, "rows": ss.GRID_ROWS}
        assert all({"col", "row", "w", "h"} <= set(t) for t in snap["layout"])

    def test_snapshot_packs_edwards_layout_and_drops_an_expired_show(self):
        show = ss.make_show_content(kind="text", body="bye", seconds=5, now=NOW - timedelta(seconds=10))
        snap = ss.build_snapshot(layout=[{"type": "clock", "size": "wide"}], layout_note="just the time",
                                 layout_updated_at=NOW, layout_updated_by="edward", show=show, now=NOW)
        assert snap["layout_source"] == "edward" and snap["layout_note"] == "just the time"
        assert snap["layout"][0]["w"] == 6 and snap["show"] is None

    def test_describe_show_for_the_pulse(self):
        assert ss.describe_show(None) == "its scene"
        note = ss.make_show_content(kind="alert", body="Biscuit", title="Dose", now=NOW)
        assert "alert" in ss.describe_show(note) and "Dose" in ss.describe_show(note)
