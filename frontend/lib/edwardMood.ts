// Edward's mood model — pure, so every claim his face makes is testable.
//
// Design contract (docs/design-system-2026-09-25.md §4): "the animation is
// a claim, so it must be true". A mood is derived from real signals only,
// in one fixed priority:
//
//   offline > speak > listen > think/work > attention > resting > sleepy > idle
//
// and each mood has one honest status line for the presence bar and the
// avatar's aria-label. Beats are one-shot reactions (≤1s, ≥1.2s apart);
// blinks and glances are the scheduling the life hook runs without React.

import type { PaneActivityClass } from "../components/pane/paneActivity";
import { parseIso } from "./time";

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

export type Mood =
  | "idle"
  | "think"
  | "work"
  | "listen"
  | "speak"
  | "attention"
  | "sleepy"
  | "resting"
  | "offline";

export type Beat = "nod" | "wince" | "wave" | "acted" | "perk";

export type Gaze = "left" | "right" | "up" | "down";

export const MOODS: readonly Mood[] = [
  "idle", "think", "work", "listen", "speak", "attention", "sleepy", "resting", "offline",
];
export const BEATS: readonly Beat[] = ["nod", "wince", "wave", "acted", "perk"];
export const GAZES: readonly Gaze[] = ["left", "right", "up", "down"];

export type VoicePhase = "listening" | "thinking" | "speaking";
export type TurnPhase = "recalling" | "thinking" | "working" | "writing";

export interface EdwardSignals {
  /** The backend answers. False means every other signal is stale. */
  online: boolean;
  /** false before the first answer about his mind (no mind/now yet, no
   *  fresh cached one): he is plain idle and his line says nothing — not
   *  "Resting" while an approval may be waiting. Undefined means known. */
  known?: boolean;
  /** Live voice session phase, if one is open. */
  voice?: VoicePhase | null;
  /** The owner's chat turn in flight, if any. */
  turn?: {
    phase: TurnPhase | null;
    runningTools: number;
    /** Tools used this turn so far (finished + running). "Working · N
     *  tools" counts these: tools run one at a time, so the running count
     *  alone always read 1 (review 2026-09-26). */
    toolsUsed?: number;
  } | null;
  /** A mind wake running now, with its reason (`morning`, `messages`, …). */
  mindWaking?: { reason: string } | null;
  /** Sends held by the taint gate for the owner's approval. */
  approvalsWaiting: number;
  /** Journal entries Edward wants the owner to read. */
  unreadThoughts: number;
  /** The Codex week is spent (meter exhausted / weekly cap). */
  quotaExhausted: boolean;
  /** When the quota comes back (UTC ISO; naive = UTC). */
  quotaResetsAt?: string | null;
  /** "day": the Claude API's daily spend cap, not the Codex week (2026-10-11). */
  quotaPeriod?: "day" | null;
  /** His hands in the owner's Chrome are down (extension link dropped). */
  handsDown?: boolean;
  /** Local hour 0–23, for the night-time sleepy pose. */
  hour: number;
}

// ---------------------------------------------------------------------------
// Mood
// ---------------------------------------------------------------------------

function isNight(hour: number): boolean {
  return hour >= 23 || hour < 6;
}

export function deriveMood(s: EdwardSignals): Mood {
  if (!s.online) return "offline";
  if (s.voice === "speaking") return "speak";
  if (s.voice === "listening") return "listen";
  if (s.voice === "thinking") return "think";

  const turn = s.turn;
  if (turn) {
    if (turn.runningTools > 0 || turn.phase === "working") return "work";
    if (turn.phase === "writing") return "speak";
    if (turn.phase === "recalling" || turn.phase === "thinking") return "think";
  }
  if (s.mindWaking) return "think";
  // Nothing heard yet: no claim about waiting, resting or sleeping.
  if (s.known === false) return "idle";
  if (s.approvalsWaiting > 0 || s.unreadThoughts > 0) return "attention";
  if (s.quotaExhausted) return "resting";
  if (isNight(s.hour)) return "sleepy";
  return "idle";
}

// ---------------------------------------------------------------------------
// Status line
// ---------------------------------------------------------------------------

/** Friendly words for a mind wake's reason. Never show the raw reason. */
export const REASON_PHRASES: Readonly<Record<string, string>> = {
  messages: "reading your messages",
  mention: "answering a mention",
  morning: "morning wake",
  evening: "evening wake",
  reflect: "reflecting on the week",
  reflect_lite: "thinking over the day",
  nightly: "thinking over the day",
  speak: "looking into something I noticed",
  self: "following up on something",
  surfaces: "tidying my screens",
  free: "free time",
  research: "researching",
  prep: "preparing for a meeting",
  manual: "you woke me",
};

export function reasonPhrase(reason: string | null | undefined): string | null {
  if (!reason) return null;
  return Object.prototype.hasOwnProperty.call(REASON_PHRASES, reason)
    ? REASON_PHRASES[reason]
    : null;
}

export interface StatusOpts {
  /** Override "now" (tests). */
  now?: Date;
  /** IANA zone for the reset time; the browser's by default. */
  tz?: string;
}

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

