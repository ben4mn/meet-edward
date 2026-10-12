// R/G red for the buddy click-greeting picker.
//
// Mirrors PaneStreet's "click reactions" — a short canned line from
// a small pool. Pure module so we can assert the contract without
// firing up the SVG.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  BUDDY_GREETINGS,
  pickBuddyGreeting,
} from "../components/pane/buddyGreeting.ts";

describe("BUDDY_GREETINGS", () => {
  it("ships a small non-trivial pool", () => {
    assert.ok(BUDDY_GREETINGS.length >= 4);
    for (const g of BUDDY_GREETINGS) {
      assert.ok(g.length > 0 && g.length <= 32, `bad greeting: ${g}`);
    }
  });
});

describe("pickBuddyGreeting", () => {
  it("returns a member of BUDDY_GREETINGS", () => {
    const seen = new Set<string>();
    for (let i = 0; i < 50; i++) {
      const g = pickBuddyGreeting();
      assert.ok(BUDDY_GREETINGS.includes(g));
      seen.add(g);
    }
    // Across 50 picks we should see more than one distinct line.
    assert.ok(seen.size >= 2, "picker should not be deterministic");
  });

  it("optionally takes a seed for deterministic tests", () => {
    assert.equal(pickBuddyGreeting(0), BUDDY_GREETINGS[0]);
    assert.equal(pickBuddyGreeting(1), BUDDY_GREETINGS[1 % BUDDY_GREETINGS.length]);
  });
});
