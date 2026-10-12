import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { beaconState, detectKind, IDLE_AFTER_MS, stableDeviceId } from "../lib/presenceBeacon.ts";

describe("beaconState", () => {
  it("is active with recent input on a visible tab", () => {
    assert.deepEqual(beaconState({ hidden: false, msSinceInput: 5_000, alwaysActive: false }), {
      state: "active",
      idle_seconds: 5,
    });
  });

  it("goes idle after the threshold", () => {
    assert.equal(beaconState({ hidden: false, msSinceInput: IDLE_AFTER_MS, alwaysActive: false }).state, "idle");
  });

  it("reports hidden tabs as hidden regardless of input", () => {
    assert.equal(beaconState({ hidden: true, msSinceInput: 0, alwaysActive: false }).state, "hidden");
  });

  it("kiosk screens are always active with zero idle", () => {
    assert.deepEqual(beaconState({ hidden: true, msSinceInput: 999_999, alwaysActive: true }), {
      state: "active",
      idle_seconds: 0,
    });
  });
});

describe("detectKind", () => {
  it("sees an iPhone", () => {
    assert.equal(detectKind("Mozilla/5.0 (iPhone; CPU iPhone OS 19_0 like Mac OS X)"), "iphone-pwa");
  });
  it("defaults to a Mac browser", () => {
    assert.equal(detectKind("Mozilla/5.0 (Macintosh; Intel Mac OS X 15_0)"), "mac-web");
  });
});

describe("stableDeviceId", () => {
  it("returns the same id on repeat calls", () => {
    const store = new Map<string, string>();
    const storage = { getItem: (k: string) => store.get(k) ?? null, setItem: (k: string, v: string) => void store.set(k, v) };
    const a = stableDeviceId("mac-web", storage);
    assert.equal(stableDeviceId("mac-web", storage), a);
    assert.match(a, /^mac-web-/);
  });
});
