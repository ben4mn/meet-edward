// Today's right pane on the desktop (UX pass 2026-09-26): sticky, but a
// pane taller than the window must never hide its own bottom — it scrolls
// with the page until its end shows, then stays (the sticky-sidebar rule).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { stickyTop } from "../components/today/stickySide.ts";

describe("stickyTop", () => {
  it("a pane that fits sticks under the top gap", () => {
    assert.equal(stickyTop(900, 600), 32);
  });
  it("a taller pane sticks by its bottom", () => {
    assert.equal(stickyTop(900, 1400), 900 - 1400 - 32);
  });
  it("unknown sizes fall back to the top gap", () => {
    assert.equal(stickyTop(0, 0), 32);
  });
});
