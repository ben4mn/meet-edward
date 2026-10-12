// R/G red for the Edward tab's shared status store.
//
// The landing's rooms list and the desktop rail both show a live line and
// a health dot per room. They read one store so the numbers agree and the
// sources are fetched once: concurrent refreshes share a load, a fresh
// snapshot is not refetched, and a failed refresh keeps what was known.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { createTabStore } from "../components/settings/edwardTabStore.ts";

function deferred<T>() {
  let resolve!: (v: T) => void;
  let reject!: (e: unknown) => void;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

describe("createTabStore", () => {
  it("shares one load between concurrent refreshes and tells subscribers", async () => {
    let loads = 0;
    const d = deferred<{ n: number }>();
    const store = createTabStore(() => {
      loads += 1;
      return d.promise;
    });
    const seen: Array<{ loading: boolean; n: number | null }> = [];
    store.subscribe(() => seen.push({ loading: store.getState().loading, n: store.getState().data?.n ?? null }));
    const a = store.refresh();
    const b = store.refresh();
    assert.equal(loads, 1);
    assert.equal(store.getState().loading, true);
    d.resolve({ n: 7 });
    await Promise.all([a, b]);
    assert.equal(store.getState().data?.n, 7);
    assert.equal(store.getState().loading, false);
    assert.deepEqual(seen.at(-1), { loading: false, n: 7 });
  });

  it("does not refetch a fresh snapshot, and does once it is old", async () => {
    let now = 1_000;
    let loads = 0;
    const store = createTabStore(async () => ({ n: ++loads }), () => now);
    await store.refresh({ maxAgeMs: 30_000 });
    await store.refresh({ maxAgeMs: 30_000 });
    assert.equal(loads, 1);
    now += 31_000;
    await store.refresh({ maxAgeMs: 30_000 });
    assert.equal(loads, 2);
    assert.equal(store.getState().data?.n, 2);
  });

  it("keeps what it knew when a refresh fails", async () => {
    let fail = false;
    const store = createTabStore(async () => {
      if (fail) throw new Error("down");
      return { n: 1 };
    });
    await store.refresh();
    fail = true;
    await store.refresh({ maxAgeMs: 0 });
    assert.equal(store.getState().data?.n, 1);
    assert.equal(store.getState().loading, false);
    assert.equal(store.getState().error, "down");
  });

  it("unsubscribes", async () => {
    const store = createTabStore(async () => ({ n: 1 }));
    let calls = 0;
    const off = store.subscribe(() => calls++);
    off();
    await store.refresh();
    assert.equal(calls, 0);
  });
});
