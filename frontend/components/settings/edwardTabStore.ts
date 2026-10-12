// A tiny shared store for the Edward tab: the landing's rooms list and the
// desktop rail read the same snapshot, fetched once. Concurrent refreshes
// share one load; a snapshot younger than `maxAgeMs` is not refetched; a
// failed refresh keeps what was known. No React here (pinned in
// __tests__/edwardTabStore.test.ts); useEdwardTabStatus wraps it.

export interface TabStoreState<T> {
  data: T | null;
  loading: boolean;
  error: string | null;
  fetchedAt: number | null;
}

export interface TabStore<T> {
  getState(): TabStoreState<T>;
  subscribe(listener: () => void): () => void;
  refresh(opts?: { maxAgeMs?: number }): Promise<void>;
  /** Tests: forget everything. */
  reset(): void;
}

export function createTabStore<T>(load: () => Promise<T>, clock: () => number = Date.now): TabStore<T> {
  let state: TabStoreState<T> = { data: null, loading: false, error: null, fetchedAt: null };
  let inflight: Promise<void> | null = null;
  const listeners = new Set<() => void>();

  const set = (next: Partial<TabStoreState<T>>) => {
    state = { ...state, ...next };
    for (const l of Array.from(listeners)) l();
  };

  return {
    getState: () => state,
    subscribe(listener) {
      listeners.add(listener);
      return () => {
        listeners.delete(listener);
      };
    },
    refresh(opts = {}) {
      if (inflight) return inflight;
      const maxAge = opts.maxAgeMs;
      if (maxAge != null && state.fetchedAt != null && clock() - state.fetchedAt < maxAge) {
        return Promise.resolve();
      }
      set({ loading: true });
      inflight = load()
        .then((data) => set({ data, loading: false, error: null, fetchedAt: clock() }))
        .catch((e: unknown) => set({ loading: false, error: e instanceof Error ? e.message : String(e) }))
        .finally(() => {
          inflight = null;
        });
      return inflight;
    },
    reset() {
      state = { data: null, loading: false, error: null, fetchedAt: null };
      inflight = null;
    },
  };
}
