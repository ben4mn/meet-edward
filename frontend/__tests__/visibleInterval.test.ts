// Pollers rest while the tab is hidden (review 2026-09-26,
// pollers-not-visibility-gated: the Workers panel polled every 5 s in hidden
// tabs). setVisibleInterval skips ticks while hidden and catches up once
// when the page comes back; the settings panels poll through it.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { setVisibleInterval } from "../lib/visibleInterval.ts";

function fakeDoc(state: "visible" | "hidden") {
  const listeners = new Set<() => void>();
  return {
    visibilityState: state as string,
    addEventListener: (_t: string, fn: () => void) => void listeners.add(fn),
    removeEventListener: (_t: string, fn: () => void) => void listeners.delete(fn),
    fire() {
      listeners.forEach((l) => l());
    },
    get listeners() {
      return listeners.size;
    },
  };
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

describe("setVisibleInterval", () => {
  it("ticks while visible, not while hidden, and catches up once on return", async () => {
    const doc = fakeDoc("visible");
    let n = 0;
    const stop = setVisibleInterval(() => void n++, 10, doc);
    await wait(35);
    const visibleTicks = n;
    assert.equal(visibleTicks >= 2, true, `ticked ${visibleTicks}`);
    doc.visibilityState = "hidden";
    doc.fire();
    const hiddenStart = n;
    await wait(40);
    assert.equal(n, hiddenStart, "no ticks while hidden");
    doc.visibilityState = "visible";
    doc.fire();
    assert.equal(n, hiddenStart + 1, "one catch-up on return");
    stop();
    const after = n;
    await wait(30);
    assert.equal(n, after, "stopped");
    assert.equal(doc.listeners, 0);
  });
});

describe("the settings panels poll through it", () => {
  for (const f of [
    "OrchestratorPanel",
    "HeartbeatPanel",
    "HeartbeatDigest",
    "WishlistPanel",
    "MindPanel",
    "CodexUsageCard",
  ]) {
    it(`${f} never polls with a bare setInterval(load, …)`, () => {
      const src = readFileSync(new URL(`../components/settings/${f}.tsx`, import.meta.url), "utf8");
      assert.equal(/setInterval\(load\b/.test(src), false);
      assert.match(src, /setVisibleInterval\(/);
    });
  }
});
