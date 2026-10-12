// The stage on /screen (2026-09-28): the pure half of the page.
//
// The backend places every piece and Edward on a 24×12 stage
// (services/screen_service.py::place_scene); this side only turns cells into
// percentages, decides which side of him his words and thoughts go, fades
// what they cross, picks the lines worth showing, and knows night. (Round 2:
// Ellis at the desk gets the normal stage — he often watches it.)

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  COMPOSE_RECT,
  FALLBACK_SCENE,
  FALLBACK_STAGE,
  askedReceipt,
  clipAbove,
  coveredCells,
  mindRect,
  careSummary,
  dimmed,
  foldFits,
  laneContent,
  laneFor,
  nightDrift,
  receiptLeft,
  rectStyle,
  shouldSpeak,
  speechRect,
  speechView,
  stageFromSnapshot,
  stageMode,
  stageStatusLine,
  stageStatusTone,
  statusSideFor,
  timerView,
  whyReceipt,
  type SpokenLine,
  type StagePiece,
} from "../app/(display)/display/lib/stageModel.ts";

const STAGE = { cols: 24, rows: 12 };

const piece = (id: string, x: number, y: number, w: number, h: number, kind: StagePiece["kind"] = "note"): StagePiece => ({
  id, kind, size: "m", enter: "fade", place: "center", rect: { x, y, w, h }, title: null,
});

describe("rectStyle", () => {
  it("maps cells to a percentage box of the stage", () => {
    assert.deepEqual(rectStyle({ x: 6, y: 3, w: 12, h: 6 }, STAGE), {
      left: "25%", top: "25%", width: "50%", height: "50%",
    });
  });

  it("never divides by zero on a malformed stage", () => {
    const s = rectStyle({ x: 1, y: 1, w: 1, h: 1 }, { cols: 0, rows: 0 });
    assert.equal(s.left, "4.1667%");
    assert.equal(s.top, "8.3333%");
  });
});

describe("laneFor — the side of Edward with more room", () => {
  it("Edward on the left floor: his words go to the right", () => {
    const lane = laneFor({ x: 4, y: 7, w: 4, h: 5 }, [], STAGE);
    assert.equal(lane.side, "right");
    assert.equal(lane.rect.x, 9, "one cell of air after him");
    assert.equal(lane.rect.x + lane.rect.w, 23, "a one-cell margin at the edge");
  });

  it("Edward on the right: to the left", () => {
    const lane = laneFor({ x: 18, y: 7, w: 4, h: 5 }, [], STAGE);
    assert.equal(lane.side, "left");
    assert.equal(lane.rect.x, 1);
    assert.equal(lane.rect.x + lane.rect.w, 17);
  });

  it("free room, not width, decides: a full right side loses to an open left", () => {
    const me = { x: 10, y: 7, w: 4, h: 5 };
    const lane = laneFor(me, [piece("big", 15, 0, 9, 12)], STAGE);
    assert.equal(lane.side, "left");
  });

  it("carries a percentage box for the page", () => {
    const lane = laneFor({ x: 4, y: 7, w: 4, h: 5 }, [], STAGE);
    assert.equal(lane.style.left, "37.5%");
  });
});

describe("dimmed", () => {
  const lane = { x: 9, y: 1, w: 14, h: 10 };
  it("a piece under the lane fades back", () => assert.equal(dimmed({ x: 16, y: 1, w: 7, h: 6 }, lane), true));
  it("a piece clear of it does not", () => assert.equal(dimmed({ x: 1, y: 1, w: 5, h: 2 }, lane), false));
  it("touching edges is not overlapping", () => assert.equal(dimmed({ x: 4, y: 1, w: 5, h: 2 }, lane), false));
  it("no lane, nothing dims", () => assert.equal(dimmed({ x: 16, y: 1, w: 7, h: 6 }, null), false));
});

