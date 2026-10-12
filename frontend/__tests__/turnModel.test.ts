// The chat turn model: one honest answer to "what is Edward doing on this
// turn right now". The UI used to infer liveness from an empty content
// string, so the rail flapped open/closed between tool rounds and a turn
// that errored kept its "thinking" flags forever. The phase comes only from
// stream events, and every end of a stream (done, stop, cut, error) lands
// in a terminal phase with no tool left running.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  startTurn,
  reduceTurn,
  endTurn,
  isTurnLive,
  isTerminalPhase,
  latestReasoningHeading,
  isToolFailure,
  turnOutcomeFor,
  type Turn,
  type TurnEvent,
} from "../lib/turnModel.ts";

const T0 = 1_000;

function run(events: TurnEvent[], turn: Turn = startTurn("a1", null, T0)): Turn {
  let t = turn;
  events.forEach((e, i) => {
    t = reduceTurn(t, e, T0 + i + 1);
  });
  return t;
}

describe("startTurn", () => {
  it("starts live, thinking, with nothing counted", () => {
    const t = startTurn("a1", "c1", T0);
    assert.equal(t.id, "a1");
    assert.equal(t.conversationId, "c1");
    assert.equal(t.phase, "thinking");
    assert.equal(t.startedAt, T0);
    assert.equal(t.endedAt, null);
    assert.deepEqual(t.tools, { running: 0, ok: 0, failed: 0 });
    assert.equal(t.lastToolEvent, null);
    assert.equal(t.reasoningHeading, null);
    assert.equal(isTurnLive(t), true);
  });

  it("a new chat has no conversation until the first event names one", () => {
    const t = startTurn("a1", undefined, T0);
    assert.equal(t.conversationId, null);
    const next = reduceTurn(t, { type: "progress", conversation_id: "c-new", step: "memory_search", status: "started" }, T0 + 1);
    assert.equal(next.conversationId, "c-new");
  });
});

describe("reduceTurn phases", () => {
  it("memory search is recalling, then back to thinking", () => {
    let t = run([{ type: "progress", step: "memory_search", status: "started" }]);
    assert.equal(t.phase, "recalling");
    t = reduceTurn(t, { type: "progress", step: "dream", status: "started" }, T0 + 5);
    assert.equal(t.phase, "recalling", "dreaming is part of recall");
    t = reduceTurn(t, { type: "progress", step: "memory_search", status: "completed", count: 3 }, T0 + 6);
    assert.equal(t.phase, "thinking");
  });

  it("a failed memory search still moves on to thinking", () => {
    const t = run([
      { type: "progress", step: "memory_search", status: "started" },
      { type: "progress", step: "memory_search", status: "error" },
    ]);
    assert.equal(t.phase, "thinking");
  });

  it("reasoning is thinking", () => {
    const t = run([{ type: "reasoning", content: "Ellis wants" }]);
    assert.equal(t.phase, "thinking");
  });

  it("composing a tool call is working; the tool running is working", () => {
    let t = run([{ type: "tool_args_delta", tool_name: "web_search", delta: "{\"q" }]);
    assert.equal(t.phase, "working");
    assert.equal(t.tools.running, 0, "args alone do not count as a running tool");
    t = reduceTurn(t, { type: "tool_start", tool_name: "web_search" }, T0 + 9);
    assert.equal(t.phase, "working");
    assert.equal(t.tools.running, 1);
  });

  it("a finished tool goes back to thinking, not writing (no flapping)", () => {
    const t = run([
      { type: "tool_start", tool_name: "web_search" },
      { type: "tool_end", tool_name: "web_search", result: "3 results" },
    ]);
    assert.equal(t.phase, "thinking");
    assert.deepEqual(t.tools, { running: 0, ok: 1, failed: 0 });
  });

  it("content is writing", () => {
    const t = run([{ type: "content", content: "Sure" }]);
    assert.equal(t.phase, "writing");
  });

  it("empty content does not change the phase", () => {
    const t = run([{ type: "content", content: "" }]);
    assert.equal(t.phase, "thinking");
  });

  it("the 'thinking' event between iterations is thinking", () => {
    const t = run([
      { type: "content", content: "Let me check." },
      { type: "thinking", content: "Thinking..." },
    ]);
    assert.equal(t.phase, "thinking");
  });

  it("writing after a tool round, then another tool, is working again", () => {
    const t = run([
      { type: "content", content: "Checking." },
      { type: "tool_start", tool_name: "get_recent_messages" },
    ]);
    assert.equal(t.phase, "working");
  });

  it("done ends the turn", () => {
    const t = run([{ type: "content", content: "ok" }, { type: "done" }]);
    assert.equal(t.phase, "done");
    assert.equal(t.endedAt, T0 + 2);
    assert.equal(isTurnLive(t), false);
  });

  it("events after the end are ignored (same object)", () => {
    const done = run([{ type: "done" }]);
    const after = reduceTurn(done, { type: "tool_start", tool_name: "web_search" }, T0 + 50);
    assert.equal(after, done);
  });

  it("unrelated events return the same object so React can skip a render", () => {
    const t = run([{ type: "content", content: "x" }]);
    assert.equal(reduceTurn(t, { type: "content", content: "y" }, T0 + 20), t);
    assert.equal(reduceTurn(t, { type: "memory_match" }, T0 + 21), t);
    assert.equal(reduceTurn(t, { type: "progress", step: "generating", status: "started" }, T0 + 22), t);
  });

  it("running out of tool iterations is remembered", () => {
    const t = run([{ type: "progress", step: "tool_loop", status: "exhausted" }]);
    assert.equal(t.exhausted, true);
  });
});

