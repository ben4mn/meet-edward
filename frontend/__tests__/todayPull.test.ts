// Pull to refresh on touch (UX pass 2026-09-26): a small custom gesture
// that replaces the refresh button. The distance resists past the trigger
// and stops at a ceiling; releasing past the trigger refreshes.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { PULL_MAX_PX, PULL_TRIGGER_PX, pullOffset, pullProgress, releaseRefreshes } from "../components/today/pullToRefresh.ts";

describe("pull to refresh", () => {
  it("does nothing for an upward or zero drag", () => {
    assert.equal(pullOffset(0), 0);
    assert.equal(pullOffset(-40), 0);
  });
  it("follows the finger at half speed, then resists, then stops", () => {
    assert.equal(pullOffset(40), 20);
    assert.ok(pullOffset(200) > PULL_TRIGGER_PX);
    assert.ok(pullOffset(200) < 100);
    assert.equal(pullOffset(2000), PULL_MAX_PX);
  });
  it("progress fills to the trigger", () => {
    assert.equal(pullProgress(0), 0);
    assert.equal(pullProgress(PULL_TRIGGER_PX / 2), 0.5);
    assert.equal(pullProgress(PULL_MAX_PX), 1);
  });
  it("only a release past the trigger refreshes", () => {
    assert.equal(releaseRefreshes(PULL_TRIGGER_PX - 1), false);
    assert.equal(releaseRefreshes(PULL_TRIGGER_PX), true);
  });
});

// Review 2026-09-26 (ptr-pip-overlaps-greeting): while refreshing, the page
// holds a gap at the top and the pip sits in it — never on the greeting.
import { PULL_HOLD_PX, pullContentOffset, pullPipY } from "../components/today/pullToRefresh.ts";

describe("while refreshing", () => {
  const PIP = 32;
  it("the content holds a gap at the top", () => {
    assert.equal(pullContentOffset(0, true), PULL_HOLD_PX);
    assert.equal(pullContentOffset(80, true), 80, "never jumps up under a finger still pulling");
    assert.equal(pullContentOffset(0, false), 0);
    assert.equal(pullContentOffset(40, false), 40);
  });
  it("the pip sits inside that gap, clear of the content", () => {
    for (const [offset, refreshing] of [[0, true], [30, false], [64, false], [96, false]] as const) {
      const y = pullPipY(offset, refreshing);
      assert.ok(y >= 0, `${offset}`);
      assert.ok(y + PIP <= pullContentOffset(offset, refreshing) + 16, `pip bottom ${y + PIP} vs content top ${pullContentOffset(offset, refreshing) + 16}`);
    }
  });
});