describe("speechView", () => {
  const now = Date.parse("2026-09-28T22:52:00Z");
  const line = (id: string, secondsAgo: number, aloud = false): SpokenLine => ({
    id, text: `line ${id}`, at: new Date(now - secondsAgo * 1000).toISOString(), aloud,
  });

  it("the newest line under three minutes old is current; the one before it moves back", () => {
    const v = speechView([line("a", 400), line("b", 30, true)], now);
    assert.equal(v.current?.id, "b");
    assert.equal(v.previous?.id, "a");
  });

  it("the previous line only while it is under ten minutes old", () => {
    const v = speechView([line("a", 700), line("b", 30)], now);
    assert.equal(v.current?.id, "b");
    assert.equal(v.previous, null);
  });

  it("nothing said lately, nothing shown", () => {
    const v = speechView([line("a", 500), line("b", 200)], now);
    assert.deepEqual(v, { current: null, previous: null });
  });

  it("out-of-order lines are read by time, and junk is ignored", () => {
    const junk = { id: "x", text: "", at: "not a time", aloud: false } as SpokenLine;
    const v = speechView([line("b", 10), junk, line("a", 60)], now);
    assert.equal(v.current?.id, "b");
    assert.equal(v.previous?.id, "a");
  });

  it("no lines at all", () => assert.deepEqual(speechView(undefined, now), { current: null, previous: null }));
});

describe("stageMode", () => {
  it("Ellis at the mini gets the normal stage: nothing dims or shrinks (he often watches it)", () => {
    assert.equal(stageMode({ ownerAtMini: true, hour: 14, show: null }), "stage");
    assert.equal(stageMode({ ownerAtMini: true, hour: 2, show: null }), "stage", "at the desk at night it is not an unattended screen");
    assert.equal(stageMode({ ownerAtMini: true, hour: 14, show: null, timeOfDay: "night" }), "night", "the gear panel's pin still holds");
  });
  it("23:00–06:00 in his zone is night", () => {
    assert.equal(stageMode({ ownerAtMini: false, hour: 23, show: null }), "night");
    assert.equal(stageMode({ ownerAtMini: false, hour: 5, show: null }), "night");
    assert.equal(stageMode({ ownerAtMini: false, hour: 6, show: null }), "stage");
    assert.equal(stageMode({ ownerAtMini: false, hour: 22, show: null }), "stage");
  });
  it("a show keeps the stage as it is under it", () => {
    assert.equal(stageMode({ ownerAtMini: false, hour: 1, show: { kind: "alert" } }), "stage");
  });
  it("the gear panel's time of day pins night, never a daytime choice", () => {
    assert.equal(stageMode({ ownerAtMini: false, hour: 14, show: null, timeOfDay: "night" }), "night");
    for (const t of ["morning", "day", "dusk"] as const) assert.equal(stageMode({ ownerAtMini: false, hour: 2, show: null, timeOfDay: t }), "stage", t);
    assert.equal(stageMode({ ownerAtMini: false, hour: 2, show: null, timeOfDay: "auto" }), "night");
  });
  it("there is no desk mode any more", () => {
    for (const hour of [0, 3, 9, 14, 23]) {
      for (const ownerAtMini of [true, false]) assert.notEqual(stageMode({ ownerAtMini, hour, show: null }) as string, "desk");
    }
  });
});

describe("nightDrift", () => {
  it("is deterministic, within ±12 px, and holds for five minutes", () => {
    const t = Date.parse("2026-09-28T05:00:00Z");
    const a = nightDrift(t);
    assert.deepEqual(nightDrift(t), a);
    assert.deepEqual(nightDrift(t + 299_000), a);
    for (let i = 0; i < 50; i++) {
      const d = nightDrift(t + i * 300_000);
      assert.ok(Math.abs(d.x) <= 12 && Math.abs(d.y) <= 12, JSON.stringify(d));
      assert.ok(Number.isInteger(d.x) && Number.isInteger(d.y));
    }
  });

  it("does move over an hour", () => {
    const t = Date.parse("2026-09-28T05:00:00Z");
    const seen = new Set(Array.from({ length: 12 }, (_, i) => JSON.stringify(nightDrift(t + i * 300_000))));
    assert.ok(seen.size > 3);
  });
});

