// Edward's presence, the pure half (2026-09-25). lib/presence.tsx owns the
// timers, fetch and the DOM; every decision it makes is here, tested under
// node --test:
//
//   * presenceSignals — the real signals behind his mood (docs/design-
//     system-2026-09-25.md §4, "the animation is a claim, so it must be
//     true"): the chat turn (ChatContext), live voice, the network, and
//     GET /api/mind/now (a wake running now, what waits on the owner, the quota,
//     his hands). Mood and status line come from lib/edwardMood.ts.
//   * pollDelayMs / isOnline — mind/now every 20 s while visible; a quick
//     first retry, then exponential backoff; offline only after two failed
//     polls in a row (one hiccup is not a claim).
//   * turnBeat / mindBeat — the one-shot beats a change earns: nod (a tool
//     or a turn went well), wince (a failure), acted (he reached outward —
//     in this turn, or a wake that acted), never a replay on mount.
//   * admitBeat / releaseHeld — beats stay ≥ 1.2 s apart; of two held the
//     more important one plays (edwardMood's pickPendingBeat).
//   * encodeMindNowCache / hydrateMindNowCache — the last mind/now answer,
//     kept for a cold open (≤ 10 min old) so the first paint is not a
//     "Resting" guess; before any answer (and no fresh cache) he makes no
//     claim at all (`known: false`).

import type { ChatPresenceSlice } from "./ChatContext";
import {
  beatForToolTransition,
  deriveMood,
  pickPendingBeat,
  statusLabel,
  throttleBeat,
  type Beat,
  type EdwardSignals,
  type VoicePhase as MoodVoicePhase,
} from "./edwardMood";
import { lastActedKey, normalizeMindNow, type MindNow } from "./mindNowApi";
import { isTerminalPhase, isTurnLive, type Turn } from "./turnModel";

export const MIND_NOW_POLL_MS = 20_000;
/** The first retry after a failed poll; each further failure doubles it. */
export const MIND_NOW_RETRY_MS = 5_000;
export const MIND_NOW_MAX_BACKOFF_MS = 300_000;
/** Failed polls in a row before he reads as offline. */
export const OFFLINE_AFTER_FAILURES = 2;
/** Away at least this long before coming back earns a perk. */
export const PERK_AFTER_HIDDEN_MS = 15_000;

export interface PresenceInputs {
  /** ChatContext's slice; null outside a ChatProvider. */
  chat: ChatPresenceSlice | null;
  online: boolean;
  /** false before the first answer about his mind (default true). */
  known?: boolean;
  /** The last mind/now answer; null before the first one or from an older backend. */
  mindNow: MindNow | null;
  /** Local hour 0–23. */
  hour: number;
}

function voiceFor(chat: ChatPresenceSlice | null): MoodVoicePhase | null {
  if (!chat?.isVoiceMode) return null;
  switch (chat.voicePhase) {
    case "listening":
    case "thinking":
    case "speaking":
      return chat.voicePhase;
    case "idle":
      // The session is up and waits for the owner: listening.
      return "listening";
    default:
      // null (the view has not reported — or it is gone and voice mode was
      // left on), connecting, error: no claim until the session is really up.
      return null;
  }
}

export function presenceSignals(inp: PresenceInputs): EdwardSignals {
  const turn = inp.chat?.turn ?? null;
  const live = turn !== null && isTurnLive(turn) ? turn : null;
  const mind = inp.mindNow;
  return {
    online: inp.online,
    known: inp.known ?? true,
    voice: voiceFor(inp.chat),
    turn: live
      ? {
          phase: live.phase as "recalling" | "thinking" | "working" | "writing",
          runningTools: live.tools.running,
          toolsUsed: live.tools.ok + live.tools.failed + live.tools.running,
        }
      : null,
    mindWaking: mind?.waking ? { reason: mind.waking.reason } : null,
    approvalsWaiting: mind?.approvals_waiting ?? 0,
    unreadThoughts: mind?.unread_thoughts ?? 0,
    quotaExhausted: mind?.quota.exhausted ?? false,
    quotaResetsAt: mind?.quota.resets_at ?? null,
    quotaPeriod: mind?.quota.period ?? null,
    handsDown: mind?.hands.connected === false,
    hour: inp.hour,
  };
}

/**
 * What the presence bar's polite live region says: his status label, but a
 * working mood is just "Working" — the running-tool count changed on every
 * tool start and end and made VoiceOver chatter through a turn (2026-09-26).
 */
export function presenceAnnouncement(s: EdwardSignals): string {
  if (deriveMood(s) === "work") return "Edward · Working";
  return statusLabel(s);
}

export interface PollTiming {
  /** Between polls while mind/now answers (default 20 s). */
  pollMs?: number;
  /** The first retry after a failure (default 5 s); doubles per further failure. */
  retryMs?: number;
  /** The backoff's ceiling (default 5 min). */
  maxMs?: number;
}

/** Wait before the next mind/now poll, given failed polls in a row. */
export function pollDelayMs(failures: number, timing: PollTiming = {}): number {
  const poll = timing.pollMs ?? MIND_NOW_POLL_MS;
  const retry = timing.retryMs ?? MIND_NOW_RETRY_MS;
  const max = timing.maxMs ?? MIND_NOW_MAX_BACKOFF_MS;
  if (failures <= 0) return poll;
  return Math.min(max, retry * 2 ** (failures - 1));
}

