// R/G red: the chat composer's primary button.
//
// One slot on the right of the box. What it does depends on what is in
// the box — the ChatGPT pattern: nothing typed → talk live, something
// typed → send. The memo mic stays a separate button. Pure so the rule
// is pinned without rendering the InputBar.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { enterAction, primaryAction, showsSeparateStop } from "../lib/composerAction.ts";

const base = {
  message: "",
  fileCount: 0,
  isLoading: false,
  canStop: false,
  isRecording: false,
  voiceEnabled: true,
};

describe("primaryAction", () => {
  it("empty box with voice on: the button talks live", () => {
    assert.equal(primaryAction(base), "talk");
  });

  it("whitespace counts as empty", () => {
    assert.equal(primaryAction({ ...base, message: "   \n" }), "talk");
  });

  it("typed text sends", () => {
    assert.equal(primaryAction({ ...base, message: "hey" }), "send");
  });

  it("an attachment alone sends", () => {
    assert.equal(primaryAction({ ...base, fileCount: 1 }), "send");
  });

  it("voice off and nothing to send: send, disabled", () => {
    assert.equal(primaryAction({ ...base, voiceEnabled: false }), "send_disabled");
  });

  // Changed 2026-09-24 (queue while running): stop used to win over typed
  // text. Now typed text during a stream queues, and Stop moves beside it.
  it("a stream that can be stopped, nothing typed: the slot is stop", () => {
    assert.equal(primaryAction({ ...base, isLoading: true, canStop: true }), "stop");
  });

  it("typing during a stream queues the next message", () => {
    assert.equal(primaryAction({ ...base, message: "x", isLoading: true, canStop: true }), "queue");
    assert.equal(primaryAction({ ...base, fileCount: 1, isLoading: true, canStop: true }), "queue");
  });

  // Changed 2026-09-24: typed text while loading without a stop handle
  // (Stop pressed, the stream still unwinding) queues instead of spinning.
  it("loading without a stop handle, nothing typed: busy", () => {
    assert.equal(primaryAction({ ...base, isLoading: true }), "busy");
  });

  it("loading without a stop handle, something typed: queue", () => {
    assert.equal(primaryAction({ ...base, message: "x", isLoading: true }), "queue");
  });

  it("with no queue available, typing during a stream falls back to the old rule", () => {
    assert.equal(primaryAction({ ...base, message: "x", isLoading: true, canStop: true, canQueue: false }), "stop");
    assert.equal(primaryAction({ ...base, message: "x", isLoading: true, canQueue: false }), "busy");
  });

  it("recording a memo: the strip owns the buttons, primary hides", () => {
    assert.equal(primaryAction({ ...base, isRecording: true }), "none");
    assert.equal(primaryAction({ ...base, isRecording: true, isLoading: true, canStop: true }), "none");
  });
});

describe("showsSeparateStop", () => {
  it("stop stays reachable while the primary slot queues", () => {
    assert.equal(showsSeparateStop({ ...base, message: "x", isLoading: true, canStop: true }), true);
  });

  it("no second stop when the slot already is stop", () => {
    assert.equal(showsSeparateStop({ ...base, isLoading: true, canStop: true }), false);
  });

  it("no stop without a stream to stop", () => {
    assert.equal(showsSeparateStop({ ...base, message: "x" }), false);
    assert.equal(showsSeparateStop({ ...base, message: "x", isLoading: true }), false);
  });
});

describe("enterAction", () => {
  it("Enter sends when idle", () => {
    assert.equal(enterAction({ ...base, message: "hey" }), "send");
  });

  it("Enter queues during a stream — it never stops the turn", () => {
    assert.equal(enterAction({ ...base, message: "hey", isLoading: true, canStop: true }), "queue");
  });

  it("Enter on an empty box does nothing, streaming or not", () => {
    assert.equal(enterAction(base), "none");
    assert.equal(enterAction({ ...base, isLoading: true, canStop: true }), "none");
  });
});