describe("the receipts", () => {
  const tz = "America/Chicago";
  it("his own scene says when and why, in his words", () => {
    assert.equal(
      whyReceipt({ source: "edward", updated_at: "2026-09-28T21:10:00+00:00", why: "You're out and nothing needs you." }, { tz }),
      "I arranged this at 4:10 pm — you're out and nothing needs you.",
    );
  });
  it("without a why, just when", () => {
    assert.equal(whyReceipt({ source: "edward", updated_at: "2026-09-28T21:10:00+00:00", why: "" }, { tz }), "I arranged this at 4:10 pm");
  });
  it("a name at the start of his why keeps its capital", () => {
    assert.equal(
      whyReceipt({ source: "edward", updated_at: "2026-09-28T21:10:00+00:00", why: "Biscuit's meds are close." }, { tz }),
      "I arranged this at 4:10 pm — Biscuit's meds are close.",
    );
  });
  it("the stock scene has no receipt", () => {
    assert.equal(whyReceipt({ source: "default", updated_at: null, why: "" }, { tz }), null);
  });
  it("Ellis's ask says when he asked and how long it stays", () => {
    assert.equal(
      askedReceipt({ at: "2026-09-28T23:10:00+00:00", until: "2026-09-29T01:10:00+00:00" }, { tz }),
      "You asked at 6:10 pm · stays up until 8:10 pm",
    );
  });
});

describe("stageStatusLine — the one line under him", () => {
  it("speaking while his voice really plays", () => {
    assert.equal(stageStatusLine({ known: true, mood: "idle", line: "Resting" }, { thinking: false, speaking: true }), "Speaking");
  });
  it("thinking while a turn really runs", () => {
    assert.equal(stageStatusLine({ known: true, mood: "idle", line: "Resting" }, { thinking: true, speaking: false }), "Thinking");
  });
  it("never hides his hands being down", () => {
    assert.equal(
      stageStatusLine({ known: true, mood: "idle", line: "Resting · Chrome link down", handsDown: true }, { thinking: true, speaking: false }),
      "Thinking · Chrome link down",
    );
  });
  it("offline wins over both", () => {
    assert.equal(stageStatusLine({ known: true, mood: "offline", line: "Offline" }, { thinking: true, speaking: true }), "Offline");
  });
  it("claims nothing before the first answer", () => {
    assert.equal(stageStatusLine({ known: false, mood: "idle", line: "" }, { thinking: true, speaking: false }), "");
  });
  it("otherwise his honest presence line", () => {
    assert.equal(stageStatusLine({ known: true, mood: "attention", line: "Waiting on your approval" }, { thinking: false, speaking: false }), "Waiting on your approval");
  });
});

describe("FALLBACK_SCENE", () => {
  it("mirrors the stock scene: clock, calendar, pets, and Edward on the floor", () => {
    assert.deepEqual(FALLBACK_SCENE.pieces.map((p) => p.kind), ["clock", "calendar", "pets"]);
    assert.equal(FALLBACK_SCENE.me.at, "floor");
    assert.equal(FALLBACK_SCENE.source, "default");
    assert.deepEqual(FALLBACK_STAGE, STAGE);
    for (const p of FALLBACK_SCENE.pieces) {
      assert.ok(p.rect.x + p.rect.w <= 24 && p.rect.y + p.rect.h <= 12, p.id);
      assert.equal(dimmed(p.rect, FALLBACK_SCENE.me.rect), false, `${p.id} clear of Edward`);
    }
  });
});

describe("stageFromSnapshot", () => {
  it("an old backend (tiles only) or no snapshot draws the stock scene", () => {
    for (const snap of [null, undefined, { layout: [], grid: { cols: 6, rows: 4 } }]) {
      const v = stageFromSnapshot(snap as never);
      assert.equal(v.scene, FALLBACK_SCENE);
      assert.deepEqual(v.stage, STAGE);
      assert.deepEqual(v.lines, []);
      assert.equal(v.asked, null);
      assert.equal(v.ownerAtMini, false);
    }
  });

  it("a stage snapshot is read as it is, and a piece without a box is left off", () => {
    const scene = { ...FALLBACK_SCENE, source: "edward" as const, pieces: [piece("a", 1, 1, 5, 2), { id: "junk", kind: "note" } as never] };
    const v = stageFromSnapshot({
      stage: STAGE, scene, asked: { at: "2026-09-28T23:10:00+00:00", until: "2026-09-29T01:10:00+00:00" },
      lines: [{ id: "l1", text: "Hi.", at: "2026-09-28T23:10:00+00:00", aloud: false }], owner_at_mini: true,
    } as never);
    assert.deepEqual(v.scene.pieces.map((p) => p.id), ["a"]);
    assert.equal(v.scene.source, "edward");
    assert.equal(v.lines.length, 1);
    assert.equal(v.asked?.until, "2026-09-29T01:10:00+00:00");
    assert.equal(v.ownerAtMini, true);
  });
});

