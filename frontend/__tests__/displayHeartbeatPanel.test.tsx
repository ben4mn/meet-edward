// /screen's heartbeat tile, empty (2026-09-26): one live Edward per screen.
// The "All quiet." Edward is a still illustration, not a second Edward
// blinking and glancing beside the real one.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";

import { HeartbeatPanel } from "../app/(display)/display/components/HeartbeatPanel.tsx";

const originalFetch = globalThis.fetch;

beforeEach(() => {
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    const json = (b: unknown) => new Response(JSON.stringify(b), { status: 200, headers: { "Content-Type": "application/json" } });
    if (url.includes("/api/heartbeat/status")) {
      return json({ running: true, enabled: true, pending_count: 0, listener_status: "running", tracks: {} });
    }
    if (url.includes("/api/heartbeat/events")) return json([]);
    return json({});
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("display HeartbeatPanel, nothing to show", () => {
  it("draws a still Edward, never a second live one", async () => {
    const { container, findByText } = render(<HeartbeatPanel />);
    await findByText("All quiet.");
    const el = container.querySelector(".pane-avatar") as HTMLElement;
    await waitFor(() => assert.ok(el));
    assert.ok(el.classList.contains("pane-static"), "expected a still avatar");
    assert.ok(!el.classList.contains("is-live"));
  });
});
