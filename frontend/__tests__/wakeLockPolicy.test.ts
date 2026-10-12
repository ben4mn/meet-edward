import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { shouldHoldWakeLock } from "../lib/wakeLockPolicy.ts";

describe("shouldHoldWakeLock", () => {
  it("holds the phone awake only while Edward is working on a visible chat", () => {
    assert.equal(shouldHoldWakeLock({ working: true, visible: true }), true);
    assert.equal(shouldHoldWakeLock({ working: false, visible: true }), false);
    assert.equal(shouldHoldWakeLock({ working: true, visible: false }), false);
  });
});