describe("speechRect — the box his words take, for fading what they cross", () => {
  const lane = { x: 9, y: 1, w: 14, h: 10 };
  const me = { x: 4, y: 7, w: 4, h: 5 };
  it("sits in the lane, ending about his head, and grows upward with the words", () => {
    const short = speechRect(lane, me, 40, 0);
    const long = speechRect(lane, me, 180, 60);
    for (const r of [short, long]) {
      assert.equal(r.x, lane.x);
      assert.equal(r.w, lane.w);
      assert.ok(r.y >= lane.y && r.y + r.h <= lane.y + lane.h);
    }
    assert.equal(short.y + short.h, long.y + long.h, "the same bottom");
    assert.ok(long.h > short.h);
  });
  it("a piece high in the lane stays clear of a short line", () => {
    assert.equal(dimmed({ x: 16, y: 1, w: 6, h: 3 }, speechRect(lane, me, 40, 0)), false);
  });
});

describe("foldFits", () => {
  const lane = { x: 9, y: 1, w: 14, h: 10 };
  it("the one earlier line needs the lane's bottom row clear", () => {
    assert.equal(foldFits(lane, []), true);
    assert.equal(foldFits(lane, [piece("pets", 17, 7, 6, 5)]), false);
    assert.equal(foldFits(lane, [piece("cal", 16, 1, 7, 6)]), true);
  });
});

describe("shouldSpeak — a line said aloud plays once, fresh, when allowed", () => {
  const now = Date.parse("2026-09-28T22:52:00Z");
  const line = (over: Partial<SpokenLine> = {}): SpokenLine => ({ id: "l1", text: "Meds at 6.", at: new Date(now - 5_000).toISOString(), aloud: true, ...over });
  const base = { enabled: true, nowMs: now, visible: true, spoken: new Set<string>() };
  it("plays a fresh aloud line", () => assert.equal(shouldSpeak(line(), base), true));
  it("not when the setting is off", () => assert.equal(shouldSpeak(line(), { ...base, enabled: false }), false));
  it("not a quiet line", () => assert.equal(shouldSpeak(line({ aloud: false }), base), false));
  it("not twice", () => assert.equal(shouldSpeak(line(), { ...base, spoken: new Set(["l1"]) }), false));
  it("not a line over a minute old", () => assert.equal(shouldSpeak(line({ at: new Date(now - 61_000).toISOString() }), base), false));
  it("not while the tab is hidden", () => assert.equal(shouldSpeak(line(), { ...base, visible: false }), false));
  it("nothing to say", () => assert.equal(shouldSpeak(null, base), false));
});

