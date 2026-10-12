// Failures are said, not rendered as "nothing here" (review 2026-09-26,
// failures-rendered-as-empty): the Work room's timeline and the Mind room's
// "Lately" say in Edward's voice what they could not read.

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

import { json, mockFetch, restoreFetch } from "./panelTestKit.ts";
import { ActivityTimeline } from "../components/settings/ActivityTimeline.tsx";
import { MindRoomView } from "../components/settings/MindRoomView.tsx";
import { edwardTabStore } from "../components/settings/useEdwardTabStatus.ts";

afterEach(() => {
  cleanup();
  restoreFetch();
  edwardTabStore.reset();
});

const said = (re: RegExp) => screen.queryByText(re) !== null;

describe("the Work room's timeline", () => {
  it("everything failing is not an empty timeline", async () => {
    mockFetch(() => json({ detail: "down" }, 503));
    render(<ActivityTimeline />);
    await waitFor(() => assert.equal(said(/I can't see what I've been doing right now/), true));
    assert.equal(said(/Nothing here yet/), false);
  });

  it("the wakes filter does not say he hasn't acted when the log failed", async () => {
    mockFetch((url) => {
      if (url.includes("/api/mind/log")) return json({ detail: "busy" }, 503);
      if (url.includes("/api/events")) return [];
      if (url.includes("/api/orchestrator/tasks")) return [];
      if (url.includes("/api/wishlist")) return { items: [] };
      if (url.includes("/api/consolidation") || url.includes("dream")) return null;
      return undefined;
    });
    render(<ActivityTimeline />);
    await waitFor(() => assert.equal(said(/I can't read my wakes right now/), true));
    fireEvent.click(screen.getByRole("radio", { name: "Wakes" }));
    assert.equal(said(/I haven't acted on my own this week/), false);
  });
});

describe("the Mind room's Lately", () => {
  it("a mind/now that failed is not 'I haven't woken this week'", async () => {
    mockFetch((url) => {
      if (url.includes("/api/mind/now")) return json({ detail: "down" }, 503);
      if (url.includes("/api/documents")) return { documents: [] };
      return json({}, 404);
    });
    render(<MindRoomView />);
    await waitFor(() => assert.equal(said(/I can't reach my mind right now/), true));
    assert.equal(said(/I haven.t woken on my own this week/), false);
  });
});
