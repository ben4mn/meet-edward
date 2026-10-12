// How Today loads (UX pass 2026-09-26). Pure: the last-feed cache in
// sessionStorage (instant paint + scroll position), the retry backoff, the
// "Couldn't refresh" note and the load state machine. The hook that wires
// them to fetch lives in useTodayFeed.ts. Pinned in
// __tests__/todayFeedState.test.ts.

import { normalizeTodayFeed, type TodayFeed } from "./todayModel";

export const TODAY_CACHE_KEY = "edward:today:v1";
/** Older than this, a cached feed would mislead more than it helps. */
export const TODAY_CACHE_MAX_AGE_MS = 24 * 3600_000;

export interface TodayCacheEntry {
  feed: TodayFeed;
  /** When it was fetched (ms since epoch). */
  at: number;
  scrollTop: number;
}

type Reader = { getItem(key: string): string | null } | null | undefined;
type Writer = { setItem(key: string, value: string): void } | null | undefined;

export function readTodayCache(storage: Reader, now: number): TodayCacheEntry | null {
  try {
    const raw = storage?.getItem(TODAY_CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { feed?: unknown; at?: unknown; scrollTop?: unknown };
    const at = typeof parsed.at === "number" ? parsed.at : NaN;
    if (!Number.isFinite(at) || now - at > TODAY_CACHE_MAX_AGE_MS || !parsed.feed) return null;
    return {
      feed: normalizeTodayFeed(parsed.feed),
      at,
      scrollTop: typeof parsed.scrollTop === "number" && parsed.scrollTop > 0 ? parsed.scrollTop : 0,
    };
  } catch {
    return null;
  }
}

export function writeTodayCache(storage: Writer, entry: TodayCacheEntry): void {
  try {
    storage?.setItem(TODAY_CACHE_KEY, JSON.stringify(entry));
  } catch {
    /* private mode, full, or blocked: the page still works, it just paints cold */
  }
}

const RETRY_BASE_MS = 2000;
const RETRY_MAX_MS = 60_000;

/** 2 s, 4 s, 8 s … up to a minute. */
export function retryDelayMs(failures: number): number {
  const n = Math.max(1, failures);
  return Math.min(RETRY_MAX_MS, RETRY_BASE_MS * 2 ** (n - 1));
}

function ago(ms: number): string {
  if (ms < 60_000) return "just now";
  if (ms < 3600_000) return `${Math.round(ms / 60_000)} min ago`;
  return `${Math.round(ms / 3600_000)} h ago`;
}

/** The quiet line over a feed that could not be refreshed. */
export function staleNote(lastOkAt: number, now: number): string {
  return `Couldn't refresh · showing ${ago(Math.max(0, now - lastOkAt))}`;
}

export interface FeedState {
  feed: TodayFeed | null;
  /** When the feed on screen was fetched. */
  okAt: number | null;
  /** Failed loads in a row. */
  failures: number;
  /** A load is in flight (pull to refresh shows it; nothing else spins). */
  refreshing: boolean;
  /** The feed on screen came from the cache and has not been confirmed. */
  fromCache: boolean;
  /** The network has answered since this feed was painted (or since the
   *  owner came back after a long sleep). An action on an unconfirmed feed is
   *  checked against a fresh one first (review 2026-09-26). */
  confirmed: boolean;
}

export const NEW_FEED_STATE: FeedState = Object.freeze({
  feed: null,
  okAt: null,
  failures: 0,
  refreshing: false,
  fromCache: false,
  confirmed: false,
}) as FeedState;

export type FeedEvent =
  | { type: "cached"; feed: TodayFeed; at: number }
  | { type: "start" }
  | { type: "ok"; feed: TodayFeed; at: number }
  | { type: "fail" }
  | { type: "patch"; feed: TodayFeed }
  /** A load that started before a change on screen: ignored. */
  | { type: "dropped" }
  /** Back after a long sleep: what is on screen needs the network again. */
  | { type: "resume" };

export function reduceFeed(state: FeedState, event: FeedEvent): FeedState {
  switch (event.type) {
    case "cached":
      if (state.feed) return state; // a live feed always wins
      return { ...state, feed: event.feed, okAt: event.at, fromCache: true, confirmed: false };
    case "start":
      return state.refreshing ? state : { ...state, refreshing: true };
    case "ok":
      return { feed: event.feed, okAt: event.at, failures: 0, refreshing: false, fromCache: false, confirmed: true };
    case "fail":
      return { ...state, failures: state.failures + 1, refreshing: false };
    case "patch":
      return state.feed ? { ...state, feed: event.feed } : state;
    case "dropped":
      return state.refreshing ? { ...state, refreshing: false } : state;
    case "resume":
      return state.confirmed ? { ...state, confirmed: false } : state;
    default:
      return state;
  }
}

export type FeedPhase = "cold" | "error" | "stale" | "ready";

export function feedPhase(state: FeedState): FeedPhase {
  if (!state.feed) return state.failures > 0 ? "error" : "cold";
  return state.failures > 0 ? "stale" : "ready";
}

// ---------------------------------------------------------------------------
// Review 2026-09-26: what the owner resolved stays resolved, and a feed from the
// cache is confirmed before anything on it is acted on.
// ---------------------------------------------------------------------------

/** A load applies only if nothing on screen changed since it started (a
 *  change bumps the generation). */
export function shouldApply(startedGen: number, currentGen: number): boolean {
  return startedGen === currentGen;
}

/** The feed to paint at once: this tab's memory, else sessionStorage —
 *  either one only while it is young enough to be honest. */
export function pickCache(
  memory: TodayCacheEntry | null,
  readStored: () => TodayCacheEntry | null,
  now: number,
): TodayCacheEntry | null {
  if (memory && now - memory.at <= TODAY_CACHE_MAX_AGE_MS) return memory;
  return readStored();
}

/** Away longer than this, the feed on screen is re-confirmed before an action. */
export const RESUME_STALE_MS = 2 * 60_000;

export function unconfirmedAfterSleep(okAt: number | null, now: number): boolean {
  return okAt != null && now - okAt > RESUME_STALE_MS;
}

/** How long a resolved card stays hidden while the server still lists it. */
export const RESOLVED_HOLD_MS = 30_000;

/** Which acted-on cards stay hidden. `hidden` maps a need id to when its
 *  action was confirmed (ms), or Infinity while it is in flight or waiting
 *  out its Undo. A confirmed one stays hidden until a feed fetched after it
 *  no longer has it — or, if the server keeps listing it, until
 *  RESOLVED_HOLD_MS has passed and the server wins. Same map back when
 *  nothing changed. */
export function stillHidden(
  hidden: ReadonlyMap<string, number>,
  feedIds: ReadonlySet<string>,
  fetchedAt: number | null,
  now: number,
): ReadonlyMap<string, number> {
  let next: Map<string, number> | null = null;
  hidden.forEach((at, id) => {
    if (at === Infinity) return;
    const confirmedGone = fetchedAt != null && fetchedAt > at && !feedIds.has(id);
    if (confirmedGone || now - at > RESOLVED_HOLD_MS) {
      next ??= new Map(hidden);
      next.delete(id);
    }
  });
  return next ?? hidden;
}
