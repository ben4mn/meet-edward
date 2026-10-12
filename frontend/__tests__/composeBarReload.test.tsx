// The /screen compose bar never loses a turn to a deploy (2026-09-27 review
// of H3). Ellis asks on /screen, presses Enter (one keydown) and Edward works
// through tools for longer than the display's half-minute quiet window; a
// reload then closed the stream and the server cancelled the turn part-way.
// The bar holds reloads while it streams; what is typed but not sent is
// held by the page itself (lib/buildReload pageHoldsWork).

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { AppRouterContext } from "next/dist/shared/lib/app-router-context.shared-runtime";

import { ComposeBar } from "../app/(display)/display/components/ComposeBar.tsx";
import { reloadHeld } from "../lib/buildReload.ts";

(globalThis as unknown as { React: typeof React }).React = React;
const originalFetch = globalThis.fetch;
const router = { push: () => {}, replace: () => {}, back: () => {}, forward: () => {}, refresh: () => {}, prefetch: () => {} };

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("ComposeBar", () => {
  it("holds a new build's reload while Edward's answer streams", async () => {
    let controller!: ReadableStreamDefaultController<Uint8Array>;
    const enc = new TextEncoder();
    globalThis.fetch = (async () => {
      const body = new ReadableStream<Uint8Array>({ start: (c) => void (controller = c) });
      return new Response(body, { status: 200, headers: { "content-type": "text/event-stream" } });
    }) as typeof fetch;
    render(
      <AppRouterContext.Provider value={router as never}>
        <ComposeBar />
      </AppRouterContext.Provider>,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Talk to Edward" }), { target: { value: "what's on tonight" } });
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Send" }));
      await new Promise((r) => setTimeout(r, 20));
    });
    await act(async () => {
      controller.enqueue(enc.encode(`data: ${JSON.stringify({ type: "tool_start", tool_name: "list_events", conversation_id: "c-screen" })}\n\n`));
      await new Promise((r) => setTimeout(r, 20));
    });
    assert.equal(reloadHeld(), true, "a reload would cancel the turn");
    await act(async () => {
      controller.enqueue(enc.encode(`data: ${JSON.stringify({ type: "content", content: "Dinner at 7.", conversation_id: "c-screen" })}\n\n`));
      controller.enqueue(enc.encode(`data: ${JSON.stringify({ type: "done", conversation_id: "c-screen" })}\n\n`));
      controller.close();
      await new Promise((r) => setTimeout(r, 30));
    });
    assert.equal(reloadHeld(), false, "the answer is in the thread; nothing is running");
  });

  it("on the stage the bar speaks its language: Ellis's send is neutral (emerald means Edward), prompts in Inter, not mono", () => {
    render(
      <AppRouterContext.Provider value={router as never}>
        <ComposeBar />
      </AppRouterContext.Provider>,
    );
    const send = screen.getByRole("button", { name: "Send" });
    assert.doesNotMatch(send.getAttribute("style") ?? "", /--ed-accent/);
    assert.equal(document.querySelector(".mc-mono"), null);
  });
});
