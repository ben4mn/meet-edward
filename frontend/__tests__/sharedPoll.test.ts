// One poll per URL on Edward's screen (2026-09-26, Jarvis polish).
//
// AlertStrip and useEdwardState each polled /api/display/signals every 2 s —
// about 40 requests a minute from one tab, and each one used to hit the
// Apple calendar. The display now shares one store per URL: every component
// that wants the signals subscribes to the same poll, which ticks at 4 s,
// never overlaps itself, pauses while the tab is hidden and stops when the
// last subscriber leaves.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  SIGNALS_POLL_MS,
  createPollStore,
  resetSharedPolls,
  sharedPollStore,
  type PollEnv,
} from "../app/(display)/display/lib/sharedPoll.ts";

/** A hand-cranked clock, interval and visibility for the store. */
function fakeEnv() {
  let t = 0;
  let visible = true;
  const intervals = new Map<number, { fn: () => void; ms: number; next: number }>();
  const visListeners = new Set<() => void>();
  let nextId = 1;
  const env: PollEnv = {
    now: () => t,
    every(fn, ms) {
      const id = nextId++;
      intervals.set(id, { fn, ms, next: t + ms });
      return () => intervals.delete(id);
    },
    isVisible: () => visible,
    onVisibilityChange(fn) {
      visListeners.add(fn);
      return () => visListeners.delete(fn);
    },
  };
  return {
    env,
    /** Move the clock forward, firing every interval due on the way. */
    advance(ms: number) {
      const end = t + ms;
      for (;;) {
        let due: { fn: () => void; ms: number; next: number } | null = null;
        for (const iv of intervals.values()) if (iv.next <= end && (!due || iv.next < due.next)) due = iv;
        if (!due) break;
        t = due.next;
        due.next += due.ms;
        due.fn();
      }
      t = end;
    },
    setVisible(v: boolean) {
      visible = v;
      for (const fn of visListeners) fn();
    },
    get intervalCount() {
      return intervals.size;
    },
    get visibilityListeners() {
      return visListeners.size;
    },
  };
}

/** Let resolved fetches run their .then handlers. */
const flush = () => new Promise((r) => setImmediate(r));

function countingFetcher<T>(value: (n: number) => T) {
  let calls = 0;
  const fetcher = async () => {
    calls += 1;
    return value(calls);
  };
  return { fetcher, get calls() { return calls; } };
}

