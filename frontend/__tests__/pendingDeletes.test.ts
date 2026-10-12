// R/G red: deleting a conversation is optimistic with Undo
// (lib/pendingDeletes.ts). The row goes at once; the API call waits for
// the Undo window to pass; Undo brings the row back and nothing is sent.
// The store outlives the drawer (it closes; the timer keeps running), and
// leaving the page commits what is pending rather than losing it.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { createPendingDeletes, UNDO_WINDOW_MS } from "../lib/pendingDeletes.ts";

function clock() {
  let t = 0;
  let seq = 0;
  const timers = new Map<number, { at: number; fn: () => void }>();
  return {
    set: (fn: () => void, ms: number) => {
      const id = ++seq;
      timers.set(id, { at: t + ms, fn });
      return id as unknown as ReturnType<typeof setTimeout>;
    },
    clear: (id: ReturnType<typeof setTimeout>) => void timers.delete(id as unknown as number),
    advance(ms: number) {
      t += ms;
      for (const [id, tm] of [...timers.entries()].sort((a, b) => a[1].at - b[1].at)) {
        if (tm.at <= t) {
          timers.delete(id);
          tm.fn();
        }
      }
    },
    size: () => timers.size,
  };
}

describe("pending deletes", () => {
  it("the undo window is six seconds", () => {
    assert.equal(UNDO_WINDOW_MS, 6000);
  });

  it("hides the row at once and commits after the window", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    const committed: string[] = [];
    store.schedule("c1", async () => void committed.push("c1"));
    assert.equal(store.isPending("c1"), true);
    assert.deepEqual([...store.pending()], ["c1"]);
    k.advance(UNDO_WINDOW_MS - 1);
    assert.deepEqual(committed, []);
    k.advance(1);
    assert.deepEqual(committed, ["c1"]);
    assert.equal(store.isPending("c1"), false);
  });

  it("undo restores it and never calls the API", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    const committed: string[] = [];
    store.schedule("c1", async () => void committed.push("c1"));
    assert.equal(store.undo("c1"), true);
    k.advance(UNDO_WINDOW_MS * 2);
    assert.deepEqual(committed, []);
    assert.equal(store.isPending("c1"), false);
    assert.equal(store.undo("c1"), false, "a second undo is a no-op");
  });

  it("tells subscribers whenever the pending set changes", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    let calls = 0;
    const off = store.subscribe(() => calls++);
    store.schedule("a", async () => {});
    store.schedule("b", async () => {});
    store.undo("a");
    k.advance(UNDO_WINDOW_MS);
    assert.equal(calls, 4);
    off();
    store.schedule("c", async () => {});
    assert.equal(calls, 4);
  });

  it("the pending snapshot is stable between changes (useSyncExternalStore)", () => {
    const store = createPendingDeletes();
    const a = store.pending();
    assert.equal(store.pending(), a);
    store.schedule("x", async () => {});
    assert.notEqual(store.pending(), a);
    store.undo("x");
  });

  it("scheduling the same id twice keeps one timer", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    let n = 0;
    store.schedule("c1", async () => void n++);
    store.schedule("c1", async () => void n++);
    assert.equal(k.size(), 1);
    k.advance(UNDO_WINDOW_MS);
    assert.equal(n, 1);
  });

  it("flush commits everything now (the page is going away)", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    const committed: string[] = [];
    store.schedule("a", async () => void committed.push("a"));
    store.schedule("b", async () => void committed.push("b"));
    store.flush();
    assert.deepEqual(committed.sort(), ["a", "b"]);
    assert.equal(k.size(), 0);
    assert.equal(store.pending().size, 0);
  });

  it("a failed delete puts the row back and reports it", async () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    const failures: string[] = [];
    store.schedule("c1", async () => {
      throw new Error("503");
    }, { onError: (id) => failures.push(id) });
    k.advance(UNDO_WINDOW_MS);
    await new Promise((r) => setTimeout(r, 0));
    assert.deepEqual(failures, ["c1"]);
    assert.equal(store.isPending("c1"), false);
  });
});

// 2026-09-26 review. (1) run() told subscribers the row was back before the
// commit had even started, so a list that drops the row inside its commit
// flashed it back for the whole round trip; the commit starts first now, in
// the same tick, so React batches the two. (2) A delete still in its undo
// window was lost when the PWA went to the background and iOS killed it:
// pagehide never fires then — visibilitychange → hidden is the one reliable
// end-of-life signal, so leaving the page commits on either.
import { flushWhenLeaving } from "../lib/pendingDeletes.ts";

describe("pending deletes: the commit and the row", () => {
  it("the commit has started by the time subscribers hear the row is no longer pending", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    let started = false;
    const seen: boolean[] = [];
    store.schedule("c1", async () => {
      started = true;
    });
    store.subscribe(() => seen.push(started));
    k.advance(UNDO_WINDOW_MS);
    assert.deepEqual(seen, [true]);
  });
});

describe("flushWhenLeaving", () => {
  function fakeWindow() {
    const handlers: Record<string, Array<() => void>> = {};
    const doc = {
      visibilityState: "visible",
      addEventListener: (t: string, fn: () => void) => void (handlers[`doc:${t}`] ??= []).push(fn),
    };
    return {
      document: doc,
      addEventListener: (t: string, fn: () => void) => void (handlers[`win:${t}`] ??= []).push(fn),
      fire: (key: string) => (handlers[key] ?? []).forEach((fn) => fn()),
    };
  }

  it("commits what is pending when the page is hidden (iOS may never send pagehide)", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    const committed: string[] = [];
    store.schedule("c1", async () => void committed.push("c1"));
    const w = fakeWindow();
    flushWhenLeaving(store, w as never);
    w.fire("doc:visibilitychange");
    assert.deepEqual(committed, [], "still visible: nothing happens");
    w.document.visibilityState = "hidden";
    w.fire("doc:visibilitychange");
    assert.deepEqual(committed, ["c1"]);
  });

  it("and on pagehide", () => {
    const k = clock();
    const store = createPendingDeletes({ setTimer: k.set, clearTimer: k.clear });
    const committed: string[] = [];
    store.schedule("c2", async () => void committed.push("c2"));
    const w = fakeWindow();
    flushWhenLeaving(store, w as never);
    w.fire("win:pagehide");
    assert.deepEqual(committed, ["c2"]);
  });
});
