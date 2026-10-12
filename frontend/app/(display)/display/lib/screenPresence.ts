// Edward's presence on /screen and the hub — pure, so every claim is tested
// (__tests__/screenPresence.test.ts).
//
// "The animation is a claim, so it must be true" (design system §1). His
// mood and his one line come only from real signals:
//
//   * /api/display/signals — voice (speaking / listening), chat turns
//     streaming, workers running, a fresh alert, an event about to start;
//   * /api/mind/now        — a wake running now (and why), approvals and
//     unread thoughts waiting on the owner, the Codex quota, his hands in Chrome.
//
// They go through the app's own vocabulary (lib/edwardMood.ts), so the
// screen says what the phone says. Priority:
//
//   offline > speak > listen > chat turn > wake > wants owner > workers
//     > resting (quota) > sleepy > idle
//
// A background worker never hides something waiting on the owner. Alerts and
// calendar events are glances (motion only, never a line): glanceCue says
// when something new arrived, gazeToward which way the tile is.
//
// Review fixes (2026-09-26):
//   * his hands down (the Chrome link) is never hidden behind a busy line:
//     the line says "· Chrome link down", the dot is amber, and `handsDown`
//     turns his antenna tip amber on /screen;
//   * the hub is the household's screen (`audience: "household"`): the owner's
//     unread thoughts, held approvals and Chrome link are not shown there —
//     on the hub amber means a late dose;
//   * poked, he says what is true (`poke`): a wince offline, the reset time
//     while resting, what waits on the owner, what he is busy with — a nod and
//     small talk only when nothing is going on.

import {
  deriveMood,
  formatResetTime,
  SPEND_CAP_NOTE,
  statusLabel,
  statusLine,
  type Beat,
  type EdwardSignals,
  type Gaze,
  type Mood,
  type StatusOpts,
} from "@/lib/edwardMood";
import type { MindNow, MindWakeRow } from "@/lib/mindNowApi";
import { formatRelative, parseIso } from "@/lib/time";

import type { PlacedTile } from "./screenModel";
import type { SignalsResponse } from "./types";

/** Consecutive failed signal reads before the screen says Offline. */
export const SCREEN_OFFLINE_AFTER = 2;

export type PresenceTone = "ok" | "warn" | "info" | "mute" | "danger";

/** Who is looking: the owner's own screen, or the household's hub. */
export type PresenceAudience = "owner" | "household";

export interface ScreenInputs {
  signals: SignalsResponse | null;
  /** Consecutive failed /api/display/signals reads. */
  signalFailures: number;
  mindNow: MindNow | null;
  /** Local hour 0–23. */
  hour: number;
  /** "household" (the hub) leaves out what is the owner's alone. Default "owner". */
  audience?: PresenceAudience;
}

/** What he does when poked. `say: null` means small talk (a greeting). */
export interface PokeReply {
  beat: Beat | null;
  say: string | null;
}

export interface ScreenPresence {
  /** false until the first answer: then there is nothing to claim yet. */
  known: boolean;
  mood: Mood;
  /** The one honest line ("" while unknown). */
  line: string;
  /** For aria-label / title: "Edward · <line>". */
  label: string;
  /** The status dot: his light while busy, amber while he waits, grey offline. */
  tone: PresenceTone;
  /** Something is running now (the dot pulses only then). */
  active: boolean;
  /** His hands in Chrome are down (never while offline, never on the hub). */
  handsDown: boolean;
  /** Poked, what he does and says. */
  poke: PokeReply;
}