describe("createPollStore", () => {
  // Review 2026-09-26: at 4 s a 2–3 s spoken reply was often never shown as
  // "Speaking", or his mouth kept moving 4 s after he stopped. One shared
  // poll at 2 s is still half the requests of main's two 2 s pollers.
  it("polls the signals every 2 s, so a short spoken reply shows as speaking", () => {
    assert.ok(SIGNALS_POLL_MS <= 2000, `signals every ${SIGNALS_POLL_MS} ms`);
    assert.ok(SIGNALS_POLL_MS >= 1000, "not faster than once a second");
  });

  it("does not fetch until someone subscribes", async () => {
    const clock = fakeEnv();
    const f = countingFetcher((n) => n);
    createPollStore(f.fetcher, 4000, clock.env);
    clock.advance(20_000);
    await flush();
    assert.equal(f.calls, 0);
  });

  it("two subscribers share one fetch per tick", async () => {
    const clock = fakeEnv();
    const f = countingFetcher((n) => ({ n }));
    const store = createPollStore(f.fetcher, 4000, clock.env);
    const a = store.subscribe(() => {});
    const b = store.subscribe(() => {});
    await flush();
    assert.equal(f.calls, 1, "one immediate fetch for both");
    clock.advance(4000);
    await flush();
    clock.advance(4000);
    await flush();
    assert.equal(f.calls, 3, "one fetch per 4 s tick, whoever is listening");
    assert.equal(clock.intervalCount, 1);
    a();
    b();
  });

  it("notifies every subscriber with the same snapshot, stable between changes", async () => {
    const clock = fakeEnv();
    const f = countingFetcher((n) => ({ n }));
    const store = createPollStore(f.fetcher, 4000, clock.env);
    let aHits = 0;
    let bHits = 0;
    const offA = store.subscribe(() => aHits++);
    const offB = store.subscribe(() => bHits++);
    await flush();
    assert.ok(aHits >= 1 && bHits >= 1);
    const snap = store.getSnapshot();
    assert.deepEqual(snap.data, { n: 1 });
    assert.equal(snap.loading, false);
    assert.equal(snap.failures, 0);
    assert.strictEqual(store.getSnapshot(), snap, "same object until something changes");
    offA();
    offB();
  });

  it("never overlaps itself: a tick while a fetch is in flight is skipped", async () => {
    const clock = fakeEnv();
    let calls = 0;
    let release: (() => void) | null = null;
    const store = createPollStore(
      () =>
        new Promise<number>((resolve) => {
          calls += 1;
          release = () => resolve(calls);
        }),
      4000,
      clock.env,
    );
    const off = store.subscribe(() => {});
    clock.advance(4000);
    clock.advance(4000);
    assert.equal(calls, 1, "a slow calendar read must not stack requests");
    release!();
    await flush();
    clock.advance(4000);
    assert.equal(calls, 2);
    off();
  });

  it("stops polling when the last subscriber leaves, and keeps the data for the next one", async () => {
    const clock = fakeEnv();
    const f = countingFetcher((n) => n);
    const store = createPollStore(f.fetcher, 4000, clock.env);
    const off = store.subscribe(() => {});
    await flush();
    off();
    assert.equal(clock.intervalCount, 0);
    assert.equal(clock.visibilityListeners, 0);
    clock.advance(40_000);
    await flush();
    assert.equal(f.calls, 1);
    assert.equal(store.getSnapshot().data, 1, "a remounted tile does not flash empty");
    const again = store.subscribe(() => {});
    await flush();
    assert.equal(f.calls, 2, "a new subscriber refreshes at once");
    again();
  });

  it("pauses while the tab is hidden and fetches at once when it comes back", async () => {
    const clock = fakeEnv();
    const f = countingFetcher((n) => n);
    const store = createPollStore(f.fetcher, 4000, clock.env);
    const off = store.subscribe(() => {});
    await flush();
    clock.setVisible(false);
    clock.advance(60_000);
    await flush();
    assert.equal(f.calls, 1, "no requests from a hidden tab");
    clock.setVisible(true);
    await flush();
    assert.equal(f.calls, 2);
    off();
  });

  it("keeps the last good data through a failure and counts consecutive failures", async () => {
    const clock = fakeEnv();
    let fail = false;
    let n = 0;
    const store = createPollStore(
      async () => {
        n += 1;
        if (fail) throw new Error("502");
        return n;
      },
      4000,
      clock.env,
    );
    const off = store.subscribe(() => {});
    await flush();
    fail = true;
    clock.advance(4000);
    await flush();
    clock.advance(4000);
    await flush();
    let snap = store.getSnapshot();
    assert.equal(snap.data, 1, "stale data stays on screen");
    assert.equal(snap.failures, 2);
    assert.match(String(snap.error), /502/);
    fail = false;
    clock.advance(4000);
    await flush();
    snap = store.getSnapshot();
    assert.equal(snap.failures, 0);
    assert.equal(snap.error, null);
    assert.equal(snap.lastSuccessAt, 12_000);
    off();
  });
});

describe("sharedPollStore", () => {
  it("hands every caller the same store for the same URL", () => {
    resetSharedPolls();
    const clock = fakeEnv();
    const f = countingFetcher((n) => n);
    const a = sharedPollStore("/api/display/signals", f.fetcher, 4000, clock.env);
    const b = sharedPollStore("/api/display/signals", f.fetcher, 4000, clock.env);
    const c = sharedPollStore("/api/mind/now", f.fetcher, 10_000, clock.env);
    assert.strictEqual(a, b);
    assert.notStrictEqual(a, c);
    resetSharedPolls();
  });
});
