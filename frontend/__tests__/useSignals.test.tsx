// The alert strip and Edward share one signals poll (2026-09-26).
//
// Rendering both consumers must cost one /api/display/signals request, not
// two, and both must see the same answer.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";

import { useSignals } from "../app/(display)/display/hooks/useSharedPoll.ts";
import { resetSharedPolls } from "../app/(display)/display/lib/sharedPoll.ts";

const originalFetch = globalThis.fetch;
const urls: string[] = [];

function Consumer({ id }: { id: string }) {
  const { data } = useSignals();
  return <span data-testid={id}>{data ? String(data.active_chat_streams) : "…"}</span>;
}

beforeEach(() => {
  resetSharedPolls();
  urls.length = 0;
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    urls.push(String(input));
    return new Response(
      JSON.stringify({
        voice_state: "idle",
        active_chat_streams: 2,
        active_orchestrator_tasks: 0,
        recent_alert_at: null,
        calendar_near_fire_at: null,
        fetched_at: new Date().toISOString(),
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  resetSharedPolls();
  globalThis.fetch = originalFetch;
});

describe("useSignals", () => {
  it("two components on the screen make one signals request", async () => {
    const { getByTestId } = render(
      <>
        <Consumer id="alerts" />
        <Consumer id="edward" />
      </>,
    );
    await waitFor(() => assert.equal(getByTestId("alerts").textContent, "2"));
    assert.equal(getByTestId("edward").textContent, "2");
    assert.equal(urls.filter((u) => u.includes("/api/display/signals")).length, 1);
  });
});
