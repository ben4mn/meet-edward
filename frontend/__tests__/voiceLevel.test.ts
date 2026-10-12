// R/G red: the level of Edward's own voice for his mouth (lib/voice/level.ts).
// An AnalyserNode after the player's gain hands back unsigned bytes centred
// on 128; the mouth wants 0..1.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { levelFromBytes } from "../lib/voice/level.ts";

describe("levelFromBytes", () => {
  it("silence is zero", () => {
    assert.equal(levelFromBytes(new Uint8Array(256).fill(128)), 0);
  });
  it("louder is higher, never above one", () => {
    const quiet = new Uint8Array(256).map((_, i) => (i % 2 ? 136 : 120));
    const loud = new Uint8Array(256).map((_, i) => (i % 2 ? 250 : 6));
    const q = levelFromBytes(quiet);
    const l = levelFromBytes(loud);
    assert.ok(q > 0 && q < l, `${q} < ${l}`);
    assert.ok(l <= 1);
  });
  it("ordinary speech (RMS ~0.1) opens the mouth well past halfway", () => {
    const speech = new Uint8Array(256).map((_, i) => (i % 2 ? 141 : 115));
    assert.ok(levelFromBytes(speech) > 0.5, String(levelFromBytes(speech)));
  });
  it("an empty buffer is zero", () => {
    assert.equal(levelFromBytes(new Uint8Array(0)), 0);
  });
});
