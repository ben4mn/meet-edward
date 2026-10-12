// The Journal panel (2026-09-26): on the design tokens, through the auth
// fetch (credentials included, a 401 goes to /login), and in words.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, screen } from "@testing-library/react";

import { JournalPanel } from "../components/settings/JournalPanel.tsx";

const originalFetch = globalThis.fetch;
const inits: (RequestInit | undefined)[] = [];

beforeEach(() => {
  inits.length = 0;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    inits.push(init);
    if (!String(input).includes("/api/journal/week")) return new Response("{}", { status: 404 });
    return new Response(JSON.stringify({
      window_days: 7,
      observations: {
        by_kind: [{ kind: "tool_failure", count: 6 }],
        recent: [{ id: "o1", kind: "tool_failure", signature: "tool_failure.browser_open.disconnected", occurrences: 4,
          description: "Chrome extension link dropped during a browser_open", last_seen_at: new Date(Date.now() - 13 * 3600e3).toISOString(), status: "open" }],
        total_in_window: 6,
      },
      wakes: {
        by_outcome: [{ outcome: "acted", count: 2 }, { outcome: "left_alone", count: 3 }],
        recent: [{ id: 9, at: new Date(Date.now() - 3600e3).toISOString(), source: "morning", outcome: "acted", note: "Left a note on the vet.", tools: ["leave_note"], surfaces: [], delivered: false }],
        total_in_window: 5,
      },
    }), { status: 200, headers: { "Content-Type": "application/json" } });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("JournalPanel", () => {
  it("reads the week in words, with credentials", async () => {
    render(<JournalPanel />);
    await screen.findByText("Chrome extension link dropped during a browser_open");
    assert.ok(screen.getAllByText("Tools that failed").length >= 1);
    assert.ok(screen.getByText("4 times · still happening · last 13h ago"));
    assert.ok(screen.getByText("Woke 5 times: acted on 2, left 3 alone."));
    assert.ok(screen.getByText("Left a note on the vet."));
    assert.equal(screen.queryByText("tool_failure"), null);
    assert.equal(inits[0]?.credentials, "include");
  });
});
