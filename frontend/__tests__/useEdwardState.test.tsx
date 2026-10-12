// useEdwardState: the screen's and the hub's Edward, from real signals only
// (2026-09-26). It reads the shared signals poll and /api/mind/now, derives
// his presence (lib/screenPresence.ts), and reports news as one-off cues —
// a fresh alert is a glance, a wake that acted outward is the `acted` beat.
// There is no timer-driven "consulting memory" any more.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render, waitFor } from "@testing-library/react";

import { useEdwardState } from "../app/(display)/display/hooks/useEdwardState.ts";
import { MIND_NOW_URL } from "../app/(display)/display/hooks/useSharedPoll.ts";
import { SIGNALS_URL, resetSharedPolls, sharedPollStore } from "../app/(display)/display/lib/sharedPoll.ts";

const originalFetch = globalThis.fetch;
let signals: Record<string, unknown>;
let mindNow: Record<string, unknown>;

function json(body: unknown) {
  return new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });
}

beforeEach(() => {
  resetSharedPolls();
  signals = {
    voice_state: "idle", active_chat_streams: 0, active_orchestrator_tasks: 0,
    recent_alert_at: null, calendar_near_fire_at: null, fetched_at: new Date().toISOString(),
  };
  mindNow = {
    now: new Date().toISOString(),
    waking: { reason: "morning", started_at: null, conversation_id: "edward-mind" },
    last: null, recent: [], next_wake_at: null, approvals_waiting: 0, unread_thoughts: 0,
    quota: { used_percent: 10, exhausted: false, resets_at: null }, hands: { connected: true, down_for_s: null },
  };
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/display/signals")) return json(signals);
    if (url.includes("/api/mind/now")) return json(mindNow);
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  resetSharedPolls();
  globalThis.fetch = originalFetch;
});

let latest: ReturnType<typeof useEdwardState> | null = null;
function Probe() {
  latest = useEdwardState("America/Chicago");
  return <span data-testid="line">{latest.presence.line}</span>;
}

async function refetchBoth() {
  await act(async () => {
    await sharedPollStore(SIGNALS_URL, async () => null, 4000).refetch();
    await sharedPollStore(MIND_NOW_URL, async () => null, 10_000).refetch();
  });
}

describe("useEdwardState", () => {
  it("says what the mind is doing, truthfully", async () => {
    const { getByTestId } = render(<Probe />);
    await waitFor(() => assert.equal(getByTestId("line").textContent, "Thinking · morning wake"));
    assert.equal(latest!.presence.mood, "think");
  });

  it("returns no legacy 'looking-up' state and no memory claim", async () => {
    render(<Probe />);
    await waitFor(() => assert.ok(latest!.presence.known));
    assert.equal((latest as unknown as { state?: string }).state, undefined);
    assert.ok(!/memory/i.test(latest!.presence.line));
  });

  it("a fresh alert between reads is a glance cue, once", async () => {
    render(<Probe />);
    await waitFor(() => assert.ok(latest!.presence.known));
    assert.equal(latest!.cue, null, "arriving is not news");
    signals = { ...signals, recent_alert_at: new Date().toISOString() };
    await refetchBoth();
    await waitFor(() => assert.equal(latest!.cue?.target, "alerts"));
    const first = latest!.cue!.key;
    await refetchBoth();
    assert.equal(latest!.cue?.key, first, "the same alert is not news twice");
  });

  it("a wake that acted outward is the acted beat", async () => {
    render(<Probe />);
    await waitFor(() => assert.ok(latest!.presence.known));
    assert.equal(latest!.beat, null);
    mindNow = {
      ...mindNow, waking: null,
      last: { at: new Date().toISOString(), reason: "messages", outcome: "acted", line: "Replied to Parker.", thought_id: "t" },
    };
    await refetchBoth();
    await waitFor(() => assert.equal(latest!.beat?.beat, "acted"));
  });
});
