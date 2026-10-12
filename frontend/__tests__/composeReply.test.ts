import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  applyComposeEvent,
  composeCardStatus,
  initialComposeState,
  replyBlocks,
  thinkingLines,
} from "../app/(display)/display/lib/composeBarController.ts";

describe("applyComposeEvent", () => {
  it("keeps thinking and reply apart and notes tools", () => {
    let s = initialComposeState();
    s = applyComposeEvent(s, { type: "reasoning", content: "Ellis wants " });
    s = applyComposeEvent(s, { type: "reasoning", content: "music." });
    s = applyComposeEvent(s, { type: "thinking", content: "Searching…" });
    s = applyComposeEvent(s, { type: "tool_start", tool_name: "web_search" });
    s = applyComposeEvent(s, { type: "content", content: "Here " });
    s = applyComposeEvent(s, { type: "content", content: "you go." });
    assert.equal(s.thinking, "Ellis wants music.\nSearching…");
    assert.equal(s.reply, "Here you go.");
    assert.deepEqual(s.tools, ["web_search"]);
  });
  it("ignores events it does not draw", () => {
    const s = applyComposeEvent(initialComposeState(), { type: "memory_match", content: "x" });
    assert.deepEqual(s, initialComposeState());
  });
});

describe("composeCardStatus", () => {
  it("narrates the phase", () => {
    assert.equal(composeCardStatus(initialComposeState(), true), "thinking…");
    const thinking = applyComposeEvent(initialComposeState(), { type: "reasoning", content: "hm" });
    assert.equal(composeCardStatus(thinking, true), "thinking…");
    const tool = applyComposeEvent(thinking, { type: "tool_start", tool_name: "web_search" });
    assert.equal(composeCardStatus(tool, true), "Web search…", "the tool in words, never snake_case");
    const done = applyComposeEvent(tool, { type: "content", content: "ok" });
    assert.equal(composeCardStatus(done, false), "thought · 1 tool");
  });
});

// Round 2 (2026-09-28): what Ellis saw on the Mac mini — raw markdown in the
// card ("**Planning larger heartbeat call**", "**Today so far…**") and a
// stack of bare "Thinking..." lines. The card now cleans its thinking lines
// and draws the answer's light markdown as React nodes (no HTML strings).
describe("thinkingLines", () => {
  it("the stream Ellis saw: placeholders collapse away, bold headings become the line", () => {
    let s = initialComposeState();
    s = applyComposeEvent(s, { type: "thinking", content: "Thinking..." });
    s = applyComposeEvent(s, { type: "thinking", content: "Thinking..." });
    s = applyComposeEvent(s, { type: "reasoning", content: "**Planning larger heartbeat call**" });
    s = applyComposeEvent(s, { type: "thinking", content: "Thinking..." });
    s = applyComposeEvent(s, { type: "reasoning", content: "**Using the project doc**\n\nThe doc has the `Riverside Fest` dates." });
    s = applyComposeEvent(s, { type: "thinking", content: "Thinking..." });
    s = applyComposeEvent(s, { type: "thinking", content: "Thinking..." });
    assert.deepEqual(thinkingLines(s.thinking), [
      { text: "Planning larger heartbeat call", heading: true },
      { text: "Using the project doc", heading: true },
      { text: "The doc has the Riverside Fest dates.", heading: false },
    ]);
  });

  it("two summaries glued together split at the second heading", () => {
    assert.deepEqual(thinkingLines("**Reading**\n\nShe asked twice.**Deciding**\n\n- Leave a note\n# Done"), [
      { text: "Reading", heading: true },
      { text: "She asked twice.", heading: false },
      { text: "Deciding", heading: true },
      { text: "Leave a note", heading: false },
      { text: "Done", heading: false },
    ]);
  });

  it("only placeholders is nothing at all (the card says thinking… once)", () => {
    assert.deepEqual(thinkingLines("Thinking...\nThinking...\nThinking…"), []);
    assert.deepEqual(thinkingLines(""), []);
  });

  it("a real thought that begins with the word is kept", () => {
    assert.deepEqual(thinkingLines("Thinking about Saturday."), [{ text: "Thinking about Saturday.", heading: false }]);
  });
});

describe("replyBlocks", () => {
  it("bold, bullets and paragraphs, the way the Today summary came", () => {
    const reply = "**Today so far, as of 4:23 PM:**\n\n- **Cabin weekend:** Budget saved.\n* **Riverside Fest:** Tickets held.\n\n**Still open:** Parker's headcount.";
    assert.deepEqual(replyBlocks(reply), [
      { kind: "p", lines: [[{ text: "Today so far, as of 4:23 PM:", bold: true }]] },
      {
        kind: "ul",
        items: [
          [{ text: "Cabin weekend:", bold: true }, { text: " Budget saved.", bold: false }],
          [{ text: "Riverside Fest:", bold: true }, { text: " Tickets held.", bold: false }],
        ],
      },
      { kind: "p", lines: [[{ text: "Still open:", bold: true }, { text: " Parker's headcount.", bold: false }]] },
    ]);
  });

  it("a lead-in line and its list in one block; lines inside a paragraph stay lines", () => {
    assert.deepEqual(replyBlocks("Ran it:\n- Ellis: $441\n- Total: $735\nSame as before.\nThe split holds."), [
      { kind: "p", lines: [[{ text: "Ran it:", bold: false }]] },
      { kind: "ul", items: [[{ text: "Ellis: $441", bold: false }], [{ text: "Total: $735", bold: false }]] },
      { kind: "p", lines: [[{ text: "Same as before.", bold: false }], [{ text: "The split holds.", bold: false }]] },
    ]);
  });

  it("a heading is a bold line; backticks go; a bold still being written is bold without its stars", () => {
    assert.deepEqual(replyBlocks("## Plan\n\nRun `ls` then **check"), [
      { kind: "p", lines: [[{ text: "Plan", bold: true }]] },
      { kind: "p", lines: [[{ text: "Run ls then ", bold: false }, { text: "check", bold: true }]] },
    ]);
  });

  it("nothing is nothing", () => assert.deepEqual(replyBlocks("  \n\n "), []));
});
