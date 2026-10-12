// `/chat?prompt=` fills the composer without sending (CLAUDE.md's route
// table). The helpers lived with Edward's ideas (plan Phase 5, 2026-09-24);
// the ideas went with unit G (2026-10-11) and the prompt param stayed.
//
// The chat page reads the prompt once into the composer (Ellis presses send)
// and drops it from the URL so a reload or back does not type it again.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { chatPromptHref, readPromptParam, withoutPrompt } from "../lib/chatPrompt.ts";

describe("chatPromptHref / readPromptParam", () => {
  it("round-trips a prompt through the URL", () => {
    const href = chatPromptHref("  Plan a climb with Alex & Jo?  ");
    assert.equal(href, "/chat?prompt=Plan%20a%20climb%20with%20Alex%20%26%20Jo%3F");
    const params = new URLSearchParams(href.split("?")[1]);
    assert.equal(readPromptParam(params), "Plan a climb with Alex & Jo?");
  });
  it("reads nothing from a URL without one", () => {
    assert.equal(readPromptParam(new URLSearchParams("c=abc")), null);
    assert.equal(readPromptParam(new URLSearchParams("prompt=%20%20")), null);
  });
  it("caps a pasted monster", () => {
    const long = "x".repeat(5000);
    assert.equal(readPromptParam(new URLSearchParams(`prompt=${long}`))?.length, 1000);
  });
});

describe("withoutPrompt", () => {
  it("keeps the conversation and drops the prompt", () => {
    assert.equal(withoutPrompt("?c=abc&prompt=hi"), "?c=abc");
    assert.equal(withoutPrompt("prompt=hi"), "");
    assert.equal(withoutPrompt(""), "");
  });
});
