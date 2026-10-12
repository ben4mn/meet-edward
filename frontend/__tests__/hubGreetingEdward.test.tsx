// Edward in the hub's greeting card (2026-09-26).
//
// He is the hub's one live Edward, his mood from real signals (the same
// presence /screen uses), his status as his accessible name. He waves when
// someone new taps in at the hub, and once a day the first time the hub
// shows him — never on every render or reload.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render } from "@testing-library/react";

import { GreetingEdward, HUB_WAVE_KEY } from "../app/(display)/hub/components/GreetingEdward.tsx";

class MemoryStorage {
  private m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, String(v)); }
  removeItem(k: string) { this.m.delete(k); }
  clear() { this.m.clear(); }
}
const storage = new MemoryStorage();
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
Object.defineProperty(window, "localStorage", { value: storage, configurable: true, writable: true });

beforeEach(() => storage.clear());
afterEach(cleanup);

function pane(root: HTMLElement): HTMLElement {
  return root.querySelector(".pane-avatar") as HTMLElement;
}

describe("GreetingEdward", () => {
  it("is the hub's live Edward, in his real mood, named by his status", () => {
    storage.setItem(HUB_WAVE_KEY, new Date().toISOString());
    const { container } = render(<GreetingEdward mood="think" label="Edward · Thinking · morning wake" waveKey={0} />);
    const el = pane(container);
    assert.equal(el.dataset.mood, "think");
    assert.ok(el.classList.contains("is-live"));
    assert.equal(el.getAttribute("aria-label"), "Edward · Thinking · morning wake");
    assert.equal(el.dataset.beat, undefined, "already waved today");
  });

  it("waves the first time the hub shows him today, and remembers it", () => {
    const { container } = render(<GreetingEdward mood="idle" label="Edward · Resting" waveKey={0} />);
    assert.equal(pane(container).dataset.beat, "wave");
    assert.ok(storage.getItem(HUB_WAVE_KEY));
  });

  it("waves when someone new taps in", () => {
    storage.setItem(HUB_WAVE_KEY, new Date().toISOString());
    const { container, rerender } = render(<GreetingEdward mood="idle" label="Edward · Resting" waveKey={0} />);
    assert.equal(pane(container).dataset.beat, undefined);
    rerender(<GreetingEdward mood="idle" label="Edward · Resting" waveKey={1} />);
    assert.equal(pane(container).dataset.beat, "wave");
  });
});