describe("careSummary — the pets piece, one row a pet", () => {
  const med = (name: string, status: string, extra: Record<string, unknown> = {}) => ({
    id: name, name, dose: null, kind: "med", window: "pm", at_time: "18:00", status, by: null, at_local: null,
    needs_review: false, minutes_late: null, ...extra,
  });
  const feed = (done: boolean, at_local: string | null = null) => ({ item_id: "f", done, by: done ? "Ellis" : null, at_local, overdue: false, marker: null, bundled_item_ids: [] });
  const board = {
    care_day: "2026-09-28", window: "pm" as const, alerts: [],
    pets: [
      { id: "biscuit", name: "Biscuit", species: "dog", photo_url: null, feedings: { am: feed(true, "6:10a"), pm: feed(false) },
        meds: [med("Cetirizine", "given", { window: "am" }), med("Carprofen", "overdue", { needs_review: true }), med("Probiotic", "overdue")] },
      { id: "ev", name: "Maple", species: "dog", photo_url: null, feedings: { am: feed(true, "6:10a"), pm: feed(true, "5:58p") }, meds: [] },
      { id: "whiskers", name: "Mr. Whiskers", species: "cat", photo_url: null, feedings: { am: feed(true), pm: feed(false) }, meds: [] },
    ],
  };
  it("late timed meds are amber and named; a note from a sitter carries its badge", () => {
    const [biscuit] = careSummary(board as never);
    assert.equal(biscuit.pet, "Biscuit");
    assert.equal(biscuit.tone, "warn");
    assert.equal(biscuit.chip, "2 late");
    assert.match(biscuit.detail, /Carprofen/);
    assert.equal(biscuit.review, true);
  });
  it("all done is calm", () => {
    const ev = careSummary(board as never)[1];
    assert.equal(ev.tone, "ok");
    assert.equal(ev.chip, "Fed 5:58 pm", "the one clock format, not the hub's compact 5:58p");
  });
  it("food not yet given is never an alert (a window, not a time)", () => {
    const whiskers = careSummary(board as never)[2];
    assert.equal(whiskers.tone, "mute");
    assert.equal(whiskers.chip, "Not yet");
  });
  it("no board, no rows", () => assert.deepEqual(careSummary(null), []));
});

describe("timerView — Ellis's pasta timer", () => {
  const start = Date.parse("2026-09-28T23:10:00Z");
  const t = { started_at: "2026-09-28T23:10:00+00:00", ends_at: "2026-09-28T23:21:00+00:00" };
  it("counts down in minutes and seconds, with how far along it is", () => {
    const v = timerView(t, start + 139_000);
    assert.equal(v.remaining, "08:41");
    assert.equal(v.progress, 0.21);
    assert.equal(v.done, false);
  });
  it("an hour or more shows hours", () => {
    const v = timerView({ started_at: t.started_at, ends_at: "2026-09-29T01:40:00+00:00" }, start);
    assert.equal(v.remaining, "2:30:00");
  });
  it("done is done", () => {
    const v = timerView(t, start + 12 * 60_000);
    assert.deepEqual({ remaining: v.remaining, progress: v.progress, done: v.done }, { remaining: "00:00", progress: 1, done: true });
  });
  it("a broken timer says nothing false", () => {
    const v = timerView({ started_at: "x", ends_at: "y" }, start);
    assert.equal(v.remaining, "—");
    assert.equal(v.done, false);
  });
});

describe("laneContent — what the lane beside him shows", () => {
  const now = Date.parse("2026-09-28T22:52:00Z");
  const said = (secondsAgo: number): SpokenLine => ({ id: "l", text: "Hi.", at: new Date(now - secondsAgo * 1000).toISOString(), aloud: false });
  it("a fresh line wins", () => assert.equal(laneContent({ current: said(10), thinking: false, fold: false, nowMs: now }), "speech"));
  it("a line said in the middle of a turn shows first, then his thinking takes the lane back", () => {
    assert.equal(laneContent({ current: said(10), thinking: true, fold: false, nowMs: now }), "speech");
    assert.equal(laneContent({ current: said(90), thinking: true, fold: false, nowMs: now }), "thinking");
  });
  it("thinking while a turn runs", () => assert.equal(laneContent({ current: null, thinking: true, fold: true, nowMs: now }), "thinking"));
  it("the earlier line when nothing else", () => assert.equal(laneContent({ current: null, thinking: false, fold: true, nowMs: now }), "fold"));
  it("otherwise nothing", () => assert.equal(laneContent({ current: null, thinking: false, fold: false, nowMs: now }), null));
  it("his mind drifts when nothing is live and nothing just folded", () => {
    assert.equal(laneContent({ current: null, thinking: false, fold: false, mind: true, nowMs: now }), "mind");
    assert.equal(laneContent({ current: null, thinking: false, fold: true, mind: true, nowMs: now }), "mind", "his mind (a just-thought included) before the footnote");
    assert.equal(laneContent({ current: null, thinking: true, fold: false, mind: true, nowMs: now }), "thinking", "live wins");
    assert.equal(laneContent({ current: said(10), thinking: false, fold: false, mind: true, nowMs: now }), "speech", "his words win");
  });
});

