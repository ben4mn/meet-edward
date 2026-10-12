// R/G red: what the Enter key does in the composer, and where the
// composer's bottom inset comes from (lib/composerAction.ts).
//
// Phone (coarse pointer): Enter is a newline — the send button sends, the
// way Messages works; a mis-tap on return never fires half a thought.
// Desktop (fine pointer): Enter sends, Shift+Enter is a newline.
// Everywhere: an IME composition (Japanese, Chinese, dictation) owns
// Enter until it commits, and Cmd/Ctrl+Enter always sends (an iPad with a
// keyboard).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { enterKeyIntent, enterKeyHintFor, composerInsetClass } from "../lib/composerAction.ts";

const key = (over: Partial<Parameters<typeof enterKeyIntent>[0]> = {}) => ({
  key: "Enter",
  shiftKey: false,
  metaKey: false,
  ctrlKey: false,
  isComposing: false,
  keyCode: 13,
  coarse: false,
  ...over,
});

describe("enterKeyIntent", () => {
  it("desktop: Enter sends", () => {
    assert.equal(enterKeyIntent(key()), "submit");
  });
  it("desktop: Shift+Enter is a newline", () => {
    assert.equal(enterKeyIntent(key({ shiftKey: true })), "newline");
  });
  it("phone: Enter is a newline; the button sends", () => {
    assert.equal(enterKeyIntent(key({ coarse: true })), "newline");
  });
  it("Cmd or Ctrl+Enter sends on either", () => {
    assert.equal(enterKeyIntent(key({ coarse: true, metaKey: true })), "submit");
    assert.equal(enterKeyIntent(key({ ctrlKey: true })), "submit");
  });
  it("an IME composition owns Enter (isComposing or Safari's keyCode 229)", () => {
    assert.equal(enterKeyIntent(key({ isComposing: true })), "ignore");
    assert.equal(enterKeyIntent(key({ keyCode: 229 })), "ignore");
    assert.equal(enterKeyIntent(key({ isComposing: true, metaKey: true })), "ignore");
  });
  it("any other key is not ours", () => {
    assert.equal(enterKeyIntent(key({ key: "a" })), "ignore");
    assert.equal(enterKeyIntent(key({ key: "Escape" })), "ignore");
  });
});

describe("enterKeyHintFor", () => {
  it("labels the phone's return key honestly: it makes a new line", () => {
    assert.equal(enterKeyHintFor(true), "enter");
  });
  it("on a desktop keyboard Enter sends", () => {
    assert.equal(enterKeyHintFor(false), "send");
  });
});

describe("composerInsetClass", () => {
  // Only the layout owns the home indicator (design system §5). The shell
  // sets --shell-inset-bottom (lib/chrome.ts shellBottomInset): 0 while the
  // dock is shown (the dock carries the inset), the inset while the dock is
  // folded away for composing. The composer pads by that variable — at
  // least 8px — and never reads env(safe-area-inset-bottom) itself, so the
  // two can never double. md+ has no dock: a fixed 16px.
  const c = composerInsetClass().split(" ");
  it("pads by the shell's variable, at least 8px", () => {
    assert.ok(c.includes("pb-[max(var(--shell-inset-bottom,0px),8px)]"), c.join(" "));
  });
  it("never takes the safe-area inset on its own", () => {
    assert.ok(!c.some((x) => x.includes("safe-area-inset")), c.join(" "));
  });
  it("md+: 16px under the composer", () => {
    assert.ok(c.includes("md:pb-4"), c.join(" "));
  });
});
