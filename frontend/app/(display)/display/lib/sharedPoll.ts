// One poll per URL for Edward's screen and the hub (2026-09-26).
//
// Before this, AlertStrip and useEdwardState each polled
// /api/display/signals every 2 s — about 40 requests a minute from one tab,
// each of which read the Apple calendar. Now every component that wants a
// URL subscribes to one store for it:
//
//   * nothing is fetched until someone subscribes;
//   * the first subscriber fetches at once, then one fetch per interval,
//     whoever is listening;
//   * a tick while a fetch is still in flight is skipped (a slow calendar
//     read never stacks requests);
//   * hidden tab → no ticks; visible again → one fetch at once;
//   * the last subscriber leaving stops the timer, and the data stays for
//     the next one, so a remounted tile never flashes empty;
//   * a failure keeps the last good data and counts consecutive failures
//     (the screen reads two in a row as "offline").
//
// The React side is hooks/useSharedPoll.ts (useSyncExternalStore). The
// environment (clock, interval, visibility) is injected so the store is
// tested without a DOM or real timers.

export const SIGNALS_URL = "/api/display/signals";
/** Was 2 s in two places at once; now 2 s from one place. Voice drives his
 *  speaking / listening pose from this poll alone, and at 4 s a 2–3 s spoken
 *  reply was often never shown (or his mouth ran on after he stopped). The
 *  backend answers it from the DB and the calendar cache, so 2 s is cheap. */
export const SIGNALS_POLL_MS = 2_000;

export interface PollSnapshot<T> {
  data: T | null;
  error: Error | null;
  /** True until the first answer (or failure). */
  loading: boolean;
  lastSuccessAt: number | null;
  /** Consecutive failed fetches; 0 after any success. */
  failures: number;
}

export interface PollEnv {
  now(): number;
  /** Call `fn` every `ms`; returns a cancel. */
  every(fn: () => void, ms: number): () => void;
  isVisible(): boolean;
  /** Listen for visibility changes; returns an unsubscribe. */
  onVisibilityChange(fn: () => void): () => void;
}

export interface PollStore<T> {
  subscribe(listener: () => void): () => void;
  getSnapshot(): PollSnapshot<T>;
  /** Fetch now (joins a fetch already in flight). */
  refetch(): Promise<void>;
  subscriberCount(): number;
}

export function browserEnv(): PollEnv {
  return {
    now: () => Date.now(),
    every(fn, ms) {
      const id = setInterval(fn, ms);
      return () => clearInterval(id);
    },
    isVisible: () => typeof document === "undefined" || document.visibilityState !== "hidden",
    onVisibilityChange(fn) {
      if (typeof document === "undefined") return () => {};
      document.addEventListener("visibilitychange", fn);
      return () => document.removeEventListener("visibilitychange", fn);
    },
  };
}

const INITIAL: PollSnapshot<never> = Object.freeze({
  data: null,
  error: null,
  loading: true,
  lastSuccessAt: null,
  failures: 0,
});

export function createPollStore<T>(
  fetcher: () => Promise<T>,
  intervalMs: number,
  env: PollEnv = browserEnv(),
): PollStore<T> {
  let snapshot: PollSnapshot<T> = INITIAL as PollSnapshot<T>;
  const listeners = new Set<() => void>();
  let inFlight: Promise<void> | null = null;
  let cancelTicks: (() => void) | null = null;
  let offVisibility: (() => void) | null = null;

  const emit = () => {
    for (const l of Array.from(listeners)) l();
  };

  const run = (): Promise<void> => {
    if (inFlight) return inFlight;
    let p: Promise<T>;
    try {
      p = Promise.resolve(fetcher());
    } catch (e) {
      p = Promise.reject(e);
    }
    inFlight = p.then(
      (data) => {
        snapshot = { data, error: null, loading: false, lastSuccessAt: env.now(), failures: 0 };
        emit();
      },
      (e: unknown) => {
        snapshot = {
          ...snapshot,
          error: e instanceof Error ? e : new Error(String(e)),
          loading: false,
          failures: snapshot.failures + 1,
        };
        emit();
      },
    ).finally(() => {
      inFlight = null;
    });
    return inFlight;
  };

  const startTicks = () => {
    if (cancelTicks) return;
    void run();
    cancelTicks = env.every(() => {
      void run();
    }, intervalMs);
  };

  const stopTicks = () => {
    cancelTicks?.();
    cancelTicks = null;
  };

  const onVisibility = () => {
    if (env.isVisible()) startTicks();
    else stopTicks();
  };

  const start = () => {
    offVisibility = env.onVisibilityChange(onVisibility);
    if (env.isVisible()) startTicks();
  };

  const stop = () => {
    stopTicks();
    offVisibility?.();
    offVisibility = null;
  };

  return {
    subscribe(listener) {
      listeners.add(listener);
      if (listeners.size === 1) start();
      return () => {
        if (!listeners.delete(listener)) return;
        if (listeners.size === 0) stop();
      };
    },
    getSnapshot: () => snapshot,
    refetch: () => run(),
    subscriberCount: () => listeners.size,
  };
}

// ---------------------------------------------------------------------------
// One store per URL
// ---------------------------------------------------------------------------

const registry = new Map<string, PollStore<unknown>>();

/** The store for `key` (a URL). The first caller's fetcher and interval win. */
export function sharedPollStore<T>(
  key: string,
  fetcher: () => Promise<T>,
  intervalMs: number,
  env?: PollEnv,
): PollStore<T> {
  let store = registry.get(key) as PollStore<T> | undefined;
  if (!store) {
    store = createPollStore(fetcher, intervalMs, env);
    registry.set(key, store as PollStore<unknown>);
  }
  return store;
}

/** Forget every store (tests). */
export function resetSharedPolls(): void {
  registry.clear();
}
