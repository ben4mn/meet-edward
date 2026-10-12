// R/G red for the chat work rail.
//
// Ellis is particular about seeing Edward's thinking, tool calls and code
// while a turn runs. The rail streams everything open, then folds to one
// line when the answer lands. The fold is a preference:
//   collapse — live, then folded (default)
//   open     — always open
//   tap      — a status line while live; tap to see the trace
// The summary label and the open/closed decision are pure so the React
// side stays a thin shell.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  summarizeWork,
  isRailOpen,
  parseWorkPref,
  liveStatus,
  type WorkMessage,
} from "../lib/workRail.ts";

const base: WorkMessage = { role: "assistant", content: "" };

describe("summarizeWork", () => {
  it("is empty when nothing happened", () => {
    const s = summarizeWork(base);
    assert.equal(s.hasWork, false);
  });

  it("counts thinking, memories, tools, code and failures", () => {
    const s = summarizeWork({
      ...base,
      content: "done",
      reasoningContent: "Ellis wants the address…",
      progressSteps: [
        { id: "memory_search-main", step: "memory_search", status: "completed", message: "Found 3", timestamp: 1, count: 3 },
        { id: "tool_execution-web_search", step: "tool_execution", status: "completed", message: "x", timestamp: 2, tool_name: "web_search" },
      ],
      toolActivities: [
        { id: "a", tool_name: "web_search", status: "completed", timestamp: 3 },
        { id: "b", tool_name: "update_contact", status: "error", timestamp: 4 },
      ],
      codeBlocks: [{ id: "c", code: "print(1)", language: "python", isExecuting: false, success: true }],
    });
    assert.equal(s.hasWork, true);
    assert.equal(s.memories, 3);
    assert.equal(s.tools, 3); // 2 activities + 1 code block
    assert.equal(s.failed, 1);
    assert.equal(s.thinking, true);
    assert.equal(s.label, "Thought · searched memory · 3 tools, 1 failed");
  });

  it("labels a thinking-only turn", () => {
    const s = summarizeWork({ ...base, content: "hi", reasoningContent: "short" });
    assert.equal(s.label, "Thought");
  });

  it("labels tools without thinking", () => {
    const s = summarizeWork({
      ...base,
      content: "hi",
      toolActivities: [{ id: "a", tool_name: "web_search", status: "completed", timestamp: 3 }],
    });
    assert.equal(s.label, "1 tool");
  });
});

describe("liveStatus", () => {
  it("narrates what is happening right now", () => {
    // 2026-09-26: capitalised, and tools read as what he is doing (lib/toolLabels.ts).
    assert.equal(liveStatus({ ...base, isThinking: true }), "Thinking…");
    assert.equal(
      liveStatus({ ...base, toolActivities: [{ id: "a", tool_name: "get_recent_messages", status: "running", timestamp: 1 }] }),
      "Reading recent messages…",
    );
    assert.equal(
      liveStatus({
        ...base,
        toolActivities: [{ id: "a", tool_name: "browser_open", status: "running", args_summary: "https://www.nws.gov/x", timestamp: 1 }],
      }),
      "Opening nws.gov…",
    );
    assert.equal(
      liveStatus({ ...base, codeBlocks: [{ id: "c", code: "", language: "python", isExecuting: true }] }),
      "Running Python…",
    );
    assert.equal(liveStatus({ ...base, content: "partial" }), "Writing…");
  });
});

describe("isRailOpen", () => {
  it("collapse: open while live, folded when done", () => {
    assert.equal(isRailOpen("collapse", true, null), true);
    assert.equal(isRailOpen("collapse", false, null), false);
  });
  it("open: always open", () => {
    assert.equal(isRailOpen("open", true, null), true);
    assert.equal(isRailOpen("open", false, null), true);
  });
  it("tap: closed until tapped", () => {
    assert.equal(isRailOpen("tap", true, null), false);
    assert.equal(isRailOpen("tap", false, null), false);
  });
  it("a manual toggle wins over the preference in every state", () => {
    assert.equal(isRailOpen("collapse", true, false), false);
    assert.equal(isRailOpen("tap", false, true), true);
    assert.equal(isRailOpen("open", false, false), false);
  });
});

