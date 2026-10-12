"""Edward's screen on the host Mac — the stage.

Why (2026-09-07): the desk monitor is Edward's whenever the owner is not
at it. He treats it like the iPhone widget — full creative control, on his
own judgment, until asked ("put a concert on your screen", "show me that
stream").

Why the stage (2026-09-28, the canvas "Edward's Screen"): the 6×4 tile
grid read as a dashboard, not as Edward. /screen is now a stage he writes
on:

- **the scene** — up to eight pieces placed freely on a 24×12 cell stage
  (words, a note, a list, a stat, a countdown, a timer, a picture, a
  video, an HTML scene, and live panels: clock, calendar, the pets…),
  plus where *he* stands (``me``: on the floor, on a piece, in a corner,
  peeking in from an edge) and one line on why. ``None`` is the stock
  scene; a stored 6×4 tile layout from before is converted on read.
- **lines** — short things he says there (``say``), the last few kept;
  the page may speak one aloud, never in quiet hours.
- **the owner's hold** — a scene or show put up in the owner's own turn
  (or by the REST routes) holds the screen: until it lapses, Edward's own turns
  cannot change or clear it. A line is never held.
- **show** — whatever is on top: the same image / video / alert / text /
  html kinds the household screen takes, drawn over the scene.

His live thinking on the page comes from ``screen_thoughts`` (in memory),
not from here. Web content is not drawn in this page (2026-09-07): Edward
opens a tab in the owner's own Chrome and goes full screen there
(``browser_hands``, with ``EDWARD_CHROME_HANDS=on``), then comes back to
this tab.

Pure: ``normalize_scene`` / ``place_scene`` / ``default_scene`` /
``tiles_to_scene`` / ``describe_scene`` / ``make_show_content`` /
``build_snapshot``. I/O at the bottom.
"""

from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from services import hub_canvas_service as canvas

SCREEN_ID = "mini"

# ---- the stage ----------------------------------------------------------------------

STAGE_COLS = 24
STAGE_ROWS = 12
MAX_PIECES = 8
MAX_WORDS_CHARS = 200
MAX_NOTE_CHARS = 1_200
MAX_HTML_CHARS = 30_000
MAX_TITLE_CHARS = 80
MAX_WHY_CHARS = 300
MAX_TIMER_SECONDS = 12 * 3600

AUTHORED_KINDS = ("words", "note", "list", "stat", "countdown", "timer", "image", "video", "html")
LIVE_KINDS = ("clock", "calendar", "pets", "alerts", "heartbeat", "recents", "workers", "vitals", "memory_ticker")
PIECE_KINDS = AUTHORED_KINDS + LIVE_KINDS

SIZE_STEPS = ("xs", "s", "m", "l", "xl")
PLACES = ("top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right")
ENTERS = ("fade", "rise", "type")


def _sizes(*wh: Tuple[int, int]) -> Dict[str, Tuple[int, int]]:
    return dict(zip(SIZE_STEPS, wh))


_SMALL_NUMBER = _sizes((4, 2), (5, 2), (6, 3), (8, 4), (10, 5))
_MEDIA = _sizes((6, 4), (8, 5), (10, 6), (12, 7), (16, 9))
_PANEL = _sizes((5, 4), (6, 5), (7, 6), (8, 7), (10, 9))
_STRIP = _sizes((8, 1), (10, 1), (12, 2), (16, 2), (24, 2))

# kind → size → (w, h) in cells
PIECE_SIZES: Dict[str, Dict[str, Tuple[int, int]]] = {
    "words": _sizes((8, 1), (10, 2), (14, 2), (16, 3), (20, 4)),
    "note": _sizes((6, 2), (7, 3), (9, 4), (11, 5), (14, 6)),
    "list": _sizes((5, 3), (6, 4), (7, 5), (9, 6), (11, 8)),
    "stat": _SMALL_NUMBER, "countdown": _SMALL_NUMBER, "clock": _SMALL_NUMBER,
    "timer": _sizes((6, 3), (8, 4), (10, 5), (12, 6), (14, 7)),
    "image": _MEDIA, "video": _MEDIA, "html": _MEDIA,
    "calendar": _PANEL, "heartbeat": _PANEL, "recents": _PANEL, "workers": _PANEL, "pets": _PANEL,
    "alerts": _STRIP, "memory_ticker": _STRIP,
    "vitals": _sizes((5, 2), (6, 3), (7, 3), (8, 4), (10, 5)),
}
DEFAULT_PIECE_SIZE = "m"
DEFAULT_SIZE_BY_KIND = {"words": "l"}

# Where Edward stands (``me.at``) and his box on the stage.
ME_AT = ("floor", "on", "corner", "edge")
FLOOR_W, FLOOR_H, FLOOR_Y, FLOOR_X = 4, 5, 7, 4
ON_W, ON_H = 3, 4
CORNER_W, CORNER_H = 2, 3
EDGE_W, EDGE_H, EDGE_Y = 2, 4, 6

_ID_RE = re.compile(r"^[a-z0-9_-]{1,24}$")
_VIDEO_FILES = (".mp4", ".webm", ".m4v", ".mov")

# The owner's own turns: the sources in which the owner is speaking
# (memory_service.OWNER_SPEAKS_SOURCES; ``ping_owner`` is the owner in
# their self-chat, 2026-10-11).
OWNER_SOURCES = ("chat", "voice", "widget", "alexa", "ping_owner", "owner_ping")
DEFAULT_HOLD_MINUTES = 120
MAX_HOLD_MINUTES = 720

MAX_LINE_CHARS = 240
MAX_LINES_KEPT = 6
LINES_SHOWN_MINUTES = 10

Rect = Tuple[int, int, int, int]

# ---- the old 6×4 tile layout (read only, to convert a stored one) -------------------

GRID_COLS = 6
GRID_ROWS = 4
MAX_TILES = 12
MAX_TILE_TEXT_CHARS = 1_200
MAX_TILE_HTML_CHARS = 30_000
MAX_LIST_ITEMS = 12

LIVE_TILES = ("clock", "edward", "alerts", "calendar", "heartbeat", "recents", "workers",
              "vitals", "memory_ticker")
AUTHORED_TILES = ("text", "image", "html", "list", "stat", "countdown")
TILE_TYPES = LIVE_TILES + AUTHORED_TILES

# name → (columns, rows) on the 6×4 grid
SIZES: Dict[str, Tuple[int, int]] = {
    "s": (2, 1), "m": (3, 1), "l": (4, 1), "wide": (6, 1),
    "tall": (2, 2), "big": (3, 2), "hero": (4, 2), "full": (6, 3),
}
DEFAULT_SIZE: Dict[str, str] = {
    "clock": "s", "edward": "hero", "alerts": "l", "calendar": "tall", "heartbeat": "tall",
    "recents": "s", "workers": "s", "vitals": "s", "memory_ticker": "wide",
    "text": "m", "image": "big", "html": "big", "list": "tall", "stat": "s", "countdown": "s",
}

