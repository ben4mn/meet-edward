// R/G red: Edward in the live voice view (lib/voice/voicePresence.ts).
//
// His mood follows the voice phase — listening → listen, thinking →
// think, speaking → speak (playback outliving the server's "listening" is
// still speaking), error → a wince — and a finished handoff is an "acted"
// beat. The status line is his, first person, short. The level meters are
// smoothed (fast attack, slow release) so the ring and mouth never jitter.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { initialVoiceState, type VoiceViewState } from "../lib/voice/voiceMachine.ts";
import { voiceMood, voiceBeat, voiceStatus, smoothLevel, visiblePhase } from "../lib/voice/voicePresence.ts";

const s = (over: Partial<VoiceViewState> = {}): VoiceViewState => ({ ...initialVoiceState(), ...over });

describe("visiblePhase", () => {
  it("playback outliving the server's listening is still speaking", () => {
    assert.equal(visiblePhase(s({ phase: "listening", awaitingPlaybackEnd: true })), "speaking");
    assert.equal(visiblePhase(s({ phase: "listening" })), "listening");
  });
});

describe("voiceMood", () => {
  it("follows the phase", () => {
    assert.equal(voiceMood(s({ phase: "listening" })), "listen");
    assert.equal(voiceMood(s({ phase: "thinking" })), "think");
    assert.equal(voiceMood(s({ phase: "speaking" })), "speak");
    assert.equal(voiceMood(s({ phase: "listening", awaitingPlaybackEnd: true })), "speak");
  });
  it("tools running while he thinks is work", () => {
    assert.equal(
      voiceMood(s({ phase: "thinking", toolActivity: [{ tool_name: "web_search", status: "started", message: "" }] })),
      "work",
    );
    assert.equal(
      voiceMood(s({ phase: "thinking", toolActivity: [{ tool_name: "web_search", status: "completed", message: "" }] })),
      "think",
    );
  });
  it("hold-to-talk, not held: resting and attentive, not listening", () => {
    assert.equal(voiceMood(s({ phase: "listening", mode: "ptt", pttHeld: false })), "idle");
    assert.equal(voiceMood(s({ phase: "listening", mode: "ptt", pttHeld: true })), "listen");
  });
  it("connecting and idle are idle; an error is offline only when the link dropped", () => {
    assert.equal(voiceMood(s({ phase: "connecting" })), "idle");
    assert.equal(voiceMood(s({ phase: "idle" })), "idle");
    assert.equal(voiceMood(s({ phase: "error", lostConnection: true })), "offline");
    assert.equal(voiceMood(s({ phase: "error" })), "idle");
  });
});

describe("voiceBeat", () => {
  it("an error winces, once", () => {
    assert.equal(voiceBeat(s({ phase: "speaking" }), s({ phase: "error" })), "wince");
    assert.equal(voiceBeat(s({ phase: "error" }), s({ phase: "error" })), null);
  });
  it("a handoff finishing is an act", () => {
    assert.equal(voiceBeat(s({ backgroundWork: "Booking it.", handoffsDone: 0 }), s({ backgroundWork: null, handoffsDone: 1 })), "acted");
    // 2026-09-26 review: a reconnect clearing the line is not an act — no
    // "reached outward" sonar for something that did not happen.
    assert.equal(voiceBeat(s({ backgroundWork: "Booking it." }), s({ backgroundWork: null })), null);
  });
  it("a failed tool winces; a tool that went well nods", () => {
    const running = s({ phase: "thinking", toolActivity: [{ tool_name: "x", status: "started", message: "" }] });
    assert.equal(
      voiceBeat(running, s({ phase: "thinking", toolActivity: [...running.toolActivity, { tool_name: "x", status: "error", message: "" }] })),
      "wince",
    );
    assert.equal(
      voiceBeat(running, s({ phase: "thinking", toolActivity: [...running.toolActivity, { tool_name: "x", status: "completed", message: "" }] })),
      "nod",
    );
  });
  it("the session opening is a small perk", () => {
    assert.equal(voiceBeat(s({ phase: "connecting" }), s({ phase: "listening" })), "perk");
  });
  it("nothing else is a beat", () => {
    assert.equal(voiceBeat(s({ phase: "listening" }), s({ phase: "thinking" })), null);
    assert.equal(voiceBeat(s({ phase: "thinking" }), s({ phase: "speaking" })), null);
  });
});

describe("voiceStatus", () => {
  it("speaks in his voice", () => {
    assert.equal(voiceStatus(s({ phase: "connecting" })), "Getting ready…");
    assert.equal(voiceStatus(s({ phase: "listening" })), "I'm listening");
    assert.equal(voiceStatus(s({ phase: "thinking" })), "Thinking…");
    assert.equal(voiceStatus(s({ phase: "speaking" })), "Speaking");
  });
  it("hold to talk says what to do", () => {
    assert.equal(voiceStatus(s({ phase: "listening", mode: "ptt" })), "Hold to talk");
    assert.equal(voiceStatus(s({ phase: "listening", mode: "ptt", pttHeld: true })), "I'm listening");
  });
  it("tools while thinking: the running tool's words", () => {
    assert.equal(
      voiceStatus(s({ phase: "thinking", toolActivity: [{ tool_name: "web_search", status: "started", message: "Searching the web" }] })),
      "Searching the web…",
    );
  });
  it("errors say what happened", () => {
    assert.equal(voiceStatus(s({ phase: "error", lostConnection: true })), "I lost the connection");
  });
  // L14 (2026-09-27 follow-ups): the headline was "Something went wrong" —
  // not his voice and not what happened. A mic that failed is "I can't hear
  // you"; anything else on the voice link says so and what to do.
  it("errors are first person and specific, never 'Something went wrong'", () => {
    assert.equal(voiceStatus(s({ phase: "error", errorMessage: "I can't find a microphone." })), "I can't hear you");
    assert.equal(voiceStatus(s({ phase: "error", errorMessage: "Mic blocked" })), "I can't hear you");
    assert.equal(voiceStatus(s({ phase: "error", errorMessage: "Voice error" })), "I lost the voice line — tap to try again");
    assert.equal(voiceStatus(s({ phase: "error", errorMessage: null })), "I lost the voice line — tap to try again");
  });
});

describe("smoothLevel", () => {
  it("rises fast", () => {
    assert.ok(smoothLevel(0, 1) > 0.5);
  });
  it("falls slowly", () => {
    const v = smoothLevel(1, 0);
    assert.ok(v > 0.7 && v < 1, String(v));
  });
  it("clamps to 0..1", () => {
    assert.equal(smoothLevel(0.5, 5), smoothLevel(0.5, 1));
    assert.ok(smoothLevel(0, -1) >= 0);
  });
  it("settles to exact zero instead of creeping forever", () => {
    let v = 0.2;
    for (let i = 0; i < 200; i++) v = smoothLevel(v, 0);
    assert.equal(v, 0);
  });
});
