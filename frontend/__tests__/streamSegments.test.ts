// The streaming assistant message, folded event by event by a pure reducer
// (it used to be ~500 lines of setMessages closures in ChatContext with a
// mutable code-block object shared between them).
//
// What this pins:
//  - Iteration texts never glue: text that arrives after a tool or thinking
//    event starts a new paragraph ("Let me check.\n\nFound it.").
//  - Reasoning that resumes after other work starts a new paragraph, and a
//    Codex summary part glued onto the last one ("…time.**Next**") is split.
//  - message.trace keeps the turn in arrival order (reasoning, tools, code,
//    memory, text) for a transcript that interleaves them.
//  - Every end of a stream clears the live flags: no isThinking, no pending
//    tool args, no tool left "running" (it is "stopped"), no code executing.
//  - Everything is immutable; replaceMessage only touches the message by id.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  newCursor,
  advanceCursor,
  applyStreamEvent,
  applyUserMessageEvent,
  finishStreamMessage,
  replaceMessage,
  joinReasoning,
  joinContent,
  type StreamCursor,
} from "../lib/streamReducer.ts";
import type { Message } from "../lib/ChatContext.tsx";
import type { StreamEvent } from "../lib/api.ts";

const ID = "a1";

function blank(): Message {
  return { id: ID, role: "assistant", content: "", codeBlocks: [], progressSteps: [], isThinking: false, wasInterrupted: false };
}

type Ev = Partial<StreamEvent> & { type: StreamEvent["type"] | "error" };

function feed(events: Ev[], start: Message = blank(), cursor: StreamCursor = newCursor()): { msg: Message; cursor: StreamCursor } {
  let msg = start;
  let cur = cursor;
  events.forEach((e, i) => {
    const ev = { conversation_id: "c1", ...e } as StreamEvent;
    const step = advanceCursor(cur, ev, ID, 1000 + i);
    cur = step.cursor;
    msg = applyStreamEvent(msg, ev, step.ctx);
  });
  return { msg, cursor: cur };
}

describe("content segments", () => {
  it("consecutive deltas join as they are", () => {
    const { msg } = feed([
      { type: "content", content: "Hello " },
      { type: "content", content: "Ellis" },
    ]);
    assert.equal(msg.content, "Hello Ellis");
  });

  it("text after a tool round starts a new paragraph", () => {
    const { msg } = feed([
      { type: "content", content: "Let me check." },
      { type: "tool_args_delta", tool_name: "web_search", call_id: "k", delta: "{}" },
      { type: "tool_start", tool_name: "web_search" },
      { type: "tool_end", tool_name: "web_search", result: "ok" },
      { type: "content", content: "Found it." },
    ]);
    assert.equal(msg.content, "Let me check.\n\nFound it.");
  });

  it("text after the 'thinking' event between iterations starts a new paragraph", () => {
    const { msg } = feed([
      { type: "content", content: "One." },
      { type: "thinking", content: "Thinking..." },
      { type: "content", content: "Two." },
    ]);
    assert.equal(msg.content, "One.\n\nTwo.");
  });

  it("the first text of a turn gets no leading break", () => {
    const { msg } = feed([
      { type: "progress", step: "memory_search", status: "started", message: "Searching memory..." },
      { type: "reasoning", content: "hm" },
      { type: "content", content: "Hi" },
    ]);
    assert.equal(msg.content, "Hi");
  });

  it("does not add a break that is already there", () => {
    assert.equal(joinContent("One.\n\n", "Two", true), "One.\n\nTwo");
    assert.equal(joinContent("One.\n", "Two", true), "One.\n\nTwo");
    assert.equal(joinContent("One.", "\n\nTwo", true), "One.\n\nTwo");
    assert.equal(joinContent("", "Two", true), "Two");
  });

  it("the 'generating' progress between the answer and done is not a break", () => {
    const { msg } = feed([
      { type: "content", content: "A" },
      { type: "progress", step: "generating", status: "started", message: "Generating response..." },
      { type: "content", content: "B" },
    ]);
    assert.equal(msg.content, "AB");
  });
});

describe("reasoning segments", () => {
  it("reasoning resuming after a tool starts a new paragraph", () => {
    const { msg } = feed([
      { type: "reasoning", content: "First I look." },
      { type: "tool_start", tool_name: "web_search" },
      { type: "tool_end", tool_name: "web_search", result: "ok" },
      { type: "reasoning", content: "Now I answer." },
    ]);
    assert.equal(msg.reasoningContent, "First I look.\n\nNow I answer.");
  });

  it("splits a summary part glued onto the last one", () => {
    assert.equal(joinReasoning("I checked the time.", "**Drafting", false), "I checked the time.\n\n**Drafting");
    assert.equal(joinReasoning("Done.", " **Next**", false), "Done. **Next**", "a space means it was not glued");
  });

  it("does not split inside a heading that ends with a period", () => {
    assert.equal(joinReasoning("**All done.", "**", false), "**All done.**");
  });

  it("splits a glue point inside one delta", () => {
    assert.equal(joinReasoning("I", " looked.**Next step**", false), "I looked.\n\n**Next step**");
  });
});

