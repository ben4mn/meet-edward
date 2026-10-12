// Gapless scheduling of PCM chunks onto a Web Audio timeline.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { nextStartTime } from "../lib/voice/pcmSchedule.ts";

describe("nextStartTime", () => {
  it("starts a little ahead of now when the queue is empty", () => {
    const t = nextStartTime({ now: 10, scheduledUntil: 0, headroom: 0.05 });
    assert.equal(t, 10.05);
  });

  it("continues from the end of the previous chunk when still ahead", () => {
    const t = nextStartTime({ now: 10, scheduledUntil: 10.4, headroom: 0.05 });
    assert.equal(t, 10.4);
  });

  it("snaps forward after an underrun instead of scheduling in the past", () => {
    const t = nextStartTime({ now: 12, scheduledUntil: 11.2, headroom: 0.05 });
    assert.equal(t, 12.05);
  });
});