describe("reduceTurn tools", () => {
  it("counts a Tool error / Unknown tool / BLOCKED result as failed", () => {
    const t = run([
      { type: "tool_start", tool_name: "a" },
      { type: "tool_end", tool_name: "a", result: "Tool error: nope" },
      { type: "tool_start", tool_name: "b" },
      { type: "tool_end", tool_name: "b", result: "Unknown tool: b" },
      { type: "tool_start", tool_name: "c" },
      { type: "tool_end", tool_name: "c", result: "BLOCKED: repeated call" },
      { type: "tool_start", tool_name: "d" },
      { type: "tool_end", tool_name: "d", result: "fine" },
    ]);
    assert.deepEqual(t.tools, { running: 0, ok: 1, failed: 3 });
  });

  it("an execution tool fails by its execution_result, not its text", () => {
    const t = run([
      { type: "tool_start", tool_name: "execute_code" },
      { type: "code", code: "1/0", language: "python" },
      { type: "execution_result", success: false, duration_ms: 3 },
      { type: "tool_end", tool_name: "execute_code", result: "ZeroDivisionError" },
    ]);
    assert.deepEqual(t.tools, { running: 0, ok: 0, failed: 1 });
    assert.equal(t.lastToolEvent?.status, "error");
  });

  it("an execution tool that succeeded is ok", () => {
    const t = run([
      { type: "tool_start", tool_name: "execute_code" },
      { type: "execution_result", success: true },
      { type: "tool_end", tool_name: "execute_code", result: "42" },
    ]);
    assert.deepEqual(t.tools, { running: 0, ok: 1, failed: 0 });
  });

  it("lastToolEvent carries a rising sequence so a watcher sees each one", () => {
    let t = run([{ type: "tool_start", tool_name: "web_search" }]);
    assert.equal(t.lastToolEvent?.tool, "web_search");
    assert.equal(t.lastToolEvent?.status, "running");
    const s1 = t.lastToolEvent!.seq;
    t = reduceTurn(t, { type: "tool_end", tool_name: "web_search", result: "ok" }, T0 + 30);
    assert.equal(t.lastToolEvent?.status, "ok");
    assert.ok(t.lastToolEvent!.seq > s1);
    assert.equal(t.lastToolEvent?.at, T0 + 30);
  });

  it("a stray tool_end never drives running below zero", () => {
    const t = run([{ type: "tool_end", tool_name: "x", result: "ok" }]);
    assert.equal(t.tools.running, 0);
  });

  it("Claude Code sessions count as tools", () => {
    let t = run([{ type: "cc_session_start", task_id: "k1" }]);
    assert.equal(t.phase, "working");
    assert.equal(t.tools.running, 1);
    t = reduceTurn(t, { type: "cc_session_end", task_id: "k1", status: "failed" }, T0 + 40);
    assert.deepEqual(t.tools, { running: 0, ok: 0, failed: 1 });
  });
});

describe("endTurn", () => {
  it("stop clears running tools without calling them ok or failed", () => {
    const live = run([{ type: "tool_start", tool_name: "web_search" }]);
    const t = endTurn(live, "stopped", T0 + 99);
    assert.equal(t.phase, "stopped");
    assert.equal(t.endedAt, T0 + 99);
    assert.deepEqual(t.tools, { running: 0, ok: 0, failed: 0 });
    assert.equal(isTurnLive(t), false);
  });

  it("error is terminal", () => {
    const t = endTurn(startTurn("a", "c", T0), "error", T0 + 1);
    assert.equal(t.phase, "error");
    assert.equal(isTerminalPhase(t.phase), true);
  });

  it("the first ending wins", () => {
    const done = endTurn(startTurn("a", "c", T0), "done", T0 + 1);
    assert.equal(endTurn(done, "error", T0 + 2), done);
  });

  it("isTurnLive is false for no turn", () => {
    assert.equal(isTurnLive(null), false);
    assert.equal(isTurnLive(undefined), false);
  });
});

