// The work rail as Ellis sees it (2026-09-26): one surface above the answer.
// Live — a small Edward mirroring the turn, the reasoning heading as the
// status, the timeline open. Done — folded to a receipt that reopens on tap.
// A turn rebuilt from history renders the same nodes as the live one.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";

import { WorkRail } from "../components/chat/WorkRail.tsx";
import type { Message } from "../lib/ChatContext.tsx";
import { hydrateHistoryTrace } from "../lib/chatHistory.ts";
import { reduceTurn, startTurn } from "../lib/turnModel.ts";

const present = (el: Element | null) => el !== null;

beforeEach(() => {
  try {
    localStorage.removeItem("edward:workDisplay");
  } catch {
    /* no storage */
  }
});
afterEach(cleanup);

const DONE: Message = {
  id: "a1",
  role: "assistant",
  content: "Book the 1–4pm slot.",
  reasoningContent: "**Looking it up**\n\nI want a current source.",
  progressSteps: [{ id: "memory_search-main", step: "memory_search", status: "completed", message: "Found 3", timestamp: 1, count: 3 }],
  toolActivities: [
    { id: "t1", tool_name: "browser_open", status: "completed", args_summary: "https://www.nws.gov/x", result: "Opened", timestamp: 2, duration_ms: 900 },
    { id: "t2", tool_name: "send_imessage", status: "completed", args_summary: "to: Parker", result: "Sent to Parker", timestamp: 3 },
  ],
  trace: [
    { kind: "memory", status: "completed", count: 3 },
    { kind: "reasoning", text: "**Looking it up**\n\nI want a current source." },
    { kind: "tool", id: "t1" },
    { kind: "tool", id: "t2" },
    { kind: "text", text: "Book the 1–4pm slot." },
  ],
};