// 2026-09-26 review: the timeline stayed open through the whole answer and
// folded at `done`, yanking the text Ellis was reading up 200–330px at the
// last token. It folds when the answer starts instead — above empty space,
// before there is anything to read — and stays folded for the rest of the
// turn (a preamble followed by more tools must not make it flap).
describe("isRailOpen once the answer has begun", () => {
  it("collapse: folds as soon as the answer starts, while still live", () => {
    assert.equal(isRailOpen("collapse", true, null, false), true);
    assert.equal(isRailOpen("collapse", true, null, true), false);
  });
  it("open and tap are unchanged, and Ellis's own toggle still wins", () => {
    assert.equal(isRailOpen("open", true, null, true), true);
    assert.equal(isRailOpen("tap", true, null, true), false);
    assert.equal(isRailOpen("collapse", true, true, true), true);
  });
});

describe("parseWorkPref", () => {
  it("accepts the three values and falls back to collapse", () => {
    assert.equal(parseWorkPref("open"), "open");
    assert.equal(parseWorkPref("tap"), "tap");
    assert.equal(parseWorkPref("collapse"), "collapse");
    assert.equal(parseWorkPref("garbage"), "collapse");
    assert.equal(parseWorkPref(null), "collapse");
  });
});

// 2026-09-25: the rail reads the turn model, not the content string.
import { isLiveMessage } from "../lib/workRail.ts";
import { startTurn, reduceTurn, endTurn } from "../lib/turnModel.ts";

describe("liveStatus with the turn", () => {
  const t0 = startTurn("a1", "c1", 0);

  it("names the reasoning heading while he thinks", () => {
    const t = reduceTurn(t0, { type: "reasoning", content: "**Checking the calendar**\n\nEllis asked" }, 1);
    assert.equal(liveStatus({ ...base, reasoningContent: "x" }, t), "Checking the calendar…");
  });

  it("says he is searching memory while recalling", () => {
    const t = reduceTurn(t0, { type: "progress", step: "memory_search", status: "started" }, 1);
    assert.equal(liveStatus(base, t), "Searching memory…");
  });

  it("names the running tool while working", () => {
    const t = reduceTurn(t0, { type: "tool_start", tool_name: "web_search" }, 1);
    assert.equal(
      liveStatus({ ...base, toolActivities: [{ id: "a", tool_name: "web_search", status: "running", timestamp: 1 }] }, t),
      "Searching the web…",
    );
  });

  it("between tools with text already on screen it is thinking, not writing", () => {
    const t = reduceTurn(
      reduceTurn(reduceTurn(t0, { type: "content", content: "Let me check." }, 1), { type: "tool_start", tool_name: "web_search" }, 2),
      { type: "tool_end", tool_name: "web_search", result: "ok" },
      3,
    );
    assert.equal(liveStatus({ ...base, content: "Let me check." }, t), "Thinking…");
  });

  it("writing while the answer streams", () => {
    const t = reduceTurn(t0, { type: "content", content: "Sure" }, 1);
    assert.equal(liveStatus({ ...base, content: "Sure" }, t), "Writing…");
  });

  it("an ended turn falls back to the message's own status", () => {
    const t = endTurn(t0, "done", 5);
    assert.equal(liveStatus({ ...base, content: "done" }, t), "Writing…");
  });
});

