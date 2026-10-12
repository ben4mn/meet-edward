"""The screen tools are always bound (Session H); the stage's tools
(2026-09-28): compose_screen, say_on_screen, and Ellis's hold on the screen."""

from datetime import datetime, timezone

import pytest

pytestmark = pytest.mark.unit


def test_screen_tools_exist_and_are_always_available():
    from services.graph.tools import SCREEN_TOOLS
    names = {t.name for t in SCREEN_TOOLS}
    assert names == {"show_on_screen", "clear_screen", "compose_screen", "say_on_screen", "reset_screen_layout",
                     "look_at_screen", "bring_up_screen"}
    src = open("services/tool_registry.py").read()
    assert "SCREEN_TOOLS" in src, "screen tools must be in the always-available set"


async def test_show_on_screen_tool_validates_and_calls_service(monkeypatch):
    from services.graph import tools
    from services import hub_canvas_service
    calls = []

    async def fake_show(**kw):
        calls.append(kw)
        return {"kind": kw["kind"]}

    monkeypatch.setattr(hub_canvas_service, "show", fake_show)
    out = await tools.show_on_screen.ainvoke({"kind": "alert", "body": "Hi Ellis", "title": "Edward", "seconds": 60})
    assert calls and calls[0]["kind"] == "alert" and "alert" in out
    out = await tools.show_on_screen.ainvoke({"kind": "nope", "body": "x"})
    assert "kind" in out.lower()


async def test_show_on_screen_routes_the_mini_screen_to_screen_service(monkeypatch):
    from services.graph import tools
    from services import screen_service, hub_canvas_service
    mini, house = [], []

    async def fake_mini(**kw):
        mini.append(kw)
        return {"kind": kw["kind"], "title": kw.get("title"), "expires_at": None}

    async def fake_house(**kw):
        house.append(kw)
        return {"kind": kw["kind"]}

    monkeypatch.setattr(screen_service, "show", fake_mini)
    monkeypatch.setattr(hub_canvas_service, "show", fake_house)
    out = await tools.show_on_screen.ainvoke({"kind": "alert", "body": "Biscuit's dose", "screen": "mini"})
    assert mini and mini[0]["kind"] == "alert" and "on this mac" in out.lower()
    assert not house
    out = await tools.show_on_screen.ainvoke({"kind": "web", "source": "https://x.y", "screen": "house"})
    assert "this mac's monitor" in out.lower() and not house  # web goes to the owner's Chrome; the house screen cannot navigate
    await tools.show_on_screen.ainvoke({"kind": "alert", "body": "hi", "screen": "house"})
    assert house and house[0]["kind"] == "alert"


async def test_clear_screen_takes_a_screen_argument(monkeypatch):
    from services.graph import tools
    from services import screen_service, hub_canvas_service
    calls = []

    async def fake_clear(**kw):
        calls.append(("mini", kw))
        return True

    async def fake_house_clear(**kw):
        calls.append(("house", kw))
        return False

    monkeypatch.setattr(screen_service, "clear", fake_clear)
    monkeypatch.setattr(hub_canvas_service, "clear", fake_house_clear)
    assert "cleared" in (await tools.clear_screen.ainvoke({"screen": "mini"})).lower()
    assert "nothing" in (await tools.clear_screen.ainvoke({})).lower()
    assert [c[0] for c in calls] == ["mini", "house"]


# ---- the stage (2026-09-28) ------------------------------------------------------------

UNTIL = datetime(2026, 9, 8, 1, 10, tzinfo=timezone.utc)  # 8:10 pm in Springfield


@pytest.fixture
def chicago(monkeypatch):
    from services import timezone_utils
    monkeypatch.setattr(timezone_utils, "_tz", None)
    monkeypatch.setattr(timezone_utils, "_tz_name", "America/Chicago")


@pytest.fixture
def stage(monkeypatch, chicago):
    """A screen service with no database: compose places for real, the rest is recorded."""
    from services import screen_service as ss
    calls = []
    state = {"owners": False, "held": None}

    def guard(by):
        if state["held"] and by == "edward":
            raise ss.ScreenHeld(state["held"])

    async def compose(pieces, *, me=None, why="", hold_minutes=0, by="edward", now=None):
        calls.append(("compose", by, hold_minutes))
        guard(by)
        scene, warnings = ss.normalize_scene(pieces, me=me, why=why)
        placed, me_out, w2 = ss.place_scene(scene)
        if not placed:
            raise ValueError("no usable pieces: " + "; ".join(warnings + w2))
        return {"pieces": placed, "me": me_out, "why": scene["why"], "warnings": warnings + w2,
                "held_until": UNTIL if by == "owner" else None}

    async def reset_scene(*, by="edward", now=None):
        calls.append(("reset", by))
        guard(by)
        return True

    async def show(**kw):
        calls.append(("show", kw.get("by")))
        guard(kw.get("by"))
        return {"kind": kw["kind"], "title": kw.get("title"), "expires_at": None}

    async def clear(*, by="edward", now=None):
        calls.append(("clear", by))
        guard(by)
        return True

    monkeypatch.setattr(ss, "compose", compose)
    monkeypatch.setattr(ss, "reset_scene", reset_scene)
    monkeypatch.setattr(ss, "show", show)
    monkeypatch.setattr(ss, "clear", clear)
    monkeypatch.setattr(ss, "is_owners_turn", lambda: state["owners"])
    return calls, state