describe("trace", () => {
  it("records the turn in arrival order", () => {
    const { msg } = feed([
      { type: "progress", step: "memory_search", status: "started", message: "Searching memory..." },
      { type: "progress", step: "memory_search", status: "completed", message: "Found 3 memories", count: 3 },
      { type: "reasoning", content: "Ellis " },
      { type: "reasoning", content: "asked." },
      { type: "content", content: "Let me " },
      { type: "content", content: "check." },
      { type: "tool_start", tool_name: "web_search", args_summary: "flights" },
      { type: "tool_end", tool_name: "web_search", result: "3 results" },
      { type: "tool_start", tool_name: "execute_code" },
      { type: "code", code: "print(1)", language: "python" },
      { type: "execution_result", success: true, duration_ms: 5 },
      { type: "tool_end", tool_name: "execute_code", result: "1" },
      { type: "content", content: "Found it." },
    ]);
    const kinds = (msg.trace ?? []).map((t) => t.kind);
    assert.deepEqual(kinds, ["memory", "reasoning", "text", "tool", "code", "text"]);
    const [mem, reasoning, text1, tool, code, text2] = msg.trace!;
    assert.deepEqual(mem, { kind: "memory", status: "completed", count: 3 });
    assert.deepEqual(reasoning, { kind: "reasoning", text: "Ellis asked." });
    assert.deepEqual(text1, { kind: "text", text: "Let me check." });
    assert.equal(tool.kind === "tool" && tool.id, msg.toolActivities![0].id);
    assert.equal(code.kind === "code" && code.id, msg.codeBlocks![0].id);
    assert.deepEqual(text2, { kind: "text", text: "Found it." });
  });
});

describe("tools and code", () => {
  it("tool_start clears the pending args preview for that tool, execution tools too", () => {
    const { msg } = feed([
      { type: "tool_args_delta", tool_name: "execute_code", call_id: "k1", delta: "{\"code\":" },
      { type: "tool_args_delta", tool_name: "web_search", call_id: "k2", delta: "{\"q\"" },
      { type: "tool_start", tool_name: "execute_code" },
    ]);
    assert.deepEqual(Object.keys(msg.pendingToolArgs ?? {}), ["k2"]);
  });

  it("marks the matching running activity done with a duration", () => {
    const { msg } = feed([
      { type: "tool_start", tool_name: "web_search" },
      { type: "tool_end", tool_name: "web_search", result: "Tool error: 500" },
    ]);
    const a = msg.toolActivities![0];
    assert.equal(a.status, "error");
    assert.equal(a.result, "Tool error: 500");
    assert.equal(a.duration_ms, 1);
  });

  it("an execution tool that never reported a result stops executing at tool_end", () => {
    const { msg } = feed([
      { type: "tool_start", tool_name: "execute_shell" },
      { type: "code", code: "ls", language: "bash" },
      { type: "tool_end", tool_name: "execute_shell", result: "Tool error: timeout" },
    ]);
    assert.equal(msg.codeBlocks![0].isExecuting, false);
    assert.equal(msg.codeBlocks![0].success, false);
    assert.equal(msg.codeBlocks![0].language, "bash");
  });

  it("execution output accumulates on the current block", () => {
    const { msg } = feed([
      { type: "tool_start", tool_name: "execute_code" },
      { type: "code", code: "for i in range(2): print(i)", language: "python" },
      { type: "execution_output", output: "0\n" },
      { type: "execution_output", output: "1\n" },
      { type: "execution_result", success: true, duration_ms: 12 },
      { type: "tool_end", tool_name: "execute_code", result: "0\n1" },
    ]);
    const b = msg.codeBlocks![0];
    assert.equal(b.output, "0\n1\n");
    assert.equal(b.success, true);
    assert.equal(b.duration_ms, 12);
  });

  it("ids are deterministic (safe under a double-invoked state updater)", () => {
    const a = feed([{ type: "tool_start", tool_name: "web_search" }]).msg;
    const b = feed([{ type: "tool_start", tool_name: "web_search" }]).msg;
    assert.equal(a.toolActivities![0].id, b.toolActivities![0].id);
  });
});

