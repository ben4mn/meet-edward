// /screen (2026-09-28): the page draws the stage from GET /api/screen and his
// thinking from GET /api/screen/live, keeps what Edward shows full screen on
// top, and still draws the stock scene when the first fetch fails.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";

import { ScreenLayout } from "../app/(display)/display/components/ScreenLayout.tsx";
import { fetchLive, LIVE_URL } from "../app/(display)/display/lib/screenApi.ts";
import { resetSharedPolls } from "../app/(display)/display/lib/sharedPoll.ts";

const originalFetch = globalThis.fetch;
let screenBody: unknown = null;
let liveBody: unknown = null;
let liveStatus = 200;
const seen: string[] = [];

const json = (body: unknown, status = 200) =>
  new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });

// Some live panels use the automatic JSX runtime Next compiles with; under
// node:test (classic runtime) they need React on the global, as in
// buildReloadWiring.test.tsx.
const g = globalThis as { React?: typeof React };
const hadReact = g.React;

beforeEach(() => {
  g.React = React;
  resetSharedPolls();
  seen.length = 0;
  liveStatus = 200;
  screenBody = null;
  liveBody = { thinking: null, folded: [], now: new Date().toISOString() };
  globalThis.fetch = (async (input: RequestInfo | URL) => {
    const url = String(input);
    seen.push(url);
    if (url.endsWith("/api/screen")) return screenBody ? json(screenBody) : new Response("", { status: 500 });
    if (url.endsWith("/api/screen/live")) return json(liveBody, liveStatus);
    return new Response("{}", { status: 404 });
  }) as typeof fetch;
});

afterEach(() => {
  cleanup();
  if (hadReact) g.React = hadReact;
  else delete g.React;
  resetSharedPolls();
  globalThis.fetch = originalFetch;
});

const now = new Date().toISOString();

describe("fetchLive", () => {
  it("an older backend without the route is no thinking, not a failure", async () => {
    liveStatus = 404;
    const live = await fetchLive();
    assert.equal(live.thinking, null);
    assert.equal(LIVE_URL, "/api/screen/live");
  });
  it("a server error is a failure the poll counts", async () => {
    liveStatus = 500;
    await assert.rejects(fetchLive());
  });
  it("keeps his recent thoughts (round 2); a backend without them sends none", async () => {
    const recent = [{ text: "Parker's Saturday plan is still open.", at: now, about: "messages", kind: "mind" }];
    liveBody = { thinking: null, folded: [], recent, now };
    assert.deepEqual((await fetchLive()).recent, recent);
    liveBody = { thinking: null, folded: [], now };
    assert.deepEqual((await fetchLive()).recent, []);
  });
});

describe("ScreenLayout — on his mind", () => {
  it("with no turn running, the lane shows one of his recent thoughts, labelled as past", async () => {
    screenBody = {
      stage: { cols: 24, rows: 12 },
      scene: {
        pieces: [{ id: "c", kind: "clock", size: "s", enter: "fade", place: "top-left", rect: { x: 1, y: 1, w: 5, h: 2 }, title: null }],
        me: { at: "floor", rect: { x: 4, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null },
        why: "", source: "edward", updated_at: now, updated_by: "edward",
      },
      asked: null, lines: [], owner_at_mini: true, show: null, now,
    };
    liveBody = { thinking: null, folded: [], recent: [{ text: "Parker's Saturday plan is still open.", at: now, about: "messages", kind: "mind" }], now };
    const { container } = render(<ScreenLayout />);
    await waitFor(() => assert.match(container.querySelector("[data-lane=mind]")?.textContent ?? "", /Parker's Saturday plan is still open\./));
    assert.match(container.querySelector("[data-lane=mind] .st-mind-over")?.textContent ?? "", /^(On my mind|Dreaming) · /);
    assert.equal((container.querySelector("[data-stage]") as HTMLElement).dataset.mode === "desk", false, "Ellis at the mini: no desk mode");
  });
});

describe("ScreenLayout", () => {
  it("draws the stage Edward composed, and asks for his thinking", async () => {
    screenBody = {
      stage: { cols: 24, rows: 12 },
      scene: {
        pieces: [{ id: "title", kind: "words", size: "l", enter: "fade", place: "top-left", rect: { x: 1, y: 1, w: 16, h: 3 }, title: null, text: "A quiet Monday afternoon." }],
        me: { at: "floor", rect: { x: 4, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null },
        why: "", source: "edward", updated_at: now, updated_by: "edward",
      },
      asked: null, lines: [], owner_at_mini: false, show: null, now,
    };
    const { findByText, container } = render(<ScreenLayout />);
    await findByText("A quiet Monday afternoon.");
    await waitFor(() => assert.ok(seen.some((u) => u.endsWith("/api/screen/live"))));
    assert.equal(container.querySelectorAll(".pane-avatar.is-live").length, 1);
    assert.equal(container.querySelector(".mc-grid"), null, "no tile grid any more");
  });

  it("a failed first fetch still draws the stock scene", async () => {
    screenBody = null;
    const { container } = render(<ScreenLayout />);
    await waitFor(() => assert.ok(container.querySelector('[data-piece][data-kind="clock"]')));
    assert.ok(container.querySelector('[data-kind="calendar"]'));
    assert.ok(container.querySelector('[data-kind="pets"]'));
  });

  it("the lane stops saying Thinking once his thinking can't be read (two failed reads)", async () => {
    screenBody = null;
    liveBody = {
      thinking: {
        turn: "t1", source: "mind", about: "messages", since: new Date().toISOString(),
        lines: [{ text: "Parker asked about Saturday.", at: new Date().toISOString() }], receipts: [],
      },
      folded: [], now: new Date().toISOString(),
    };
    const { container } = render(<ScreenLayout />);
    await waitFor(() => assert.ok(container.querySelector('[data-lane="thinking"]')), { timeout: 4000 });
    liveStatus = 502;
    // Two failed reads at the 2 s live poll; a plain wait (waitFor's DOM dumps are heavy here).
    let gone = false;
    for (let k = 0; k < 40 && !gone; k++) {
      await new Promise((r) => setTimeout(r, 250));
      gone = container.querySelector('[data-lane="thinking"]') === null;
    }
    assert.ok(gone, "still claims Thinking with the live read failing");
  });
});