SHOW_KINDS = canvas.KINDS


# ---- small helpers ------------------------------------------------------------------

def _str(v: Any, cap: int) -> Optional[str]:
    s = " ".join(str(v).split()) if isinstance(v, (str, int, float)) else ""
    return s[:cap] or None


def cut_at_word(text: str, limit: int) -> str:
    """At most ``limit`` chars, cut at a word with "…" when it had to be cut."""
    if len(text) <= limit:
        return text
    cut = text[:limit - 1]
    if text[limit - 1] != " " and " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:-—") + "…"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _utc(dt: datetime) -> datetime:
    return _aware(dt).astimezone(timezone.utc)


def _naive(dt: datetime) -> datetime:
    return _utc(dt).replace(tzinfo=None)


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return _utc(dt).isoformat() if dt else None


def _parse_when(raw: Any) -> datetime:
    """An ISO datetime as aware UTC. A naive one is in the owner's zone — every tool
    time is (``timezone_utils.local_naive_to_utc``)."""
    text = str(raw or "").strip()
    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        from services.timezone_utils import local_naive_to_utc
        return local_naive_to_utc(dt)
    return dt.astimezone(timezone.utc)


def _items(raw: Any) -> List[Dict[str, Optional[str]]]:
    out: List[Dict[str, Optional[str]]] = []
    if not isinstance(raw, list):
        return out
    for it in raw[:MAX_LIST_ITEMS]:
        if isinstance(it, dict):
            label = _str(it.get("label") or it.get("text"), 160)
            detail = _str(it.get("detail"), 160)
        else:
            label, detail = _str(it, 160), None
        if label:
            out.append({"label": label, "detail": detail})
    return out