const BUSY: ReadonlySet<Mood> = new Set<Mood>(["think", "work", "speak", "listen"]);

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** The app's EdwardSignals for what the screen knows (workers excluded). */
export function screenSignals(inp: ScreenInputs): EdwardSignals {
  const s = inp.signals;
  const m = inp.mindNow;
  // The hub is the household's: the owner's own queue and Chrome are not.
  const household = inp.audience === "household";
  const voice = s?.voice_state === "speaking" ? "speaking" : s?.voice_state === "listening" ? "listening" : null;
  return {
    online: inp.signalFailures < SCREEN_OFFLINE_AFTER,
    voice,
    turn: s && s.active_chat_streams > 0 ? { phase: "thinking", runningTools: 0 } : null,
    mindWaking: m?.waking ? { reason: m.waking.reason } : null,
    approvalsWaiting: household ? 0 : m?.approvals_waiting ?? 0,
    unreadThoughts: household ? 0 : m?.unread_thoughts ?? 0,
    quotaExhausted: m?.quota.exhausted ?? false,
    quotaResetsAt: m?.quota.resets_at ?? null,
    quotaPeriod: m?.quota.period ?? null,
    handsDown: !household && m?.hands.connected === false,
    hour: inp.hour,
  };
}

const HANDS_DOWN = "Chrome link down";

export function screenPresence(inp: ScreenInputs, opts: StatusOpts = {}): ScreenPresence {
  if (!inp.signals && !inp.mindNow && inp.signalFailures < SCREEN_OFFLINE_AFTER) {
    return {
      known: false, mood: "idle", line: "", label: "Edward", tone: "mute", active: false, handsDown: false,
      poke: { beat: null, say: "One moment — I'm still catching up." },
    };
  }
  const signals = screenSignals(inp);
  let mood = deriveMood(signals);
  let line = statusLine(signals, opts);
  let label = statusLabel(signals, opts);

  if (signals.turn && mood === "think") {
    line = "Thinking · in a chat";
    label = `Edward · ${line}`;
  }

  const workers = inp.signals?.active_orchestrator_tasks ?? 0;
  if (workers > 0 && (mood === "idle" || mood === "sleepy" || mood === "resting")) {
    mood = "work";
    line = `Working · ${plural(workers, "worker", "workers")}`;
    label = `Edward · ${line}`;
  }

  // Never "fine" while his hands are down — whatever else he is doing.
  const handsDown = mood !== "offline" && !!signals.handsDown;
  if (handsDown && !line.includes(HANDS_DOWN)) {
    line = `${line} · ${HANDS_DOWN}`;
    label = `Edward · ${line}`;
  }

  let tone: PresenceTone;
  if (mood === "offline") tone = "mute";
  else if (mood === "attention" || mood === "resting" || handsDown) tone = "warn";
  else tone = "ok";

  const poke = pokeFor(mood, signals, workers, handsDown, opts);
  return { known: true, mood, line, label, tone, active: BUSY.has(mood), handsDown, poke };
}

// ---------------------------------------------------------------------------
// Poked: what he says is true
// ---------------------------------------------------------------------------

/** First-person words for what a wake is doing (REASON_PHRASES speak of him). */
const WAKE_WORDS: Readonly<Record<string, string>> = {
  messages: "Reading your messages",
  mention: "Answering a mention",
  morning: "Morning wake",
  evening: "Evening wake",
  reflect: "Reflecting on the week",
  reflect_lite: "Thinking over the day",
  self: "Following up on something",
  surfaces: "Tidying my screens",
  free: "Free time — thinking",
  research: "Researching",
  prep: "Preparing for a meeting",
  manual: "You woke me",
};

