// Settings → Attention → Notifications (2026-09-26): one headline sentence,
// a "This device" switch, the diagnostics folded behind "Troubleshoot", and
// the last pushes from the ledger with whether Ellis opened them.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";

import { NotificationsPanel } from "../components/settings/NotificationsPanel.tsx";

const originalFetch = globalThis.fetch;

// jsdom has no matchMedia (isPWA reads display-mode).
if (!window.matchMedia) {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      matches: false, media: query, onchange: null,
      addEventListener() {}, removeEventListener() {}, addListener() {}, removeListener() {}, dispatchEvent: () => false,
    }),
  });
}

beforeEach(() => {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.includes("/api/approvals")) {
      return json({
        approvals: [
          { id: 31, status: "pending", tool_name: "send_imessage", summary: "iMessage to Jordan: “Sunday works — 6pm at ours?”", conversation_id: "t-jordan-held", created_at: new Date().toISOString() },
        ],
      });
    }
    if (url.includes("/api/push/status")) return json({ configured: true, subscriptionCount: 2, vapidPublicKey: "k" });
    if (url.includes("/api/push/log")) {
      return json({
        pushes: [
          { id: "p1", source: "mind", title: "Dog food is on the porch", url: "/chat?c=t1", sent_at: new Date(Date.now() - 4 * 3600e3).toISOString(), opened_at: new Date().toISOString() },
          { id: "p2", source: "approval", title: "Send to Jordan?", url: "/today", sent_at: new Date(Date.now() - 5 * 3600e3).toISOString(), opened_at: null },
        ],
      });
    }
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("NotificationsPanel", () => {
  it("leads with one sentence and a switch for this device", async () => {
    render(<NotificationsPanel />);
    // jsdom has no Push API: the sentence says so, and the switch cannot turn on.
    await screen.findByText("Notifications are not supported in this browser.");
    const sw = screen.getByRole("switch", { name: /this device/i });
    assert.equal(sw.getAttribute("aria-checked"), "false");
    assert.equal((sw as HTMLButtonElement).disabled, true);
  });

  it("keeps the diagnostics behind Troubleshoot", async () => {
    render(<NotificationsPanel />);
    await screen.findByText("Notifications are not supported in this browser.");
    assert.equal(screen.queryByText(/active subscriptions/i), null);
    fireEvent.click(screen.getByRole("button", { name: /troubleshoot/i }));
    assert.ok(screen.getByText(/active subscriptions/i));
    assert.ok(screen.getByRole("button", { name: /send a test/i }));
  });

  it("lists the last pushes and whether you opened them", async () => {
    render(<NotificationsPanel />);
    await screen.findByText("Dog food is on the porch");
    assert.ok(screen.getByText("Send to Jordan?"));
    assert.ok(screen.getByText("opened"));
    assert.ok(screen.getByText("not opened"));
    assert.ok(screen.getByText("from a wake · 4h ago"));
  });

  it("puts a send held for your OK first, answerable here (a search for 'approval' lands on this panel)", async () => {
    render(<NotificationsPanel />);
    const waiting = await screen.findByRole("region", { name: "Waiting on you" });
    assert.ok(within(waiting).getByText("Sunday works — 6pm at ours?"));
    assert.ok(within(waiting).getByRole("button", { name: "Send" }));
  });
});
