// The browser-side voice state machine. Server messages and local audio
// events go in; a view state comes out. No sockets, no audio.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { initialVoiceState, reduceVoice, type VoiceViewState } from "../lib/voice/voiceMachine.ts";

function run(events: Parameters<typeof reduceVoice>[1][]): VoiceViewState {
  return events.reduce((s, e) => reduceVoice(s, e), initialVoiceState());
}

describe("reduceVoice", () => {
  it("starts idle and connects", () => {
    assert.equal(initialVoiceState().phase, "idle");
    assert.equal(run([{ type: "connect" }]).phase, "connecting");
  });

  it("session_started opens the mic and records the ids", () => {
    const s = run([
      { type: "connect" },
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto", sample_rate_in: 16000, sample_rate_out: 24000 } },
    ]);
    assert.equal(s.phase, "listening");
    assert.equal(s.conversationId, "c1");
    assert.equal(s.mode, "auto");
    assert.equal(s.outputSampleRate, 24000);
  });

  it("partial transcripts show live captions, committed ones replace them", () => {
    const base = [
      { type: "connect" } as const,
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } } as const,
    ];
    const partial = run([...base, { type: "server", msg: { type: "partial_transcript", text: "what ti" } }]);
    assert.equal(partial.liveCaption, "what ti");
    const committed = run([
      ...base,
      { type: "server", msg: { type: "partial_transcript", text: "what ti" } },
      { type: "server", msg: { type: "transcript", text: "what time is it" } },
    ]);
    assert.equal(committed.liveCaption, "");
    assert.equal(committed.lastTranscript, "what time is it");
  });

  it("follows the server's state, collects subtitles, and clears them on done", () => {
    const s = run([
      { type: "connect" },
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } },
      { type: "server", msg: { type: "transcript", text: "hi" } },
      { type: "server", msg: { type: "state", state: "thinking" } },
      { type: "server", msg: { type: "bridge", text: "One sec." } },
      { type: "server", msg: { type: "response_text", text: "Hello Ellis.", is_final: false } },
      { type: "server", msg: { type: "state", state: "speaking" } },
      { type: "server", msg: { type: "response_text", text: "Nice day.", is_final: true } },
    ]);
    assert.equal(s.phase, "speaking");
    assert.equal(s.bridge, "One sec.");
    assert.deepEqual(s.subtitles, ["Hello Ellis.", "Nice day."]);

    const done = reduceVoice(reduceVoice(s, { type: "server", msg: { type: "state", state: "listening" } }), { type: "server", msg: { type: "response_done" } });
    assert.equal(done.phase, "listening");
    assert.equal(done.bridge, "");
    // Subtitles stay visible until playback actually finishes.
    assert.deepEqual(done.subtitles, ["Hello Ellis.", "Nice day."]);
    assert.equal(done.awaitingPlaybackEnd, true);
    const drained = reduceVoice(done, { type: "playback_drained" });
    assert.deepEqual(drained.subtitles, []);
    assert.equal(drained.awaitingPlaybackEnd, false);
  });

  it("barge-in clears subtitles immediately and asks playback to stop", () => {
    const s = run([
      { type: "connect" },
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } },
      { type: "server", msg: { type: "state", state: "speaking" } },
      { type: "server", msg: { type: "response_text", text: "Long story.", is_final: false } },
      { type: "server", msg: { type: "state", state: "listening", reason: "barge_in" } },
    ]);
    assert.equal(s.phase, "listening");
    assert.deepEqual(s.subtitles, []);
    assert.equal(s.stopPlaybackSeq, 1);
  });

  it("tool activity is tracked and cleared with the turn", () => {
    const s = run([
      { type: "connect" },
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } },
      { type: "server", msg: { type: "state", state: "thinking" } },
      { type: "server", msg: { type: "tool_activity", tool_name: "web_search", status: "started", message: "Searching" } },
      { type: "server", msg: { type: "tool_activity", tool_name: "web_search", status: "completed", message: "Done" } },
    ]);
    assert.equal(s.toolActivity.length, 2);
    const next = run([
      { type: "connect" },
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } },
      { type: "server", msg: { type: "state", state: "thinking" } },
      { type: "server", msg: { type: "tool_activity", tool_name: "web_search", status: "started", message: "Searching" } },
      { type: "server", msg: { type: "state", state: "listening" } },
      { type: "server", msg: { type: "response_done" } },
      { type: "playback_drained" },
    ]);
    assert.equal(next.toolActivity.length, 0);
  });

  it("errors and closes are terminal but retryable", () => {
    const err = run([{ type: "connect" }, { type: "server", msg: { type: "error", message: "boom" } }]);
    assert.equal(err.phase, "error");
    assert.equal(err.errorMessage, "boom");
    const dropped = run([{ type: "connect" }, { type: "socket_closed", clean: false }]);
    assert.equal(dropped.phase, "error");
    const clean = run([{ type: "connect" }, { type: "socket_closed", clean: true }]);
    assert.equal(clean.phase, "idle");
    assert.equal(run([{ type: "connect" }, { type: "server", msg: { type: "error", message: "x" } }, { type: "connect" }]).phase, "connecting");
  });

  it("push-to-talk toggles are local", () => {
    const s = run([
      { type: "connect" },
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "ptt" } },
      { type: "ptt_press" },
    ]);
    assert.equal(s.mode, "ptt");
    assert.equal(s.pttHeld, true);
    assert.equal(reduceVoice(s, { type: "ptt_release" }).pttHeld, false);
  });
});