function pokeFor(
  mood: Mood,
  s: EdwardSignals,
  workers: number,
  handsDown: boolean,
  opts: StatusOpts,
): PokeReply {
  switch (mood) {
    case "offline":
      return { beat: "wince", say: "I can't reach the server right now. I'll keep trying." };
    case "speak":
      return { beat: null, say: "Mid-sentence — one moment." };
    case "listen":
      return { beat: null, say: "I'm listening." };
    case "think":
    case "work": {
      if (s.turn) return { beat: null, say: "In the middle of a chat — one moment." };
      if (s.mindWaking) {
        const words = Object.prototype.hasOwnProperty.call(WAKE_WORDS, s.mindWaking.reason)
          ? WAKE_WORDS[s.mindWaking.reason]
          : "Thinking something over";
        return { beat: null, say: `${words} — one moment.` };
      }
      if (workers > 0) {
        // A worker runs in the background; he himself is free to answer.
        const who = workers === 1 ? "A worker is" : `${workers} workers are`;
        return { beat: "nod", say: `${who} on it — I'm here.` };
      }
      return { beat: null, say: "Busy — one moment." };
    }
    case "attention":
      if (s.approvalsWaiting > 0) {
        return {
          beat: "perk",
          say: s.approvalsWaiting === 1
            ? "1 send is waiting for your approval."
            : `${s.approvalsWaiting} sends are waiting for your approval.`,
        };
      }
      return {
        beat: "perk",
        say: s.unreadThoughts === 1
          ? "1 new thought for you — it's in Chat."
          : `${s.unreadThoughts} new thoughts for you — they're in Chat.`,
      };
    case "resting": {
      const when = s.quotaResetsAt ? formatResetTime(s.quotaResetsAt, opts) : null;
      const spent = s.quotaPeriod === "day" ? "today's budget is spent" : "the week's quota is spent";
      return { beat: null, say: when ? `Resting until ${when} — ${spent}.` : `Resting — ${spent}.` };
    }
    default:
      if (handsDown) return { beat: "wince", say: "My Chrome link is down — I can't use the browser until it's back." };
      return { beat: "nod", say: null };
  }
}

// ---------------------------------------------------------------------------
// Glances: something new arrived
// ---------------------------------------------------------------------------

/** How recent an alert must be to earn a glance. */
export const ALERT_GLANCE_WITHIN_MS = 60_000;

export type GlanceTarget = "alerts" | "calendar";

/**
 * Which tile deserves a glance, comparing the previous read with this one.
 * Never on the first read (arriving is not news), never for an alert that
 * was already there, and an alert wins over the calendar.
 */
export function glanceCue(
  prev: SignalsResponse | null,
  next: SignalsResponse | null,
  nowMs: number,
): GlanceTarget | null {
  if (!prev || !next) return null;
  const alert = next.recent_alert_at;
  if (alert && alert !== prev.recent_alert_at) {
    const t = parseIso(alert).getTime();
    if (Number.isFinite(t) && nowMs - t < ALERT_GLANCE_WITHIN_MS) return "alerts";
  }
  const cal = next.calendar_near_fire_at;
  if (cal && cal !== prev.calendar_near_fire_at) return "calendar";
  return null;
}

/** Which way `to` is from `from` on the tile grid (the dominant axis). */
export function gazeToward(from: PlacedTile | undefined, to: PlacedTile | undefined): Gaze | null {
  if (!from || !to) return null;
  const dx = to.col + to.w / 2 - (from.col + from.w / 2);
  const dy = to.row + to.h / 2 - (from.row + from.h / 2);
  if (dx === 0 && dy === 0) return null;
  // Rows are shorter than columns are wide on a 16:9 screen with a 6×4
  // grid, so weigh a row step a little less than a column step.
  if (Math.abs(dx) >= Math.abs(dy) * 1.2) return dx < 0 ? "left" : "right";
  return dy < 0 ? "up" : "down";
}

// ---------------------------------------------------------------------------
// The last wake, in his words
// ---------------------------------------------------------------------------

export interface LastWakeCard {
  body: string;
  /** "12m ago". */
  when: string;
  outcome: string | null;
  /** The journal entry to open, when the wake acted. */
  thoughtId: string | null;
}

/** What he did last. Never a raw error or internal word. */
export function lastWakeCard(row: MindWakeRow | null | undefined, now: Date = new Date()): LastWakeCard | null {
  if (!row) return null;
  let body: string;
  switch (row.outcome) {
    case "failed":
      body = "I couldn't finish that one. I'll look again next time.";
      break;
    case "skipped":
      body = SPEND_CAP_NOTE.test(row.line ?? "")
        ? "I sat that one out — today's budget is spent."
        : "I sat that one out to save the week's quota.";
      break;
    case "left_alone":
      body = row.line || "I looked around. Nothing needed me.";
      break;
    default:
      body = row.line || "I took care of something.";
  }
  return { body, when: formatRelative(row.at, { now }), outcome: row.outcome, thoughtId: row.thought_id };
}