SCENE = {
    "pieces": [
        {"kind": "words", "text": "A quiet Monday afternoon.", "place": "top-left", "id": "title"},
        {"kind": "image", "source": "https://x.y/a.jpg", "place": "top-right", "size": "xs"},
        {"kind": "note", "id": "thought", "title": "A thought", "body": "x", "place": "bottom-left", "size": "s"},
    ],
    "me": {"on": "thought"},
    "why": "you're out",
}


async def test_compose_screen_says_what_is_where(stage):
    from services.graph import tools
    calls, _ = stage
    out = await tools.compose_screen.ainvoke(SCENE)
    assert out == ("Your screen now: words 'A quiet Monday…' top-left, image top-right, note 'A thought' "
                   "bottom-left; you stand on 'thought'.")
    assert calls == [("compose", "edward", 0)]


async def test_compose_screen_reports_notes_and_refuses_an_empty_scene(stage):
    from services.graph import tools
    out = await tools.compose_screen.ainvoke({"pieces": [{"kind": "clock"}, {"kind": "weather"}]})
    assert out.startswith("Your screen now: clock center; you stand on the floor.") and " Notes: " in out
    assert "weather" in out
    out = await tools.compose_screen.ainvoke({"pieces": [{"kind": "weather"}]})
    assert out.startswith("Not changed:") and "weather" in out


async def test_in_owners_turn_the_scene_holds(stage):
    from services.graph import tools
    calls, state = stage
    state["owners"] = True
    out = await tools.compose_screen.ainvoke({**SCENE, "hold_minutes": 90})
    assert out.endswith("It holds until 8:10 pm because Ellis asked.")
    assert calls == [("compose", "owner", 90)]


async def test_a_hold_refuses_edwards_own_changes_but_not_a_line(stage):
    from services.graph import tools
    from services import screen_service as ss
    _, state = stage
    state["held"] = UNTIL
    for tool, args in ((tools.compose_screen, SCENE), (tools.reset_screen_layout, {}),
                       (tools.show_on_screen, {"kind": "alert", "body": "x", "screen": "mini"}),
                       (tools.clear_screen, {"screen": "mini"})):
        out = await tool.ainvoke(args)
        assert out.startswith("Not changed:"), (tool.name, out)
        assert "8:10 pm" in out and "say_on_screen" in out
    said = []

    async def say(text, *, aloud=False, by="edward", now=None):
        said.append(text)
        return {"line": {"id": "a1b2c3d4", "text": text, "at": "2026-09-07T22:52:00+00:00", "aloud": aloud},
                "quiet": False}

    ss_say = ss.say
    try:
        ss.say = say
        out = await tools.say_on_screen.ainvoke({"line": "Still here."})
    finally:
        ss.say = ss_say
    assert out == "Said on your screen at 5:52 pm." and said == ["Still here."]


async def test_owners_own_turn_passes_owner_to_every_screen_change(stage):
    from services.graph import tools
    calls, state = stage
    state["owners"] = True
    await tools.reset_screen_layout.ainvoke({})
    await tools.show_on_screen.ainvoke({"kind": "alert", "body": "x", "screen": "mini"})
    await tools.clear_screen.ainvoke({"screen": "mini"})
    assert [c[1] for c in calls] == ["owner", "owner", "owner"]


async def test_reset_screen_layout_replies(stage, monkeypatch):
    from services.graph import tools
    from services import screen_service as ss
    assert await tools.reset_screen_layout.ainvoke({}) == "Screen reset to the stock scene."

    async def nothing(*, by="edward", now=None):
        return False

    monkeypatch.setattr(ss, "reset_scene", nothing)
    assert await tools.reset_screen_layout.ainvoke({}) == "The screen was already on the stock scene."


async def test_say_on_screen_replies(monkeypatch, chicago):
    from services.graph import tools
    from services import screen_service as ss
    seen = []

    async def say(text, *, aloud=False, by="edward", now=None):
        seen.append((text, aloud))
        if not str(text).strip():
            raise ValueError("the line was empty")
        quiet = text == "late"
        at = "2026-09-08T04:48:00+00:00" if quiet else "2026-09-07T22:52:00+00:00"
        return {"line": {"id": "a1b2c3d4", "text": text, "at": at, "aloud": aloud and not quiet},
                "quiet": quiet and aloud}

    monkeypatch.setattr(ss, "say", say)
    assert await tools.say_on_screen.ainvoke({"line": "Biscuit's evening meds are due at 6."}) == \
        "Said on your screen at 5:52 pm."
    assert await tools.say_on_screen.ainvoke({"line": "Dinner.", "aloud": True}) == \
        "Said aloud on your screen at 5:52 pm."
    assert await tools.say_on_screen.ainvoke({"line": "late", "aloud": True}) == \
        "Said on your screen at 11:48 pm (quietly — it's quiet hours)."
    assert await tools.say_on_screen.ainvoke({"line": "   "}) == "Not said: the line was empty."
    assert seen[1] == ("Dinner.", True)