interface LocalParts {
  y: number;
  m: number;
  d: number;
  hour: number;
  minute: number;
  weekday: string;
}

function localParts(date: Date, tz: string | undefined): LocalParts {
  const fmt = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    weekday: "short",
    hourCycle: "h23",
  });
  const get = (t: string) => fmt.formatToParts(date).find((p) => p.type === t)?.value ?? "";
  return {
    y: Number(get("year")),
    m: Number(get("month")),
    d: Number(get("day")),
    hour: Number(get("hour")) % 24,
    minute: Number(get("minute")),
    weekday: get("weekday"),
  };
}

function dayKey(p: { y: number; m: number; d: number }): number {
  return Date.UTC(p.y, p.m - 1, p.d);
}

/** "7:34 pm" / "9:00 am" — the one clock format in the app (review
 *  2026-09-26: Today showed "Due 18:00", "7:34 PM", "6:32 pm" and "8:33pm"
 *  on one screen; the backend's today_service._clock writes the same). */
export function formatClock(hour: number, minute: number): string {
  const h12 = hour % 12 || 12;
  return `${h12}:${String(minute).padStart(2, "0")} ${hour < 12 ? "am" : "pm"}`;
}

function clock(p: LocalParts): string {
  return formatClock(p.hour, p.minute);
}

/** "5:30 pm" today, "tomorrow 9:00 am", otherwise "Tue 3:00 pm" — in the user's zone. */
export function formatResetTime(iso: string, opts: StatusOpts = {}): string | null {
  const when = parseIso(iso);
  if (Number.isNaN(when.getTime())) return null;
  const now = opts.now ?? new Date();
  const at = localParts(when, opts.tz);
  const today = localParts(now, opts.tz);
  const diffDays = Math.round((dayKey(at) - dayKey(today)) / 86_400_000);
  if (diffDays === 0) return clock(at);
  if (diffDays === 1) return `tomorrow ${clock(at)}`;
  return `${at.weekday} ${clock(at)}`;
}

function thinkingLine(s: EdwardSignals): string {
  if (s.voice === "thinking") return "Thinking";
  if (s.turn?.phase === "recalling") return "Remembering";
  if (s.turn?.phase === "thinking") return "Thinking";
  const reason = s.mindWaking?.reason;
  if (reason === "surfaces") return "Thinking about my screens";
  const phrase = reasonPhrase(reason);
  return phrase ? `Thinking · ${phrase}` : "Thinking";
}

/** A skipped wake's note that names the Claude day's spend cap
 *  (services/llm_spend.QUOTA_SKIP_NOTE), not the Codex week. */
export const SPEND_CAP_NOTE = /spend cap/i;

function restingLine(s: EdwardSignals, opts: StatusOpts): string {
  const when = s.quotaResetsAt ? formatResetTime(s.quotaResetsAt, opts) : null;
  return when ? `Resting until ${when}` : "Resting · quota spent";
}

const HANDS_DOWN = "Chrome link down";

/** A line that says something is fine or waiting also says his hands are
 *  down (design §1.2: never "fine" when his hands are down). A busy line
 *  keeps what he is doing — the amber antenna tip carries it there. */
function withHands(line: string, s: EdwardSignals): string {
  return s.handsDown ? `${line} · ${HANDS_DOWN}` : line;
}

/** The one honest line under his name. */
export function statusLine(s: EdwardSignals, opts: StatusOpts = {}): string {
  const mood = deriveMood(s);
  switch (mood) {
    case "offline":
      return "Offline";
    case "speak":
      return s.voice === "speaking" ? "Speaking" : "Writing";
    case "listen":
      return "Listening";
    case "think":
      return thinkingLine(s);
    case "work": {
      const n = Math.max(s.turn?.toolsUsed ?? 0, s.turn?.runningTools ?? 0);
      return n > 0 ? `Working · ${plural(n, "tool", "tools")}` : "Working";
    }
    case "attention":
      if (s.approvalsWaiting > 0) {
        return withHands(
          s.approvalsWaiting === 1
            ? "Waiting on your approval"
            : `Waiting on your approval · ${s.approvalsWaiting}`,
          s,
        );
      }
      return withHands(`Wants you · ${plural(s.unreadThoughts, "new thought", "new thoughts")}`, s);
    case "resting":
      return withHands(restingLine(s, opts), s);
    case "sleepy":
    case "idle":
    default:
      if (s.known === false) return "";
      return withHands("Resting", s);
  }
}

/** Longer form for aria-label / title: names him, explains a quota rest,
 *  and says his hands are down even when the visible line is busy (a
 *  screen reader cannot see the amber tip). */
export function statusLabel(s: EdwardSignals, opts: StatusOpts = {}): string {
  const line = statusLine(s, opts);
  if (!line) return "Edward";
  const mood = deriveMood(s);
  const why = mood === "resting" && s.quotaResetsAt
    ? (s.quotaPeriod === "day" ? " (today's budget spent)" : " (week's quota spent)")
    : "";
  const hands = s.handsDown && mood !== "offline" && !line.includes(HANDS_DOWN) ? ` (${HANDS_DOWN})` : "";
  return `Edward · ${line}${why}${hands}`;
}

