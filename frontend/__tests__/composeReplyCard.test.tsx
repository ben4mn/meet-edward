// The /screen compose bar's reply card, drawn (round 2, 2026-09-28). Ellis saw
// raw markdown in it ("**Planning larger heartbeat call**", "**Today so far…**")
// under a stack of bare "Thinking..." lines. Now the thinking is clean lines
// (a summary's heading its own line, placeholders gone) and the answer's
// light markdown is real bold, bullets and paragraphs — React nodes, never an
// HTML string. The bar also says when it is up, so the stage can keep his
// lane clear of it.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { ComposeBar } from "../app/(display)/display/components/ComposeBar.tsx";

(globalThis as unknown as { React: typeof React }).React = React;
const originalFetch = globalThis.fetch;
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const EVENTS = [
  { type: "thinking", content: "Thinking..." },
  { type: "reasoning", content: "**Planning larger heartbeat call**" },
  { type: "thinking", content: "Thinking..." },
  { type: "reasoning", content: "**Using the project doc**" },
  { type: "thinking", content: "Thinking..." },
  { type: "thinking", content: "Thinking..." },
  { type: "tool_start", tool_name: "read_document" },
  {
    type: "content",
    content: "**Today so far, as of 4:23 pm:**\n\n- **Cabin weekend:** Budget saved.\n- **Riverside Fest:** Tickets held.\n\nNothing else needs you.",
  },
  { type: "done" },
];

async function ask(onActiveChange?: (up: boolean) => void) {
  const enc = new TextEncoder();
  globalThis.fetch = (async () => {
    const body = new ReadableStream<Uint8Array>({
      start(c) {
        for (const e of EVENTS) c.enqueue(enc.encode(`data: ${JSON.stringify({ ...e, conversation_id: "c-screen" })}\n\n`));
        c.close();
      },
    });
    return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
  }) as typeof fetch;
  const utils = render(
    <AppRouterContext.Provider value={router as never}>
      <ComposeBar onActiveChange={onActiveChange} />
    </AppRouterContext.Provider>,
  );
  fireEvent.change(screen.getByRole("textbox", { name: "Talk to Edward" }), { target: { value: "Today's summary" } });
  await act(async () => {
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await new Promise((r) => setTimeout(r, 60));
  });
  return utils;
}

describe("the compose bar's reply card", () => {
  it("thinking is clean lines: no stars, no stack of Thinking...", async () => {
    await ask();
    const card = screen.getByTestId("compose-reply-card");
    const lines = [...card.querySelectorAll("[data-think-line]")].map((n) => n.textContent);
    assert.deepEqual(lines, ["Planning larger heartbeat call", "Using the project doc"]);
    assert.doesNotMatch(card.textContent ?? "", /\*\*/);
    assert.doesNotMatch(card.textContent ?? "", /Thinking\.\.\./);
  });

  it("the answer is real bold, bullets and paragraphs", async () => {
    await ask();
    const card = screen.getByTestId("compose-reply-card");
    const answer = card.querySelector("[data-reply]") as HTMLElement;
    assert.ok(answer);
    assert.deepEqual([...answer.querySelectorAll("strong")].map((n) => n.textContent), [
      "Today so far, as of 4:23 pm:",
      "Cabin weekend:",
      "Riverside Fest:",
    ]);
    assert.deepEqual([...answer.querySelectorAll("li")].map((n) => n.textContent), [
      "Cabin weekend: Budget saved.",
      "Riverside Fest: Tickets held.",
    ]);
    assert.equal(answer.querySelectorAll("p").length, 2);
    assert.doesNotMatch(answer.textContent ?? "", /\*\*/);
  });

  it("the header names the tool in words", async () => {
    await ask();
    assert.match(screen.getByTestId("compose-reply-card").textContent ?? "", /thought · 1 tool/);
  });

  it("says when it is up (a reply card or a focused input), so the stage keeps his lane clear", async () => {
    const seen: boolean[] = [];
    await ask((up) => seen.push(up));
    assert.equal(seen[seen.length - 1], true, "the reply card is up");
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    });
    assert.equal(seen[seen.length - 1], false, "dismissed, nothing is up");
    await act(async () => {
      fireEvent.focus(screen.getByRole("textbox", { name: "Talk to Edward" }));
    });
    assert.equal(seen[seen.length - 1], true, "typing to him");
  });
});
