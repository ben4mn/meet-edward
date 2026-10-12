"use client";

// Loads Today (UX pass 2026-09-26): paints at once from the last feed this
// tab saw (module memory, then sessionStorage), revalidates in the
// background, polls every minute while visible, and retries a failed load
// with backoff (2 s … 1 min). Every decision is pure in todayFeedState.ts.
//
// Review 2026-09-26: every change on screen (`patch`) bumps a generation,
// and a load that started before it is dropped — a refresh already in
// flight when the owner logged a dose used to put the card back. A load after a
// change is always a new request. A feed painted from the cache, or left on
// screen through a long sleep, is `confirmed: false` until the network
// answers (TodayView checks an action against a fresh feed first).

import { useCallback, useEffect, useLayoutEffect, useReducer, useRef } from "react";
import { getToday } from "@/lib/api";
import { SIGNED_OUT_EVENT } from "@/lib/authSession";
import { normalizeTodayFeed, type TodayFeed } from "./todayModel";
import {
  NEW_FEED_STATE,
  feedPhase,
  pickCache,
  readTodayCache,
  TODAY_CACHE_KEY,
  reduceFeed,
  retryDelayMs,
  shouldApply,
  unconfirmedAfterSleep,
  writeTodayCache,
  type TodayCacheEntry,
} from "./todayFeedState";

const POLL_MS = 60_000;

const useIsoLayoutEffect = typeof window === "undefined" ? useEffect : useLayoutEffect;

// The last feed and scroll position, kept across client-side navigation
// (Today → Chat → Today paints without a frame of skeleton).
let memory: TodayCacheEntry | null = null;
// Bumped by a sign-out: a load that started before it never remembers
// what it fetched (review 2026-09-26, logout-keeps-today-cache).
let cacheGeneration = 0;

/** Forget Today — module memory and sessionStorage (sign-out). */
export function clearTodayCache(): void {
  memory = null;
  cacheGeneration += 1;
  try {
    session()?.removeItem(TODAY_CACHE_KEY);
  } catch {
    /* storage blocked: nothing kept there either */
  }
}

if (typeof window !== "undefined") {
  // AuthContext.logout dispatches it synchronously, before the page leaves.
  window.addEventListener(SIGNED_OUT_EVENT, clearTodayCache);
}

/** Tests: set (or clear) the remembered feed. */
export function __setTodayMemoryForTests(entry: TodayCacheEntry | null) {
  memory = entry;
}

function session(): Storage | null {
  try {
    return typeof window === "undefined" ? null : window.sessionStorage;
  } catch {
    return null;
  }
}

function remember(entry: TodayCacheEntry) {
  memory = entry;
  writeTodayCache(session(), entry);
}

/** Where the owner had scrolled (called on scroll; written with the next feed or on leave). */
export function rememberScroll(scrollTop: number, flush = false) {
  if (!memory) return;
  memory = { ...memory, scrollTop };
  if (flush) writeTodayCache(session(), memory);
}

export interface TodayFeedApi {
  state: ReturnType<typeof reduceFeed>;
  phase: ReturnType<typeof feedPhase>;
  /** Load now (pull to refresh, Retry, a scroll-to-top tap). Resolves to
   *  the feed it put on screen, or null (failed, or overtaken by a change). */
  load: () => Promise<TodayFeed | null>;
  /** Replace the feed on screen (after an action) and remember it. Any load
   *  already running is dropped when it lands. */
  patch: (fn: (feed: TodayFeed) => TodayFeed) => void;
  /** Something changed on the server (an undo, a new goal): drop what is in
   *  flight and load afresh. */
  reload: () => Promise<TodayFeed | null>;
  /** The scroll position the cached feed was left at (0 when none). */
  restoreScrollTop: number;
}

interface InFlight {
  gen: number;
  run: Promise<TodayFeed | null>;
}

export function useTodayFeed(fetchFeed: () => Promise<unknown> = getToday): TodayFeedApi {
  const [state, dispatch] = useReducer(reduceFeed, NEW_FEED_STATE);
  const stateRef = useRef(state);
  stateRef.current = state;
  const restoreRef = useRef(0);
  const mounted = useRef(true);
  const gen = useRef(0);
  const inFlight = useRef<InFlight | null>(null);
  const fetchRef = useRef(fetchFeed);
  fetchRef.current = fetchFeed;

  useIsoLayoutEffect(() => {
    const cached = pickCache(memory, () => readTodayCache(session(), Date.now()), Date.now());
    if (cached) {
      memory = cached;
      restoreRef.current = cached.scrollTop;
      dispatch({ type: "cached", feed: cached.feed, at: cached.at });
    }
  }, []);

  const load = useCallback((): Promise<TodayFeed | null> => {
    const current = inFlight.current;
    if (current && current.gen === gen.current) return current.run;
    dispatch({ type: "start" });
    const started = gen.current;
    const startedCache = cacheGeneration;
    const entry: InFlight = { gen: started, run: Promise.resolve(null) };
    inFlight.current = entry;
    entry.run = (async () => {
      try {
        const feed = normalizeTodayFeed(await fetchRef.current());
        const at = Date.now();
        if (!mounted.current) return null;
        if (!shouldApply(started, gen.current)) {
          if (inFlight.current === entry) dispatch({ type: "dropped" });
          return null;
        }
        dispatch({ type: "ok", feed, at });
        if (startedCache === cacheGeneration) remember({ feed, at, scrollTop: memory?.scrollTop ?? 0 });
        return feed;
      } catch {
        if (!mounted.current) return null;
        if (shouldApply(started, gen.current)) dispatch({ type: "fail" });
        else if (inFlight.current === entry) dispatch({ type: "dropped" });
        return null;
      } finally {
        if (inFlight.current === entry) inFlight.current = null;
      }
    })();
    return entry.run;
  }, []);

  const patch = useCallback((fn: (feed: TodayFeed) => TodayFeed) => {
    const current = stateRef.current.feed;
    if (!current) return;
    gen.current += 1;
    const next = fn(current);
    dispatch({ type: "patch", feed: next });
    if (memory) remember({ ...memory, feed: next });
  }, []);

  const reload = useCallback(() => {
    gen.current += 1;
    return load();
  }, [load]);

  // First load, the minute poll while visible, and a load on return.
  useEffect(() => {
    mounted.current = true;
    void load();
    const id = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, POLL_MS);
    const onVisible = () => {
      if (document.visibilityState !== "visible") return;
      // Long away: what is on screen may be last night's; confirm before acting.
      if (unconfirmedAfterSleep(stateRef.current.okAt, Date.now())) dispatch({ type: "resume" });
      void load();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      mounted.current = false;
      clearInterval(id);
      document.removeEventListener("visibilitychange", onVisible);
      if (memory) writeTodayCache(session(), memory);
    };
  }, [load]);

  // A failed load retries on its own: 2 s, 4 s, 8 s … a minute.
  useEffect(() => {
    if (state.failures === 0) return;
    const t = setTimeout(() => {
      if (document.visibilityState === "visible") void load();
    }, retryDelayMs(state.failures));
    return () => clearTimeout(t);
  }, [state.failures, load]);

  return { state, phase: feedPhase(state), load, patch, reload, restoreScrollTop: restoreRef.current };
}
