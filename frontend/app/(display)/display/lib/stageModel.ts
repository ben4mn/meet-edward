/** The stage on /screen (2026-09-28) — pure, tested in __tests__/stageModel.test.ts.
 *
 * The screen is a stage Edward writes on, not a grid he fills: one scene of
 * freely placed pieces, where he stands, the lines he says, and his thinking
 * while a turn really runs. The backend places everything on a 24×12 stage
 * (services/screen_service.py::place_scene); this side turns cells into
 * percentages, picks the side of him his words and thoughts go (the lane),
 * fades what the lane crosses, chooses which lines are still worth showing,
 * and knows when it is night — none of it a model call. (Round 2: the owner at the
 * desk gets the normal stage; they often watch it.)
 */

import { formatClock, parseIso } from "@/lib/time";
import type { Mood } from "@/lib/edwardMood";

import type { HubBoard } from "../../hub/lib/types";
import type { ListItem, ShowContent } from "./screenModel";

// ---------------------------------------------------------------------------
// The snapshot's shapes (GET /api/screen, spec §6)
// ---------------------------------------------------------------------------

export interface StageGrid {
  cols: number;
  rows: number;
}

/** A box in stage cells: top-left x/y, width and height. */
export interface StageRect {
  x: number;
  y: number;
  w: number;
  h: number;
}

export type AuthoredKind = "words" | "note" | "list" | "stat" | "countdown" | "timer" | "image" | "video" | "html";
export type LiveKind =
  | "clock" | "calendar" | "pets" | "alerts" | "heartbeat" | "recents" | "workers" | "vitals" | "memory_ticker";
export type PieceKind = AuthoredKind | LiveKind;
export type PieceSize = "xs" | "s" | "m" | "l" | "xl";
export type PieceEnter = "fade" | "rise" | "type";

export interface StagePiece {
  id: string;
  kind: PieceKind;
  size: PieceSize;
  enter: PieceEnter;
  place: string | { x: number; y: number; w?: number; h?: number };
  rect: StageRect;
  title?: string | null;
  /** words */
  text?: string;
  /** note, html */
  body?: string;
  /** list */
  items?: ListItem[] | unknown;
  /** stat */
  value?: string;
  detail?: string | null;
  /** countdown (ISO) */
  target?: string;
  /** timer (aware ISO) */
  started_at?: string;
  ends_at?: string;
  /** image, video */
  source?: string;
  embed_url?: string | null;
}

export type MeAt = "floor" | "on" | "corner" | "edge";

export interface StageMe {
  at: MeAt;
  rect: StageRect;
  /** The piece he stands on (at "on"). */
  on: string | null;
  side: "left" | "right";
  /** A piece id, "owner", or nothing in particular. */
  look: string | null;
}

export interface StageScene {
  pieces: StagePiece[];
  me: StageMe;
  why: string;
  source: "edward" | "owner" | "default";
  updated_at: string | null;
  updated_by: string | null;
}

export interface SpokenLine {
  id: string;
  text: string;
  at: string;
  aloud: boolean;
}

export interface AskedHold {
  at: string;
  until: string;
}

/** The stage half of the /api/screen snapshot. */
export interface StageSnapshot {
  stage: StageGrid;
  scene: StageScene;
  asked: AskedHold | null;
  lines: SpokenLine[];
  owner_at_mini: boolean;
  show: ShowContent | null;
  now: string;
}

// ---------------------------------------------------------------------------
// The stock scene, for a failed first fetch (mirrors screen_service.default_scene())
// ---------------------------------------------------------------------------

export const FALLBACK_STAGE: StageGrid = { cols: 24, rows: 12 };

