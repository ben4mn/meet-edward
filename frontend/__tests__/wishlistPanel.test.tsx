// Settings → Work → Wishlist (2026-09-26): undo instead of "tap again to
// delete" — a deleted wish leaves at once, the DELETE waits out the Undo
// window, and Undo brings it back without anything being sent. Adding a
// wish takes its priority from a three-way control.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { WishlistPanel } from "../components/settings/WishlistPanel.tsx";
import { ToastProvider } from "../components/ui/Toast.tsx";
import { reloadHeld } from "../lib/buildReload.ts";

const originalFetch = globalThis.fetch;
const sent: { method: string; url: string; body: unknown }[] = [];

function wish(id: string, over: Record<string, unknown> = {}) {
  return { id, title: `Wish ${id}`, why: "Because.", detail: null, status: "open", priority: 2, source: "edward",
    conversation_id: null, times_wanted: 1, created_at: new Date().toISOString(), updated_at: new Date().toISOString(), ...over };
}

beforeEach(() => {
  sent.length = 0;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (method !== "GET") {
      sent.push({ method, url, body: init?.body ? JSON.parse(String(init.body)) : null });
      return json(method === "DELETE" ? { deleted: true } : wish("new"));
    }
    return json({ items: [wish("w1"), wish("w2", { title: "Read my own logs" })] });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const ui = (undoMs = 60) => (
  <ToastProvider>
    <WishlistPanel undoMs={undoMs} />
  </ToastProvider>
);

describe("WishlistPanel", () => {
  it("deletes with Undo, sending nothing until the window closes", async () => {
    render(ui(10_000));
    await screen.findByText("Read my own logs");
    fireEvent.click(screen.getByRole("button", { name: "Delete Read my own logs" }));
    // Never hand a DOM node to assert: node:assert inspecting a jsdom element kills the run.
    assert.equal(screen.queryByRole("button", { name: "Delete Read my own logs" }) === null, true, "the card is gone at once");
    assert.deepEqual(sent, [], "nothing sent yet");
    fireEvent.click(await screen.findByRole("button", { name: "Undo" }));
    await screen.findByRole("button", { name: "Delete Read my own logs" });
    assert.deepEqual(sent, []);
  });

  it("sends the delete once the Undo window has passed", async () => {
    render(ui(40));
    await screen.findByText("Read my own logs");
    fireEvent.click(screen.getByRole("button", { name: "Delete Read my own logs" }));
    await act(async () => { await new Promise((r) => setTimeout(r, 120)); });
    await waitFor(() => assert.deepEqual(sent.map((s) => [s.method, s.url.split("/api")[1]]), [["DELETE", "/wishlist/w2"]]));
  });

  it("adds a wish with the priority you chose", async () => {
    render(ui());
    await screen.findByText("Read my own logs");
    // The form lives in a sheet so Edward's own wishes lead the page.
    assert.equal(screen.queryByLabelText(/what should i be able to do/i) === null, true);
    fireEvent.click(screen.getByRole("button", { name: /add a wish/i }));
    fireEvent.change(await screen.findByLabelText(/what should i be able to do/i), { target: { value: "Watch the garage camera" } });
    fireEvent.change(screen.getByLabelText(/why it matters/i), { target: { value: "Packages." } });
    fireEvent.click(screen.getByRole("radio", { name: "High" }));
    fireEvent.click(screen.getByRole("button", { name: /add wish/i }));
    await waitFor(() => assert.deepEqual(sent[0]?.body, { title: "Watch the garage camera", why: "Packages.", priority: 1 }));
  });

  // The sheet's fields are one-line inputs, which a deploy's reload only
  // waits for while focused; the phone drops focus when the PWA goes to
  // the background (2026-09-27 integration).
  it("holds a new build's reload while a wish is half written, and lets go when the sheet closes", async () => {
    render(ui());
    await screen.findByText("Read my own logs");
    fireEvent.click(screen.getByRole("button", { name: /add a wish/i }));
    const title = await screen.findByLabelText(/what should i be able to do/i);
    assert.equal(reloadHeld(), false, "an empty sheet holds nothing");
    fireEvent.change(title, { target: { value: "Watch the garage camera" } });
    fireEvent.blur(title);
    assert.equal(reloadHeld(), true, "a reload would lose the wish");
    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => assert.equal(reloadHeld(), false, "closed: nothing on screen to lose"));
  });
});