describe("resuming after the phone screen went dark", () => {
  const live = [
    { type: "connect" } as const,
    { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } } as const,
  ];

  it("a socket dropped while hidden marks the session resumable", () => {
    const s = run([...live, { type: "socket_closed", clean: false }]);
    assert.equal(s.phase, "error");
    assert.equal(s.lostConnection, true);
  });

  it("a mic or server error is not resumable", () => {
    const mic = run([...live, { type: "server", msg: { type: "error", message: "No microphone found." } }]);
    assert.equal(mic.lostConnection, false);
    const clean = run([...live, { type: "socket_closed", clean: true }]);
    assert.equal(clean.lostConnection, false);
  });

  it("reconnecting clears the flag", () => {
    const s = run([...live, { type: "socket_closed", clean: false }, { type: "connect" }]);
    assert.equal(s.lostConnection, false);
    assert.equal(s.phase, "connecting");
  });

  it("a handed-off turn shows as background work until it reports done", () => {
    const started = run([
      { type: "connect" },
      { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } },
      { type: "server", msg: { type: "handoff", text: "This'll take a minute.", conversation_id: "c1" } },
    ]);
    assert.equal(started.backgroundWork, "This'll take a minute.");
    const done = reduceVoice(started, { type: "server", msg: { type: "handoff_done", outcome: "spoken", conversation_id: "c1" } });
    assert.equal(done.backgroundWork, null);
  });
});

// The voice view's rolling transcript (2026-09-26 chat chrome): the last
// few exchanges — what Ellis said, what Edward answered — kept past the end
// of a turn so the view can show a short history, not only the live line.
import { MAX_EXCHANGES } from "../lib/voice/voiceMachine.ts";

describe("exchanges", () => {
  const start = [
    { type: "connect" } as const,
    { type: "server", msg: { type: "session_started", conversation_id: "c1", mode: "auto" } } as const,
  ];
  const turn = (you: string, words: string[]) => [
    { type: "server", msg: { type: "transcript", text: you } } as const,
    { type: "server", msg: { type: "state", state: "thinking" } } as const,
    { type: "server", msg: { type: "state", state: "speaking" } } as const,
    ...words.map((w) => ({ type: "server", msg: { type: "response_text", text: w, is_final: false } }) as const),
    { type: "server", msg: { type: "response_done" } } as const,
    { type: "playback_drained" } as const,
  ];

  it("keeps what Ellis said and what Edward answered, after the turn ends", () => {
    const s = run([...start, ...turn("what's left today?", ["Biscuit's meds, ", "then Rowan lands."])]);
    assert.deepEqual(s.exchanges, [{ you: "what's left today?", edward: "Biscuit's meds, then Rowan lands." }]);
    assert.deepEqual(s.subtitles, [], "the live subtitles still clear");
  });

  it(`holds only the last ${3} exchanges`, () => {
    assert.equal(MAX_EXCHANGES, 3);
    const s = run([...start, ...turn("one", ["a"]), ...turn("two", ["b"]), ...turn("three", ["c"]), ...turn("four", ["d"])]);
    assert.deepEqual(s.exchanges.map((e) => e.you), ["two", "three", "four"]);
  });

  it("a barge-in keeps what was already said, marked as cut off", () => {
    const s = run([
      ...start,
      { type: "server", msg: { type: "transcript", text: "tell me a story" } },
      { type: "server", msg: { type: "state", state: "speaking" } },
      { type: "server", msg: { type: "response_text", text: "Once upon", is_final: false } },
      { type: "server", msg: { type: "state", state: "listening", reason: "barge_in" } },
    ]);
    assert.deepEqual(s.exchanges, [{ you: "tell me a story", edward: "Once upon", cut: true }]);
  });

  it("a reconnect keeps the history; ending the session clears it", () => {
    const talked = run([...start, ...turn("hi", ["Hey."])]);
    const again = reduceVoice(talked, { type: "connect" });
    assert.equal(again.exchanges.length, 1);
    assert.equal(reduceVoice(talked, { type: "disconnect" }).exchanges.length, 0);
  });

  // 2026-09-26 review: switching hands-free / hold-to-talk or the mic
  // sensitivity reconnects (disconnect, then connect) — and the disconnect
  // wiped the rolling transcript the connect branch was written to keep.
  it("switching mode or sensitivity keeps the conversation and the transcript", () => {
    const talked = run([...start, ...turn("hi", ["Hey."])]);
    const closed = reduceVoice(talked, { type: "disconnect", keep: true });
    assert.equal(closed.exchanges.length, 1);
    assert.equal(closed.conversationId, talked.conversationId);
    assert.equal(closed.phase, "idle");
    assert.equal(reduceVoice(closed, { type: "connect" }).exchanges.length, 1);
  });

  it("counts finished handoffs (the 'acted' beat reads the count, not a cleared line)", () => {
    const started = run([...start, { type: "server", msg: { type: "handoff", text: "On it.", conversation_id: "c1" } }]);
    assert.equal(started.handoffsDone, 0);
    const done = reduceVoice(started, { type: "server", msg: { type: "handoff_done", outcome: "spoken", conversation_id: "c1" } });
    assert.equal(done.handoffsDone, 1);
  });

  it("an answer with no transcript before it (a handoff report) is Edward's alone", () => {
    const s = run([
      ...start,
      { type: "server", msg: { type: "state", state: "speaking" } },
      { type: "server", msg: { type: "response_text", text: "Okay, that's done.", is_final: true } },
    ]);
    assert.deepEqual(s.exchanges, [{ you: "", edward: "Okay, that's done." }]);
  });
});
