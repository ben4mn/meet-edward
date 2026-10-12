"""services/screen_service.py — the stage (2026-09-28).

Why (Ellis approved the canvas "Edward's Screen"): /screen stops being a 6×4
tile grid and becomes a stage Edward writes on — one scene of freely
placed pieces, where he stands, the lines he says, and his live thinking.
Placement is pure and deterministic so the page and the pulse agree on
what is where; a scene with one typo still lands; Ellis's asks hold the
screen against Edward's own changes until they lapse.
"""

from datetime import datetime, timedelta, timezone

import pytest

from services import screen_service as ss

NOW = datetime(2026, 9, 7, 15, 0, tzinfo=timezone.utc)


def _norm(pieces, me=None, why="", now=NOW):
    return ss.normalize_scene(pieces, me=me, why=why, now=now)


def _place(pieces, me=None, why=""):
    scene, w1 = _norm(pieces, me, why)
    placed, me_out, w2 = ss.place_scene(scene)
    return placed, me_out, w1 + w2


def _rect(p):
    r = p["rect"]
    return (r["x"], r["y"], r["w"], r["h"])


def _overlap(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


# ---- the pieces --------------------------------------------------------------------

@pytest.mark.unit
class TestPieces:
    def test_every_kind_has_all_five_sizes_inside_the_stage(self):
        for kind in ss.PIECE_KINDS:
            sizes = ss.PIECE_SIZES[kind]
            assert list(sizes) == ["xs", "s", "m", "l", "xl"]
            for w, h in sizes.values():
                assert 1 <= w <= ss.STAGE_COLS and 1 <= h <= ss.STAGE_ROWS
        assert "edward" not in ss.PIECE_KINDS  # Edward is `me`, never a piece
        assert (ss.STAGE_COLS, ss.STAGE_ROWS, ss.MAX_PIECES) == (24, 12, 8)

    def test_authored_pieces_keep_their_content(self):
        scene, warnings = _norm([
            {"kind": "words", "text": "A quiet Monday afternoon."},
            {"kind": "note", "title": "A thought", "body": "Biscuit ate  everything."},
            {"kind": "list", "title": "Later today", "items": ["a", {"label": "b", "detail": "c"}]},
            {"kind": "stat", "value": "42", "label": "memories", "detail": "this week"},
            {"kind": "countdown", "target": "2026-12-25T00:00:00+00:00", "caption": "Christmas"},
            {"kind": "image", "source": "https://x.y/cat.jpg", "title": "Biscuit"},
            {"kind": "html", "body": "<b>hi</b>"},
            {"kind": "calendar", "title": "Today"},
        ])
        assert warnings == []
        p = scene["pieces"]
        assert [x["kind"] for x in p] == ["words", "note", "list", "stat", "countdown", "image", "html", "calendar"]
        assert p[0]["text"] == "A quiet Monday afternoon." and p[0]["size"] == "l"  # words default l
        assert p[1]["body"] == "Biscuit ate everything." and p[1]["title"] == "A thought" and p[1]["size"] == "m"
        assert p[2]["items"] == [{"label": "a", "detail": None}, {"label": "b", "detail": "c"}]
        assert p[3]["value"] == "42" and p[3]["title"] == "memories" and p[3]["detail"] == "this week"
        assert p[4]["target"] == "2026-12-25T00:00:00+00:00" and p[4]["title"] == "Christmas"
        assert p[5]["source"] == "https://x.y/cat.jpg"
        assert p[7] == {**p[7], "kind": "calendar", "title": "Today"}
        for x in p:
            assert x["enter"] == "fade" and x["place"] == "center"

    def test_bad_pieces_are_dropped_with_a_warning_never_fatal(self):
        scene, warnings = _norm([
            {"kind": "words", "text": ""},
            {"kind": "weather"},
            {"kind": "edward"},
            {"kind": "image", "source": "ftp://nope"},
            "not a piece",
            {"kind": "clock"},
        ])
        assert [x["kind"] for x in scene["pieces"]] == ["clock"]
        assert len(warnings) == 5
        assert any("weather" in w for w in warnings)

    def test_words_are_capped_and_html_over_the_cap_is_dropped(self):
        scene, warnings = _norm([
            {"kind": "words", "text": "word " * 100},
            {"kind": "html", "body": "x" * (ss.MAX_HTML_CHARS + 1)},
            {"kind": "note", "body": "y" * 5000},
        ])
        kinds = [x["kind"] for x in scene["pieces"]]
        assert kinds == ["words", "note"] and warnings
        assert len(scene["pieces"][0]["text"]) <= 200
        assert len(scene["pieces"][1]["body"]) <= 1200

    def test_video_is_youtube_or_a_direct_file(self):
        scene, warnings = _norm([
            {"kind": "video", "source": "https://youtu.be/dQw4w9WgXcQ"},
            {"kind": "video", "source": "https://x.y/clip.mp4", "title": "Clip"},
            {"kind": "video", "source": "https://vimeo.com/12345"},
        ])
        v = scene["pieces"]
        assert len(v) == 2 and len(warnings) == 1
        assert v[0]["embed_url"].startswith("https://www.youtube.com/embed/dQw4w9WgXcQ")
        assert v[1]["embed_url"] is None and v[1]["source"] == "https://x.y/clip.mp4"

    def test_a_timer_is_stored_as_start_and_end_in_aware_utc(self):
        scene, warnings = _norm([
            {"kind": "timer", "seconds": 600, "title": "Tea"},
            {"kind": "timer", "until": "2026-09-07T16:00:00+00:00"},
            {"kind": "timer", "seconds": 999_999},
            {"kind": "timer"},
            {"kind": "timer", "until": "2026-09-07T14:00:00+00:00"},  # already over
        ])
        t = scene["pieces"]
        assert len(t) == 3 and len(warnings) == 2
        assert t[0]["started_at"] == NOW.isoformat() and t[0]["ends_at"] == (NOW + timedelta(seconds=600)).isoformat()
        assert t[0]["ends_at"].endswith("+00:00")
        assert t[1]["ends_at"] == "2026-09-07T16:00:00+00:00"
        assert t[2]["ends_at"] == (NOW + timedelta(seconds=43_200)).isoformat()

    def test_a_countdown_needs_an_iso_target_and_is_stored_in_aware_utc(self):
        scene, warnings = _norm([
            {"kind": "countdown", "target": "soon"},
            {"kind": "countdown", "target": "2026-12-25T06:00:00Z"},
        ])
        assert len(scene["pieces"]) == 1 and len(warnings) == 1
        assert scene["pieces"][0]["target"] == "2026-12-25T06:00:00+00:00"

    def test_normalizing_a_normalized_scene_changes_nothing(self):
        """The stored scene is normalized again on every read."""
        scene, _ = _norm([
            {"kind": "timer", "seconds": 600, "id": "tea"},
            {"kind": "video", "source": "https://youtu.be/dQw4w9WgXcQ"},
            {"kind": "list", "items": ["a"], "place": {"x": 2, "y": 3}},
            {"kind": "words", "text": "hi", "place": "top", "size": "s", "enter": "rise"},
        ], me={"on": "tea", "look": "owner"}, why="  a  reason ")
        again, warnings = ss.normalize_scene(scene["pieces"], me=scene["me"], why=scene["why"],
                                             now=NOW + timedelta(hours=2))
        assert again == scene and warnings == []
        assert scene["why"] == "a reason"

    def test_ids_are_kept_lower_cased_else_numbered(self):
        scene, _ = _norm([
            {"kind": "clock", "id": "Title"},
            {"kind": "clock"},
            {"kind": "clock", "id": "title"},        # duplicate
            {"kind": "clock", "id": "Big Title!"},   # not an id
            {"kind": "clock", "id": "p1"},           # explicit, claims p1 first
        ])
        assert [p["id"] for p in scene["pieces"]] == ["title", "p2", "p3", "p4", "p1"]

    def test_place_size_and_enter_fall_back(self):
        scene, warnings = _norm([
            {"kind": "note", "body": "a", "place": "upstairs", "size": "enormous", "enter": "explode"},
            {"kind": "note", "body": "b", "place": {"x": 2, "y": 3}},
            {"kind": "note", "body": "c", "place": {"x": 20, "y": 10, "w": 10, "h": 5}},
        ])
        a, b, c = scene["pieces"]
        assert a["place"] == "center" and a["size"] == "m" and a["enter"] == "fade"
        assert len(warnings) == 2 and any("upstairs" in w for w in warnings) and any("enormous" in w for w in warnings)
        assert b["place"] == {"x": 2, "y": 3}
        assert c["place"] == {"x": 14, "y": 7, "w": 10, "h": 5}  # clamped into the stage

    def test_at_most_eight_pieces(self):
        scene, warnings = _norm([{"kind": "clock", "size": "xs"}] * 11)
        assert len(scene["pieces"]) == ss.MAX_PIECES and any("8" in w for w in warnings)

    def test_me_is_read_with_its_shorthand(self):
        scene, _ = _norm([{"kind": "clock"}], me={"on": "p1"})
        assert scene["me"]["at"] == "on" and scene["me"]["on"] == "p1"
        scene, warnings = _norm([{"kind": "clock"}], me={"at": "ceiling", "x": 99, "side": "up"})
        assert scene["me"]["at"] == "floor" and scene["me"]["x"] == 23 and scene["me"]["side"] == "left"
        assert warnings
        scene, _ = _norm([{"kind": "clock"}])
        assert scene["me"]["at"] == "floor" and scene["me"]["look"] is None


# ---- placement ---------------------------------------------------------------------

@pytest.mark.unit
class TestPlacement:
    def test_anchors_sit_one_cell_in_from_the_edges(self):
        placed, me, warnings = _place([
            {"kind": "words", "text": "A quiet Monday afternoon.", "place": "top-left"},  # l 16x3
            {"kind": "stat", "value": "3", "place": "top-right", "size": "s"},          # 5x2
            {"kind": "note", "body": "x", "place": "center", "size": "xs"},             # 6x2
        ], me={"at": "edge", "side": "right"})
        assert warnings == []
        assert [_rect(p) for p in placed] == [(1, 1, 16, 3), (18, 1, 5, 2), (9, 5, 6, 2)]
        assert me == {"at": "edge", "rect": {"x": 22, "y": 6, "w": 2, "h": 4}, "on": None, "side": "right",
                      "look": None}

    def test_edward_is_reserved_first_on_the_floor(self):
        placed, me, _ = _place([{"kind": "note", "body": "x", "place": "bottom-left", "size": "s"}])
        assert me["at"] == "floor" and me["rect"] == {"x": 4, "y": 7, "w": 4, "h": 5}
        assert not _overlap(_rect(placed[0]), (4, 7, 4, 5))

    def test_corner_and_edge(self):
        _, me, _ = _place([{"kind": "clock"}], me={"at": "corner"})
        assert me["rect"] == {"x": 0, "y": 9, "w": 2, "h": 3} and me["side"] == "left"
        _, me, _ = _place([{"kind": "clock"}], me={"at": "corner", "side": "right"})
        assert me["rect"] == {"x": 22, "y": 9, "w": 2, "h": 3}
        _, me, _ = _place([{"kind": "clock"}], me={"at": "edge"})
        assert me["rect"] == {"x": 0, "y": 6, "w": 2, "h": 4}

    def test_a_blocked_piece_moves_to_the_nearest_free_spot_and_nothing_overlaps(self):
        placed, me, warnings = _place([
            {"kind": "note", "body": "a", "place": "center"},
            {"kind": "note", "body": "b", "place": "center"},
            {"kind": "note", "body": "c", "place": "center"},
        ], me={"at": "edge", "side": "right"})
        rects = [_rect(p) for p in placed] + [(22, 6, 2, 4)]
        assert len(placed) == 3
        for i, a in enumerate(rects):
            for b in rects[i + 1:]:
                assert not _overlap(a, b)
        again, _, _ = _place([
            {"kind": "note", "body": "a", "place": "center"},
            {"kind": "note", "body": "b", "place": "center"},
            {"kind": "note", "body": "c", "place": "center"},
        ], me={"at": "edge", "side": "right"})
        assert [_rect(p) for p in again] == [_rect(p) for p in placed]  # deterministic

    def test_a_piece_with_no_room_shrinks_then_is_left_off(self):
        placed, _, warnings = _place([
            {"kind": "html", "body": "<b>x</b>", "place": {"x": 1, "y": 1, "w": 16, "h": 9}},
            {"kind": "note", "body": "b", "place": "right", "size": "xl"},
            {"kind": "image", "source": "https://x.y/a.jpg", "size": "xl"},
            {"kind": "image", "source": "https://x.y/b.jpg", "size": "xl"},
        ], me={"at": "edge", "side": "right"})
        kinds = [(p["kind"], p["size"]) for p in placed]
        assert ("note", "xs") in kinds or ("note", "s") in kinds
        assert any("smaller" in w for w in warnings)
        assert any("did not fit and was left off" in w for w in warnings)
        assert len(placed) < 4

    def test_explicit_cells_are_used_as_given(self):
        placed, _, _ = _place([
            {"kind": "note", "body": "x", "place": {"x": 20, "y": 10, "w": 10, "h": 5}},
            {"kind": "note", "body": "y", "place": {"x": 2, "y": 2}, "size": "s"},
        ], me={"at": "corner"})
        assert [_rect(p) for p in placed] == [(14, 7, 10, 5), (2, 2, 7, 3)]

    def test_standing_on_a_piece(self):
        placed, me, warnings = _place([
            {"kind": "words", "text": "A quiet Monday afternoon.", "place": "top-left", "id": "title"},
            {"kind": "note", "id": "thought", "title": "A thought", "body": "x", "place": "bottom-left", "size": "s"},
        ], me={"on": "thought", "look": "thought"})
        assert warnings == []
        assert _rect(placed[1]) == (1, 8, 7, 3)
        assert me == {"at": "on", "rect": {"x": 5, "y": 4, "w": 3, "h": 4}, "on": "thought", "side": "left",
                      "look": "thought"}

    def test_no_room_above_falls_back_to_the_floor(self):
        _, me, warnings = _place([
            {"kind": "words", "text": "hello", "place": "top-left", "id": "title"},
        ], me={"on": "title"})
        assert me["at"] == "floor" and me["on"] is None and me["rect"]["y"] == 7
        assert any("no room above 'title'" in w for w in warnings)
        _, me, warnings = _place([{"kind": "clock"}], me={"on": "ghost"})
        assert me["at"] == "floor" and warnings

    def test_a_full_floor_puts_him_in_the_corner(self):
        _, me, warnings = _place([
            {"kind": "words", "text": "top", "id": "top", "place": {"x": 0, "y": 0}, "size": "xs"},
            {"kind": "html", "body": "<b>band</b>", "place": {"x": 2, "y": 7, "w": 22, "h": 5}},
        ], me={"on": "top"})
        assert me["at"] == "corner" and me["rect"] == {"x": 0, "y": 9, "w": 2, "h": 3}
        # one note, and a true one (it used to say "you stand on the floor" first)
        assert warnings == ["there was no room above 'top'; you sit in the corner"]

    def test_a_blocked_perch_never_puts_him_on_a_piece(self):
        # Review 2026-09-28: the note sits at the top (no room above it), the
        # words and the stat block every floor spot, and one cell each blocks
        # both corners and both edges — he was drawn over the words and told
        # "the stage is full" with 173 of 288 cells free.
        placed, me, warnings = _place([
            {"kind": "stat", "value": "3", "label": "x", "place": "bottom-right", "size": "l"},
            {"kind": "note", "id": "n1", "title": "A thought", "body": "b", "place": "top-right", "size": "l"},
            {"kind": "words", "text": "Hello there", "place": "bottom", "size": "m"},
        ], me={"on": "n1"})
        mine = (me["rect"]["x"], me["rect"]["y"], me["rect"]["w"], me["rect"]["h"])
        assert not any(_overlap(mine, _rect(p)) for p in placed)
        assert not any("stage is full" in w for w in warnings)
        assert len([w for w in warnings if w.startswith("there was no room above")]) == 1
        assert not any("stand on the floor" in w for w in warnings)  # he is not on the floor

    def test_he_overlaps_a_piece_only_when_no_box_is_free_anywhere(self):
        import random
        rng = random.Random(20260928)
        kinds = [("words", {"text": "Hello there"}), ("note", {"title": "T", "body": "b"}),
                 ("stat", {"value": "3", "label": "x"}), ("list", {"title": "L", "items": ["a"]}),
                 ("image", {"source": "https://x.org/a.png"}), ("clock", {})]
        smallest = [(ss.CORNER_W, ss.CORNER_H), (ss.EDGE_W, ss.EDGE_H)]
        for _ in range(1500):
            pieces = []
            for i in range(rng.randint(2, 5)):
                kind, extra = rng.choice(kinds)
                pieces.append({"kind": kind, "id": f"q{i}", "place": rng.choice(sorted(ss.PLACES)),
                               "size": rng.choice(["s", "m", "l"]), **extra})
            placed, me, warnings = _place(pieces, me={"on": f"q{rng.randint(0, len(pieces) - 1)}"})
            mine = (me["rect"]["x"], me["rect"]["y"], me["rect"]["w"], me["rect"]["h"])
            if any(_overlap(mine, _rect(p)) for p in placed):
                grid = ss._Grid()
                for p in placed:
                    grid.fill(_rect(p))
                assert all(grid.nearest(0, 0, w, h) is None for w, h in smallest), (pieces, me)
                assert any("stage is full" in w for w in warnings)

    def test_look_at_a_missing_piece_is_dropped(self):
        _, me, warnings = _place([{"kind": "clock", "id": "clock"}], me={"look": "ghost"})
        assert me["look"] is None and warnings
        _, me, _ = _place([{"kind": "clock", "id": "clock"}], me={"look": "owner"})
        assert me["look"] == "owner"
        _, me, _ = _place([{"kind": "clock", "id": "clock"}], me={"look": "clock"})
        assert me["look"] == "clock"

    def test_the_stock_scene_places_cleanly(self):
        scene = ss.default_scene()
        assert [p["kind"] for p in scene["pieces"]] == ["clock", "calendar", "pets"]
        assert [p["id"] for p in scene["pieces"]] == ["p1", "p2", "p3"]
        placed, me, warnings = ss.place_scene(scene)
        assert warnings == [] and len(placed) == 3
        # the page's FALLBACK_SCENE mirrors these cells
        assert [_rect(p) for p in placed] == [(1, 1, 5, 2), (16, 1, 7, 6), (17, 7, 6, 5)]
        assert me["at"] == "floor" and me["rect"]["x"] == 4 and scene["why"] == ""


# ---- the old tile layout -------------------------------------------------------------

@pytest.mark.unit
class TestLegacy:
    def test_a_stored_tile_layout_becomes_a_scene(self):
        scene = ss.tiles_to_scene([
            {"type": "clock", "size": "s"},             # 2x1 at 0,0
            {"type": "text", "body": "hi", "size": "m"},  # 3x1 at 2,0
            {"type": "edward", "size": "hero"},         # 4x2 at 0,1
        ])
        kinds = [p["kind"] for p in scene["pieces"]]
        assert kinds == ["clock", "note"]
        assert scene["pieces"][0]["place"] == {"x": 0, "y": 0, "w": 8, "h": 3}
        assert scene["pieces"][1]["place"] == {"x": 8, "y": 0, "w": 12, "h": 3}
        assert scene["pieces"][1]["body"] == "hi"
        assert scene["me"]["at"] == "floor" and scene["me"]["x"] == 0
        placed, _, _ = ss.place_scene(scene)
        assert [_rect(p) for p in placed] == [(0, 0, 8, 3), (8, 0, 12, 3)]


# ---- the snapshot --------------------------------------------------------------------

@pytest.mark.unit
class TestSnapshot:
    def test_nothing_stored_is_the_stock_scene(self):
        snap = ss.build_snapshot(layout=None, layout_note=None, layout_updated_at=None, layout_updated_by=None,
                                 show=None, now=NOW)
        assert snap["stage"] == {"cols": 24, "rows": 12}
        assert snap["scene"]["source"] == "default" and snap["scene"]["why"] == ""
        assert [p["kind"] for p in snap["scene"]["pieces"]] == ["clock", "calendar", "pets"]
        assert all("rect" in p for p in snap["scene"]["pieces"]) and "rect" in snap["scene"]["me"]
        assert snap["asked"] is None and snap["lines"] == [] and snap["owner_at_mini"] is False
        assert snap["now"] == NOW.isoformat()
        # the old keys stay one release so an open old tab keeps drawing
        assert snap["grid"] == {"cols": 6, "rows": 4} and snap["layout_source"] == "default" and snap["layout"]

    def test_a_stored_scene_is_placed_with_aware_timestamps(self):
        scene, _ = _norm([{"kind": "words", "text": "Hello", "id": "hi"}], me={"look": "hi"}, why="you're out")
        snap = ss.build_snapshot(scene=scene, scene_why="you're out", scene_updated_at=datetime(2026, 9, 7, 14, 0),
                                 scene_updated_by="edward", show=None, now=NOW, owner_at_mini=True)
        s = snap["scene"]
        assert s["source"] == "edward" and s["why"] == "you're out" and s["updated_by"] == "edward"
        assert s["updated_at"] == "2026-09-07T14:00:00+00:00"
        assert s["pieces"][0]["text"] == "Hello" and s["me"]["look"] == "hi"
        assert snap["owner_at_mini"] is True
        snap = ss.build_snapshot(scene=scene, scene_updated_by="owner", now=NOW)
        assert snap["scene"]["source"] == "owner"

    def test_a_legacy_layout_is_shown_as_a_scene(self):
        snap = ss.build_snapshot(layout=[{"type": "clock", "size": "wide"}], layout_note="just the time",
                                 layout_updated_at=NOW.replace(tzinfo=None), layout_updated_by="edward", show=None,
                                 now=NOW)
        assert snap["scene"]["source"] == "edward" and snap["scene"]["why"] == "just the time"
        assert snap["scene"]["pieces"][0]["rect"] == {"x": 0, "y": 0, "w": 24, "h": 3}
        assert snap["layout_source"] == "edward"

    def test_a_hold_shows_only_while_it_lasts(self):
        at, until = NOW - timedelta(minutes=10), NOW + timedelta(minutes=50)
        scene, _ = _norm([{"kind": "words", "text": "Ellis's"}])
        snap = ss.build_snapshot(scene=scene, scene_updated_by="owner", asked_at=at.replace(tzinfo=None),
                                 asked_until=until.replace(tzinfo=None), now=NOW)
        assert snap["asked"] == {"at": at.isoformat(), "until": until.isoformat()}
        assert snap["held_until"] == until.isoformat()
        snap = ss.build_snapshot(scene=scene, scene_updated_by="owner", asked_at=at.replace(tzinfo=None),
                                 asked_until=until.replace(tzinfo=None), now=until + timedelta(seconds=1))
        assert snap["asked"] is None and snap["held_until"] is None

    def test_the_page_is_told_of_a_hold_only_while_owners_ask_is_on_it(self):
        # Review 2026-09-28: after a tap took Ellis's show down, the page still
        # read "You asked at 3:13 pm · stays up until 5:13 pm" under
        # Edward's own scene for two hours. The hold itself still stands
        # (a tap does not end it), and the pulse is still told of it.
        at, until = NOW - timedelta(minutes=10), NOW + timedelta(minutes=50)
        hold = dict(asked_at=at.replace(tzinfo=None), asked_until=until.replace(tzinfo=None), now=NOW)
        edwards, _ = _norm([{"kind": "words", "text": "Edward's"}], why="you're out")
        snap = ss.build_snapshot(scene=edwards, scene_updated_by="edward", **hold)
        assert snap["scene"]["source"] == "edward" and snap["show"] is None
        assert snap["asked"] is None and snap["held_until"] == until.isoformat()
        assert ss.build_snapshot(**hold)["asked"] is None  # the stock scene
        show = ss.make_show_content(kind="alert", body="Dose", now=NOW - timedelta(minutes=10), shown_by="user")
        snap = ss.build_snapshot(scene=edwards, scene_updated_by="edward", show=show, **hold)
        assert snap["asked"] == {"at": at.isoformat(), "until": until.isoformat()}

    def test_lines_are_the_last_ten_minutes_oldest_first(self):
        lines = [
            {"id": "a" * 8, "text": "old", "at": (NOW - timedelta(minutes=11)).isoformat(), "aloud": False},
            {"id": "b" * 8, "text": "recent", "at": (NOW - timedelta(minutes=9)).isoformat(), "aloud": True},
            {"id": "c" * 8, "text": "now", "at": NOW.isoformat(), "aloud": False},
        ]
        snap = ss.build_snapshot(lines=lines, now=NOW)
        assert [x["text"] for x in snap["lines"]] == ["recent", "now"]
        assert snap["last_said"]["text"] == "now"


# ---- describing it to the pulse --------------------------------------------------------

@pytest.mark.unit
class TestDescribe:
    def test_the_stock_scene(self):
        snap = ss.build_snapshot(now=NOW)
        assert ss.describe_scene(snap) == "the stock scene"
        assert ss.describe_scene(None) == "the stock scene"

    def test_his_scene_names_pieces_where_he_stands_and_why(self):
        scene, _ = _norm([
            {"kind": "words", "text": "A quiet Monday afternoon.", "place": "top-left", "id": "title"},
            {"kind": "image", "source": "https://x.y/a.jpg", "place": "top-right", "size": "xs"},
            {"kind": "note", "id": "thought", "title": "A thought", "body": "x", "place": "bottom-left", "size": "s"},
        ], me={"on": "thought"}, why="you're out")
        snap = ss.build_snapshot(scene=scene, scene_why="you're out", scene_updated_by="edward", now=NOW)
        assert ss.describe_scene(snap) == (
            "your scene (words 'A quiet Monday…', image, note 'A thought'; you stand on 'thought'; why: you're out)")

    def test_the_reply_says_where_each_piece_landed_not_where_it_asked(self):
        # Review 2026-09-28: three pieces all asking for top-left were
        # reported "top-left" though the image went right and the list,
        # shrunk, went to the bottom — with no word that the list moved.
        placed, me, warnings = _place([
            {"kind": "note", "title": "A", "body": "b", "place": "top-left", "size": "l"},
            {"kind": "image", "source": "https://x.org/a.png", "place": "top-left", "size": "l"},
            {"kind": "list", "title": "L", "items": ["a"], "place": "top-left"},
        ])
        assert [ss.region(p["rect"]) for p in placed] == ["top-left", "right", "bottom"]
        assert ss.describe_placed(placed, me) == (
            "note 'A' top-left, image right, list 'L' bottom; you stand on the floor")
        assert "list 'L' was made smaller (s) to fit" in warnings
        assert "list 'L' was moved to make room" in warnings

    def test_a_piece_that_landed_where_it_asked_keeps_its_own_word(self):
        # a big piece anchored left has its centre in the middle third
        placed, me, _ = _place([{"kind": "words", "text": "Hi", "place": "left", "size": "l"}])
        assert ss.region(placed[0]["rect"]) == "center"
        assert ss.describe_placed(placed, me) == "words 'Hi' left; you stand on the floor"

    def test_the_description_does_not_change_between_polls(self):
        scene, _ = _norm([{"kind": "clock"}], why="calm")
        a = ss.build_snapshot(scene=scene, scene_why="calm", now=NOW)
        b = ss.build_snapshot(scene=scene, scene_why="calm", now=NOW + timedelta(seconds=4))
        assert ss.describe_scene(a) == ss.describe_scene(b)


# ---- whose turn ----------------------------------------------------------------------

@pytest.mark.unit
class TestOwnersTurn:
    def test_only_owners_own_sources_count(self):
        from types import SimpleNamespace
        from services import turn_ledger
        assert ss.is_owners_turn() is False  # no turn at all is not Ellis's
        for source, owners in (("chat", True), ("voice", True), ("widget", True), ("alexa", True),
                             ("mind", False), ("pulse", False), ("heartbeat", False), ("worker", False)):
            token = turn_ledger._current.set(SimpleNamespace(source=source))
            try:
                assert ss.is_owners_turn() is owners, source
            finally:
                turn_ledger._current.reset(token)


# ---- lines ---------------------------------------------------------------------------

@pytest.mark.unit
class TestLines:
    def test_a_line_is_collapsed_and_cut_at_a_word(self):
        assert ss.clean_line("  Biscuit's   evening meds\nare due at 6. ") == "Biscuit's evening meds are due at 6."
        long = ss.clean_line("word " * 100)
        assert len(long) <= 240 and long.endswith("…") and not long.endswith(" …")
        with pytest.raises(ValueError):
            ss.clean_line("   ")

    def test_quiet_hours_are_ten_to_seven_in_owners_zone(self, monkeypatch):
        from services import timezone_utils
        monkeypatch.setattr(timezone_utils, "_tz", None)
        monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
        # 23:48 CDT
        assert ss.is_quiet_hour(datetime(2026, 9, 8, 4, 48, tzinfo=timezone.utc)) is True
        # 06:59 CDT
        assert ss.is_quiet_hour(datetime(2026, 9, 8, 11, 59, tzinfo=timezone.utc)) is True
        # 17:52 CDT
        assert ss.is_quiet_hour(datetime(2026, 9, 7, 22, 52, tzinfo=timezone.utc)) is False

    def test_clock_is_owners_zone_lower_case(self, monkeypatch):
        from services import timezone_utils
        monkeypatch.setattr(timezone_utils, "_tz", None)
        monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
        assert ss.clock(datetime(2026, 9, 8, 1, 10, tzinfo=timezone.utc)) == "8:10 pm"
        assert ss.clock(datetime(2026, 9, 8, 1, 10)) == "8:10 pm"  # naive is UTC


# ---- the stored screen: holds and lines ------------------------------------------------

async def _fresh_row():
    from sqlalchemy import delete
    from services.database import ScreenStateModel, async_session
    async with async_session() as session:
        await session.execute(delete(ScreenStateModel))
        await session.commit()


@pytest.mark.integration
class TestStored:
    async def test_compose_then_read_back(self, clean_db):
        await _fresh_row()
        out = await ss.compose([{"kind": "words", "text": "Hello", "id": "hi"}, {"kind": "weather"}],
                               me={"on": "hi"}, why="a calm hour", by="edward", now=NOW)
        assert out["held_until"] is None and any("weather" in w for w in out["warnings"])
        snap = await ss.get_snapshot(now=NOW)
        assert snap["scene"]["source"] == "edward" and snap["scene"]["why"] == "a calm hour"
        assert [p["id"] for p in snap["scene"]["pieces"]] == ["hi"]
        assert snap["asked"] is None
        with pytest.raises(ValueError):
            await ss.compose([{"kind": "weather"}], by="edward", now=NOW)

    async def test_owners_ask_holds_the_screen_against_edward(self, clean_db):
        await _fresh_row()
        out = await ss.compose([{"kind": "words", "text": "Ellis's"}], by="owner", now=NOW)
        assert out["held_until"] == NOW + timedelta(minutes=120)
        snap = await ss.get_snapshot(now=NOW)
        assert snap["scene"]["source"] == "owner" and snap["asked"]["until"] == (NOW + timedelta(minutes=120)).isoformat()

        later = NOW + timedelta(minutes=30)
        for attempt in (
            ss.compose([{"kind": "clock"}], by="edward", now=later),
            ss.reset_scene(by="edward", now=later),
            ss.show(kind="alert", body="x", by="edward", now=later),
            ss.clear(by="edward", now=later),
        ):
            with pytest.raises(ss.ScreenHeld) as exc:
                await attempt
            assert exc.value.until == NOW + timedelta(minutes=120)
        snap = await ss.get_snapshot(now=later)
        assert snap["scene"]["pieces"][0]["text"] == "Ellis's" and snap["show"] is None

        said = await ss.say("Still here.", by="edward", now=later)  # a line is never held
        assert said["line"]["text"] == "Still here."

        # it lapses on its own
        after = NOW + timedelta(minutes=121)
        await ss.compose([{"kind": "clock"}], by="edward", now=after)
        assert (await ss.get_snapshot(now=after))["scene"]["source"] == "edward"

    async def test_hold_minutes_are_bounded_and_owner_can_end_the_hold(self, clean_db):
        await _fresh_row()
        out = await ss.compose([{"kind": "clock"}], hold_minutes=5000, by="owner", now=NOW)
        assert out["held_until"] == NOW + timedelta(minutes=720)
        out = await ss.compose([{"kind": "clock"}], hold_minutes=15, by="owner", now=NOW)
        assert out["held_until"] == NOW + timedelta(minutes=15)
        await ss.clear(by="owner", now=NOW + timedelta(minutes=1))
        assert (await ss.get_snapshot(now=NOW + timedelta(minutes=1)))["asked"] is None
        await ss.compose([{"kind": "clock"}], by="edward", now=NOW + timedelta(minutes=2))

    async def test_a_show_owner_asked_for_holds_until_it_expires(self, clean_db):
        await _fresh_row()
        shown = await ss.show(kind="alert", body="Dose", seconds=600, by="owner", now=NOW)
        assert shown["kind"] == "alert"
        snap = await ss.get_snapshot(now=NOW)
        assert snap["asked"]["until"] == (NOW + timedelta(seconds=600)).isoformat()
        await _fresh_row()
        await ss.show(kind="alert", body="Dose", by="owner", now=NOW)
        assert (await ss.get_snapshot(now=NOW))["asked"]["until"] == (NOW + timedelta(minutes=120)).isoformat()

    async def test_reset_by_owner_ends_the_hold_and_returns_the_stock_scene(self, clean_db):
        await _fresh_row()
        await ss.compose([{"kind": "words", "text": "x"}], by="owner", now=NOW)
        assert await ss.reset_scene(by="owner", now=NOW) is True
        snap = await ss.get_snapshot(now=NOW)
        assert snap["scene"]["source"] == "default" and snap["asked"] is None
        assert await ss.reset_scene(by="edward", now=NOW) is False

    async def test_a_tap_neither_obeys_nor_ends_a_hold(self, clean_db):
        await _fresh_row()
        await ss.show(kind="alert", body="Dose", by="owner", now=NOW)
        assert await ss.clear(by="tap", now=NOW) is True
        snap = await ss.get_snapshot(now=NOW)
        # nothing Ellis asked for is on the page, so it claims no ask…
        assert snap["show"] is None and snap["asked"] is None
        # …but the hold stands, and the pulse is told of it
        assert snap["held_until"] == (NOW + timedelta(minutes=120)).isoformat()
        with pytest.raises(ss.ScreenHeld):
            await ss.compose([{"kind": "clock"}], by="edward", now=NOW + timedelta(minutes=5))

    async def test_a_tap_on_owners_show_leaves_his_scene_asked_for(self, clean_db):
        await _fresh_row()
        await ss.compose([{"kind": "words", "text": "Ellis's"}], hold_minutes=60, by="owner", now=NOW)
        await ss.show(kind="alert", body="Dose", seconds=60, by="owner", now=NOW)
        await ss.clear(by="tap", now=NOW + timedelta(seconds=10))
        snap = await ss.get_snapshot(now=NOW + timedelta(minutes=5))
        assert snap["scene"]["source"] == "owner"
        assert snap["asked"]["until"] == (NOW + timedelta(minutes=60)).isoformat()

    async def test_a_short_show_does_not_cut_owners_longer_scene_hold(self, clean_db):
        # Review 2026-09-28: a scene Ellis held all evening, then a 30-second
        # alert he asked for — the alert's end became the hold's, and the
        # next pulse replaced his scene five minutes later.
        await _fresh_row()
        out = await ss.compose([{"kind": "words", "text": "Ellis's evening"}], hold_minutes=720, by="owner", now=NOW)
        held = NOW + timedelta(minutes=720)
        assert out["held_until"] == held
        await ss.show(kind="alert", body="Dose", seconds=30, by="owner", now=NOW + timedelta(minutes=1))
        later = NOW + timedelta(minutes=5)
        snap = await ss.get_snapshot(now=later)
        assert snap["asked"]["until"] == held.isoformat()
        with pytest.raises(ss.ScreenHeld) as exc:
            await ss.compose([{"kind": "clock"}], by="edward", now=later)
        assert exc.value.until == held

    async def test_a_short_scene_does_not_cut_the_hold_of_owners_show_over_it(self, clean_db):
        await _fresh_row()
        await ss.show(kind="text", body="Keep this up", by="owner", now=NOW)  # no end: two hours
        out = await ss.compose([{"kind": "clock"}], hold_minutes=15, by="owner", now=NOW + timedelta(minutes=1))
        assert out["held_until"] == NOW + timedelta(minutes=16)  # the scene's own hold, as asked
        later = NOW + timedelta(minutes=30)
        with pytest.raises(ss.ScreenHeld) as exc:
            await ss.clear(by="edward", now=later)
        assert exc.value.until == NOW + timedelta(minutes=120)
        assert (await ss.get_snapshot(now=later))["show"]["body"] == "Keep this up"

    async def test_a_new_scene_from_owner_replaces_his_old_scene_hold(self, clean_db):
        await _fresh_row()
        await ss.compose([{"kind": "clock"}], hold_minutes=720, by="owner", now=NOW)
        await ss.compose([{"kind": "clock"}], hold_minutes=15, by="owner", now=NOW)
        snap = await ss.get_snapshot(now=NOW)
        assert snap["asked"]["until"] == (NOW + timedelta(minutes=15)).isoformat()

    async def test_lines_keep_the_last_six_and_are_quiet_at_night(self, clean_db, monkeypatch):
        from services import timezone_utils
        monkeypatch.setattr(timezone_utils, "_tz", None)
        monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")
        await _fresh_row()
        day = datetime(2026, 9, 7, 22, 52, tzinfo=timezone.utc)  # 5:52 pm CDT
        for i in range(8):
            out = await ss.say(f"line {i}", aloud=True, now=day + timedelta(seconds=i))
        assert out["quiet"] is False and out["line"]["aloud"] is True
        assert len(out["line"]["id"]) == 8 and out["line"]["at"].endswith("+00:00")
        snap = await ss.get_snapshot(now=day + timedelta(seconds=10))
        assert [x["text"] for x in snap["lines"]] == [f"line {i}" for i in range(2, 8)]
        night = datetime(2026, 9, 8, 4, 48, tzinfo=timezone.utc)  # 11:48 pm CDT
        out = await ss.say("shh", aloud=True, now=night)
        assert out["quiet"] is True and out["line"]["aloud"] is False
        with pytest.raises(ValueError):
            await ss.say("  ", now=night)


# ---- the routes ----------------------------------------------------------------------

@pytest.mark.unit
class TestRoutes:
    async def test_the_scene_routes_act_as_owner(self, monkeypatch):
        from fastapi import HTTPException
        from routers import screen as r
        calls = []

        async def compose(pieces, *, me=None, why="", hold_minutes=0, by="edward", now=None):
            calls.append(("compose", by, hold_minutes, why))
            if not pieces:
                raise ValueError("no usable pieces: the list was empty")
            return {"pieces": [], "me": {}, "why": why, "warnings": [], "held_until": NOW}

        async def reset_scene(*, by="edward", now=None):
            calls.append(("reset", by))
            return True

        async def show(**kw):
            calls.append(("show", kw.get("by"), kw.get("shown_by")))
            return {"kind": kw["kind"]}

        monkeypatch.setattr(ss, "compose", compose)
        monkeypatch.setattr(ss, "reset_scene", reset_scene)
        monkeypatch.setattr(ss, "show", show)
        out = await r.put_scene(r.SceneRequest(pieces=[{"kind": "clock"}], why="for Ellis", hold_minutes=30))
        assert out["held_until"] == NOW.isoformat()
        with pytest.raises(HTTPException) as exc:
            await r.put_scene(r.SceneRequest(pieces=[]))
        assert exc.value.status_code == 422
        assert await r.delete_scene() == {"reset": True}
        await r.post_show(r.ShowRequest(kind="alert", body="x"))
        assert calls == [("compose", "owner", 30, "for Ellis"), ("compose", "owner", 0, ""), ("reset", "owner"),
                         ("show", "owner", "user")]
