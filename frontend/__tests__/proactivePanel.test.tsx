// Settings → Attention → Proactive (2026-09-26): the wakes he takes on his
// own, the open loops (Done / Snooze, undoable), and the people and things
// he tracks — on the design system, no raw colours.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { ProactivePanel } from "../components/settings/ProactivePanel.tsx";

const originalFetch = globalThis.fetch;
const patches: { url: string; body: unknown }[] = [];

beforeEach(() => {
  patches.length = 0;
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const json = (body: unknown) => new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
    if (init?.method === "PATCH") {
      patches.push({ url, body: JSON.parse(String(init.body)) });
      return json({});
    }
    if (url.includes("/api/proactive/status")) {
      return json({
        config: { dossier_enabled: true, dossier_min_acted_pushes: 3, dossier_min_inbound_threads: 5, dossier_window_days: 30 },
        entity_count: 1, open_loop_count: 1, wakes: [{ kind: "morning", cron: "0 7 * * *" }],
      });
    }
    if (url.includes("/api/proactive/open-loops")) {
      return json([{ id: "ol-1", description: "Tell Dad about Sunday", source_memory_id: null, source_conversation_id: null,
        entity_id: null, status: "open", priority: 0.8, detected_at: new Date(Date.now() - 5 * 3600e3).toISOString(),
        last_nudged_at: null, resolved_at: null, snooze_until: null, nudge_count: 2 }]);
    }
    if (url.includes("/api/proactive/entities")) {
      return json([{ id: "e1", name: "Rowan", entity_type: "person", aliases: ["M"], mention_count: 812, first_seen: null,
        last_seen: new Date(Date.now() - 3600e3).toISOString() }]);
    }
    return json({});
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("ProactivePanel", () => {
  it("leads with his wakes", async () => {
    render(<ProactivePanel />);
    await screen.findByText("Morning wake");
    assert.ok(screen.getByText("07:00 every day"));
  });

  it("closes an open loop in one tap", async () => {
    render(<ProactivePanel />);
    fireEvent.click(await screen.findByRole("radio", { name: /open loops/i }));
    await screen.findByText("Tell Dad about Sunday");
    assert.ok(screen.getByText(/nudged 2×/));
    fireEvent.click(screen.getByRole("button", { name: /done/i }));
    await waitFor(() => assert.deepEqual(patches.map((p) => p.body), [{ status: "resolved" }]));
    assert.equal(screen.queryByText("Tell Dad about Sunday"), null);
  });

  it("lists who and what he keeps track of", async () => {
    render(<ProactivePanel />);
    fireEvent.click(await screen.findByRole("radio", { name: /people/i }));
    await screen.findByText("Rowan");
    assert.ok(screen.getByText("person · also M"));
  });
});