def _body(raw: Any) -> str:
    """Runs of spaces collapsed, line breaks kept (a note may have paragraphs)."""
    lines = [" ".join(line.split()) for line in str(raw or "").strip().splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def clock(dt: datetime) -> str:
    """"8:10 pm" in the owner's zone (a naive time is UTC)."""
    try:
        from services.timezone_utils import utc_to_local
        return utc_to_local(dt).strftime("%-I:%M %p").lower()
    except Exception:  # noqa: BLE001
        return _utc(dt).strftime("%H:%M UTC")


# ---- pieces -------------------------------------------------------------------------

def _piece_content(kind: str, raw: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """The content fields of one authored piece, or ValueError."""
    if kind == "words":
        text = " ".join(str(raw.get("text") or raw.get("body") or raw.get("words") or "").split())
        if not text:
            raise ValueError("words need text")
        return {"text": cut_at_word(text, MAX_WORDS_CHARS)}
    if kind == "note":
        body = _body(raw.get("body") or raw.get("text") or raw.get("content"))
        if not body:
            raise ValueError("a note needs a body")
        return {"body": cut_at_word(body, MAX_NOTE_CHARS)}
    if kind == "list":
        items = _items(raw.get("items"))
        if not items:
            raise ValueError("a list needs items")
        return {"items": items}
    if kind == "stat":
        value = _str(raw.get("value"), 40)
        if not value:
            raise ValueError("a stat needs a value")
        return {"value": value, "detail": _str(raw.get("detail"), 120)}
    if kind == "countdown":
        try:
            target = _parse_when(raw.get("target") or raw.get("target_date"))
        except (ValueError, TypeError):
            raise ValueError("a countdown needs an ISO target datetime")
        return {"target": target.isoformat()}
    if kind == "timer":
        return _timer(raw, now)
    if kind == "image":
        return {"source": canvas._check_url(raw.get("source") or raw.get("url"))}
    if kind == "video":
        source = canvas._check_url(raw.get("source") or raw.get("url"))
        embed = canvas._youtube_embed(source) if source.startswith("https://") else None
        if embed is None and not (source.split("?")[0].lower().endswith(_VIDEO_FILES) or source.startswith("/api/")):
            raise ValueError("a video must be a YouTube link or a direct https .mp4 file")
        return {"source": source, "embed_url": embed}
    if kind == "html":
        body = str(raw.get("body") or raw.get("html") or "").strip()
        if not body:
            raise ValueError("an html piece needs a body")
        if len(body) > MAX_HTML_CHARS:
            raise ValueError(f"the html body is too long ({len(body)} > {MAX_HTML_CHARS} chars)")
        return {"body": body}
    return {}


def _timer(raw: Dict[str, Any], now: datetime) -> Dict[str, Any]:
    """``seconds`` (1–12 h) or ``until`` → ``started_at`` / ``ends_at``. A stored
    timer (``ends_at`` already there) is kept as it is, even once it is over."""
    if raw.get("ends_at") and raw.get("seconds") is None and raw.get("until") is None:
        try:
            ends = _parse_when(raw["ends_at"])
            started = _parse_when(raw.get("started_at") or raw["ends_at"])
        except (ValueError, TypeError):
            raise ValueError("a timer needs seconds or until")
        return {"started_at": started.isoformat(), "ends_at": ends.isoformat()}
    now = _utc(now)
    cap = now + timedelta(seconds=MAX_TIMER_SECONDS)
    if raw.get("until") is not None:
        try:
            ends = _parse_when(raw["until"])
        except (ValueError, TypeError):
            raise ValueError("a timer's until must be an ISO datetime")
        if ends <= now:
            raise ValueError("that timer is already over")
        ends = min(ends, cap)
    else:
        try:
            seconds = int(raw.get("seconds") or 0)
        except (TypeError, ValueError):
            seconds = 0
        if seconds < 1:
            raise ValueError("a timer needs seconds (1–43200) or until")
        ends = min(now + timedelta(seconds=seconds), cap)
    return {"started_at": now.isoformat(), "ends_at": ends.isoformat()}


def _place(raw: Any, kind: str, warnings: List[str]) -> Any:
    if isinstance(raw, dict):
        try:
            x, y = int(raw["x"]), int(raw["y"])
            if "w" in raw or "h" in raw:
                w = max(1, min(int(raw.get("w") or 1), STAGE_COLS))
                h = max(1, min(int(raw.get("h") or 1), STAGE_ROWS))
                return {"x": max(0, min(x, STAGE_COLS - w)), "y": max(0, min(y, STAGE_ROWS - h)), "w": w, "h": h}
            return {"x": max(0, min(x, STAGE_COLS - 1)), "y": max(0, min(y, STAGE_ROWS - 1))}
        except (KeyError, TypeError, ValueError):
            warnings.append(f"{kind}: place needs whole-number x and y; put in the center")
            return "center"
    place = str(raw or "").strip().lower().replace("_", "-").replace(" ", "-")
    if place in ("centre", "middle"):
        place = "center"
    if place in PLACES:
        return place
    if place:
        warnings.append(f"{kind}: place {raw!r} is not one of {', '.join(PLACES)}; put in the center")
    return "center"


def normalize_piece(raw: Any, *, now: datetime, warnings: List[str]) -> Dict[str, Any]:
    """One clean piece (without its final ``id``) or ValueError. Place, size
    and enter fall back rather than fail."""
    if not isinstance(raw, dict):
        raise ValueError("a piece must be an object with a kind")
    kind = str(raw.get("kind") or raw.get("type") or "").strip().lower()
    if kind == "edward":
        raise ValueError("you are not a piece — say where you stand with me")
    if kind not in PIECE_KINDS:
        raise ValueError(f"{kind or '(missing)'} is not a piece kind; use one of {', '.join(PIECE_KINDS)}")
    default = DEFAULT_SIZE_BY_KIND.get(kind, DEFAULT_PIECE_SIZE)
    size = str(raw.get("size") or "").strip().lower()
    if size not in SIZE_STEPS:
        if size:
            warnings.append(f"{kind}: size {size!r} is not one of {', '.join(SIZE_STEPS)}; using {default}")
        size = default
    enter = str(raw.get("enter") or "").strip().lower()
    piece: Dict[str, Any] = {
        "id": str(raw.get("id") or "").strip().lower() or None,
        "kind": kind,
        "place": _place(raw.get("place"), kind, warnings),
        "size": size,
        "enter": enter if enter in ENTERS else "fade",
        "title": _str(raw.get("title") or raw.get("label") or raw.get("caption"), MAX_TITLE_CHARS),
    }
    piece.update(_piece_content(kind, raw, now))
    return piece


def _normalize_me(me: Any, warnings: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"at": "floor", "on": None, "x": None, "side": "left", "look": None}
    if isinstance(me, str):
        me = {"at": me}
    if not isinstance(me, dict):
        return out
    on = str(me.get("on") or "").strip().lower() or None
    at = str(me.get("at") or "").strip().lower() or ("on" if on else "floor")
    if at not in ME_AT:
        warnings.append(f"you cannot stand {at!r}; use one of {', '.join(ME_AT)} — you stand on the floor")
        at = "floor"
    if at == "on" and not on:
        warnings.append("say which piece you stand on (me.on); you stand on the floor")
        at = "floor"
    out["at"], out["on"] = at, on if at == "on" else None
    if me.get("x") is not None:
        try:
            out["x"] = max(0, min(int(me["x"]), STAGE_COLS - 1))
        except (TypeError, ValueError):
            pass
    side = str(me.get("side") or "").strip().lower()
    out["side"] = side if side in ("left", "right") else "left"
    look = me.get("look")
    out["look"] = str(look).strip().lower() or None if look else None
    return out


def normalize_scene(pieces: Any, me: Any = None, why: Any = "", *,
                    now: Optional[datetime] = None) -> Tuple[Dict[str, Any], List[str]]:
    """``({"pieces", "me", "why"}, warnings)``. Bad pieces are dropped with a
    warning, never fatal — a scene with one typo should still land. Pieces
    past eight are dropped too. Ids are kept (lower-cased) when they are
    valid and new, else numbered ``p1``, ``p2``…"""
    now = _utc(now or datetime.now(timezone.utc))
    warnings: List[str] = []
    kept: List[Dict[str, Any]] = []
    if not isinstance(pieces, list):
        pieces, warnings = [], ["pieces must be a list"]
    for i, raw in enumerate(pieces):
        if len(kept) >= MAX_PIECES:
            warnings.append(f"at most {MAX_PIECES} pieces; the rest were left off")
            break
        try:
            kept.append(normalize_piece(raw, now=now, warnings=warnings))
        except ValueError as exc:
            warnings.append(f"left off piece {i + 1}: {exc}")

    taken: set = set()
    for p in kept:
        if p["id"] and _ID_RE.match(p["id"]) and p["id"] not in taken:
            taken.add(p["id"])
        else:
            p["id"] = None
    n = 0
    for p in kept:
        if p["id"] is None:
            n += 1
            while f"p{n}" in taken:
                n += 1
            p["id"] = f"p{n}"
            taken.add(p["id"])

    scene = {"pieces": kept, "me": _normalize_me(me, warnings),
             "why": cut_at_word(" ".join(str(why or "").split()), MAX_WHY_CHARS)}
    return scene, warnings


# ---- placement ----------------------------------------------------------------------

def _anchor(place: str, w: int, h: int) -> Tuple[int, int]:
    xs = {"left": 1, "center": (STAGE_COLS - w) // 2, "right": STAGE_COLS - 1 - w}
    ys = {"top": 1, "middle": (STAGE_ROWS - h) // 2, "bottom": STAGE_ROWS - 1 - h}
    v, _, hz = place.partition("-")
    if place in ("left", "right", "center"):
        v, hz = "middle", place
    elif not hz:  # "top" / "bottom"
        hz = "center"
    return xs[hz], ys[v]


def _desired(place: Any, w: int, h: int) -> Tuple[int, int]:
    if isinstance(place, dict):
        x, y = place["x"], place["y"]
    else:
        x, y = _anchor(place if place in PLACES else "center", w, h)
    return max(0, min(x, STAGE_COLS - w)), max(0, min(y, STAGE_ROWS - h))


class _Grid:
    def __init__(self) -> None:
        self.cells = [[False] * STAGE_COLS for _ in range(STAGE_ROWS)]

    def free(self, r: Rect) -> bool:
        x, y, w, h = r
        if x < 0 or y < 0 or x + w > STAGE_COLS or y + h > STAGE_ROWS:
            return False
        return all(not self.cells[yy][xx] for yy in range(y, y + h) for xx in range(x, x + w))

    def fill(self, r: Rect) -> None:
        x, y, w, h = r
        for yy in range(y, y + h):
            for xx in range(x, x + w):
                self.cells[yy][xx] = True

    def nearest(self, x0: int, y0: int, w: int, h: int) -> Optional[Rect]:
        """The free w×h spot whose centre is nearest (x0, y0)'s; ties go to the
        smaller y, then the smaller x."""
        best = None
        for y in range(0, STAGE_ROWS - h + 1):
            for x in range(0, STAGE_COLS - w + 1):
                if self.free((x, y, w, h)):
                    key = ((x - x0) ** 2 + (y - y0) ** 2, y, x)
                    if best is None or key < best[0]:
                        best = (key, (x, y, w, h))
        return best[1] if best else None


def _rect_dict(r: Rect) -> Dict[str, int]:
    return {"x": r[0], "y": r[1], "w": r[2], "h": r[3]}


def piece_phrase(p: Dict[str, Any]) -> str:
    """"words 'A quiet Monday…'" / "note 'A thought'" / "image"."""
    if p.get("kind") == "words" and p.get("text"):
        return f"words '{cut_at_word(p['text'], 16)}'"
    return f"{p.get('kind')} '{cut_at_word(p['title'], 24)}'" if p.get("title") else str(p.get("kind"))


def _candidates(p: Dict[str, Any]) -> List[Tuple[Optional[str], int, int]]:
    """Sizes to try, largest first: the piece's own, then each smaller step."""
    sizes = PIECE_SIZES[p["kind"]]
    place = p.get("place")
    if isinstance(place, dict) and "w" in place:
        w0, h0 = place["w"], place["h"]
        smaller = [(n, w, h) for n, (w, h) in reversed(list(sizes.items())) if w <= w0 and h <= h0 and (w, h) != (w0, h0)]
        return [(None, w0, h0)] + smaller
    size = p.get("size") if p.get("size") in SIZE_STEPS else DEFAULT_PIECE_SIZE
    return [(n, *sizes[n]) for n in reversed(SIZE_STEPS[:SIZE_STEPS.index(size) + 1])]


def _place_piece(grid: _Grid, p: Dict[str, Any], warnings: List[str]) -> Optional[Dict[str, Any]]:
    for i, (name, w, h) in enumerate(_candidates(p)):
        x0, y0 = _desired(p.get("place"), w, h)
        rect: Optional[Rect] = (x0, y0, w, h)
        if not grid.free(rect):
            rect = grid.nearest(x0, y0, w, h)
        if rect is None:
            continue
        grid.fill(rect)
        placed = {**p, "rect": _rect_dict(rect)}
        if i > 0:
            placed["size"] = name or p.get("size")
            warnings.append(f"{piece_phrase(p)} was made smaller ({placed['size']}) to fit")
        if ((rect[0] - x0) ** 2 + (rect[1] - y0) ** 2) ** 0.5 > 4:  # shrunk or not
            warnings.append(f"{piece_phrase(p)} was moved to make room")
        return placed
    warnings.append(f"{piece_phrase(p)} did not fit and was left off")
    return None


def _me_rects(at: str, me: Dict[str, Any], grid: _Grid) -> List[Tuple[str, str, Rect]]:
    """(at, side, rect) to try in order for floor → corner → edge."""
    side = me.get("side") or "left"
    other = "right" if side == "left" else "left"
    corner = {"left": (0, STAGE_ROWS - CORNER_H, CORNER_W, CORNER_H),
              "right": (STAGE_COLS - CORNER_W, STAGE_ROWS - CORNER_H, CORNER_W, CORNER_H)}
    edge = {"left": (0, EDGE_Y, EDGE_W, EDGE_H), "right": (STAGE_COLS - EDGE_W, EDGE_Y, EDGE_W, EDGE_H)}
    out: List[Tuple[str, str, Rect]] = []
    if at == "floor":
        hint = me.get("x")
        hint = FLOOR_X if hint is None else hint
        hint = max(0, min(hint, STAGE_COLS - FLOOR_W))
        xs = sorted(range(0, STAGE_COLS - FLOOR_W + 1), key=lambda x: (abs(x - hint), x))
        out += [("floor", side, (x, FLOOR_Y, FLOOR_W, FLOOR_H)) for x in xs]
    if at in ("floor", "corner"):
        out += [("corner", side, corner[side]), ("corner", other, corner[other])]
    out += [("edge", side, edge[side]), ("edge", other, edge[other])]
    return out


_STAND_PHRASE = {"floor": "you stand on the floor", "corner": "you sit in the corner",
                 "edge": "you peek in from the edge"}

# When every floor, corner and edge spot is taken (only possible for
# ``on``, whose pieces are placed before him): the nearest free box of
# these sizes, largest first, near where he would have stood.
_SPARE_BOXES = (("floor", FLOOR_W, FLOOR_H), ("floor", ON_W, ON_H),
                ("corner", CORNER_W, CORNER_H), ("edge", EDGE_W, EDGE_H))


def _stand(grid: _Grid, at: str, me: Dict[str, Any], warnings: List[str],
           why: Optional[str] = None) -> Tuple[str, str, Rect]:
    """Floor → corner → edge from ``at``; else any free box; else, only
    when nothing at all is free, his own edge over whatever is there.
    ``why`` opens the note when he does not get the spot he asked for."""
    no_room = why or f"there was no room {'on the floor' if at == 'floor' else 'in the corner'}"
    tries = _me_rects(at, me, grid)
    for got_at, side, rect in tries:
        if grid.free(rect):
            if got_at != at or why:
                warnings.append(f"{no_room}; {_STAND_PHRASE[got_at]}")
            return got_at, side, rect
    hint = me.get("x")
    hint = FLOOR_X if hint is None else hint
    for got_at, w, h in _SPARE_BOXES:
        rect = grid.nearest(hint, FLOOR_Y, w, h)
        if rect is not None:
            side = me.get("side") or "left"
            warnings.append(f"{no_room}, and the floor, the corners and the edges are taken; "
                            f"you stand where there was room ({region(_rect_dict(rect))})")
            return got_at, side, rect
    got_at, side, rect = tries[-2]  # nowhere free at all: peek in from his edge anyway
    warnings.append(f"{no_room}; the stage is full, so you peek in from the edge over what is there")
    return got_at, side, rect


def place_scene(scene: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], Dict[str, Any], List[str]]:
    """Deterministic placement on the 24×12 stage. Edward first (floor,
    corner, edge), then the pieces in order — their spot if free, else the
    nearest free spot of that size, else one size smaller, else left off —
    then Edward on a piece, above its right half, or back on the floor."""
    pieces = list(scene.get("pieces") or [])
    me = scene.get("me") or {"at": "floor"}
    grid = _Grid()
    warnings: List[str] = []
    at, side = me.get("at") or "floor", me.get("side") or "left"
    me_rect: Optional[Rect] = None
    if at in ("floor", "corner", "edge"):
        at, side, me_rect = _stand(grid, at, me, warnings)
        grid.fill(me_rect)

    placed: List[Dict[str, Any]] = []
    for p in pieces:
        if p.get("kind") not in PIECE_SIZES:
            continue
        got = _place_piece(grid, p, warnings)
        if got:
            placed.append(got)

    on = None
    if at == "on":
        target = next((q for q in placed if q.get("id") == me.get("on")), None)
        rect = None
        if target:
            tx, ty, tw, _ = (target["rect"][k] for k in ("x", "y", "w", "h"))
            rect = (max(0, min(tx + (3 * tw) // 4 - 1, STAGE_COLS - ON_W)), ty - ON_H, ON_W, ON_H)
        if target and rect[1] >= 0 and grid.free(rect):
            me_rect, on = rect, target["id"]
        else:
            why = (f"there was no room above '{me.get('on')}'" if target else
                   f"there is no piece '{me.get('on')}' on the stage")
            at, side, me_rect = _stand(grid, "floor", me, warnings, why=why)

    look = me.get("look")
    if look and look != "owner" and look not in {q["id"] for q in placed}:
        warnings.append(f"there is no piece '{look}' to look at")
        look = None
    me_out = {"at": at, "rect": _rect_dict(me_rect), "on": on, "side": side, "look": look or None}
    return placed, me_out, warnings


def default_scene() -> Dict[str, Any]:
    """The stock scene: the clock, today's calendar, the pets, and Edward on the floor."""
    scene, _ = normalize_scene([
        {"kind": "clock", "place": "top-left", "size": "s"},
        {"kind": "calendar", "place": "top-right", "size": "m"},
        {"kind": "pets", "place": "bottom-right", "size": "s"},
    ], me={"at": "floor", "x": FLOOR_X}, why="")
    return scene


def region(rect: Dict[str, int]) -> str:
    """Which of the nine places a rect's centre is in."""
    cx, cy = rect["x"] + rect["w"] / 2, rect["y"] + rect["h"] / 2
    hz = "left" if cx < STAGE_COLS / 3 else ("center" if cx < 2 * STAGE_COLS / 3 else "right")
    v = "top" if cy < STAGE_ROWS / 3 else ("middle" if cy < 2 * STAGE_ROWS / 3 else "bottom")
    if v == "middle":
        return hz
    return v if hz == "center" else f"{v}-{hz}"


def me_phrase(me: Dict[str, Any]) -> str:
    at = (me or {}).get("at") or "floor"
    side = (me or {}).get("side") or "left"
    if at == "on" and me.get("on"):
        return f"you stand on '{me['on']}'"
    if at == "corner":
        return f"you sit in the bottom-{side} corner"
    if at == "edge":
        return f"you peek in from the {side}"
    return "you stand on the floor"


def _landed_as_asked(p: Dict[str, Any]) -> bool:
    """The piece sits exactly where its named place puts a piece its size."""
    place, r = p.get("place"), p["rect"]
    if not isinstance(place, str):
        return False
    return _desired(place, r["w"], r["h"]) == (r["x"], r["y"])


def describe_placed(pieces: List[Dict[str, Any]], me: Dict[str, Any]) -> str:
    """"words 'A quiet Monday…' top-left, image top-right; you stand on 'thought'"
    — for the tool reply: where each piece landed. Its own place name when
    it sits there, else the region its rect is in (a moved piece is never
    reported where it asked to go)."""
    parts = []
    for p in pieces:
        where = p["place"] if _landed_as_asked(p) else region(p["rect"])
        parts.append(f"{piece_phrase(p)} {where}")
    return ", ".join(parts) + "; " + me_phrase(me)


# ---- the old tile layout ------------------------------------------------------------

def normalize_tile(raw: Any) -> Dict[str, Any]:
    """One clean (legacy) tile or ValueError. Sizes fall back rather than fail."""
    if not isinstance(raw, dict):
        raise ValueError("a tile must be an object with a type")
    t = str(raw.get("type") or "").strip().lower()
    if t not in TILE_TYPES:
        raise ValueError(f"{t or '(missing)'} is not a tile type; use one of {', '.join(TILE_TYPES)}")
    tile: Dict[str, Any] = {"type": t, "size": DEFAULT_SIZE[t]}
    size = str(raw.get("size") or "").strip().lower()
    if size in SIZES:
        tile["size"] = size
    elif size:
        tile["size_warning"] = f"{t}: size {size!r} is not one of {', '.join(SIZES)}; using {tile['size']}"
    title = _str(raw.get("title") or raw.get("label") or raw.get("caption"), 80)
    if title:
        tile["title"] = title

    if t == "text":
        body = str(raw.get("body") or raw.get("content") or "").strip()
        if not body:
            raise ValueError("text tile needs a body")
        tile["body"] = body[:MAX_TILE_TEXT_CHARS]
    elif t == "html":
        body = str(raw.get("body") or raw.get("html") or "").strip()
        if not body:
            raise ValueError("html tile needs a body")
        if len(body) > MAX_TILE_HTML_CHARS:
            raise ValueError(f"html tile body is too long ({len(body)} > {MAX_TILE_HTML_CHARS} chars)")
        tile["body"] = body
    elif t == "image":
        tile["source"] = canvas._check_url(raw.get("source") or raw.get("url"))
    elif t == "list":
        items = _items(raw.get("items"))
        if not items:
            raise ValueError("list tile needs items")
        tile["items"] = items
    elif t == "stat":
        value = _str(raw.get("value"), 40)
        if not value:
            raise ValueError("stat tile needs a value")
        tile["value"] = value
        detail = _str(raw.get("detail"), 120)
        if detail:
            tile["detail"] = detail
    elif t == "countdown":
        target = str(raw.get("target") or raw.get("target_date") or "").strip()
        try:
            datetime.fromisoformat(target.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("countdown tile needs an ISO target datetime")
        tile["target"] = target
    return tile


def normalize_layout(tiles: Any) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Clean list + warnings. Bad tiles are dropped, not fatal."""
    warnings: List[str] = []
    out: List[Dict[str, Any]] = []
    if not isinstance(tiles, list):
        return out, ["tiles must be a list"]
    for i, raw in enumerate(tiles):
        if len(out) >= MAX_TILES:
            warnings.append(f"at most {MAX_TILES} tiles; the rest were dropped")
            break
        try:
            tile = normalize_tile(raw)
        except ValueError as exc:
            warnings.append(f"dropped tile {i + 1}: {exc}")
            continue
        sw = tile.pop("size_warning", None)
        if sw:
            warnings.append(sw)
        out.append(tile)
    return out, warnings


def pack_layout(tiles: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[str]]:
    """First-fit on the 6×4 grid, in the order given. Returns tiles with
    ``col``/``row``/``w``/``h`` and a warning per tile that could not fit."""
    grid = [[False] * GRID_COLS for _ in range(GRID_ROWS)]
    placed: List[Dict[str, Any]] = []
    warnings: List[str] = []

    def fits(c: int, r: int, w: int, h: int) -> bool:
        if c + w > GRID_COLS or r + h > GRID_ROWS:
            return False
        return all(not grid[rr][cc] for rr in range(r, r + h) for cc in range(c, c + w))

    for tile in tiles:
        w, h = SIZES.get(tile.get("size") or "", SIZES[DEFAULT_SIZE[tile["type"]]])
        spot = next(((c, r) for r in range(GRID_ROWS) for c in range(GRID_COLS) if fits(c, r, w, h)), None)
        if spot is None:
            warnings.append(f"{tile['type']} ({tile.get('size')}) did not fit on the {GRID_COLS}x{GRID_ROWS} grid and was left off")
            continue
        c, r = spot
        for rr in range(r, r + h):
            for cc in range(c, c + w):
                grid[rr][cc] = True
        placed.append({**tile, "col": c, "row": r, "w": w, "h": h})
    return placed, warnings


def default_layout() -> List[Dict[str, Any]]:
    """The old stock view: 24 cells, no gaps (an old open tab still draws it)."""
    return [
        {"type": "clock", "size": "s"},
        {"type": "alerts", "size": "l"},
        {"type": "calendar", "size": "tall"},
        {"type": "edward", "size": "hero"},
        {"type": "heartbeat", "size": "s"},
        {"type": "recents", "size": "s"},
        {"type": "vitals", "size": "s"},
    ]


def tiles_to_scene(tiles: Any) -> Dict[str, Any]:
    """A stored 6×4 tile layout as a scene: each tile keeps its cells (a column
    is 4 stage cells, a row 3); a text tile becomes a note; the ``edward``
    tile says where he stands on the floor."""
    clean, _ = normalize_layout(tiles)
    packed, _ = pack_layout(clean)
    pieces: List[Dict[str, Any]] = []
    me: Dict[str, Any] = {"at": "floor"}
    for t in packed:
        cells = {"x": t["col"] * 4, "y": t["row"] * 3, "w": t["w"] * 4, "h": t["h"] * 3}
        if t["type"] == "edward":
            me["x"] = min(cells["x"], STAGE_COLS - FLOOR_W)
            continue
        piece = {k: v for k, v in t.items() if k not in ("type", "size", "col", "row", "w", "h")}
        piece.update(kind="note" if t["type"] == "text" else t["type"], place=cells)
        pieces.append(piece)
    scene, _ = normalize_scene(pieces, me=me, why="")
    return scene


# ---- show (what is on top) ---------------------------------------------------------

def make_show_content(*, kind: str, title: Optional[str] = None, source: Optional[str] = None,
                      body: Optional[str] = None, seconds: int = 0, now: Optional[datetime] = None,
                      shown_by: str = "edward") -> Dict[str, Any]:
    if kind not in canvas.KINDS:
        raise ValueError(f"unknown kind {kind!r}; use one of {', '.join(SHOW_KINDS)}")
    return canvas.make_content(kind=kind, title=title, source=source, body=body, seconds=seconds,
                               now=now, shown_by=shown_by).as_dict()


def show_is_usable(show: Optional[Dict[str, Any]]) -> bool:
    """A stored show the page can draw. A row written before a kind was
    retired (``web``, 2026-09-07) is treated as nothing showing."""
    return bool(show) and show.get("kind") in SHOW_KINDS


def show_is_expired(show: Optional[Dict[str, Any]], now: datetime) -> bool:
    if not show or not show.get("expires_at"):
        return False
    exp = datetime.fromisoformat(show["expires_at"])
    return canvas._utc(now) >= canvas._utc(exp)


def describe_show(show: Optional[Dict[str, Any]]) -> str:
    """One phrase for the pulse trigger and tool replies."""
    if not show:
        return "its scene"
    kind = show.get("kind")
    t = f" '{show['title']}'" if show.get("title") else ""
    return f"an {kind}{t}" if kind and kind[0] in "aeiou" else f"a {kind}{t}"


# ---- lines --------------------------------------------------------------------------

def clean_line(text: Any) -> str:
    """One line to say: whitespace collapsed, at most 240 chars (cut at a word)."""
    line = " ".join(str(text or "").split())
    if not line:
        raise ValueError("the line was empty")
    return cut_at_word(line, MAX_LINE_CHARS)


def is_quiet_hour(now: datetime) -> bool:
    """Quiet hours in the owner's zone (``speak_gate.quiet_hours``:
    ``EDWARD_QUIET_HOURS``, else 22:00–07:00): nothing is said aloud."""
    from services.speak_gate import in_quiet_hours, quiet_hours
    from services.timezone_utils import utc_to_local
    return in_quiet_hours(utc_to_local(_utc(now)).hour, *quiet_hours())


SURPLUS_PURPOSE_PREFIX = "surplus:"
# The day's free-time wake (design decision, 2026-10-11) — the surplus
# queue's free time, now one budgeted run a day.
FREE_TIME_PURPOSE = "mind:free"


def in_surplus_turn() -> bool:
    """Is the call running inside a surplus job (``purpose_scope("surplus:…")``)
    or the day's free time (``mind:free``)? Nobody asked for that turn and
    the owner may be busy or asleep, so a line it says on the screen is shown,
    never spoken (round 2, 2026-09-28)."""
    try:
        from services.call_purpose import current_purpose
        purpose = str(current_purpose() or "")
        return purpose.startswith(SURPLUS_PURPOSE_PREFIX) or purpose == FREE_TIME_PURPOSE
    except Exception:  # noqa: BLE001
        return False


def _recent_lines(lines: Any, now: datetime) -> List[Dict[str, Any]]:
    out = []
    for line in lines if isinstance(lines, list) else []:
        try:
            at = _parse_when(line["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if at >= now - timedelta(minutes=LINES_SHOWN_MINUTES):
            out.append((at, line))
    return [line for _, line in sorted(out, key=lambda x: x[0])]


def _last_said(lines: Any) -> Optional[Dict[str, Any]]:
    best = None
    for line in lines if isinstance(lines, list) else []:
        try:
            at = _parse_when(line["at"])
        except (KeyError, TypeError, ValueError):
            continue
        if best is None or at >= best[0]:
            best = (at, line)
    return {"text": best[1].get("text"), "at": best[0].isoformat()} if best else None


# ---- whose turn --------------------------------------------------------------------

def is_owners_turn() -> bool:
    """Is the owner speaking in the turn running now? A call outside any
    turn is not theirs (the REST routes say ``by="owner"`` themselves)."""
    try:
        from services import turn_ledger
        rec = turn_ledger.current_turn()
    except Exception:  # noqa: BLE001
        return False
    return rec is not None and getattr(rec, "source", None) in OWNER_SOURCES


def owner_asked_now() -> bool:
    """Is the turn running now one the owner asked for? The toolset's
    ``TurnPolicy`` says so when it is set (a ping from the owner, an event
    they asked for); otherwise their own turn (``is_owners_turn``)."""
    try:
        from services.toolset.policy import current_policy
    except ImportError:
        current_policy = None
    if current_policy is not None:
        try:
            policy = current_policy()
        except Exception:  # noqa: BLE001
            policy = None
        if policy is not None:
            return bool(getattr(policy, "owner_asked", False))
    return is_owners_turn()


class ScreenHeld(Exception):
    """The owner asked for what is on the screen; Edward's own turn cannot
    change it until ``until``."""

    def __init__(self, until: datetime):
        from services.owner import owner_name

        self.until = until
        super().__init__(f"{owner_name()}'s ask holds the screen until {until.isoformat()}")


# ---- snapshot ----------------------------------------------------------------------

def _legacy_keys(layout: Any, layout_note: Optional[str], layout_updated_at: Optional[datetime],
                 layout_updated_by: Optional[str]) -> Dict[str, Any]:
    """The 6×4 keys, kept one release so an open old tab keeps drawing until
    it reloads onto the stage."""
    source = "edward" if layout else "default"
    tiles, _ = normalize_layout(layout if layout else default_layout())
    placed, _ = pack_layout(tiles)
    if not placed:
        placed, _ = pack_layout(default_layout())
        source = "default"
    return {
        "grid": {"cols": GRID_COLS, "rows": GRID_ROWS},
        "layout": placed,
        "layout_source": source,
        "layout_note": layout_note if source == "edward" else None,
        "layout_updated_at": layout_updated_at.isoformat() if (source == "edward" and layout_updated_at) else None,
        "layout_updated_by": layout_updated_by if source == "edward" else None,
    }


def build_snapshot(*, layout: Optional[List[Dict[str, Any]]] = None, layout_note: Optional[str] = None,
                   layout_updated_at: Optional[datetime] = None, layout_updated_by: Optional[str] = None,
                   show: Optional[Dict[str, Any]] = None, now: Optional[datetime] = None,
                   scene: Optional[Dict[str, Any]] = None, scene_why: Optional[str] = None,
                   scene_updated_at: Optional[datetime] = None, scene_updated_by: Optional[str] = None,
                   asked_at: Optional[datetime] = None, asked_until: Optional[datetime] = None,
                   lines: Any = None, owner_at_mini: bool = False) -> Dict[str, Any]:
    """What ``GET /api/screen`` answers (the page polls it every 4 s)."""
    now = _utc(now or datetime.now(timezone.utc))
    show = None if (not show_is_usable(show) or show_is_expired(show, now)) else show

    source, why, updated_at, updated_by = "default", "", None, None
    stored: Optional[Dict[str, Any]] = None
    if isinstance(scene, dict) and scene.get("pieces"):
        stored = scene
        source = "owner" if scene_updated_by == "owner" else "edward"
        why = scene_why if scene_why is not None else scene.get("why") or ""
        updated_at, updated_by = scene_updated_at, scene_updated_by
    elif layout:
        stored = tiles_to_scene(layout)
        source, why = "edward", layout_note or ""
        updated_at, updated_by = layout_updated_at, layout_updated_by

    placed: List[Dict[str, Any]] = []
    me: Dict[str, Any] = {}
    if stored is not None:
        clean, _ = normalize_scene(stored.get("pieces"), me=stored.get("me"), why=why, now=now)
        placed, me, _ = place_scene(clean)
        why = clean["why"]
    if not placed:  # nothing stored, or it no longer validates → the stock scene
        placed, me, _ = place_scene(default_scene())
        source, why, updated_at, updated_by = "default", "", None, None

    # The hold stands until it lapses (a tap does not end it), but the page
    # is told of it — "You asked at … · stays up until …" — only while
    # something the owner asked for is on it: their scene, or a show over it
    # (a tap takes their show down and leaves Edward's scene; review 2026-09-28).
    held = _utc(asked_until) if asked_until and _utc(asked_until) > now else None
    asked = None
    if held and (source == "owner" or show):
        asked = {"at": _iso(asked_at), "until": _iso(held)}

    return {
        "stage": {"cols": STAGE_COLS, "rows": STAGE_ROWS},
        "scene": {"pieces": placed, "me": me, "why": why, "source": source,
                  "updated_at": _iso(updated_at), "updated_by": updated_by},
        "asked": asked,
        # Not on the page: the hold whatever is showing, for the pulse.
        "held_until": _iso(held),
        "lines": _recent_lines(lines, now),
        # Not on the page: the newest line whatever its age, so the pulse's
        # description does not change when a line ages off the screen.
        "last_said": _last_said(lines),
        "owner_at_mini": bool(owner_at_mini),
        "show": show,
        "now": now.isoformat(),
        **_legacy_keys(layout, layout_note, layout_updated_at, layout_updated_by),
    }


def describe_scene(snap: Optional[Dict[str, Any]]) -> str:
    """"the stock scene" / "your scene (words 'A quiet Monday…', image, note
    'A thought'; you stand on 'thought'; why: you're out)" — for the pulse.
    Built only from what is stored, so it is the same on every poll until
    the scene really changes."""
    scene = (snap or {}).get("scene") or {}
    source = scene.get("source")
    if not scene or source not in ("edward", "owner"):
        return "the stock scene"
    parts = [", ".join(piece_phrase(p) for p in scene.get("pieces") or []), me_phrase(scene.get("me") or {})]
    if scene.get("why"):
        parts.append(f"why: {scene['why']}")
    if source == "edward":
        head = "your scene"
    else:
        from services.owner import owner_name

        head = f"the scene {owner_name()} asked for"
    return f"{head} ({'; '.join(parts)})"


# ---- I/O ---------------------------------------------------------------------------

async def _row(session):
    from services.database import ScreenStateModel
    row = await session.get(ScreenStateModel, SCREEN_ID)
    if row is None:
        row = ScreenStateModel(id=SCREEN_ID)
        session.add(row)
    return row


def _loads(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return None


def _now(now: Optional[datetime]) -> datetime:
    return _utc(now or datetime.now(timezone.utc))


def _held_until(row, now: datetime) -> Optional[datetime]:
    if row.asked_until and _utc(row.asked_until) > now:
        return _utc(row.asked_until)
    return None


def _guard(row, by: str, now: datetime) -> None:
    """Edward's own turn cannot change what the owner asked for while it holds."""
    if by == "edward":
        until = _held_until(row, now)
        if until:
            raise ScreenHeld(until)


def _stored_until(raw: Any) -> Optional[datetime]:
    """A hold end kept inside the scene or show JSON, as aware UTC; None
    when missing or unreadable."""
    if not raw:
        return None
    try:
        return _utc(datetime.fromisoformat(str(raw)))
    except (TypeError, ValueError):
        return None


def _scene_hold(row) -> Optional[datetime]:
    """How long the owner's scene holds on its own (None when the scene is not theirs)."""
    scene = _loads(row.scene)
    if row.scene_updated_by != "owner" or not isinstance(scene, dict):
        return None
    return _stored_until(scene.get("held_until")) or (_utc(row.asked_until) if row.asked_until else None)


def _show_hold(row, now: datetime) -> Optional[datetime]:
    """How long the show the owner asked for holds on its own (None when no
    show of theirs is up)."""
    show = _loads(row.show)
    if not show_is_usable(show) or show_is_expired(show, now):
        return None
    return _stored_until(show.get("held_until"))


def _hold(row, now: datetime, until: datetime, other: Optional[datetime] = None) -> None:
    """The owner's new ask holds the screen until ``until`` — or until
    ``other``, the hold of their other ask still on the screen (their scene
    under a new show, their show over a new scene), when that runs later. One ask never
    cuts the hold of another it does not replace (review 2026-09-28)."""
    row.asked_at = _naive(now)
    row.asked_until = _naive(max(until, other) if other and other > now else until)


def _hold_minutes(minutes: Any) -> int:
    try:
        m = int(minutes or 0)
    except (TypeError, ValueError):
        m = 0
    return min(m if m > 0 else DEFAULT_HOLD_MINUTES, MAX_HOLD_MINUTES)


def _owner_at_mini(now: datetime) -> bool:
    try:
        from services import presence_service
        return presence_service.owner_at_mini(now)
    except Exception:  # noqa: BLE001
        return False


async def get_snapshot(*, now: Optional[datetime] = None) -> Dict[str, Any]:
    from services.database import async_session
    now = _now(now)
    async with async_session() as session:
        row = await _row(session)
        data = dict(layout=_loads(row.layout), layout_note=row.layout_note, layout_updated_at=row.layout_updated_at,
                    layout_updated_by=row.layout_updated_by, show=_loads(row.show),
                    scene=_loads(row.scene), scene_why=row.scene_why, scene_updated_at=row.scene_updated_at,
                    scene_updated_by=row.scene_updated_by, asked_at=row.asked_at, asked_until=row.asked_until,
                    lines=_loads(row.lines))
    return build_snapshot(now=now, owner_at_mini=_owner_at_mini(now), **data)


async def current_show(*, now: Optional[datetime] = None) -> Optional[Dict[str, Any]]:
    from services.database import async_session
    async with async_session() as session:
        row = await _row(session)
        show = _loads(row.show)
    return None if (not show_is_usable(show) or show_is_expired(show, _now(now))) else show


async def compose(pieces: Any, *, me: Any = None, why: str = "", hold_minutes: Any = 0, by: str = "edward",
                  now: Optional[datetime] = None) -> Dict[str, Any]:
    """Set the scene. ``by="owner"`` (their own turn, or the REST route) holds the
    screen for ``hold_minutes`` (default two hours, at most twelve; longer
    while a show they asked for over it holds longer). ``held_until`` is the
    scene's own hold.
    ValueError when no piece is usable; ScreenHeld while the owner's ask holds
    it against ``by="edward"``."""
    from services.database import async_session
    now = _now(now)
    scene, warnings = normalize_scene(pieces, me=me, why=why, now=now)
    placed, me_out, place_warnings = place_scene(scene)
    warnings += place_warnings
    if not placed:
        raise ValueError("no usable pieces: " + ("; ".join(warnings) or "the list was empty"))
    held = None
    async with async_session() as session:
        row = await _row(session)
        _guard(row, by, now)
        stored: Dict[str, Any] = {"pieces": scene["pieces"], "me": scene["me"]}
        if by == "owner":
            held = now + timedelta(minutes=_hold_minutes(hold_minutes))
            stored["held_until"] = held.isoformat()  # the scene's own hold
            _hold(row, now, held, other=_show_hold(row, now))
        row.scene = json.dumps(stored)
        row.scene_why = scene["why"] or None
        row.scene_updated_at = _naive(now)
        row.scene_updated_by = by
        await session.commit()
    print(f"[screen] scene set by {by}: {', '.join(p['kind'] for p in placed)}" + (f" — {scene['why']}" if scene["why"] else ""))
    return {"pieces": placed, "me": me_out, "why": scene["why"], "warnings": warnings, "held_until": held}


async def reset_scene(*, by: str = "edward", now: Optional[datetime] = None) -> bool:
    """Back to the stock scene (an old tile layout goes too). The owner's
    reset ends their hold."""
    from services.database import async_session
    now = _now(now)
    async with async_session() as session:
        row = await _row(session)
        _guard(row, by, now)
        had = bool(row.scene or row.layout)
        row.scene = None
        row.scene_why = None
        row.layout = None
        row.layout_note = None
        row.scene_updated_at = _naive(now)
        row.scene_updated_by = by
        if by == "owner":
            row.asked_until = _naive(now)
        await session.commit()
    if had:
        print(f"[screen] scene reset by {by}")
    return had


async def show(*, kind: str, title: Optional[str] = None, source: Optional[str] = None, body: Optional[str] = None,
               seconds: int = 0, now: Optional[datetime] = None, shown_by: str = "edward",
               by: str = "edward") -> Dict[str, Any]:
    """Put something over the scene. The owner's own ask holds the screen until
    the show expires (two hours when it has no end) — or longer, while a
    scene they asked for holds longer. Edward's own show with no ``seconds``
    expires after two hours (2026-10-11, ``canvas.AUTONOMOUS_SHOW_SECONDS``)."""
    from services.database import async_session
    now = _now(now)
    seconds = canvas.default_seconds(seconds, "edward" if by != "owner" else "owner",
                                     by == "owner" or owner_asked_now())
    content = make_show_content(kind=kind, title=title, source=source, body=body, seconds=seconds, now=now,
                                shown_by=shown_by)
    async with async_session() as session:
        row = await _row(session)
        _guard(row, by, now)
        if by == "owner":
            until = _parse_when(content["expires_at"]) if content.get("expires_at") else \
                now + timedelta(minutes=DEFAULT_HOLD_MINUTES)
            content["held_until"] = until.isoformat()  # the show's own hold
            _hold(row, now, until, other=_scene_hold(row))
        row.show = json.dumps(content)
        row.show_dismissed_at = None
        row.show_dismissed_by = None
        await session.commit()
    print(f"[screen] show {describe_show(content)}" +
          (f" until {content['expires_at']}" if content.get("expires_at") else " until cleared"))
    return content


async def clear(*, by: str = "edward", now: Optional[datetime] = None) -> bool:
    """Take the show down. ``by`` is also who dismissed it ("tap" from the
    page: a person at the screen neither obeys nor ends a hold); the owner's
    own clear ends their hold."""
    from services.database import async_session
    now = _now(now)
    async with async_session() as session:
        row = await _row(session)
        _guard(row, by, now)
        had = bool(row.show)
        row.show = None
        row.show_dismissed_at = _naive(now)
        row.show_dismissed_by = by
        if by == "owner":
            row.asked_until = _naive(now)
        await session.commit()
    if had:
        print(f"[screen] cleared by {by}")
    return had


async def say(text: Any, *, aloud: bool = False, by: str = "edward", now: Optional[datetime] = None) -> Dict[str, Any]:
    """Say one line on the screen (never held). ``aloud`` is dropped in quiet
    hours and in a surplus turn (free time is shown, never spoken);
    ``quiet`` in the answer says it was and ``quiet_why`` says why
    (``"surplus"`` | ``"hours"`` | None)."""
    from services.database import async_session
    now = _now(now)
    line_text = clean_line(text)
    quiet_why = None
    if aloud:
        quiet_why = "surplus" if in_surplus_turn() else "hours" if is_quiet_hour(now) else None
    quiet = quiet_why is not None
    line = {"id": secrets.token_hex(4), "text": line_text, "at": now.isoformat(), "aloud": bool(aloud) and not quiet}
    async with async_session() as session:
        row = await _row(session)
        kept = _loads(row.lines)
        kept = kept if isinstance(kept, list) else []
        row.lines = json.dumps((kept + [line])[-MAX_LINES_KEPT:])
        await session.commit()
    print(f"[screen] {by} said{' aloud' if line['aloud'] else ''}: {line_text[:80]}")
    return {"line": line, "quiet": quiet, "quiet_why": quiet_why}
