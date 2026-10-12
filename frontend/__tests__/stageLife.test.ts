// Edward's idle life on the stage (2026-09-28): glances and small steps,
// random on the page and costing no quota. Deterministic with an injected
// rand. Motion only — never a claim: his mood still comes from real signals.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  LIFE_MAX_MS,
  LIFE_MIN_MS,
  WANDER_MAX_MS,
  WANDER_MIN_CELLS,
  WANDER_MIN_MS,
  clearOfBand,
  floorSpan,
  nextWanderDelayMs,
  pickWanderSpot,
  walkKind,
  walkMs,
  gazeBetween,
  glanceForChange,
  nextLifeDelayMs,
  pickLifeAction,
} from "../app/(display)/display/lib/stageLife.ts";
import { COMPOSE_RECT, FALLBACK_SCENE, laneFor, receiptLeft, type StageMe, type StagePiece } from "../app/(display)/display/lib/stageModel.ts";

const STAGE = { cols: 24, rows: 12 };
const piece = (id: string, x: number, y: number, w: number, h: number, extra: Partial<StagePiece> = {}): StagePiece => ({
  id, kind: "note", size: "m", enter: "fade", place: "center", rect: { x, y, w, h }, title: null, ...extra,
});
const floor = (x = 4): StageMe => ({ at: "floor", rect: { x, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null });

/** A rand that returns the given values in turn, then repeats the last. */
function seq(...values: number[]): () => number {
  let i = 0;
  return () => values[Math.min(i++, values.length - 1)];
}

describe("nextLifeDelayMs", () => {
  it("every 8 to 24 seconds", () => {
    assert.equal(LIFE_MIN_MS, 8_000);
    assert.equal(LIFE_MAX_MS, 24_000);
    assert.equal(nextLifeDelayMs(() => 0), 8_000);
    assert.equal(nextLifeDelayMs(() => 0.999999), 23_999);
  });
});

describe("gazeBetween", () => {
  const me = { x: 4, y: 7, w: 4, h: 5 };
  it("the dominant axis wins", () => {
    assert.equal(gazeBetween(me, { x: 16, y: 7, w: 6, h: 4 }), "right");
    assert.equal(gazeBetween(me, { x: 0, y: 8, w: 2, h: 2 }), "left");
    assert.equal(gazeBetween(me, { x: 4, y: 0, w: 4, h: 3 }), "up");
  });
  it("the same place is no direction", () => assert.equal(gazeBetween(me, me), null));
});

describe("floorSpan", () => {
  it("the free run of his floor around him", () => {
    const span = floorSpan(floor(4), [piece("a", 0, 8, 2, 3), piece("b", 14, 6, 6, 6)], STAGE);
    assert.deepEqual(span, { min: 2, max: 10 });
  });
  it("only on the floor", () => {
    assert.equal(floorSpan({ ...floor(), at: "corner" }, [], STAGE), null);
  });
});

describe("pickLifeAction", () => {
  const pieces = [piece("later", 16, 7, 7, 4), piece("title", 1, 1, 16, 3)];

  it("while thinking or speaking he looks at his lane and nothing else", () => {
    const a = pickLifeAction({ me: floor(), x: 4, pieces, stage: STAGE, busy: true, laneSide: "right" }, seq(0, 0));
    assert.deepEqual(a, { kind: "glance", gaze: "right", target: "lane" });
  });

  it("on the floor he sometimes takes a one- or two-cell step inside his free span", () => {
    const a = pickLifeAction({ me: floor(), x: 4, pieces, stage: STAGE, busy: false, laneSide: "right" }, seq(0.1, 0.9, 0.9));
    assert.equal(a.kind, "step");
    if (a.kind === "step") {
      assert.ok(Math.abs(a.x - 4) >= 1 && Math.abs(a.x - 4) <= 2, String(a.x));
      const span = floorSpan(floor(), pieces, STAGE)!;
      assert.ok(a.x >= span.min && a.x <= span.max);
    }
  });

  it("a step never leaves the free span", () => {
    const tight = [piece("l", 0, 7, 4, 5), piece("r", 9, 7, 4, 5)];
    for (const r of [0, 0.25, 0.5, 0.75, 0.99]) {
      const a = pickLifeAction({ me: floor(4), x: 5, pieces: tight, stage: STAGE, busy: false, laneSide: "right" }, seq(0.1, r, r));
      if (a.kind === "step") assert.ok(a.x >= 4 && a.x <= 5, `x=${a.x}`);
    }
  });

  it("otherwise a glance at a piece or at Ellis", () => {
    const atPiece = pickLifeAction({ me: floor(), x: 4, pieces, stage: STAGE, busy: false, laneSide: "right" }, seq(0.9, 0));
    assert.deepEqual(atPiece, { kind: "glance", gaze: "right", target: "later" });
    const atOwner = pickLifeAction({ me: floor(), x: 4, pieces, stage: STAGE, busy: false, laneSide: "right" }, seq(0.9, 0.99));
    assert.deepEqual(atOwner, { kind: "glance", gaze: null, target: "owner" });
  });

  it("not on the floor, he never steps", () => {
    const me: StageMe = { at: "on", rect: { x: 5, y: 3, w: 3, h: 4 }, on: "title", side: "left", look: null };
    for (const r of [0, 0.1, 0.3]) {
      assert.notEqual(pickLifeAction({ me, x: 5, pieces, stage: STAGE, busy: false, laneSide: "right" }, seq(r, 0)).kind, "step");
    }
  });
});

describe("his walk stays clear of his lane and the why receipt", () => {
  // The lane, the joint, the speech box and the receipt are laid out from
  // where the scene put him; a walk into them would put him on his own words.
  const overlaps = (a: { x: number; w: number }, b: { x: number; w: number }) => a.x < b.x + b.w && b.x < a.x + a.w;
  const mulberry = (seed: number) => () => {
    seed = (seed + 0x6d2b79f5) | 0;
    let t = Math.imul(seed ^ (seed >>> 15), 1 | seed);
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t;
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
  const walk = (me: StageMe, pieces: StagePiece[], seed: number) => {
    const rand = mulberry(seed);
    let x = me.rect.x;
    const xs: number[] = [];
    for (let i = 0; i < 300; i++) {
      const a = pickLifeAction({ me, x, pieces, stage: STAGE, busy: false, laneSide: null }, rand);
      if (a.kind === "step") x = a.x;
      xs.push(x);
    }
    return xs;
  };

  it("the stock scene: an hour of idle life never walks him into the lane or over the receipt", () => {
    const { me, pieces } = FALLBACK_SCENE;
    const lane = laneFor(me.rect, pieces, STAGE).rect;
    const receipt = receiptLeft(me);
    for (const seed of [1, 2, 3, 4, 5]) {
      const xs = walk(me, pieces, seed);
      assert.ok(xs.some((x) => x !== me.rect.x), "he still takes steps");
      for (const x of xs) {
        const box = { x, w: me.rect.w };
        assert.ok(!overlaps(box, { x: lane.x - 1, w: lane.w + 2 }), `seed ${seed}: x=${x} is in his lane (${lane.x}..${lane.x + lane.w})`);
        if (receipt > me.rect.x) assert.ok(x + me.rect.w < receipt, `seed ${seed}: x=${x} runs over the receipt at ${receipt}`);
      }
    }
  });

  it("with the lane on his left he walks only right, and never into the lane", () => {
    const me = floor(14);
    const pieces = [piece("far", 21, 1, 2, 2)];
    const lane = laneFor(me.rect, pieces, STAGE);
    assert.equal(lane.side, "left");
    for (const seed of [7, 8, 9]) {
      for (const x of walk(me, pieces, seed)) {
        assert.ok(x >= me.rect.x, `x=${x}`);
        assert.ok(!overlaps({ x, w: me.rect.w }, { x: lane.rect.x - 1, w: lane.rect.w + 2 }), `x=${x}`);
      }
    }
  });
});

describe("glanceForChange", () => {
  const me = floor().rect;
  it("never on the first read (arriving is not news)", () => {
    assert.equal(glanceForChange(me, null, [piece("a", 16, 1, 6, 4)]), null);
  });
  it("a piece that just appeared", () => {
    const g = glanceForChange(me, [piece("a", 1, 1, 5, 2)], [piece("a", 1, 1, 5, 2), piece("b", 16, 1, 6, 4)]);
    assert.deepEqual(g, { gaze: "right", target: "b" });
  });
  it("a piece whose words changed", () => {
    const g = glanceForChange(me, [piece("a", 4, 0, 5, 2, { body: "one" })], [piece("a", 4, 0, 5, 2, { body: "two" })]);
    assert.deepEqual(g, { gaze: "up", target: "a" });
  });
  it("nothing changed, no glance", () => {
    assert.equal(glanceForChange(me, [piece("a", 1, 1, 5, 2)], [piece("a", 1, 1, 5, 2)]), null);
  });
});

// Round 2 (2026-09-28): now and then he wanders somewhere else entirely —
// any free spot along the floor band, clear of pieces, with room for his
// lane beside him and clear of the why receipt. The lane is laid out again
// from where he ends up.
describe("wandering", () => {
  const overlaps = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) =>
    a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

  it("every one to four minutes, at random", () => {
    assert.equal(WANDER_MIN_MS, 60_000);
    assert.equal(WANDER_MAX_MS, 240_000);
    assert.equal(nextWanderDelayMs(() => 0), 60_000);
    assert.equal(nextWanderDelayMs(() => 0.999999), 239_999);
  });

  it("to a free spot on the floor band, a real walk away, with room for his lane and clear of the receipt", () => {
    const { me, pieces } = FALLBACK_SCENE;
    const seen = new Set<number>();
    for (let i = 0; i < 40; i++) {
      const x = pickWanderSpot({ me, x: me.rect.x, pieces, stage: STAGE, receiptCells: 12 }, () => i / 40);
      assert.ok(x !== null);
      seen.add(x!);
      const box = { ...me.rect, x: x! };
      assert.ok(Math.abs(x! - me.rect.x) >= WANDER_MIN_CELLS, `x=${x} is barely a step`);
      assert.ok(x! >= 0 && x! + me.rect.w <= STAGE.cols, `x=${x} is off the stage`);
      for (const p of pieces) assert.ok(!overlaps(box, p.rect), `x=${x} stands on ${p.id}`);
      assert.ok(laneFor(box, pieces, STAGE).rect.w >= 5, `x=${x} leaves no room for his lane`);
      const r = receiptLeft({ ...me, rect: box });
      if (r <= x!) assert.ok(x! >= 1 + 12 + 1, `x=${x} stands on the receipt`);
    }
    assert.ok(seen.size >= 3, `anywhere, not one spot: ${[...seen]}`);
  });

  it("only from the floor, and nowhere when there is no free spot", () => {
    const on: StageMe = { at: "on", rect: { x: 5, y: 3, w: 3, h: 4 }, on: "t", side: "left", look: null };
    assert.equal(pickWanderSpot({ me: on, x: 5, pieces: [], stage: STAGE, receiptCells: 0 }, () => 0.5), null);
    const wall = [piece("l", 0, 6, 11, 6), piece("r", 13, 6, 11, 6)];
    assert.equal(pickWanderSpot({ me: floor(9), x: 9, pieces: wall, stage: STAGE, receiptCells: 0 }, () => 0.5), null);
  });

  it("walks when the way is clear; fades across when a piece is in the way", () => {
    assert.equal(walkKind(floor(4), 4, 12, [piece("top", 4, 1, 10, 3)]), "walk");
    assert.equal(walkKind(floor(4), 4, 16, [piece("mid", 9, 8, 3, 3)]), "fade");
  });

  it("a longer walk takes longer, one to four seconds", () => {
    assert.ok(walkMs(4, 7) < walkMs(4, 16));
    assert.ok(walkMs(4, 5) >= 1_000);
    assert.ok(walkMs(0, 20) <= 4_000);
  });
});

describe("clear of the compose bar", () => {
  const overlaps = (a: { x: number; y: number; w: number; h: number }, b: { x: number; y: number; w: number; h: number }) =>
    a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
  // The bar as measured on a 1440 px mini: min(880 px, 70%) centred, run to the stage's bottom.
  const BAR = { x: 4, y: 10, w: 16, h: 2 };

  it("where the bar is not, he stays", () => {
    assert.equal(clearOfBand(floor(4), { x: 8, y: 10, w: 8, h: 2 }, [], STAGE), null);
    const top: StageMe = { at: "on", rect: { x: 5, y: 1, w: 3, h: 4 }, on: "t", side: "left", look: null };
    assert.equal(clearOfBand(top, COMPOSE_RECT, [], STAGE), null);
  });

  it("on the floor he steps aside along it to the nearest spot clear of the bar and the pieces", () => {
    assert.deepEqual(clearOfBand(floor(4), BAR, [], STAGE), { x: 0, y: 7, w: 4, h: 5 });
    assert.deepEqual(clearOfBand(floor(15), BAR, [], STAGE), { x: 20, y: 7, w: 4, h: 5 });
    assert.deepEqual(clearOfBand(floor(4), BAR, [piece("left", 0, 6, 3, 6)], STAGE), { x: 20, y: 7, w: 4, h: 5 });
  });

  it("with no room beside it, he stands up on the bar instead — clear of the pieces there when he can be", () => {
    assert.deepEqual(clearOfBand(floor(4), COMPOSE_RECT, [], STAGE), { x: 4, y: 5, w: 4, h: 5 });
    assert.deepEqual(clearOfBand(floor(4), COMPOSE_RECT, [piece("n", 2, 4, 7, 3)], STAGE), { x: 9, y: 5, w: 4, h: 5 });
    const corner: StageMe = { at: "corner", rect: { x: 20, y: 8, w: 4, h: 4 }, on: null, side: "right", look: null };
    assert.deepEqual(clearOfBand(corner, COMPOSE_RECT, [], STAGE), { x: 20, y: 6, w: 4, h: 4 });
  });

  it("wherever he stood on the floor, the bar never covers him", () => {
    for (const band of [COMPOSE_RECT, BAR, { x: 5, y: 5, w: 14, h: 7 }]) {
      for (let x = 0; x + 4 <= STAGE.cols; x++) {
        const box = clearOfBand(floor(x), band, FALLBACK_SCENE.pieces, STAGE) ?? floor(x).rect;
        assert.equal(overlaps(box, band), false, `x=${x} band=${JSON.stringify(band)} → ${JSON.stringify(box)}`);
      }
    }
  });
});
