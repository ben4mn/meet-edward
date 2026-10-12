// Settings → Attention → Heartbeat (2026-09-26, Jarvis polish).
//
// - With the mind on, an escalation reads "Handed to his mind", never
//   "Told you", and the triage-cycle card is gone (the mind does that work).
// - The digest is computed from its own unfiltered fetch of today, not from
//   the log's filtered, paged list.
// - "Show message text" governs the full log and the event detail too.
// - The full log and the configuration are sub-views (?view=log|config).

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { HeartbeatPanel } from "../components/settings/HeartbeatPanel.tsx";
import { SHOW_TEXT_KEY } from "../lib/showMessageText.ts";

class MemoryStorage {
  private m = new Map<string, string>();
  getItem(k: string) { return this.m.has(k) ? this.m.get(k)! : null; }
  setItem(k: string, v: string) { this.m.set(k, String(v)); }
  removeItem(k: string) { this.m.delete(k); }
  clear() { this.m.clear(); }
}
const storage = new MemoryStorage();
Object.defineProperty(globalThis, "localStorage", { value: storage, configurable: true, writable: true });
Object.defineProperty(window, "localStorage", { value: storage, configurable: true, writable: true });

const originalFetch = globalThis.fetch;
let mindEnabled = true;
const urls: string[] = [];

function ev(id: string, over: Record<string, unknown>) {
  return {
    id, source: "imessage", event_type: "message_received", sender: "+1", contact_name: "Mom", chat_identifier: "c1",
    chat_name: "Family", summary: "Is Ellis bringing the dogs?", raw_data: { text: "Is Ellis bringing the dogs?" },
    is_from_user: false, created_at: new Date(Date.now() - 10 * 60e3).toISOString(), triage_status: "escalated", briefed: false,
    ...over,
  };
}

beforeEach(() => {
  storage.clear();
  mindEnabled = true;
  urls.length = 0;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    urls.push(url);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.includes("/api/heartbeat/status")) {
      return json({
        running: true, enabled: true, triage_interval_seconds: 120, pending_count: 1,
        last_triage_at: new Date(Date.now() - 3 * 60e3).toISOString(), next_triage_at: null, listener_status: "running",
        allowed_senders: [], tracks: { imessage: { enabled: true, status: "running" } },
        imessage_enabled: true, imessage_poll_seconds: 10, imessage_thread_limit_direct: 20, imessage_thread_limit_group: 30,
        calendar_enabled: false, calendar_poll_seconds: 300, calendar_lookahead_minutes: 30, email_enabled: false,
        email_poll_seconds: 300, gmail_enabled: false, gmail_poll_seconds: 300, mind_enabled: mindEnabled,
      });
    }
    if (url.includes("/api/heartbeat/events")) {
      const all = [
        ev("e1", {}),
        ev("e2", { source: "calendar", contact_name: null, chat_name: null, sender: null, summary: "Call with the contractor",
          raw_data: { title: "Call with the contractor" }, triage_status: "calendar_urgent" }),
      ];
      return json(url.includes("source=calendar") ? all.filter((e) => e.source === "calendar") : all);
    }
    if (url.includes("/api/heartbeat/triage")) {
      return json([{ id: "tc", cycle_number: 500, events_total: 3, layer_reached: 2, duration_ms: 1200,
        haiku_input_tokens: 100, haiku_output_tokens: 10, created_at: new Date().toISOString() }]);
    }
    if (url.includes("/api/heartbeat/feedback")) return json([]);
    return new Response("[]", { status: 200 });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

const present = (el: Element | null) => el !== null;

describe("HeartbeatPanel — the digest", () => {
  it("says an escalation was handed to his mind, and hides the triage cycle", async () => {
    render(<HeartbeatPanel />);
    await screen.findByText("Handed to his mind");
    assert.equal(present(screen.queryByText("Told you")), false);
    assert.equal(present(screen.queryByText(/triage cycle/i)), false);
    assert.equal(present(screen.queryByText("I looked at this on my next wake.")), true);
  });

  it("keeps the old words and the cycle card with the mind off", async () => {
    mindEnabled = false;
    render(<HeartbeatPanel />);
    await screen.findByText("Told you");
    await screen.findByText(/last look at new messages/i);
  });

  it("names a calendar event by its title and states status in one sentence", async () => {
    render(<HeartbeatPanel />);
    await screen.findByText("Call with the contractor");
    assert.equal(present(screen.queryByText("Unknown")), false);
    await screen.findByText("I'm listening to messages — last looked 3m ago, 1 waiting.");
  });

  it("fetches its own, unfiltered day", async () => {
    render(<HeartbeatPanel />);
    await screen.findByText("Handed to his mind");
    const digestFetches = urls.filter((u) => u.includes("/api/heartbeat/events"));
    assert.ok(digestFetches.length >= 1);
    assert.ok(digestFetches.every((u) => !u.includes("source=")), "the digest never inherits a source filter");
  });

  it("links to the full log and the configuration as sub-views", async () => {
    render(<HeartbeatPanel />);
    const log = await screen.findByRole("link", { name: /full log/i });
    assert.match(log.getAttribute("href") ?? "", /\?view=log$/);
    assert.match(screen.getByRole("link", { name: /configuration/i }).getAttribute("href") ?? "", /\?view=config$/);
  });
});

describe("HeartbeatPanel — the full log", () => {
  it("hides message text until the one switch shows it, in the rows and the detail", async () => {
    render(<HeartbeatPanel view="log" />);
    await screen.findByText("Mom");
    assert.equal(present(screen.queryByText("Is Ellis bringing the dogs?")), false, "hidden by default");
    // Open the event: its detail still hides the words.
    fireEvent.click(screen.getByRole("button", { name: /Mom/ }));
    assert.equal(present(screen.queryByText(/message hidden/i)), true);
    assert.equal(present(screen.queryByText("Is Ellis bringing the dogs?")), false);
    act(() => screen.getByRole("switch", { name: /show message text/i }).click());
    await waitFor(() => assert.ok(screen.getAllByText("Is Ellis bringing the dogs?").length >= 1));
    assert.equal(storage.getItem(SHOW_TEXT_KEY), "1");
  });
});

describe("HeartbeatPanel — the configuration", () => {
  // The calendar listener is gone (2026-10-11): Edward reads the calendar
  // from Calendar's own database whenever he needs it, so there is no
  // Calendar track to switch on — only messages and email.
  it("lists messages and email, and no Calendar track", async () => {
    render(<HeartbeatPanel view="config" />);
    await screen.findByText("Messages");
    assert.equal(present(screen.queryByText("Email")), true);
    assert.equal(present(screen.queryByRole("switch", { name: /calendar/i })), false);
    assert.equal(present(screen.queryByText("Look ahead")), false);
    assert.equal(present(screen.queryByText("Calendar")), false);
  });
});