export function isOnline(navigatorOnline: boolean, failures: number): boolean {
  return navigatorOnline && failures < OFFLINE_AFTER_FAILURES;
}

const ZERO = { ok: 0, failed: 0, outward: 0 };

function tally(t: Turn) {
  return { ok: t.tools.ok, failed: t.tools.failed, outward: t.outward ?? 0 };
}

/**
 * The beat a turn change earns. A tool finishing: wince on a failure, acted
 * when a send/push/note went out, nod when it simply worked. The turn
 * ending: nod when done (not when it ran out of iterations), wince on an
 * error, nothing on a stop. The first sight of a turn (mount, reload) is a
 * baseline, never replayed.
 */
export function turnBeat(prev: Turn | null | undefined, next: Turn | null | undefined): Beat | null {
  if (!next || !prev || prev === next) return null;
  const same = prev.id === next.id;
  const tool = beatForToolTransition(same ? tally(prev) : ZERO, tally(next));
  let end: Beat | null = null;
  if (same && !isTerminalPhase(prev.phase) && isTerminalPhase(next.phase)) {
    if (next.phase === "done" && !next.exhausted) end = "nod";
    else if (next.phase === "error") end = "wince";
  }
  if (tool && end) return pickPendingBeat(tool, end);
  return tool ?? end;
}

/** `acted` when mind/now's last wake changes to one that acted. The first poll is a baseline. */
export function mindBeat(prev: MindNow | null | undefined, next: MindNow | null | undefined): Beat | null {
  if (!prev || !next) return null;
  const key = lastActedKey(next);
  return key && key !== lastActedKey(prev) ? "acted" : null;
}

export interface BeatGate {
  /** When the last beat started playing. */
  lastAt: number | null;
  /** A beat waiting for the gap to pass. */
  held: Beat | null;
}

export const NEW_GATE: BeatGate = Object.freeze({ lastAt: null, held: null }) as BeatGate;

export interface GateStep {
  gate: BeatGate;
  /** Play this beat now (null: nothing, or held). */
  play: Beat | null;
  /** When held: try releaseHeld after this long. */
  waitMs: number;
}

/** A beat arrives: play it now, or hold it behind the ≥ 1.2 s gap (keeping the more important one). */
export function admitBeat(gate: BeatGate, beat: Beat, now: number): GateStep {
  const t = throttleBeat(gate.lastAt, now);
  if (t.ok) {
    return { gate: { lastAt: now, held: null }, play: pickPendingBeat(gate.held, beat), waitMs: 0 };
  }
  return { gate: { ...gate, held: pickPendingBeat(gate.held, beat) }, play: null, waitMs: t.waitMs };
}

/** The hold timer fired: play the held beat if the gap has passed. */
export function releaseHeld(gate: BeatGate, now: number): GateStep {
  if (!gate.held) return { gate, play: null, waitMs: 0 };
  const t = throttleBeat(gate.lastAt, now);
  if (!t.ok) return { gate, play: null, waitMs: t.waitMs };
  return { gate: { lastAt: now, held: null }, play: gate.held, waitMs: 0 };
}

/** Back in the foreground after a while away (not a flick between apps). */
export function shouldPerk(hiddenAt: number | null, now: number): boolean {
  return hiddenAt !== null && now - hiddenAt >= PERK_AFTER_HIDDEN_MS;
}

// ---------------------------------------------------------------------------
// The last answer, kept for a cold open
// ---------------------------------------------------------------------------

/** localStorage: the last mind/now answer and when it arrived. */
export const MIND_NOW_CACHE_KEY = "edward:mindNow:v1";
/** Older than this, a cached answer is a guess: no claim instead. */
export const MIND_NOW_CACHE_MAX_AGE_MS = 10 * 60_000;
/** A wake "running now" is only believed from an answer this fresh. */
export const MIND_NOW_CACHE_WAKING_MAX_AGE_MS = 60_000;
/** A clock a little ahead of the stamp is fine; minutes ahead is not. */
const CACHE_FUTURE_SKEW_MS = 30_000;

export function encodeMindNowCache(data: MindNow, at: number): string {
  return JSON.stringify({ at: new Date(at).toISOString(), data });
}

/**
 * A cached answer worth painting on open, or null (missing, unreadable,
 * older than ten minutes, stamped in the future). A wake it says was running
 * is dropped once the answer is over a minute old — it has likely finished,
 * and the first poll says so within a second anyway.
 */
export function hydrateMindNowCache(raw: string | null | undefined, now: number): MindNow | null {
  if (!raw) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return null;
  }
  if (typeof parsed !== "object" || parsed === null) return null;
  const { at, data } = parsed as { at?: unknown; data?: unknown };
  const t = typeof at === "string" ? Date.parse(at) : NaN;
  if (Number.isNaN(t)) return null;
  const age = now - t;
  if (age > MIND_NOW_CACHE_MAX_AGE_MS || age < -CACHE_FUTURE_SKEW_MS) return null;
  if (typeof data !== "object" || data === null) return null;
  const mind = normalizeMindNow(data);
  return age > MIND_NOW_CACHE_WAKING_MAX_AGE_MS && mind.waking ? { ...mind, waking: null } : mind;
}