// ---------------------------------------------------------------------------
// Beats
// ---------------------------------------------------------------------------

/** How long each beat's animation runs. globals.css must stay within it. */
export const BEAT_MS: Readonly<Record<Beat, number>> = {
  nod: 520,
  wince: 640,
  wave: 960,
  acted: 900,
  perk: 700,
};

/** Beats never crowd each other: at least this long between starts. */
export const BEAT_MIN_GAP_MS = 1200;

const BEAT_RANK: Readonly<Record<Beat, number>> = {
  perk: 1,
  nod: 2,
  wave: 3,
  wince: 4,
  acted: 5, // he reached outward — a trust signal, never dropped for a nod
};

export function throttleBeat(
  lastAt: number | null,
  now: number,
  gapMs: number = BEAT_MIN_GAP_MS,
): { ok: boolean; waitMs: number } {
  if (lastAt === null) return { ok: true, waitMs: 0 };
  const since = now - lastAt;
  if (since >= gapMs) return { ok: true, waitMs: 0 };
  return { ok: false, waitMs: gapMs - since };
}

/** Two beats waiting on the throttle: keep the one that matters more. */
export function pickPendingBeat(held: Beat | null, incoming: Beat): Beat {
  if (!held) return incoming;
  return BEAT_RANK[incoming] >= BEAT_RANK[held] ? incoming : held;
}

export interface ToolTally {
  /** Tools finished successfully so far this turn. */
  ok: number;
  /** Tools that failed so far this turn. */
  failed: number;
  /** Successful outward acts (a send, a push, a note) so far. */
  outward?: number;
}

/** A tool finishing is a beat: wince on a failure, a sonar ping when he
 *  reached outward, a nod when something simply worked. */
export function beatForToolTransition(prev: ToolTally | null, next: ToolTally): Beat | null {
  const p = prev ?? { ok: 0, failed: 0, outward: 0 };
  if (next.failed > p.failed) return "wince";
  if ((next.outward ?? 0) > (p.outward ?? 0)) return "acted";
  if (next.ok > p.ok) return "nod";
  return null;
}

export function beatForTurnOutcome(outcome: string | null | undefined): Beat | null {
  if (outcome === "completed") return "nod";
  if (outcome === "error") return "wince";
  return null;
}

/** Wave once per local day (first open, greeting). */
export function shouldWave(lastWavedAt: string | null, now: Date, tz?: string): boolean {
  if (!lastWavedAt) return true;
  const last = parseIso(lastWavedAt);
  if (Number.isNaN(last.getTime())) return true;
  return dayKey(localParts(last, tz)) !== dayKey(localParts(now, tz));
}

// ---------------------------------------------------------------------------
// Life: blinks and glances
// ---------------------------------------------------------------------------

type Rand = () => number;

const STARE_CHANCE = 0.12;
const DOUBLE_BLINK_CHANCE = 0.15;

/** Wait before the next blink: 2.2–6s, a long stare 12% of the time, slower
 *  when sleepy or resting, and never while offline (null). */
export function nextBlinkMs(rand: Rand = Math.random, mood?: Mood): number | null {
  if (mood === "offline") return null;
  const slow = mood === "sleepy" || mood === "resting" ? 1.6 : 1;
  const base = rand() < STARE_CHANCE ? 6500 + rand() * 3500 : 2200 + rand() * 3800;
  return Math.round(base * slow);
}

/** Close/open/close durations for one blink: a single close, or a quick
 *  double 15% of the time. Sleepy blinks are slow. */
export function blinkPattern(rand: Rand = Math.random, mood?: Mood): number[] {
  const close = mood === "sleepy" || mood === "resting" ? 260 : 120;
  return rand() < DOUBLE_BLINK_CHANCE ? [close, 110, close] : [close];
}

/** Wait before the next idle glance: 8–20s. */
export function nextGlanceMs(rand: Rand = Math.random): number {
  return Math.round(8000 + rand() * 12000);
}

export function pickGlance(rand: Rand = Math.random): { gaze: Gaze; holdMs: number } {
  const r = rand();
  const gaze: Gaze = r < 1 / 3 ? "left" : r < 2 / 3 ? "right" : "down";
  return { gaze, holdMs: Math.round(700 + rand() * 1000) };
}

// ---------------------------------------------------------------------------
// Back-compat with the act-* activity classes (/screen, hub)
// ---------------------------------------------------------------------------

export function moodToActivity(mood: Mood): PaneActivityClass {
  switch (mood) {
    case "think":
    case "work":
      return "act-think";
    case "speak":
      return "act-speak";
    case "listen":
      return "act-listen";
    default:
      return "act-stand";
  }
}

export function moodFromActivity(activity: PaneActivityClass | string | null | undefined): Mood {
  switch (activity) {
    case "act-think":
      return "think";
    case "act-speak":
      return "speak";
    case "act-listen":
      return "listen";
    default:
      return "idle";
  }
}