// 2026-09-26 review: with the timeline open, the running node already says
// what he is doing ("Searching memory…", "Running Python…"); the header
// saying it again was two identical lines for the whole turn. Beside an
// open timeline the header says what he is thinking about instead; folded,
// it still names the step.
describe("liveStatus beside an open timeline", () => {
  const t0 = startTurn("a1", "c1", 0);
  const running = { ...base, toolActivities: [{ id: "a", tool_name: "web_search", status: "running" as const, timestamp: 1 }] };

  it("working: never the running node's own label", () => {
    const t = reduceTurn(t0, { type: "tool_start", tool_name: "web_search" }, 1);
    assert.equal(liveStatus(running, t, { timelineOpen: true }), "Working…");
    assert.equal(liveStatus(running, t, { timelineOpen: false }), "Searching the web…");
    assert.equal(liveStatus(running, t), "Searching the web…", "folded is the default");
  });

  it("working after a reasoning heading: the heading", () => {
    let t = reduceTurn(t0, { type: "reasoning", content: "**Checking the forecast**\n\nx" }, 1);
    t = reduceTurn(t, { type: "tool_start", tool_name: "web_search" }, 2);
    assert.equal(liveStatus(running, t, { timelineOpen: true }), "Checking the forecast…");
  });

  it("recalling: not 'Searching memory…' above 'Searching memory…'", () => {
    const t = reduceTurn(t0, { type: "progress", step: "memory_search", status: "started" }, 1);
    assert.equal(liveStatus(base, t, { timelineOpen: true }), "Checking what I know…");
    assert.equal(liveStatus(base, t, { timelineOpen: false }), "Searching memory…");
  });
});

describe("summarizeWork with stopped tools", () => {
  it("counts a tool the stream ended under as stopped, not failed", () => {
    const s = summarizeWork({
      ...base,
      toolActivities: [
        { id: "a", tool_name: "web_search", status: "completed", timestamp: 1 },
        { id: "b", tool_name: "browser_open", status: "stopped", timestamp: 2 },
      ],
    });
    assert.equal(s.failed, 0);
    assert.equal(s.stopped, 1);
    assert.equal(s.label, "2 tools, 1 stopped");
  });
});

describe("isLiveMessage", () => {
  it("only the message the turn streams into is live", () => {
    assert.equal(isLiveMessage("a1", "a1"), true);
    assert.equal(isLiveMessage("a1", "a2"), false);
    assert.equal(isLiveMessage("a1", null), false);
  });
});

// 2026-09-26: the rail as one timeline, and the receipt it folds into.
import { buildTimeline, reasoningSteps, workReceipt, formatElapsed, receiptText } from "../lib/workRail.ts";

describe("liveStatus heading", () => {
  it("drops the heading's own full stop before the ellipsis", () => {
    const t = reduceTurn(startTurn("a1", "c1", 0), { type: "reasoning", content: "**Checking your calendar.**\n\nx" }, 1);
    assert.equal(liveStatus(base, t), "Checking your calendar…");
  });
});

describe("reasoningSteps", () => {
  it("splits Codex summary parts on their bold headings", () => {
    const steps = reasoningSteps(
      "**Checking what I already know**\n\nMemory first, then the web.\n\n**Looking it up**\n\nI want a **current** source.",
    );
    assert.deepEqual(steps, [
      { title: "Checking what I already know", body: "Memory first, then the web." },
      { title: "Looking it up", body: "I want a **current** source." },
    ]);
  });

  it("keeps text before the first heading, and a heading still streaming", () => {
    assert.deepEqual(reasoningSteps("Ellis asked about Saturday.\n\n**Draft"), [
      { title: null, body: "Ellis asked about Saturday." },
      { title: "Draft", body: "" },
    ]);
    assert.deepEqual(reasoningSteps(""), []);
  });
});

const TOOLS = [
  { id: "t1", tool_name: "remember_search", status: "completed" as const, args_summary: '"cabin"', timestamp: 1 },
  { id: "t2", tool_name: "browser_open", status: "completed" as const, args_summary: "https://a.com/x", timestamp: 2 },
  { id: "t3", tool_name: "send_imessage", status: "completed" as const, args_summary: "to: Parker", result: "Sent to Parker", timestamp: 3 },
];