describe("receiptLeft — the receipt stays bottom-left, clear of him", () => {
  it("one cell in from the edge", () => {
    assert.equal(receiptLeft({ at: "on", rect: { x: 5, y: 3, w: 3, h: 4 }, on: "t", side: "left", look: null }), 1);
    assert.equal(receiptLeft({ at: "floor", rect: { x: 16, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null }), 1);
  });
  it("past him when he stands at the bottom-left", () => {
    assert.equal(receiptLeft({ at: "floor", rect: { x: 4, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null }), 9);
    assert.equal(receiptLeft({ at: "corner", rect: { x: 0, y: 9, w: 2, h: 3 }, on: null, side: "left", look: null }), 3);
  });
});

describe("stageStatusTone — the dot beside his line matches the line", () => {
  const p = (over = {}) => ({ known: true, mood: "attention" as const, tone: "warn" as const, handsDown: false, ...over });
  it("thinking or speaking is his light, even with something waiting", () => {
    assert.equal(stageStatusTone(p(), { thinking: true, speaking: false }), "ok");
    assert.equal(stageStatusTone(p(), { thinking: false, speaking: true }), "ok");
  });
  it("amber while his hands are down", () => {
    assert.equal(stageStatusTone(p({ handsDown: true }), { thinking: true, speaking: false }), "warn");
  });
  it("offline stays grey", () => {
    assert.equal(stageStatusTone(p({ mood: "offline", tone: "mute" }), { thinking: true, speaking: false }), "mute");
  });
  it("otherwise his presence's own tone", () => {
    assert.equal(stageStatusTone(p(), { thinking: false, speaking: false }), "warn");
  });
});

describe("speechRect is not greedy", () => {
  it("one short line takes two rows above his head, not the lane", () => {
    const r = speechRect({ x: 1, y: 1, w: 20, h: 10 }, { x: 22, y: 9, w: 2, h: 3 }, 36, 0);
    assert.equal(r.h, 2);
  });
  it("a two-line line with one before it takes three", () => {
    const r = speechRect({ x: 7, y: 1, w: 16, h: 10 }, { x: 2, y: 7, w: 4, h: 5 }, 63, 57);
    assert.equal(r.h, 3);
    assert.equal(r.y + r.h, 9);
  });
});

describe("statusSideFor — standing on a piece, his line goes beside him", () => {
  const G = { cols: 24, rows: 12 };
  it("away from his lane when there is room", () => {
    assert.equal(statusSideFor({ x: 8, y: 4, w: 3, h: 4 }, "right", G), "left");
    assert.equal(statusSideFor({ x: 12, y: 4, w: 3, h: 4 }, "left", G), "right");
  });
  it("toward the lane when the other side would run off the screen", () => {
    assert.equal(statusSideFor({ x: 1, y: 4, w: 3, h: 4 }, "right", G), "right");
    assert.equal(statusSideFor({ x: 21, y: 4, w: 3, h: 4 }, "left", G), "left");
  });
});

describe("clipAbove — the lane stays clear of the compose bar", () => {
  it("a lane over the bar's band ends above it", () => {
    assert.deepEqual(clipAbove({ x: 9, y: 1, w: 14, h: 10 }, COMPOSE_RECT), { x: 9, y: 1, w: 14, h: 9 });
  });
  it("a lane beside the band is untouched", () => {
    assert.deepEqual(clipAbove({ x: 21, y: 1, w: 2, h: 10 }, COMPOSE_RECT), { x: 21, y: 1, w: 2, h: 10 });
    assert.deepEqual(clipAbove({ x: 9, y: 1, w: 14, h: 8 }, COMPOSE_RECT), { x: 9, y: 1, w: 14, h: 8 });
  });
  it("never less than a row", () => {
    assert.equal(clipAbove({ x: 9, y: 10, w: 14, h: 2 }, COMPOSE_RECT).h, 1);
  });
});

describe("mindRect — where a thought on his mind sits in the lane", () => {
  const me = { x: 4, y: 7, w: 4, h: 5 };
  it("beside his head, in the free run of the lane next to him, so it fades nothing", () => {
    const pieces = [piece("cal", 16, 1, 7, 6), piece("pets", 17, 7, 6, 5)];
    const lane = laneFor(me, pieces, STAGE).rect;
    const r = mindRect(lane, me, pieces, 120);
    assert.equal(r.x, lane.x, "it starts beside him");
    assert.ok(r.x + r.w <= 16, `it stops before the calendar and the pets: ${JSON.stringify(r)}`);
    assert.ok(r.y + r.h <= me.y + 3 && r.y + r.h >= me.y + 1, `about his head: ${JSON.stringify(r)}`);
    for (const p of pieces) assert.equal(dimmed(p.rect, r), false, p.id);
  });
  it("a longer thought takes more rows", () => {
    const lane = laneFor(me, [], STAGE).rect;
    const short = mindRect(lane, me, [], 40);
    const long = mindRect(lane, me, [], 220);
    assert.ok(long.h > short.h, `${short.h} < ${long.h}`);
  });
  it("with the lane on his left it grows leftward from beside him", () => {
    const right = { x: 14, y: 7, w: 4, h: 5 };
    const lane = laneFor(right, [piece("far", 21, 1, 2, 2)], STAGE);
    assert.equal(lane.side, "left");
    const r = mindRect(lane.rect, right, [piece("far", 21, 1, 2, 2)], 120);
    assert.equal(r.x + r.w, lane.rect.x + lane.rect.w, "it ends beside him");
  });
  it("a piece beside his head: the thought rises to the free room above it rather than fade it", () => {
    const at10 = { x: 10, y: 7, w: 4, h: 5 };
    const pieces = [piece("title", 1, 1, 14, 2), piece("note", 1, 7, 9, 4), piece("list", 15, 6, 8, 5), piece("stat", 17, 1, 6, 3)];
    const lane = laneFor(at10, pieces, STAGE).rect;
    const r = mindRect(lane, at10, pieces, 90);
    for (const p of pieces) assert.equal(dimmed(p.rect, r), false, `${p.id}: ${JSON.stringify(r)}`);
    assert.ok(r.w >= 6, JSON.stringify(r));
  });

  it("a narrow gap beside his head still holds a thought, wrapped narrower, rather than laid over a picture", () => {
    // The harness's own scene: he stands on a note; a photo top-right and a list bottom-right leave a five-cell gap.
    const on = { x: 6, y: 4, w: 3, h: 4 };
    const pieces = [piece("title", 1, 1, 14, 2), piece("photo", 15, 1, 8, 5, "image"), piece("thought", 1, 8, 9, 3), piece("later", 15, 7, 8, 4, "list")];
    const lane = laneFor(on, pieces, STAGE).rect;
    const r = mindRect(lane, on, pieces, 88);
    for (const p of pieces) assert.equal(dimmed(p.rect, r), false, `${p.id}: ${JSON.stringify(r)}`);
  });

  it("hemmed in by pieces, it takes the lane's width and fades what it crosses", () => {
    const pieces = [piece("tight", 10, 4, 6, 6)];
    const lane = laneFor(me, pieces, STAGE).rect;
    const r = mindRect(lane, me, pieces, 120);
    assert.ok(r.w >= 6, JSON.stringify(r));
  });
});

describe("coveredCells — what the compose bar and its reply card cover, in stage cells", () => {
  const stageBox = { left: 0, top: 0, width: 1440, height: 810 };
  it("rounds outward to whole cells and runs to the bottom of the stage", () => {
    // The card measured at 379..1061 px wide, 372 px down; the bar below it.
    assert.deepEqual(coveredCells({ left: 379, top: 372, right: 1061, bottom: 775 }, stageBox, STAGE), { x: 6, y: 5, w: 12, h: 7 });
  });
  it("nothing measured (no layout yet) is nothing", () => {
    assert.equal(coveredCells({ left: 0, top: 0, right: 0, bottom: 0 }, stageBox, STAGE), null);
    assert.equal(coveredCells({ left: 10, top: 10, right: 20, bottom: 20 }, { left: 0, top: 0, width: 0, height: 0 }, STAGE), null);
  });
});
