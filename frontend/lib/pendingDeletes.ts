// Optimistic deletes with Undo (2026-09-26; design system §1.7: undo
// instead of confirm). A deleted conversation leaves the list at once; the
// API call waits out the Undo window; Undo brings the row back and nothing
// is ever sent. The store is module-level so it outlives the drawer that
// scheduled it (the drawer closes, the timer keeps running), and leaving
// the page commits whatever is pending rather than losing it — on pagehide
// and on visibilitychange → hidden, because iOS suspends and then kills a
// backgrounded PWA without ever sending pagehide (2026-09-26 review).
//
// When the window ends, the commit starts *before* subscribers hear the id
// is no longer pending, in the same tick: a list that drops the row inside
// its commit lands both updates in one React batch, so the row never
// flashes back for the round trip.
//
// React reads it with useSyncExternalStore(subscribe, pending).

export const UNDO_WINDOW_MS = 6000;

type Timer = ReturnType<typeof setTimeout>;

export interface PendingDeletesDeps {
  setTimer?: (fn: () => void, ms: number) => Timer;
  clearTimer?: (t: Timer) => void;
  windowMs?: number;
}

export interface ScheduleOptions {
  /** The API call failed: the row is back; say so. */
  onError?: (id: string, error: unknown) => void;
}

export interface PendingDeletes {
  schedule(id: string, commit: () => Promise<unknown>, opts?: ScheduleOptions): void;
  /** True when there was something to undo. */
  undo(id: string): boolean;
  isPending(id: string): boolean;
  /** The ids hidden right now. Same object until the set changes. */
  pending(): ReadonlySet<string>;
  subscribe(listener: () => void): () => void;
  /** Commit everything now (pagehide). */
  flush(): void;
}

interface Entry {
  timer: Timer;
  commit: () => Promise<unknown>;
  opts: ScheduleOptions;
}

export function createPendingDeletes(deps: PendingDeletesDeps = {}): PendingDeletes {
  const setTimer = deps.setTimer ?? ((fn, ms) => setTimeout(fn, ms));
  const clearTimer = deps.clearTimer ?? ((t) => clearTimeout(t));
  const windowMs = deps.windowMs ?? UNDO_WINDOW_MS;
  const entries = new Map<string, Entry>();
  const listeners = new Set<() => void>();
  let snapshot: ReadonlySet<string> = new Set();

  const changed = () => {
    snapshot = new Set(entries.keys());
    listeners.forEach((l) => l());
  };

  const run = (id: string) => {
    const e = entries.get(id);
    if (!e) return;
    entries.delete(id);
    let p: Promise<unknown>;
    try {
      p = Promise.resolve(e.commit());
    } catch (err) {
      p = Promise.reject(err);
    }
    changed();
    p.catch((err) => e.opts.onError?.(id, err));
  };

  return {
    schedule(id, commit, opts = {}) {
      const existing = entries.get(id);
      if (existing) clearTimer(existing.timer);
      entries.set(id, { timer: setTimer(() => run(id), windowMs), commit, opts });
      if (!existing) changed();
    },
    undo(id) {
      const e = entries.get(id);
      if (!e) return false;
      clearTimer(e.timer);
      entries.delete(id);
      changed();
      return true;
    },
    isPending: (id) => entries.has(id),
    pending: () => snapshot,
    subscribe(listener) {
      listeners.add(listener);
      return () => void listeners.delete(listener);
    },
    flush() {
      for (const [id, e] of Array.from(entries.entries())) {
        clearTimer(e.timer);
        run(id);
      }
    },
  };
}

interface LeavingTarget {
  addEventListener(type: "pagehide", fn: () => void): void;
  document: { visibilityState: string; addEventListener(type: "visibilitychange", fn: () => void): void };
}

/** Commit what is pending when the page goes away: pagehide, or hidden (iOS). */
export function flushWhenLeaving(store: PendingDeletes, win: LeavingTarget): void {
  win.addEventListener("pagehide", () => store.flush());
  win.document.addEventListener("visibilitychange", () => {
    if (win.document.visibilityState === "hidden") store.flush();
  });
}

/** The app's one store. Leaving the page commits what is pending. */
export const pendingDeletes: PendingDeletes = createPendingDeletes();

if (typeof window !== "undefined") flushWhenLeaving(pendingDeletes, window);
