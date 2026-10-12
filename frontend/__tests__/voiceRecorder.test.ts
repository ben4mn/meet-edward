// Voice memo recorder: choosing a MediaRecorder format the browser can
// actually produce, and the little state machine behind the mic button.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { pickRecorderMime } from "../lib/voice/recorderMime.ts";
import { initialRecorderState, reduceRecorder } from "../lib/voice/recorderState.ts";

describe("pickRecorderMime", () => {
  it("prefers webm/opus where supported (Chrome)", () => {
    const pick = pickRecorderMime((m) => m.startsWith("audio/webm"));
    assert.equal(pick.mimeType, "audio/webm;codecs=opus");
    assert.equal(pick.extension, "webm");
  });

  it("falls back to mp4 on Safari", () => {
    const pick = pickRecorderMime((m) => m === "audio/mp4");
    assert.equal(pick.mimeType, "audio/mp4");
    assert.equal(pick.extension, "m4a");
  });

  it("lets the browser choose when nothing is reported supported", () => {
    const pick = pickRecorderMime(() => false);
    assert.equal(pick.mimeType, "");
    assert.equal(pick.extension, "webm");
  });
});

describe("reduceRecorder", () => {
  it("idle -> recording -> finishing -> idle", () => {
    let s = initialRecorderState();
    assert.equal(s.phase, "idle");
    s = reduceRecorder(s, { type: "start", startedAt: 1000 });
    assert.equal(s.phase, "recording");
    s = reduceRecorder(s, { type: "tick", now: 4500 });
    assert.equal(s.elapsedMs, 3500);
    s = reduceRecorder(s, { type: "stop", intent: "send" });
    assert.equal(s.phase, "finishing");
    assert.equal(s.intent, "send");
    s = reduceRecorder(s, { type: "finished" });
    assert.equal(s.phase, "idle");
    assert.equal(s.elapsedMs, 0);
  });

  it("cancel discards without an intent", () => {
    let s = reduceRecorder(initialRecorderState(), { type: "start", startedAt: 0 });
    s = reduceRecorder(s, { type: "cancel" });
    assert.equal(s.phase, "idle");
    assert.equal(s.intent, null);
  });

  it("auto-stops at the maximum length", () => {
    let s = reduceRecorder(initialRecorderState(), { type: "start", startedAt: 0 });
    s = reduceRecorder(s, { type: "tick", now: 5 * 60 * 1000 + 1 });
    assert.equal(s.phase, "finishing");
    assert.equal(s.intent, "send");
  });

  it("errors surface and clear on the next start", () => {
    let s = reduceRecorder(initialRecorderState(), { type: "error", message: "mic denied" });
    assert.equal(s.phase, "idle");
    assert.equal(s.error, "mic denied");
    s = reduceRecorder(s, { type: "start", startedAt: 0 });
    assert.equal(s.error, null);
  });
});