describe("turnOutcomeFor", () => {
  it("maps how a stream ended to the turn's ending", () => {
    assert.equal(turnOutcomeFor("done"), "done");
    assert.equal(turnOutcomeFor("stopped"), "stopped");
    assert.equal(turnOutcomeFor("cut"), "stopped", "a cut stream is not an error: the server may still finish");
    assert.equal(turnOutcomeFor("error"), "error");
  });
});

describe("reasoning heading", () => {
  it("parses the latest **Heading** from Codex reasoning summaries", () => {
    const text = "**Checking the calendar**\n\nEllis asked about Friday.\n\n**Drafting a reply**\n\nI'll keep it short.";
    assert.equal(latestReasoningHeading(text), "Drafting a reply");
  });

  it("ignores a heading still being streamed", () => {
    assert.equal(latestReasoningHeading("**Checking the calendar**\n\nEllis asked.\n\n**Draft"), "Checking the calendar");
  });

  it("is null without a heading", () => {
    assert.equal(latestReasoningHeading("just prose"), null);
    assert.equal(latestReasoningHeading(""), null);
  });

  it("prefers headings that open a line over inline bold", () => {
    const text = "**Looking at Parker's thread**\n\nHe said **tomorrow** twice.";
    assert.equal(latestReasoningHeading(text), "Looking at Parker's thread");
  });

  it("falls back to inline bold when the parts were glued", () => {
    assert.equal(latestReasoningHeading("Done with that.**Next step**"), "Next step");
  });

  it("the turn follows the heading across word-sized deltas", () => {
    const words = ["**Checking ", "the ", "calendar**", "\n\nEllis ", "asked.", "\n\n**Drafting ", "a ", "reply**"];
    let t = startTurn("a", "c", T0);
    const seen: (string | null)[] = [];
    for (const w of words) {
      t = reduceTurn(t, { type: "reasoning", content: w }, T0);
      seen.push(t.reasoningHeading);
    }
    assert.equal(seen[1], null);
    assert.equal(seen[2], "Checking the calendar");
    assert.equal(seen[6], "Checking the calendar");
    assert.equal(seen[7], "Drafting a reply");
  });

  it("keeps only a bounded tail of reasoning in the turn", () => {
    let t = startTurn("a", "c", T0);
    for (let i = 0; i < 500; i++) t = reduceTurn(t, { type: "reasoning", content: "word " }, T0);
    assert.ok(t.reasoningTail.length <= 400);
  });
});

describe("isToolFailure", () => {
  it("matches the backend's failure prefixes only", () => {
    assert.equal(isToolFailure("Tool error: x"), true);
    assert.equal(isToolFailure("Unknown tool: x"), true);
    assert.equal(isToolFailure("BLOCKED: x"), true);
    assert.equal(isToolFailure("It said Tool error: inside"), false);
    assert.equal(isToolFailure(undefined), false);
  });
});

describe("outward acts", () => {
  it("counts a send, a push or a note that went out", () => {
    const t = run([
      { type: "tool_start", tool_name: "send_imessage" },
      { type: "tool_end", tool_name: "send_imessage", result: "Message sent to Parker" },
      { type: "tool_start", tool_name: "send_push_notification" },
      { type: "tool_end", tool_name: "send_push_notification", result: "Push sent to 2 devices" },
      { type: "tool_start", tool_name: "web_search" },
      { type: "tool_end", tool_name: "web_search", result: "3 results" },
    ]);
    assert.equal(t.outward, 2);
    assert.equal(t.tools.ok, 3);
  });

  it("a failed or held send is not an outward act", () => {
    const t = run([
      { type: "tool_start", tool_name: "send_message" },
      { type: "tool_end", tool_name: "send_message", result: "Tool error: blocked — the text contains what looks like a secret" },
      { type: "tool_start", tool_name: "send_imessage" },
      { type: "tool_end", tool_name: "send_imessage", result: "Waiting for Ellis's approval (#12)" },
      { type: "tool_start", tool_name: "send_imessage" },
      { type: "tool_end", tool_name: "send_imessage", result: "Already sent that to Parker 3 minutes ago." },
    ]);
    assert.equal(t.outward, 0);
  });

  it("starts at zero", () => {
    assert.equal(startTurn("a", "c", T0).outward, 0);
  });
});