describe("immutability", () => {
  it("never mutates the message it was given", () => {
    const start = blank();
    const frozen = JSON.stringify(start);
    feed([
      { type: "content", content: "x" },
      { type: "tool_start", tool_name: "execute_code" },
      { type: "execution_output", output: "y" },
    ], start);
    assert.equal(JSON.stringify(start), frozen);
  });

  it("an event that changes nothing returns the same message", () => {
    const { msg, cursor } = feed([{ type: "content", content: "x" }]);
    const ev = { type: "done", conversation_id: "c1" } as StreamEvent;
    const step = advanceCursor(cursor, ev, ID, 5);
    assert.equal(applyStreamEvent(msg, ev, step.ctx), msg);
  });
});

describe("finishStreamMessage", () => {
  function live(): Message {
    return feed([
      { type: "thinking", content: "Thinking..." },
      { type: "tool_args_delta", tool_name: "x", call_id: "k", delta: "{" },
      { type: "tool_start", tool_name: "web_search" },
      { type: "tool_start", tool_name: "execute_code" },
      { type: "progress", step: "memory_search", status: "started", message: "Searching memory..." },
    ]).msg;
  }

  it("done clears every live flag", () => {
    const m = finishStreamMessage({ ...live(), isThinking: true }, "done");
    assert.equal(m.isThinking, false);
    assert.equal(m.pendingToolArgs, undefined);
    assert.equal(m.toolActivities![0].status, "stopped");
    assert.equal(m.codeBlocks![0].isExecuting, false);
    assert.equal(m.progressSteps!.find((s) => s.step === "memory_search")!.status, "stopped");
    const mem = m.trace!.find((t) => t.kind === "memory");
    assert.equal(mem && mem.kind === "memory" && mem.status, "stopped");
    assert.equal(m.wasInterrupted, false);
  });

  it("stop keeps the partial answer and marks it interrupted", () => {
    const m = finishStreamMessage({ ...live(), content: "Half" }, "stopped");
    assert.equal(m.content, "Half");
    assert.equal(m.wasInterrupted, true);
    assert.equal(m.isThinking, false);
  });

  it("a cut stream clears the live flags without claiming Ellis stopped it", () => {
    const m = finishStreamMessage({ ...live(), content: "Half" }, "cut");
    assert.equal(m.wasInterrupted, false);
    assert.equal(m.pendingToolArgs, undefined);
  });

  it("error keeps a partial reply as it came, and marks the message cut off", () => {
    const m = finishStreamMessage({ ...live(), content: "Half" }, "error");
    assert.equal(m.content, "Half");
    assert.equal(m.wasCutOff, true);
    assert.equal(m.isThinking, false);
    assert.equal(m.toolActivities![0].status, "stopped");
  });

  it("error with nothing streamed shows the refusal (his answer), else only the cut-off notice", () => {
    const refused = finishStreamMessage(blank(), "error", { refusal: "Not now." });
    assert.equal(refused.content, "Not now.");
    assert.equal(refused.wasCutOff, undefined);
    const cut = finishStreamMessage(blank(), "error");
    assert.equal(cut.content, "");
    assert.equal(cut.wasCutOff, true);
  });

  it("does not mutate its input", () => {
    const m = live();
    const before = JSON.stringify(m);
    finishStreamMessage(m, "error");
    assert.equal(JSON.stringify(m), before);
  });
});

describe("replaceMessage", () => {
  const user: Message = { id: "u1", role: "user", content: "hi" };
  it("replaces only the message with that id", () => {
    const list = [user, blank()];
    const next = replaceMessage(list, ID, { ...blank(), content: "yo" });
    assert.notEqual(next, list);
    assert.equal(next[0], user);
    assert.equal(next[1].content, "yo");
  });

  it("returns the same array when the id is not on screen", () => {
    const list = [user];
    assert.equal(replaceMessage(list, ID, blank()), list);
  });

  it("accepts an updater", () => {
    const next = replaceMessage([blank()], ID, (m) => ({ ...m, content: "z" }));
    assert.equal(next[0].content, "z");
  });
});

describe("applyUserMessageEvent", () => {
  it("fills a voice memo's transcript and stored file ids", () => {
    const user: Message = {
      id: "u1", role: "user", content: "",
      attachments: [{ id: "att-1", filename: "memo.m4a", mime_type: "audio/mp4", size: 10 }],
    };
    const ev = { type: "user_message", conversation_id: "c1", content: "What's left?", attachments: [{ file_id: "f9" }] } as StreamEvent;
    const next = applyUserMessageEvent(user, ev);
    assert.equal(next.content, "What's left?");
    assert.equal(next.attachments![0].file_id, "f9");
    assert.equal(user.content, "", "input untouched");
  });
});
