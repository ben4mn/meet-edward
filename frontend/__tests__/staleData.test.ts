// R/G red for the panel staleness helper. A panel that hasn't gotten
// a fresh poll in 2.5× its expected interval shows a faint badge so
// the user knows the data isn't live.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { isStale, staleAgeLabel } from "../app/(display)/display/lib/staleness.ts";

describe("isStale", () => {
  it("returns false when lastSuccessAt is null (still loading first poll)", () => {
    assert.equal(isStale(null, 10_000, 1_000_000), false);
  });

  it("returns false when recent", () => {
    const now = 1_000_000;
    assert.equal(isStale(now - 2_000, 10_000, now), false);
  });

  it("returns true past the 2.5x interval threshold", () => {
    const now = 1_000_000;
    assert.equal(isStale(now - 30_000, 10_000, now), true);
  });

  it("is false just under the threshold", () => {
    const now = 1_000_000;
    assert.equal(isStale(now - 24_000, 10_000, now), false);
  });
});

describe("staleAgeLabel", () => {
  it("returns null when not stale", () => {
    const now = 1_000_000;
    assert.equal(staleAgeLabel(now - 1_000, now), null);
  });

  it("renders seconds for under-a-minute staleness (>= 5s)", () => {
    const now = 1_000_000;
    assert.equal(staleAgeLabel(now - 30_000, now), "30s ago");
  });

  it("renders minutes for older staleness", () => {
    const now = 1_000_000;
    assert.equal(staleAgeLabel(now - 5 * 60_000, now), "5m ago");
  });

  it("renders hours past 60 minutes", () => {
    const now = 1_000_000;
    assert.equal(staleAgeLabel(now - 2 * 60 * 60_000, now), "2h ago");
  });
});
