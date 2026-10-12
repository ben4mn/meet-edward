// R/G red for the phone "focus mode" in chat.
//
// On a phone the header and the bottom dock eat a third of the screen once
// the keyboard is up. When Ellis taps into the composer on /chat, both fold
// away; they come back on blur. Pure decision so the layout stays a shell.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { shouldHideChrome } from "../lib/chrome.ts";

describe("shouldHideChrome", () => {
  it("hides on /chat while composing", () => {
    assert.equal(shouldHideChrome("/chat", true), true);
    assert.equal(shouldHideChrome("/chat?c=abc", true), true);
  });
  it("never hides when not composing", () => {
    assert.equal(shouldHideChrome("/chat", false), false);
  });
  it("never hides off the chat page, even while typing", () => {
    assert.equal(shouldHideChrome("/today", true), false);
    assert.equal(shouldHideChrome("/settings/memories", true), false);
  });
});

// The notification prompt must never sit over the dock or the composer
// (2026-09-26): on the phone it rides above the dock, except on chat where
// the composer lives there, so it goes under the presence bar; while Ellis is
// typing it waits.
import { promptPlacement, shellBottomInset } from "../lib/chrome.ts";

describe("promptPlacement", () => {
  it("rides above the dock on Today and the Edward tab", () => {
    assert.equal(promptPlacement("/today", false), "above-dock");
    assert.equal(promptPlacement("/settings/room/mind", false), "above-dock");
  });
  it("goes under the presence bar on chat, where the composer sits above the dock", () => {
    assert.equal(promptPlacement("/chat", false), "top");
    assert.equal(promptPlacement("/chat?c=abc", false), "top");
  });
  it("waits while Ellis is typing", () => {
    assert.equal(promptPlacement("/chat", true), "hidden");
    assert.equal(promptPlacement("/today", true), "hidden");
  });
});

// Only the layout owns the bottom inset (design system §5): with the dock
// shown the dock takes the home indicator, so the composer adds nothing;
// with the dock folded away (composing) the composer must take it.
describe("shellBottomInset", () => {
  it("is zero while the dock is shown", () => {
    assert.equal(shellBottomInset(true), "0px");
  });
  it("is the home-indicator inset while the dock is hidden", () => {
    assert.equal(shellBottomInset(false), "max(env(safe-area-inset-bottom), 8px)");
  });
});
