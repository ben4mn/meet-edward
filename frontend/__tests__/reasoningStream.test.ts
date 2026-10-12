// R/G TDD for the frontend half of N4 — surfacing Codex's reasoning
// summary stream into the chat UI.
//
// Backend now emits SSE events of type "reasoning" carrying delta text
// while the LLM is in its thinking phase. The frontend needs to:
//   1. Recognize "reasoning" as a valid StreamEventType
//   2. Accumulate event.content into the message's reasoningContent
//   3. Keep reasoning separate from main content so the UI can render
//      it in a distinct (italic, muted) panel above the answer.
//
// This file pins the contract for a small pure reducer that's shared
// between ChatContext (live SSE consumption) and MessageBubble
// (rendering). Run: node --test frontend/__tests__/reasoningStream.test.ts

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  applyReasoningDelta,
  applyContentDelta,
  type StreamMessageState,
} from "../lib/streamReducer.ts";


describe("applyReasoningDelta", () => {
  it("appends reasoning text into reasoningContent", () => {
    const before: StreamMessageState = { content: "", reasoningContent: "" };
    const after = applyReasoningDelta(before, "Considering ");
    assert.equal(after.reasoningContent, "Considering ");
    assert.equal(after.content, "");
  });

  it("accumulates across multiple deltas", () => {
    let s: StreamMessageState = { content: "", reasoningContent: "" };
    s = applyReasoningDelta(s, "Step 1. ");
    s = applyReasoningDelta(s, "Step 2. ");
    s = applyReasoningDelta(s, "Step 3.");
    assert.equal(s.reasoningContent, "Step 1. Step 2. Step 3.");
  });

  it("does not mutate the input object (returns a new state)", () => {
    const before: StreamMessageState = { content: "x", reasoningContent: "y" };
    const after = applyReasoningDelta(before, "z");
    assert.notEqual(after, before, "should return a new object");
    assert.equal(before.reasoningContent, "y", "input should be unchanged");
  });

  it("ignores empty deltas", () => {
    const before: StreamMessageState = { content: "", reasoningContent: "abc" };
    const after = applyReasoningDelta(before, "");
    assert.equal(after.reasoningContent, "abc");
    assert.equal(after, before, "no-op deltas should return the same object");
  });

  it("treats undefined reasoningContent as empty string", () => {
    const before: StreamMessageState = { content: "" };
    const after = applyReasoningDelta(before, "Thinking");
    assert.equal(after.reasoningContent, "Thinking");
  });
});


describe("applyContentDelta", () => {
  it("appends to content without touching reasoningContent", () => {
    const before: StreamMessageState = { content: "", reasoningContent: "thoughts" };
    const after = applyContentDelta(before, "Hello");
    assert.equal(after.content, "Hello");
    assert.equal(after.reasoningContent, "thoughts", "reasoning trace must persist alongside content");
  });
});