describe("buildTimeline", () => {
  it("follows the trace's arrival order, one node per reasoning step", () => {
    const nodes = buildTimeline({
      ...base,
      reasoningContent: "**One**\n\nfirst\n\n**Two**\n\nsecond",
      toolActivities: TOOLS.slice(0, 2),
      progressSteps: [{ id: "memory_search-main", step: "memory_search", status: "completed", message: "Found 3", timestamp: 1, count: 3 }],
      trace: [
        { kind: "memory", status: "completed", count: 3 },
        { kind: "reasoning", text: "**One**\n\nfirst" },
        { kind: "tool", id: "t1" },
        { kind: "reasoning", text: "**Two**\n\nsecond" },
        { kind: "tool", id: "t2" },
        { kind: "text", text: "answer" },
      ],
    });
    assert.deepEqual(
      nodes.map((n) => (n.kind === "reasoning" ? `r:${n.title}` : n.kind === "tool" ? `t:${n.activity.tool_name}` : n.kind)),
      ["memory", "r:One", "t:remember_search", "r:Two", "t:browser_open"],
    );
    const last = nodes.filter((n) => n.kind === "reasoning").pop();
    assert.equal(last?.kind === "reasoning" && last.latest, true);
  });

  it("without a trace: memory, reasoning, tools, code, sessions", () => {
    const nodes = buildTimeline({
      ...base,
      reasoningContent: "Just thinking.",
      toolActivities: [TOOLS[1]],
      codeBlocks: [{ id: "c1", code: "print(1)", language: "python", isExecuting: false, success: true }],
      progressSteps: [{ id: "memory_search-main", step: "memory_search", status: "started", message: "…", timestamp: 1 }],
    });
    assert.deepEqual(nodes.map((n) => n.kind), ["memory", "reasoning", "tool", "code"]);
    assert.equal(nodes[0].kind === "memory" && nodes[0].status, "running");
  });

  it("a plan replaces its plan tools with one plan node", () => {
    const nodes = buildTimeline({
      ...base,
      toolActivities: [
        { id: "p1", tool_name: "create_plan", status: "completed", timestamp: 1 },
        { id: "p2", tool_name: "update_plan_step", status: "completed", timestamp: 2 },
        { id: "w", tool_name: "web_search", status: "completed", timestamp: 3 },
        { id: "p3", tool_name: "complete_plan", status: "completed", timestamp: 4 },
      ],
      planBlock: { id: "plan", steps: [{ id: "s", title: "Check", status: "completed" }], isComplete: true },
    });
    assert.deepEqual(nodes.map((n) => n.kind), ["plan", "tool"]);
  });

  it("appends tool args being composed and an exhausted loop", () => {
    const nodes = buildTimeline({
      ...base,
      pendingToolArgs: { c1: { name: "web_search", args: '{"query": "ka' } },
      progressSteps: [{ id: "tool_loop-main", step: "tool_loop", status: "exhausted" as never, message: "Ran out of tool iterations (30)", timestamp: 1 }],
    });
    assert.deepEqual(nodes.map((n) => n.kind), ["pending", "exhausted"]);
  });
});

