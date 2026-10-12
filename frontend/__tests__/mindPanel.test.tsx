// Settings → Mind → Thoughts (2026-09-26, Jarvis polish).
//
// - "Wake now" never blocks: POST /api/mind/wake answers 202, the panel
//   shows "Waking…" at once and polls the log until a wake it did not have
//   lands; a 5xx while polling means "still running", not "failed".
// - The verdict is a compact two-option control on its own row under the
//   meta line, and the meta line no longer repeats it ("you said: …").
// - What he is keeping an eye on shows with its next look.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { MindPanel } from "../components/settings/MindPanel.tsx";

const iso = (msAgo: number) => new Date(Date.now() - msAgo).toISOString();

function wake(over: Record<string, unknown>) {
  return {
    id: 1, at: iso(3600_000), source: "evening", outcome: "acted", note: "Pushed Ellis about the vet.",
    tools: ["send_push_notification"], surfaces: [], delivered: true, turn_id: "t", pushes: [], verdict: null,
    thought_id: null, ...over,
  };
}

let logWakes: Record<string, unknown>[] = [];
let logStatus = 200;
const calls: { url: string; method: string; body: unknown }[] = [];
const originalFetch = globalThis.fetch;

beforeEach(() => {
  logWakes = [
    wake({ id: 12, source: "messages", note: "Replied to Parker about Saturday.", thought_id: "t-reply-parker", at: iso(20 * 60_000) }),
    wake({ id: 11, source: "manual", outcome: "left_alone", note: "Nothing needs you.", tools: [], at: iso(2 * 3600_000) }),
  ];
  logStatus = 200;
  calls.length = 0;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? "GET";
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : null });
    const json = (status: number, body: unknown) =>
      new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
    if (url.includes("/api/mind/log")) {
      return logStatus === 200 ? json(200, { days: 7, wakes: logWakes }) : json(logStatus, { detail: "busy" });
    }
    if (url.includes("/api/mind/intentions")) {
      return json(200, {
        intentions: [{
          id: "i1", content: "Check whether Parker confirmed Saturday.", created_at: iso(3600_000),
          valid_until: null, event_id: "e1", next_fire_at: new Date(Date.now() + 2 * 86400_000).toISOString(),
          next_fire_why: "Confirm Parker's pick-up time",
        }],
      });
    }
    if (url.includes("/api/mind/wake")) return json(202, { accepted: true, reason: "manual", joined: false });
    if (url.includes("/api/mind/verdict")) return json(200, { id: "sig", kind: "mind_verdict" });
    return json(404, {});
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const present = (el: Element | null) => el !== null;

describe("MindPanel", () => {
  it("shows what he is watching, with its next look", async () => {
    render(<MindPanel />);
    await screen.findByText("Check whether Parker confirmed Saturday.");
    assert.equal(present(screen.queryByText("Next look in 2d")), true);
  });

  it("puts the verdict on its own row as two options, without repeating it in the meta line", async () => {
    logWakes[0] = { ...logWakes[0], verdict: { verdict: "right_call", note: null } };
    render(<MindPanel />);
    await screen.findByText("Replied to Parker about Saturday.");
    const groups = screen.getAllByRole("radiogroup", { name: /right call\?/i });
    assert.equal(groups.length, 2, "one per wake that decided something");
    const radios = screen.getAllByRole("radio");
    assert.equal(radios.length, 4);
    assert.equal(radios[0].getAttribute("aria-checked"), "true");
    assert.equal(present(screen.queryByText(/you said/i)), false);
  });

  it("links a wake that acted to its thinking", async () => {
    render(<MindPanel />);
    const link = await screen.findByRole("link", { name: /read my thinking/i });
    assert.equal(link.getAttribute("href"), "/chat?c=t-reply-parker");
  });

  it("posts a verdict at once", async () => {
    render(<MindPanel />);
    await screen.findByText("Replied to Parker about Saturday.");
    fireEvent.click(screen.getAllByRole("radio", { name: "Wrong call" })[0]);
    await waitFor(() => assert.ok(calls.some((c) => c.url.includes("/api/mind/verdict"))));
    const post = calls.find((c) => c.url.includes("/api/mind/verdict"))!;
    assert.deepEqual(post.body, { ledger_id: 12, verdict: "wrong_call", note: null });
  });

  it("wakes without blocking and waits for the new wake to land", async () => {
    render(<MindPanel pollMs={20} />);
    await screen.findByText("Replied to Parker about Saturday.");
    fireEvent.click(screen.getByRole("button", { name: /wake now/i }));
    // Optimistic: the row is there before the POST answers.
    assert.equal(present(screen.queryByText(/waking/i)), true);
    await waitFor(() => assert.ok(calls.some((c) => c.url.includes("/api/mind/wake") && c.method === "POST")));
    // A 5xx while polling is "still running".
    logStatus = 503;
    await act(async () => { await new Promise((r) => setTimeout(r, 80)); });
    assert.equal(present(screen.queryByText(/waking/i)), true);
    assert.equal(present(screen.queryByRole("alert")), false);
    // The wake lands.
    logStatus = 200;
    logWakes = [wake({ id: 13, source: "manual", outcome: "left_alone", note: "Looked because you asked.", tools: [], at: iso(1000) }), ...logWakes];
    await screen.findByText("Looked because you asked.");
    await waitFor(() => assert.equal(present(screen.queryByText(/waking/i)), false));
  });

  it("tucks a failed wake's internal reason behind Details, never in mono under the sentence", async () => {
    logWakes = [wake({ id: 14, source: "morning", outcome: "failed", note: "Pack source timed out (calendar).", tools: [], at: iso(60_000) })];
    const { container } = render(<MindPanel />);
    const reason = await screen.findByText("Pack source timed out (calendar).");
    assert.equal(reason.closest("details") !== null, true, "inside a closed disclosure");
    assert.equal(reason.closest("details")!.hasAttribute("open"), false);
    assert.equal(container.querySelector("article .font-mono") === null, true, "no mono in a wake card");
  });
});