export const FALLBACK_SCENE: StageScene = {
  pieces: [
    { id: "p1", kind: "clock", size: "s", enter: "fade", place: "top-left", rect: { x: 1, y: 1, w: 5, h: 2 }, title: null },
    { id: "p2", kind: "calendar", size: "m", enter: "fade", place: "top-right", rect: { x: 16, y: 1, w: 7, h: 6 }, title: null },
    { id: "p3", kind: "pets", size: "s", enter: "fade", place: "bottom-right", rect: { x: 17, y: 7, w: 6, h: 5 }, title: null },
  ],
  me: { at: "floor", rect: { x: 4, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null },
  why: "",
  source: "default",
  updated_at: null,
  updated_by: null,
};

// ---------------------------------------------------------------------------
// Cells → the page
// ---------------------------------------------------------------------------

function pct(n: number): string {
  return `${Number(n.toFixed(4))}%`;
}

function safeGrid(stage: StageGrid | null | undefined): StageGrid {
  const cols = stage && stage.cols > 0 ? stage.cols : FALLBACK_STAGE.cols;
  const rows = stage && stage.rows > 0 ? stage.rows : FALLBACK_STAGE.rows;
  return { cols, rows };
}

export interface BoxStyle {
  left: string;
  top: string;
  width: string;
  height: string;
}

/** A cell box as a percentage box of the stage. */
export function rectStyle(rect: StageRect, stage: StageGrid): BoxStyle {
  const { cols, rows } = safeGrid(stage);
  return {
    left: pct((rect.x / cols) * 100),
    top: pct((rect.y / rows) * 100),
    width: pct((rect.w / cols) * 100),
    height: pct((rect.h / rows) * 100),
  };
}

function overlapArea(a: StageRect, b: StageRect): number {
  const w = Math.min(a.x + a.w, b.x + b.w) - Math.max(a.x, b.x);
  const h = Math.min(a.y + a.h, b.y + b.h) - Math.max(a.y, b.y);
  return w > 0 && h > 0 ? w * h : 0;
}

// ---------------------------------------------------------------------------
// The lane: where his words and thoughts go
// ---------------------------------------------------------------------------

export interface Lane {
  side: "left" | "right";
  rect: StageRect;
  style: BoxStyle;
}

/**
 * The side of Edward with more free room, as a box: from one cell of air
 * beside him to a one-cell margin at the stage's edge, full height less the
 * margins. Room is the side's area less what pieces cover; a tie goes right
 * (read left to right, he speaks into the page).
 */
export function laneFor(me: StageRect, pieces: Pick<StagePiece, "rect">[], stage: StageGrid): Lane {
  const { cols, rows } = safeGrid(stage);
  const top = 1;
  const height = Math.max(1, rows - 2);
  const leftEnd = Math.max(1, me.x - 1);
  const rightStart = Math.min(cols - 1, me.x + me.w + 1);
  const left: StageRect = { x: 1, y: top, w: Math.max(0, leftEnd - 1), h: height };
  const right: StageRect = { x: rightStart, y: top, w: Math.max(0, cols - 1 - rightStart), h: height };
  const room = (r: StageRect) => r.w * r.h - pieces.reduce((sum, p) => sum + overlapArea(r, p.rect), 0);
  const side = room(left) > room(right) ? "left" : "right";
  const rect = side === "left" ? left : right;
  return { side, rect, style: rectStyle(rect, { cols, rows }) };
}

/** His line is up to 28vw wide: about this many cells. */
const STATUS_CELLS = 6;

/**
 * Standing on a piece, his line sits beside him (under him is the card, over
 * his head reads as a caption): away from his lane, unless that side would
 * run off the screen.
 */
export function statusSideFor(me: StageRect, laneSide: "left" | "right", stage: StageGrid): "left" | "right" {
  const { cols } = safeGrid(stage);
  const away = laneSide === "right" ? "left" : "right";
  const room = away === "left" ? me.x : cols - (me.x + me.w);
  return room >= STATUS_CELLS ? away : laneSide;
}

/** Where the compose bar sits when it wakes (bottom-centre, about 70% wide,
 *  about two rows tall with its scrim): the pieces it covers fade back. */
export const COMPOSE_RECT: StageRect = { x: 3, y: 10, w: 18, h: 2 };

/**
 * What the compose bar and its reply card cover, in stage cells: a box in
 * page pixels (the bar and the card, measured) against the stage's own box,
 * rounded outward and run to the stage's bottom. Null when nothing is laid
 * out yet — the page then assumes the bar's band (COMPOSE_RECT).
 */
export function coveredCells(
  box: { left: number; top: number; right: number; bottom: number },
  stageBox: { left: number; top: number; width: number; height: number },
  stage: StageGrid,
): StageRect | null {
  const { cols, rows } = safeGrid(stage);
  if (!(stageBox.width > 0 && stageBox.height > 0) || !(box.right > box.left && box.bottom > box.top)) return null;
  const cw = stageBox.width / cols;
  const ch = stageBox.height / rows;
  const x = Math.max(0, Math.floor((box.left - stageBox.left) / cw));
  const right = Math.min(cols, Math.ceil((box.right - stageBox.left) / cw));
  const y = Math.max(0, Math.min(rows - 1, Math.floor((box.top - stageBox.top) / ch)));
  if (right <= x) return null;
  return { x, y, w: right - x, h: rows - y };
}

/** A box that would run under the compose bar ends above it (the bar's band is `band`). */
export function clipAbove(rect: StageRect, band: StageRect): StageRect {
  const across = rect.x < band.x + band.w && band.x < rect.x + rect.w;
  if (!across || rect.y + rect.h <= band.y) return rect;
  return { ...rect, h: Math.max(1, band.y - rect.y) };
}

/** A piece the lane crosses fades back while the lane has something to say. */
export function dimmed(piece: StageRect, lane: StageRect | null | undefined): boolean {
  return !!lane && overlapArea(piece, lane) > 0;
}

// ---------------------------------------------------------------------------
// What he said
// ---------------------------------------------------------------------------

/** A line is his current one for three minutes… */
export const SPEECH_CURRENT_MS = 180_000;
/** …and the one before it stays, muted, for ten. */
export const SPEECH_PREVIOUS_MS = 600_000;

export interface SpeechView {
  current: SpokenLine | null;
  previous: SpokenLine | null;
}

export function speechView(lines: SpokenLine[] | null | undefined, nowMs: number): SpeechView {
  const timed = (lines ?? [])
    .filter((l) => l && typeof l.text === "string" && l.text.trim())
    .map((l) => ({ l, t: parseIso(l.at).getTime() }))
    .filter((x) => Number.isFinite(x.t))
    .sort((a, b) => a.t - b.t);
  const last = timed[timed.length - 1];
  if (!last || nowMs - last.t > SPEECH_CURRENT_MS) return { current: null, previous: null };
  const before = timed[timed.length - 2];
  const previous = before && nowMs - before.t <= SPEECH_PREVIOUS_MS ? before.l : null;
  return { current: last.l, previous };
}

// ---------------------------------------------------------------------------
// Night
// ---------------------------------------------------------------------------

export type StageMode = "stage" | "night";

/**
 * night: 23:00–06:00 in the owner's zone — it dims and drifts; the gear panel's
 * time of day can pin it ("night" all day, a daytime choice never). The owner at
 * the host Mac (its own presence probe) gets the normal stage, day or night:
 * they are often watching it, and a dimmed, drifting screen is for an empty
 * room (round 2 removed the desk mode that stepped the stage back). Something
 * shown full screen keeps the stage as it is under it.
 */
export function stageMode({
  ownerAtMini,
  hour,
  show,
  timeOfDay = "auto",
}: {
  ownerAtMini: boolean;
  hour: number;
  show: unknown;
  timeOfDay?: "auto" | "morning" | "day" | "dusk" | "night";
}): StageMode {
  if (show) return "stage";
  if (timeOfDay === "night") return "night";
  if (timeOfDay !== "auto" || ownerAtMini) return "stage";
  if (hour >= 23 || hour < 6) return "night";
  return "stage";
}

export const NIGHT_DRIFT_PX = 12;
export const NIGHT_DRIFT_EVERY_MS = 300_000;

function hash32(n: number): number {
  let h = (n | 0) ^ 0x9e3779b9;
  h = Math.imul(h ^ (h >>> 16), 0x85ebca6b);
  h = Math.imul(h ^ (h >>> 13), 0xc2b2ae35);
  return (h ^ (h >>> 16)) >>> 0;
}

/** The whole stage shifts a little every five minutes at night (burn-in). */
export function nightDrift(nowMs: number): { x: number; y: number } {
  const slot = Math.floor(nowMs / NIGHT_DRIFT_EVERY_MS);
  const span = 2 * NIGHT_DRIFT_PX + 1;
  return {
    x: (hash32(slot) % span) - NIGHT_DRIFT_PX,
    y: (hash32(slot + 7919) % span) - NIGHT_DRIFT_PX,
  };
}

// ---------------------------------------------------------------------------
// Receipts and his line
// ---------------------------------------------------------------------------

/** Sentence openers that read naturally lower-cased after "—"; a name ("Biscuit's…") keeps its capital. */
const LOWERABLE = new Set([
  "a", "all", "an", "because", "both", "everything", "it", "it's", "its", "just", "no", "nobody", "nothing", "now",
  "since", "some", "that", "the", "there", "there's", "this", "today", "tonight", "while", "you", "you're", "your",
]);

function lowerFirst(s: string): string {
  const first = (s.split(/\s/)[0] ?? "").replace(/[^A-Za-z'’]/g, "").replace("’", "'").toLowerCase();
  return LOWERABLE.has(first) ? s[0].toLowerCase() + s.slice(1) : s;
}

/** "I arranged this at 4:10 pm — you're out and nothing needs you." (bottom-left). */
export function whyReceipt(
  scene: Pick<StageScene, "source" | "updated_at" | "why">,
  { tz }: { tz?: string } = {},
): string | null {
  if (scene.source === "default" || !scene.updated_at) return null;
  const when = formatClock(scene.updated_at, { tz });
  const lead = scene.source === "owner" ? `You set this at ${when}` : `I arranged this at ${when}`;
  const why = (scene.why ?? "").trim();
  return why ? `${lead} — ${lowerFirst(why)}` : lead;
}

/** "You asked at 6:10 pm · stays up until 8:10 pm". */
export function askedReceipt(asked: AskedHold, { tz }: { tz?: string } = {}): string {
  return `You asked at ${formatClock(asked.at, { tz })} · stays up until ${formatClock(asked.until, { tz })}`;
}

/**
 * The dot beside that line matches it: his light while he really speaks or
 * thinks (amber if his hands are down), grey offline, else presence's tone.
 */
export function stageStatusTone(
  presence: { known: boolean; mood: Mood; tone: "ok" | "warn" | "info" | "mute" | "danger"; handsDown?: boolean },
  { thinking, speaking }: { thinking: boolean; speaking: boolean },
): "ok" | "warn" | "info" | "mute" | "danger" {
  if (presence.mood === "offline") return presence.tone;
  if (speaking || thinking) return presence.handsDown ? "warn" : "ok";
  return presence.tone;
}

/**
 * The one line under him. His presence line (lib/screenPresence.ts), except
 * while his voice really plays ("Speaking") or a turn really runs
 * ("Thinking"). Offline wins; nothing is claimed before the first answer.
 */
export function stageStatusLine(
  presence: { known: boolean; mood: Mood; line: string; handsDown?: boolean },
  { thinking, speaking }: { thinking: boolean; speaking: boolean },
): string {
  if (!presence.known) return "";
  if (presence.mood === "offline") return presence.line;
  // Never "fine" while his hands are down, whatever else he is doing.
  const hands = presence.handsDown ? " · Chrome link down" : "";
  if (speaking) return `Speaking${hands}`;
  if (thinking) return `Thinking${hands}`;
  return presence.line;
}

// ---------------------------------------------------------------------------
// Reading the snapshot
// ---------------------------------------------------------------------------

export interface StageView {
  stage: StageGrid;
  scene: StageScene;
  asked: AskedHold | null;
  lines: SpokenLine[];
  ownerAtMini: boolean;
  /** The stock scene, drawn before the first read (or for an old backend): not his. */
  stock?: boolean;
}

function isRect(r: unknown): r is StageRect {
  const o = r as StageRect | null;
  return !!o && [o.x, o.y, o.w, o.h].every((n) => typeof n === "number" && Number.isFinite(n)) && o.w > 0 && o.h > 0;
}

/** The stage out of GET /api/screen; an old backend (tiles only) or no answer draws the stock scene. */
export function stageFromSnapshot(snap: Partial<StageSnapshot> | null | undefined): StageView {
  const scene = snap?.scene;
  if (!scene || !Array.isArray(scene.pieces) || !scene.me || !isRect(scene.me.rect)) {
    return { stage: FALLBACK_STAGE, scene: FALLBACK_SCENE, asked: null, lines: [], ownerAtMini: false, stock: true };
  }
  return {
    stage: safeGrid(snap?.stage),
    scene: { ...scene, why: scene.why ?? "", pieces: scene.pieces.filter((p) => p && typeof p.id === "string" && isRect(p.rect)) },
    asked: snap?.asked && snap.asked.until ? snap.asked : null,
    lines: Array.isArray(snap?.lines) ? snap!.lines : [],
    ownerAtMini: snap?.owner_at_mini === true,
  };
}

// ---------------------------------------------------------------------------
// How much of the lane his words take
// ---------------------------------------------------------------------------

/**
 * The box his words take in the lane: ending about his head, growing upward
 * with the words (a cell holds ~2 characters of the big line and ~4.5 of the
 * muted one). Only what this box crosses fades back, not the whole lane.
 */
export function speechRect(lane: StageRect, me: StageRect, currentLen: number, previousLen: number): StageRect {
  const floor = lane.y + lane.h;
  const bottom = Math.min(floor, Math.max(lane.y + 2, me.y + Math.ceil(me.h * 0.3)));
  const width = Math.max(1, lane.w);
  const current = Math.max(1, Math.ceil(currentLen / (width * 2.2))) * 0.95;
  const previous = previousLen > 0 ? Math.ceil(previousLen / (width * 4.5)) * 0.5 + 0.3 : 0;
  const rows = Math.max(2, Math.round(0.4 + previous + current));
  const top = Math.max(lane.y, bottom - rows);
  return { x: lane.x, y: top, w: lane.w, h: bottom - top };
}

/** A thought on his mind is set this wide at most, and needs at least this much free room (it wraps narrower). */
const MIND_MAX_W = 12;
const MIND_MIN_W = 4;

/** Rows a thought of `len` characters takes at `w` cells wide (≈4.4 characters a cell, ≈0.53 rows a line, plus its overline). */
function mindRows(len: number, w: number): number {
  const lines = Math.max(1, Math.ceil(len / (Math.max(1, w) * 4.4)));
  return Math.max(2, Math.ceil(0.5 + lines * 0.53));
}

/**
 * Where a thought on his mind sits: about his head, in the free run of the
 * lane next to him (so a drifting thought fades nothing), up to twelve cells
 * wide. A piece beside his head: it rises to free room above it (then tries
 * below). Hemmed in everywhere, it takes the lane's width at his head and
 * fades what it crosses, like his words.
 */
export function mindRect(lane: StageRect, me: StageRect, pieces: Pick<StagePiece, "rect">[], len: number): StageRect {
  const floor = lane.y + lane.h;
  const head = Math.min(floor, Math.max(lane.y + 2, me.y + Math.ceil(me.h * 0.3)));
  const rightward = lane.x >= me.x + me.w;
  const inner = rightward ? lane.x : lane.x + lane.w;
  const widest = Math.max(1, Math.min(lane.w, MIND_MAX_W));
  const box = (w: number, bottom: number): StageRect => {
    const top = Math.max(lane.y, bottom - mindRows(len, w));
    return { x: rightward ? inner : inner - w, y: top, w, h: bottom - top };
  };
  /** How far from beside him the rows of `r` stay clear of every piece. */
  const freeRun = (r: StageRect): number => {
    let run = 0;
    while (run < widest) {
      const x = rightward ? inner + run : inner - run - 1;
      const cell: StageRect = { x, y: r.y, w: 1, h: r.h };
      if (pieces.some((p) => overlapArea(cell, p.rect) > 0)) break;
      run += 1;
    }
    return run;
  };
  /** The widest clear box ending at `bottom`, or null when the free run there is too narrow. */
  const clearAt = (bottom: number): StageRect | null => {
    let w = widest;
    for (let i = 0; i < 4; i++) {
      const r = box(w, bottom);
      const run = freeRun(r);
      if (run >= w) return r;
      if (run < Math.min(MIND_MIN_W, widest)) return null;
      w = run;
    }
    return null;
  };
  const bottoms: number[] = [];
  for (let b = head; b >= lane.y + 2; b--) bottoms.push(b);
  for (let b = head + 1; b <= floor; b++) bottoms.push(b);
  for (const b of bottoms) {
    const r = clearAt(b);
    if (r) return r;
  }
  return box(widest, head);
}

/** The one "Earlier · …" line sits on the lane's bottom row; only when nothing is there. */
export function foldFits(lane: StageRect, pieces: Pick<StagePiece, "rect">[]): boolean {
  const strip: StageRect = { x: lane.x, y: lane.y + lane.h - 1, w: lane.w, h: 1 };
  return !pieces.some((p) => overlapArea(strip, p.rect) > 0);
}

// ---------------------------------------------------------------------------
// Aloud
// ---------------------------------------------------------------------------

/** A line older than this is not played (a tab opened later must not recite). */
export const SPEAK_WITHIN_MS = 60_000;

export function shouldSpeak(
  line: SpokenLine | null | undefined,
  { enabled, nowMs, visible, spoken }: { enabled: boolean; nowMs: number; visible: boolean; spoken: ReadonlySet<string> },
): boolean {
  if (!line || !line.aloud || !enabled || !visible || spoken.has(line.id)) return false;
  const t = parseIso(line.at).getTime();
  return Number.isFinite(t) && nowMs - t <= SPEAK_WITHIN_MS;
}

// ---------------------------------------------------------------------------
// The pets piece
// ---------------------------------------------------------------------------

export type CareTone = "ok" | "warn" | "mute";

export interface CareRow {
  pet: string;
  detail: string;
  chip: string;
  tone: CareTone;
  /** A med from a sitter's note: shown with its badge, never as authoritative. */
  review: boolean;
}

const WINDOW_MEAL = { am: "Breakfast", pm: "Dinner" } as const;

/** The hub's compact "5:58p" / "6:40a" in the one clock format, "5:58 pm". */
function clockWords(s: string): string {
  return s.replace(/^(\d{1,2}:\d{2})\s*([ap])m?$/i, (_, t: string, m: string) => `${t} ${m.toLowerCase()}m`);
}

/**
 * One row a pet for this care window (the hub board, services/hub_pet_service.py).
 * Only a timed med gone late is amber; food is given "whenever" and never
 * alerts; a missed dose is recorded but no longer shouts (hub rules 1–2).
 */
export function careSummary(board: HubBoard | null | undefined): CareRow[] {
  if (!board || !Array.isArray(board.pets)) return [];
  const win = board.window;
  return board.pets.map((pet) => {
    const meds = (pet.meds ?? []).filter((m) => m.window === win || m.window === null);
    const feeding = pet.feedings?.[win] ?? null;
    const late = meds.filter((m) => m.status === "overdue");
    const due = meds.filter((m) => m.status === "due");
    const review = meds.some((m) => m.needs_review);
    const meal = WINDOW_MEAL[win] ?? "Food";
    if (late.length) {
      return { pet: pet.name, detail: late.map((m) => m.name).join(", "), chip: `${late.length} late`, tone: "warn", review };
    }
    const pending = [...due.map((m) => m.name), ...(feeding && !feeding.done ? [meal] : [])];
    if (pending.length) return { pet: pet.name, detail: pending.join(", "), chip: "Not yet", tone: "mute", review };
    if (meds.some((m) => m.status === "missed")) {
      return { pet: pet.name, detail: meds.filter((m) => m.status === "missed").map((m) => m.name).join(", "), chip: "Missed", tone: "mute", review };
    }
    const given = [...meds].reverse().find((m) => m.status === "given" && m.at_local);
    const chip = feeding?.done && feeding.at_local ? `Fed ${clockWords(feeding.at_local)}` : given ? `Given ${clockWords(given.at_local!)}` : "Done";
    const names = [...meds.map((m) => m.name), ...(feeding ? [meal] : [])];
    return { pet: pet.name, detail: names.length ? names.join(", ") : "Nothing due", chip, tone: "ok", review };
  });
}

// ---------------------------------------------------------------------------
// The timer piece
// ---------------------------------------------------------------------------

export interface TimerView {
  /** "08:41", "2:30:00", "00:00" when done, "—" when unreadable. */
  remaining: string;
  /** 0..1, two decimals. */
  progress: number;
  done: boolean;
}

const pad2 = (n: number) => String(n).padStart(2, "0");

export function timerView(t: { started_at?: string; ends_at?: string }, nowMs: number): TimerView {
  const start = t.started_at ? parseIso(t.started_at).getTime() : NaN;
  const end = t.ends_at ? parseIso(t.ends_at).getTime() : NaN;
  if (!Number.isFinite(end)) return { remaining: "—", progress: 0, done: false };
  const left = Math.max(0, Math.ceil((end - nowMs) / 1000));
  const total = Number.isFinite(start) && end > start ? (end - start) / 1000 : 0;
  const progress = total > 0 ? Number(Math.min(1, Math.max(0, 1 - left / total)).toFixed(2)) : left === 0 ? 1 : 0;
  const h = Math.floor(left / 3600);
  const m = Math.floor((left % 3600) / 60);
  const s = left % 60;
  const remaining = h > 0 ? `${h}:${pad2(m)}:${pad2(s)}` : `${pad2(m)}:${pad2(s)}`;
  return { remaining, progress, done: left === 0 };
}

// ---------------------------------------------------------------------------
// The lane's content, and the receipt's place
// ---------------------------------------------------------------------------

export type LaneContent = "speech" | "thinking" | "fold" | "mind" | null;

/** A line said during a turn has the lane this long before his thinking takes it back. */
export const SPEECH_OVER_THINKING_MS = 45_000;

export function laneContent({
  current,
  thinking,
  fold,
  mind = false,
  nowMs,
}: {
  current: SpokenLine | null;
  thinking: boolean;
  fold: boolean;
  /** A recent thought to drift through (round 2): the lane is never empty while he has one. */
  mind?: boolean;
  nowMs: number;
}): LaneContent {
  if (current) {
    const age = nowMs - parseIso(current.at).getTime();
    if (!thinking || age <= SPEECH_OVER_THINKING_MS) return "speech";
  }
  if (thinking) return "thinking";
  // His mind (a thought that just folded included) before the footnote: the
  // one-line fold is only for when he has nothing else of his mind to show.
  if (mind) return "mind";
  return fold ? "fold" : null;
}

/** The receipt's left edge in cells: one in from the edge, or just past him when he stands at the bottom-left. */
export function receiptLeft(me: StageMe): number {
  const r = me.rect;
  const atBottom = r.y + r.h >= FALLBACK_STAGE.rows - 1;
  if (me.at !== "on" && atBottom && r.x < 10) return r.x + r.w + 1;
  return 1;
}
