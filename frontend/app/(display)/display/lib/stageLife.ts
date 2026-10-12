/** Edward's idle life on the stage (2026-09-28) — pure, tested in __tests__/stageLife.test.ts.
 *
 * The model decides what is on the stage and where he stands; the page
 * keeps him alive between scenes, at no quota: every 8–24 s a glance at a
 * piece or at the owner, or — on the floor — a one- or two-cell step inside the
 * free run of his floor, on the side away from his lane and the why receipt
 * (walkSpan). A piece that just appeared or changed always earns
 * a glance. While he thinks or speaks he only looks at his lane.
 *
 * Round 2 (2026-09-28): now and then (every 1–4 min) he wanders somewhere
 * else entirely — any free spot along the floor band, clear of pieces, with
 * room for his lane beside him and clear of the why receipt; the page lays
 * his lane out again from there. A clear floor is a walk; a piece in the way
 * (or reduced motion) is a fade across.
 *
 * This is motion, never a claim: his mood comes from real signals
 * (lib/screenPresence.ts), not from here. `rand` is injected for tests.
 */

import type { Gaze } from "@/lib/edwardMood";

import { laneFor, receiptLeft, type StageGrid, type StageMe, type StagePiece, type StageRect } from "./stageModel";

export type Rand = () => number;

export const LIFE_MIN_MS = 8_000;
export const LIFE_MAX_MS = 24_000;
/** How often an idle beat on the floor is a step rather than a glance. */
export const STEP_CHANCE = 0.35;
/** How long a glance holds. */
export const GLANCE_HOLD_MS = 1_600;

export function nextLifeDelayMs(rand: Rand = Math.random): number {
  return Math.floor(LIFE_MIN_MS + rand() * (LIFE_MAX_MS - LIFE_MIN_MS));
}

/** Which way `to` is from `from` (the dominant axis; stage cells are about square). */
export function gazeBetween(from: StageRect, to: StageRect): Gaze | null {
  const dx = to.x + to.w / 2 - (from.x + from.w / 2);
  const dy = to.y + to.h / 2 - (from.y + from.h / 2);
  if (dx === 0 && dy === 0) return null;
  if (Math.abs(dx) >= Math.abs(dy)) return dx < 0 ? "left" : "right";
  return dy < 0 ? "up" : "down";
}

function overlaps(a: StageRect, b: StageRect): boolean {
  return a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;
}

/** The free run of x positions for his floor box around where he stands (null off the floor). */
export function floorSpan(
  me: StageMe,
  pieces: Pick<StagePiece, "rect">[],
  stage: StageGrid,
): { min: number; max: number } | null {
  if (me.at !== "floor") return null;
  const free = (x: number) => !pieces.some((p) => overlaps({ ...me.rect, x }, p.rect));
  const last = Math.max(0, stage.cols - me.rect.w);
  const start = Math.min(Math.max(0, me.rect.x), last);
  if (!free(start)) return { min: start, max: start };
  let min = start;
  let max = start;
  while (min - 1 >= 0 && free(min - 1)) min -= 1;
  while (max + 1 <= last && free(max + 1)) max += 1;
  return { min, max };
}

/**
 * Where an idle step may take him: his free floor run, but never toward his
 * lane or the why receipt. The lane, the joint, the speech box and the
 * receipt are all laid out from where the scene put him, so a walk that way
 * would put him on his own words; he wanders on the other side of his spot.
 */
export function walkSpan(
  me: StageMe,
  pieces: Pick<StagePiece, "rect">[],
  stage: StageGrid,
): { min: number; max: number } | null {
  const span = floorSpan(me, pieces, stage);
  if (!span) return null;
  const home = me.rect.x;
  let { min, max } = span;
  const lane = laneFor(me.rect, pieces, stage);
  if (lane.side === "right") max = Math.min(max, home);
  else min = Math.max(min, home);
  if (receiptLeft(me) > home) max = Math.min(max, home);
  return { min: Math.min(min, home), max: Math.max(max, home) };
}

export type LifeAction =
  | { kind: "glance"; gaze: Gaze | null; target: string }
  | { kind: "step"; x: number };

export interface LifeInputs {
  me: StageMe;
  /** Where he stands now (a step moves him within his floor span). */
  x: number;
  pieces: StagePiece[];
  stage: StageGrid;
  /** A turn is running or he is speaking: he looks at his lane. */
  busy: boolean;
  laneSide: "left" | "right" | null;
}

export function pickLifeAction(inp: LifeInputs, rand: Rand = Math.random): LifeAction {
  if (inp.busy) return { kind: "glance", gaze: inp.laneSide, target: "lane" };
  const here: StageRect = { ...inp.me.rect, x: inp.x };

  if (inp.me.at === "floor" && rand() < STEP_CHANCE) {
    const span = walkSpan(inp.me, inp.pieces, inp.stage);
    const dir = rand() < 0.5 ? -1 : 1;
    const size = rand() < 0.5 ? 1 : 2;
    if (span) {
      for (const d of [dir, -dir]) {
        const x = Math.min(span.max, Math.max(span.min, inp.x + d * size));
        if (x !== inp.x) return { kind: "step", x };
      }
    }
  } else if (inp.me.at !== "floor") {
    rand(); // keep one draw per decision, wherever he stands
  }

  const targets: (StagePiece | "owner")[] = [...inp.pieces, "owner"];
  const pick = targets[Math.min(targets.length - 1, Math.floor(rand() * targets.length))];
  if (pick === "owner") return { kind: "glance", gaze: null, target: "owner" };
  return { kind: "glance", gaze: gazeBetween(here, pick.rect), target: pick.id };
}

