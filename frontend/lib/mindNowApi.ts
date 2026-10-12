// GET /api/mind/now — Edward's own live state (2026-09-25, the calm-presence
// design: "the animation is a claim, so it must be true").
//
// Not /api/presence: that is the owner's device beacons. This is Edward: a mind
// wake or surfaces turn running right now, his last few wakes, when he wakes
// next, what waits on the owner (held approvals, unread thoughts), the Codex
// quota and his hands in the owner's Chrome. The backend (services/wake_state.py) reads
// every source on its own bound and degrades it to null/0; the client
// normalises again so a partial or odd body can never throw in a poller.
// Timestamps are Z-suffixed UTC (read them with lib/time.ts parseIso).

import { API_URL } from "./api";
import { redirectToLogin } from "./authSession";

export type WakeOutcome = "acted" | "left_alone" | "failed" | "skipped";

/** A wake running now — a mind wake, or the surfaces pulse's turn (reason `surfaces`). */
export interface MindWaking {
  reason: string;
  started_at: string | null;
  conversation_id: string | null;
}

/** One finished wake from the mind's ledger. */
export interface MindWakeRow {
  at: string;
  reason: string | null;
  /** Usually a WakeOutcome; kept as the server's string when it is something newer. */
  outcome: WakeOutcome | string | null;
  /** His closing line. */
  line: string | null;
  /** The journal entry (conversation id) when the wake acted. */
  thought_id: string | null;
}

export interface MindNowQuota {
  used_percent: number | null;
  exhausted: boolean;
  resets_at: string | null;
  /** "day" on the Claude API (the daily spend cap, back at the owner's midnight);
   *  absent on Codex (the week's quota). */
  period?: "day";
}

export interface MindNowHands {
  /** The Edward extension's link to the owner's Chrome; null = unknown. */
  connected: boolean | null;
  down_for_s: number | null;
}

export interface MindNow {
  now: string | null;
  waking: MindWaking | null;
  last: MindWakeRow | null;
  /** Newest first, at most MAX_RECENT. */
  recent: MindWakeRow[];
  /** The earliest pending mind wake / tick. */
  next_wake_at: string | null;
  /** Sends held by the taint gate for the owner's approval. */
  approvals_waiting: number;
  /** Edward-initiated conversations the owner has not opened (the sidebar's unread dot). */
  unread_thoughts: number;
  quota: MindNowQuota;
  hands: MindNowHands;
}

export const MAX_RECENT = 5;

export const EMPTY_MIND_NOW: MindNow = Object.freeze({
  now: null,
  waking: null,
  last: null,
  recent: [],
  next_wake_at: null,
  approvals_waiting: 0,
  unread_thoughts: 0,
  quota: { used_percent: null, exhausted: false, resets_at: null },
  hands: { connected: null, down_for_s: null },
}) as MindNow;

type Obj = Record<string, unknown>;

function isObj(v: unknown): v is Obj {
  return typeof v === "object" && v !== null && !Array.isArray(v);
}

function str(v: unknown): string | null {
  return typeof v === "string" && v.trim() ? v : null;
}

function num(v: unknown): number | null {
  return typeof v === "number" && Number.isFinite(v) ? v : null;
}

function count(v: unknown): number {
  const n = num(v);
  return n !== null && n > 0 ? Math.floor(n) : 0;
}

function bool(v: unknown): boolean | null {
  return typeof v === "boolean" ? v : null;
}

function waking(v: unknown): MindWaking | null {
  if (!isObj(v)) return null;
  const reason = str(v.reason);
  if (!reason) return null;
  return { reason, started_at: str(v.started_at), conversation_id: str(v.conversation_id) };
}

function wakeRow(v: unknown): MindWakeRow | null {
  if (!isObj(v)) return null;
  const at = str(v.at);
  if (!at) return null;
  return {
    at,
    reason: str(v.reason),
    outcome: str(v.outcome),
    line: str(v.line),
    thought_id: str(v.thought_id),
  };
}

/** Any body → a MindNow. Never throws; a missing or odd source reads as unknown (null / 0). */
export function normalizeMindNow(raw: unknown): MindNow {
  if (!isObj(raw)) return EMPTY_MIND_NOW;
  const recent = Array.isArray(raw.recent)
    ? raw.recent.map(wakeRow).filter((r): r is MindWakeRow => r !== null).slice(0, MAX_RECENT)
    : [];
  const quota = isObj(raw.quota) ? raw.quota : {};
  const hands = isObj(raw.hands) ? raw.hands : {};
  return {
    now: str(raw.now),
    waking: waking(raw.waking),
    last: wakeRow(raw.last),
    recent,
    next_wake_at: str(raw.next_wake_at),
    approvals_waiting: count(raw.approvals_waiting),
    unread_thoughts: count(raw.unread_thoughts),
    quota: {
      used_percent: num(quota.used_percent),
      exhausted: quota.exhausted === true,
      resets_at: str(quota.resets_at),
      ...(quota.period === "day" ? { period: "day" as const } : {}),
    },
    hands: { connected: bool(hands.connected), down_for_s: num(hands.down_for_s) },
  };
}

/** Identifies the last wake when it acted outward, else null (the `acted` beat watches it change). */
export function lastActedKey(now: MindNow | null | undefined): string | null {
  const last = now?.last;
  if (!last || last.outcome !== "acted") return null;
  return `${last.at}|${last.reason ?? ""}`;
}

/**
 * - `ok`          — the backend answered (the body normalised, possibly empty).
 * - `missing`     — it answered 404: an older backend without the route (not offline).
 * - `unauthorized`— 401: the session died; the app goes to /login once.
 * - `unreachable` — a network error or a 5xx (the tunnel answers 502/530 when the host Mac is down).
 * - `aborted`     — the caller cancelled (the tab was hidden): says nothing about Edward.
 */
export type MindNowResult =
  | { status: "ok"; data: MindNow }
  | { status: "missing" }
  | { status: "unauthorized" }
  | { status: "unreachable"; error: string }
  | { status: "aborted" };

export async function fetchMindNow(opts: { signal?: AbortSignal } = {}): Promise<MindNowResult> {
  let response: Response;
  try {
    response = await fetch(`${API_URL}/api/mind/now`, {
      credentials: "include",
      cache: "no-store",
      signal: opts.signal,
    });
  } catch (error) {
    if (opts.signal?.aborted || (error instanceof Error && error.name === "AbortError")) {
      return { status: "aborted" };
    }
    return { status: "unreachable", error: error instanceof Error ? error.message : String(error) };
  }
  if (response.status === 401) {
    redirectToLogin();
    return { status: "unauthorized" };
  }
  if (response.status === 404) return { status: "missing" };
  if (!response.ok) return { status: "unreachable", error: `HTTP ${response.status}` };
  let body: unknown = null;
  try {
    body = await response.json();
  } catch (error) {
    if (opts.signal?.aborted) return { status: "aborted" };
    body = null;
  }
  return { status: "ok", data: normalizeMindNow(body) };
}