describe("workReceipt", () => {
  it("leads with how long he thought and highlights what went out", () => {
    const parts = workReceipt(
      {
        ...base,
        content: "done",
        reasoningContent: "**x**",
        progressSteps: [{ id: "memory_search-main", step: "memory_search", status: "completed", message: "", timestamp: 1, count: 3 }],
        toolActivities: [...TOOLS, { id: "t4", tool_name: "browser_open", status: "completed", args_summary: "b.com", timestamp: 4 }],
      },
      { elapsedMs: 14_200 },
    );
    assert.equal(receiptText(parts), "Thought for 14s · texted Parker · searched memory · opened 2 pages");
    assert.deepEqual(
      parts.map((p) => p.tone),
      ["lead", "outward", "plain", "plain"],
    );
  });

  it("says Thought without a time, names what failed and counts stops", () => {
    const parts = workReceipt({
      ...base,
      reasoningContent: "**Checking the calendar**",
      toolActivities: [
        { id: "c", tool_name: "apple_calendar_list_events", status: "error", result: "Tool error: timed out", timestamp: 1 },
        { id: "w", tool_name: "web_search", status: "stopped", timestamp: 2 },
      ],
    });
    assert.equal(receiptText(parts), "Thought · searched the web · 1 stopped · couldn't check your calendar");
    assert.equal(parts[parts.length - 1].tone, "bad");
  });

  it("never says he texted someone when the send failed or was held", () => {
    const failed = workReceipt({
      ...base,
      toolActivities: [{ id: "s", tool_name: "send_imessage", status: "error", args_summary: "to: Parker", result: "Tool error: no", timestamp: 1 }],
    });
    assert.equal(receiptText(failed), "Couldn't text Parker");
    const held = workReceipt({
      ...base,
      toolActivities: [
        { id: "s", tool_name: "send_imessage", status: "completed", args_summary: "to: Parker", result: "Waiting for Ellis's approval (#4)", timestamp: 1 },
      ],
    });
    assert.equal(receiptText(held), "Held a send for your approval");
  });

  it("counts code by language and names a worked-for time without reasoning", () => {
    const parts = workReceipt(
      { ...base, codeBlocks: [{ id: "c", code: "", language: "python", isExecuting: false, success: true }] },
      { elapsedMs: 65_000 },
    );
    assert.equal(receiptText(parts), "Worked for 1m 5s · ran Python");
  });

  it("folds a long tail into 'and N more'", () => {
    const names = ["list_goals", "list_documents", "calendar", "reminders", "list_wishlist"];
    const parts = workReceipt({
      ...base,
      toolActivities: names.map((n, i) => ({ id: `x${i}`, tool_name: n, status: "completed" as const, timestamp: i })),
    });
    assert.equal(receiptText(parts), "Checked your goals · listed documents · checked your calendar · and 2 more");
  });
});

// 2026-09-26 review: a turn with no reasoning read "searched memory · ran
// Python" — a lowercase first word that looks like a truncation. The first
// part is capitalised when nothing leads it.
describe("workReceipt without a lead", () => {
  it("starts with a capital, whatever comes first", () => {
    const parts = workReceipt({
      ...base,
      progressSteps: [{ id: "memory_search-main", step: "memory_search", status: "completed", message: "", timestamp: 1, count: 1 }],
      codeBlocks: [{ id: "c", code: "", language: "python", isExecuting: false, success: true }],
    });
    assert.equal(receiptText(parts), "Searched memory · ran Python");
    const failed = workReceipt({
      ...base,
      toolActivities: [{ id: "s", tool_name: "send_imessage", status: "error", args_summary: "to: Parker", result: "Tool error: no", timestamp: 1 }],
    });
    assert.equal(receiptText(failed), "Couldn't text Parker");
    assert.equal(failed[0].tone, "bad", "the tone is unchanged");
  });
});

describe("formatElapsed", () => {
  it("seconds, then minutes", () => {
    assert.equal(formatElapsed(400), "1s");
    assert.equal(formatElapsed(14_200), "14s");
    assert.equal(formatElapsed(65_000), "1m 5s");
    assert.equal(formatElapsed(120_000), "2m");
  });
});

describe("workReceipt failures", () => {
  it("folds repeated failures of one kind", () => {
    const parts = workReceipt({
      ...base,
      toolActivities: [
        { id: "a", tool_name: "web_search", status: "error", args_summary: '"x"', result: "Tool error: 500", timestamp: 1 },
        { id: "b", tool_name: "web_search", status: "error", args_summary: '"y"', result: "Tool error: 500", timestamp: 2 },
      ],
    });
    assert.equal(receiptText(parts), "Couldn't search the web ×2");
    assert.deepEqual(parts.map((p) => p.tone), ["bad"]);
  });
});
