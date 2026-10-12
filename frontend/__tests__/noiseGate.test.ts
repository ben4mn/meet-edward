// R/G red: the client-side noise gate for live voice.
//
// Scribe does VAD on the server, and it hears everything — a TV in the
// next room commits "transcripts" and Edward answers them, or barges in
// on himself. The gate sits in front of the socket: frames that don't
// rise clearly above the room's noise floor are sent as silence, so the
// server never sees them. Pure: RMS in, open/closed out.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  createNoiseGate,
  gateFrame,
  gateParams,
  SENSITIVITIES,
  parseSensitivity,
} from "../lib/voice/noiseGate.ts";

const FRAME_MS = 43; // 2048 samples at 48 kHz

function feed(gate: ReturnType<typeof createNoiseGate>, rms: number, ms: number): boolean {
  let open = false;
  for (let t = 0; t < ms; t += FRAME_MS) open = gateFrame(gate, rms, FRAME_MS);
  return open;
}

describe("noise gate", () => {
  it("lists the three levels in hears-everything → noisy-room order", () => {
    assert.deepEqual([...SENSITIVITIES], ["high", "normal", "low"]);
  });

  it("'high' never gates — today's behaviour, for a quiet room", () => {
    const g = createNoiseGate("high");
    assert.equal(gateFrame(g, 0, FRAME_MS), true);
    assert.equal(gateFrame(g, 0.001, FRAME_MS), true);
  });

  it("silence stays closed", () => {
    const g = createNoiseGate("normal");
    assert.equal(feed(g, 0.001, 2000), false);
  });

  it("a voice opens it at once", () => {
    const g = createNoiseGate("normal");
    feed(g, 0.001, 1000);
    assert.equal(gateFrame(g, 0.2, FRAME_MS), true);
  });

  it("holds open through a short pause, then closes", () => {
    const g = createNoiseGate("normal");
    feed(g, 0.001, 1000);
    gateFrame(g, 0.2, FRAME_MS);
    assert.equal(feed(g, 0.001, 200), true, "still open 200 ms into a pause");
    assert.equal(feed(g, 0.001, 800), false, "closed a second later");
  });

  it("learns a steady hum as the floor and ignores it", () => {
    const g = createNoiseGate("normal");
    // Fan / fridge / TV murmur: loud enough to open a naive threshold.
    const open = feed(g, 0.03, 8000);
    assert.equal(open, false, "steady noise is the floor, not a voice");
    assert.equal(gateFrame(g, 0.3, FRAME_MS), true, "a real voice still gets through");
  });

  it("does not learn a voice as the floor", () => {
    const g = createNoiseGate("normal");
    feed(g, 0.005, 2000);
    assert.equal(feed(g, 0.2, 3000), true, "three seconds of talking stays open");
  });

  it("'low' (noisy room) needs a bigger margin than 'normal'", () => {
    assert.ok(gateParams("low").ratio > gateParams("normal").ratio);
    assert.ok(gateParams("low").minRms > gateParams("normal").minRms);
  });

  it("parses stored values and falls back to normal", () => {
    assert.equal(parseSensitivity("low"), "low");
    assert.equal(parseSensitivity("high"), "high");
    assert.equal(parseSensitivity(null), "normal");
    assert.equal(parseSensitivity("banana"), "normal");
  });
});