describe("WorkRail", () => {
  it("renders nothing for a turn with no work", () => {
    const { container } = render(<WorkRail message={{ id: "x", role: "assistant", content: "hi" }} live={false} turn={null} />);
    assert.equal(container.querySelector("[data-testid=work-rail]"), null);
  });

  it("while live: one header with Edward and the reasoning heading, the timeline open", () => {
    let turn = startTurn("a1", "c1", Date.now() - 3000);
    turn = reduceTurn(turn, { type: "reasoning", content: "**Checking your calendar**\n\nEllis asked" }, Date.now());
    const msg: Message = { id: "a1", role: "assistant", content: "", reasoningContent: "**Checking your calendar**\n\nEllis asked", trace: [{ kind: "reasoning", text: "**Checking your calendar**\n\nEllis asked" }] };
    render(<WorkRail message={msg} live turn={turn} />);
    const rail = screen.getByTestId("work-rail");
    assert.equal(rail.getAttribute("data-live"), "true");
    assert.equal(present(screen.queryByText("Checking your calendar…")), true);
    assert.equal(rail.querySelectorAll(".pane-avatar").length, 1, "a small Edward mirrors the turn");
    assert.equal(rail.querySelectorAll(".pane-avatar.is-live").length, 0, "he does not breathe: the page has one live Edward");
    assert.equal(screen.getAllByTestId("rail-reasoning").length, 1);
    assert.equal(present(screen.queryByText(/elapsed|\ds/)), true, "elapsed seconds");
  });

  it("live with nothing yet is only the header, never a second spinner", () => {
    const turn = startTurn("a2", "c1", Date.now());
    render(<WorkRail message={{ id: "a2", role: "assistant", content: "" }} live turn={turn} />);
    assert.equal(present(screen.queryByText("Thinking…")), true);
    assert.equal(screen.getByTestId("work-rail").querySelectorAll("li").length, 0);
  });

  it("when done: folds to a receipt that says what went out, and reopens on tap", () => {
    render(<WorkRail message={DONE} live={false} turn={null} />);
    const receipt = screen.getByRole("button", { name: /thought/i });
    assert.equal(receipt.getAttribute("aria-expanded"), "false");
    assert.equal(receipt.textContent?.replace(/\s+/g, " ").includes("texted Parker"), true);
    assert.equal(present(screen.queryByText("texted Parker")), true);
    assert.equal(present(screen.queryByTestId("rail-tool")), false);
    fireEvent.click(receipt);
    assert.equal(receipt.getAttribute("aria-expanded"), "true");
    assert.equal(present(screen.queryByText("Opened nws.gov")), true);
    assert.equal(present(screen.queryByText("Texted Parker")), true);
    assert.equal(present(screen.queryByText("Edward acted")), true);
  });

  it("an error shows its line in the node", () => {
    render(
      <WorkRail
        message={{
          id: "e",
          role: "assistant",
          content: "The calendar timed out.",
          toolActivities: [
            { id: "c", tool_name: "apple_calendar_list_events", status: "error", result: "Tool error: calendar MCP call timed out after 10s", timestamp: 1 },
          ],
        }}
        live={false}
        turn={null}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /couldn't check your calendar/i }));
    assert.equal(present(screen.queryByText("Calendar MCP call timed out after 10s")), true);
    const node = screen.getByTestId("rail-tool");
    assert.equal(node.textContent?.includes("Couldn't check your calendar"), true, "the node says it failed, not 'Checked'");
  });

  it("a turn rebuilt from history renders the same nodes as live", () => {
    const hydrated: Message = {
      id: "h1",
      role: "assistant",
      content: DONE.content,
      ...hydrateHistoryTrace(
        "h1",
        {
          reasoning: DONE.reasoningContent,
          memory_hits: 3,
          tools: [
            { name: "browser_open", args_summary: "https://www.nws.gov/x", status: "ok", duration_ms: 900, result_preview: "Opened" },
            { name: "send_imessage", args_summary: "to: Parker", status: "ok", duration_ms: null, result_preview: "Sent to Parker" },
          ],
        },
        DONE.content,
      ),
    };
    const titles = (m: Message) => {
      const { unmount } = render(<WorkRail message={m} live={false} turn={null} />);
      fireEvent.click(screen.getByRole("button", { name: /thought/i }));
      const rail = screen.getByTestId("work-rail");
      const out = Array.from(rail.querySelectorAll("li")).map((li) => li.textContent);
      unmount();
      return out;
    };
    assert.deepEqual(titles(hydrated), titles(DONE));
  });
});

// 2026-09-26 review fixes.
describe("WorkRail while the answer streams", () => {
  const WORKING: Message = {
    id: "w1",
    role: "assistant",
    content: "",
    toolActivities: [{ id: "t1", tool_name: "web_search", status: "running", args_summary: '"Lake Tahoe CA hourly forecast"', timestamp: 1 }],
    trace: [{ kind: "tool", id: "t1" }],
  };

  it("folds the timeline when the answer starts — before there is text to jump — and stays live", () => {
    let turn = startTurn("w1", "c1", Date.now() - 4000);
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "web_search" }, Date.now());
    turn = reduceTurn(turn, { type: "tool_end", tool_name: "web_search", result: "ok" }, Date.now());
    turn = reduceTurn(turn, { type: "content", content: "Saturday" }, Date.now());
    const msg: Message = {
      ...WORKING,
      content: "Saturday",
      toolActivities: [{ ...WORKING.toolActivities![0], status: "completed", result: "ok" }],
    };
    render(<WorkRail message={msg} live turn={turn} />);
    const rail = screen.getByTestId("work-rail");
    assert.equal(rail.getAttribute("data-live"), "true");
    assert.equal(rail.querySelector("[aria-expanded]")?.getAttribute("aria-expanded"), "false");
    assert.equal(rail.querySelectorAll("li").length, 0, "the timeline is folded");
  });

  it("the fold at the first words is instant — the answer appears in place, it never slides up", async () => {
    let turn = startTurn("w1", "c1", Date.now() - 4000);
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "web_search" }, Date.now());
    turn = reduceTurn(turn, { type: "tool_end", tool_name: "web_search", result: "ok" }, Date.now());
    const done = { ...WORKING, toolActivities: [{ ...WORKING.toolActivities![0], status: "completed" as const, result: "ok" }] };
    const { rerender } = render(<WorkRail message={done} live turn={turn} />);
    const rail = screen.getByTestId("work-rail");
    assert.equal(rail.querySelectorAll("li").length, 1, "open while he works");
    const writing = reduceTurn(turn, { type: "content", content: "Saturday" }, Date.now());
    rerender(<WorkRail message={{ ...done, content: "Saturday" }} live turn={writing} />);
    await new Promise((r) => setTimeout(r, 60));
    assert.equal(rail.querySelectorAll("li").length, 0, "gone within a frame or two, not after a 170ms height animation");
  });

  it("never says the running step twice: the open timeline names it, the header does not", () => {
    let turn = startTurn("w1", "c1", Date.now() - 2000);
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "web_search" }, Date.now());
    render(<WorkRail message={WORKING} live turn={turn} />);
    const rail = screen.getByTestId("work-rail");
    const header = rail.querySelector("button[aria-expanded]")!;
    const node = screen.getByTestId("rail-tool");
    const label = node.querySelector(".truncate")!.textContent!.trim();
    assert.ok(label.length > 0);
    assert.equal(header.textContent!.includes(label), false, `header repeats "${label}"`);
  });

  it("names the running step in the header once the timeline has folded (a preamble, then more tools)", () => {
    let turn = startTurn("w1", "c1", Date.now() - 2000);
    turn = reduceTurn(turn, { type: "content", content: "Let me check the forecast." }, Date.now());
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "web_search" }, Date.now());
    render(<WorkRail message={{ ...WORKING, content: "Let me check the forecast." }} live turn={turn} />);
    const header = screen.getByTestId("work-rail").querySelector("button[aria-expanded]")!;
    assert.equal(header.getAttribute("aria-expanded"), "false", "stays folded: no flapping between rounds");
    assert.equal(/Searching/.test(header.textContent ?? ""), true, header.textContent ?? "");
  });

  it("nothing loops inside the transcript: no shimmer, no pulsing dot (design system §1.4, §7)", () => {
    let turn = startTurn("w1", "c1", Date.now() - 2000);
    turn = reduceTurn(turn, { type: "tool_start", tool_name: "web_search" }, Date.now());
    const { container } = render(<WorkRail message={WORKING} live turn={turn} />);
    const loops = container.querySelectorAll('[class*="animate-shimmer"], [class*="animate-pulse"], [class*="animate-breathe"]');
    assert.equal(loops.length, 0, Array.from(loops).map((e) => e.className).join(" | "));
  });
});

describe("a tool call being composed", () => {
  it("shows what its arguments say, never the raw partial JSON", () => {
    const turn = reduceTurn(startTurn("p1", "c1", Date.now() - 1000), { type: "tool_args_delta", call_id: "k", delta: "x" }, Date.now());
    const msg: Message = {
      id: "p1",
      role: "assistant",
      content: "",
      pendingToolArgs: { k: { name: "web_search", args: '{"query": "kayak\\nweather Satur' } },
    };
    render(<WorkRail message={msg} live turn={turn} />);
    const node = screen.getByTestId("tool-args-stream");
    const text = node.textContent ?? "";
    assert.equal(/[{}]|\\n|"query"/.test(text), false, text);
    assert.ok(text.includes("kayak weather Satur"), text);
    assert.equal(node.querySelector(".font-mono"), null, "Edward's words, not a debug dump");
  });
});