/** What makes a piece "changed": its content and its place, not its id. */
function signature(p: StagePiece): string {
  return JSON.stringify([
    p.kind, p.rect, p.title ?? null, p.text ?? null, p.body ?? null, p.items ?? null, p.value ?? null,
    p.detail ?? null, p.target ?? null, p.started_at ?? null, p.ends_at ?? null, p.source ?? null,
  ]);
}

/**
 * A piece that just appeared or changed since the last read earns a glance.
 * Never on the first read: arriving is not news.
 */
export function glanceForChange(
  me: StageRect,
  prev: StagePiece[] | null,
  next: StagePiece[],
): { gaze: Gaze | null; target: string } | null {
  if (!prev) return null;
  const before = new Map(prev.map((p) => [p.id, signature(p)]));
  const changed = next.find((p) => before.get(p.id) !== signature(p));
  if (!changed) return null;
  return { gaze: gazeBetween(me, changed.rect), target: changed.id };
}

// ---------------------------------------------------------------------------
// Wandering (round 2)
// ---------------------------------------------------------------------------

export const WANDER_MIN_MS = 60_000;
export const WANDER_MAX_MS = 240_000;
/** A wander is a real walk away, not another step. */
export const WANDER_MIN_CELLS = 3;
/** His lane needs this much room beside wherever he goes. */
const WANDER_LANE_CELLS = 5;

export function nextWanderDelayMs(rand: Rand): number {
  return Math.floor(WANDER_MIN_MS + rand() * (WANDER_MAX_MS - WANDER_MIN_MS));
}

/**
 * A free spot along the floor band to wander to, at random, or null (off the
 * floor, or nowhere to go). `x` is where he stands now; `receiptCells` is how
 * long the why receipt runs from the left edge (0 when there is none).
 */
export function pickWanderSpot(
  { me, x, pieces, stage, receiptCells }: { me: StageMe; x: number; pieces: Pick<StagePiece, "rect">[]; stage: StageGrid; receiptCells: number },
  rand: Rand,
): number | null {
  if (me.at !== "floor") return null;
  const spots: number[] = [];
  for (let at = 0; at + me.rect.w <= stage.cols; at++) {
    if (Math.abs(at - x) < WANDER_MIN_CELLS) continue;
    const box: StageRect = { ...me.rect, x: at };
    if (pieces.some((p) => overlaps(box, p.rect))) continue;
    if (laneFor(box, pieces, stage).rect.w < WANDER_LANE_CELLS) continue;
    // The receipt steps past him when he stands bottom-left; elsewhere it runs from the left edge.
    if (receiptCells > 0 && receiptLeft({ ...me, rect: box }) <= at && at < 1 + receiptCells + 1) continue;
    spots.push(at);
  }
  if (!spots.length) return null;
  return spots[Math.min(spots.length - 1, Math.floor(rand() * spots.length))];
}

/** A clear floor between here and there is a walk; a piece in the way is a fade across. */
export function walkKind(me: StageMe, from: number, to: number, pieces: Pick<StagePiece, "rect">[]): "walk" | "fade" {
  const path: StageRect = { ...me.rect, x: Math.min(from, to), w: Math.abs(to - from) + me.rect.w };
  return pieces.some((p) => overlaps(path, p.rect)) ? "fade" : "walk";
}

/** How long the walk takes: about a third of a second a cell, one to four seconds. */
export function walkMs(from: number, to: number): number {
  return Math.min(4_000, Math.max(1_000, Math.abs(to - from) * 320));
}

// ---------------------------------------------------------------------------
// Out of the compose bar's way (round 2)
// ---------------------------------------------------------------------------

/** Nearest first: `from`, then one cell either side, two, … (left before right). */
function nearest(from: number, last: number): number[] {
  const out: number[] = [];
  for (let d = 0; d <= last; d++) {
    for (const x of d === 0 ? [from] : [from - d, from + d]) if (x >= 0 && x <= last) out.push(x);
  }
  return out;
}

/**
 * Where he stands while the compose bar (and a reply card on it) covers
 * `band`: null when it does not cover him. On the floor he steps aside along
 * it to the nearest spot clear of the bar and the pieces; with no room there
 * (or off the floor) he stands up on the bar — his feet on its top edge — at
 * the nearest spot clear of the pieces, else where he is. The owner at the desk
 * raises the bar whenever they move the mouse; it must never cover Edward.
 */
export function clearOfBand(
  me: StageMe,
  band: StageRect,
  pieces: Pick<StagePiece, "rect">[],
  stage: StageGrid,
): StageRect | null {
  if (!overlaps(me.rect, band)) return null;
  const last = Math.max(0, stage.cols - me.rect.w);
  const from = Math.min(Math.max(0, me.rect.x), last);
  const clear = (box: StageRect) => !overlaps(box, band) && !pieces.some((p) => overlaps(box, p.rect));
  if (me.at === "floor") {
    for (const x of nearest(from, last)) {
      const box = { ...me.rect, x };
      if (clear(box)) return box;
    }
  }
  const y = Math.max(0, band.y - me.rect.h);
  if (me.at === "floor") {
    for (const x of nearest(from, last)) {
      const box = { ...me.rect, x, y };
      if (clear(box)) return box;
    }
  }
  return { ...me.rect, x: from, y };
}
