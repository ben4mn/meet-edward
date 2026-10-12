// The hub is the household's screen (review 2026-09-26).
//
// Its greeting Edward used to take /screen's presence as is, so Ellis's unread
// thoughts (non-zero most of the day) and his held approvals put an amber
// antenna on him at the pet station — where amber means a late dose, and
// nobody standing there can read or clear it. The hub now asks for the
// household's presence: what Edward is really doing, without Ellis's queue or
// his Chrome link.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";

import { useEdwardState } from "../app/(display)/display/hooks/useEdwardState.ts";
import { resetSharedPolls } from "../app/(display)/display/lib/sharedPoll.ts";
import type { PresenceAudience } from "../app/(display)/display/lib/screenPresence.ts";

const originalFetch = globalThis.fetch;

const SIGNALS = {
  pending_interrupt: null,
  voice_state: "idle",
  active_chat_streams: 0,
  active_orchestrator_tasks: 0,
  recent_alert_at: null,
  calendar_near_fire_at: null,
  fetched_at: new Date().toISOString(),
};

const MIND = {
  waking: null,
  last: null,
  recent: [],
  approvals_waiting: 1,
  unread_thoughts: 3,
  quota: { used_percent: 40, exhausted: false, resets_at: null },
  hands: { connected: false, down_for_s: 300 },
};

const json = (body: unknown) =>
  new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } });

beforeEach(() => {
  resetSharedPolls();
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes("/api/display/signals")) return json(SIGNALS);
    if (url.includes("/api/mind/now")) return json(MIND);
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  resetSharedPolls();
  globalThis.fetch = originalFetch;
});

function Probe({ audience }: { audience?: PresenceAudience }) {
  const { presence, mindNow } = useEdwardState("America/Chicago", audience ? { audience } : undefined);
  return (
    <div data-mood={presence.mood} data-hands={String(presence.handsDown)} data-ready={String(!!mindNow)}>
      {presence.label}
    </div>
  );
}

async function presenceFor(audience?: PresenceAudience): Promise<HTMLElement> {
  const { container } = render(<Probe audience={audience} />);
  const el = container.firstElementChild as HTMLElement;
  await waitFor(() => assert.equal(el.dataset.ready, "true"));
  return el;
}

describe("Edward's presence for the household hub", () => {
  it("Ellis's screen: his unread thoughts and approvals are attention", async () => {
    const el = await presenceFor();
    assert.equal(el.dataset.mood, "attention");
    assert.equal(el.dataset.hands, "true");
  });

  it("the household: no amber 'wants you', no approvals, no Chrome link", async () => {
    const el = await presenceFor("household");
    // Resting by day, sleepy from 23:00 — either way never the amber
    // attention (the assertion used to say "idle" and failed every night).
    assert.ok(["idle", "sleepy"].includes(el.dataset.mood ?? ""), `mood was ${el.dataset.mood}`);
    assert.equal(el.dataset.hands, "false");
    assert.doesNotMatch(el.textContent ?? "", /approval|thought|Chrome/i);
  });

  it("the hub home asks for the household's presence", () => {
    // Since the 2026-09-26 perf pass the greeting's Edward (and his
    // presence) is its own chunk: the page mounts HubEdward, which asks.
    const page = readFileSync(new URL("../app/(display)/hub/page.tsx", import.meta.url), "utf8");
    assert.match(page, /import\("\.\/components\/HubEdward"\)/);
    assert.match(page, /<HubEdward\b/);
    const src = readFileSync(new URL("../app/(display)/hub/components/HubEdward.tsx", import.meta.url), "utf8");
    assert.match(src, /useEdwardState\([^;]{0,80}\{\s*audience:\s*"household"\s*\}\s*\)/);
  });
});
