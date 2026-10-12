// R/G red for the display heartbeat panel's interactive state.
//
// Extracted into a pure module so the React component stays a thin
// shell. Reducer + filter helper live in
// app/(display)/display/lib/heartbeatPanelState.ts.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  initialHeartbeatPanelState,
  heartbeatPanelReducer,
  filterHeartbeatEvents,
  type HeartbeatFilter,
} from "../app/(display)/display/lib/heartbeatPanelState.ts";

function ev(id: string, triage_status: string | null) {
  return {
    id,
    triage_status,
    source: "imessage",
    contact_name: "x",
    sender: "x",
    summary: "y",
    created_at: "2026-05-15T12:00:00Z",
  };
}

describe("heartbeatPanelReducer", () => {
  it("initial filter is all and no row expanded", () => {
    assert.equal(initialHeartbeatPanelState.filter, "all");
    assert.equal(initialHeartbeatPanelState.expandedId, null);
  });

  it("SET_FILTER updates the filter", () => {
    const next = heartbeatPanelReducer(initialHeartbeatPanelState, {
      type: "SET_FILTER",
      filter: "urgent",
    });
    assert.equal(next.filter, "urgent");
  });

  it("TOGGLE_EXPAND sets and unsets the expanded row", () => {
    const a = heartbeatPanelReducer(initialHeartbeatPanelState, {
      type: "TOGGLE_EXPAND",
      id: "evt-1",
    });
    assert.equal(a.expandedId, "evt-1");
    const b = heartbeatPanelReducer(a, { type: "TOGGLE_EXPAND", id: "evt-1" });
    assert.equal(b.expandedId, null);
  });

  it("TOGGLE_EXPAND switches between rows", () => {
    const a = heartbeatPanelReducer(initialHeartbeatPanelState, {
      type: "TOGGLE_EXPAND",
      id: "evt-1",
    });
    const b = heartbeatPanelReducer(a, { type: "TOGGLE_EXPAND", id: "evt-2" });
    assert.equal(b.expandedId, "evt-2");
  });

  it("SET_FILTER clears expanded row (it might not match new filter)", () => {
    const a = heartbeatPanelReducer(initialHeartbeatPanelState, {
      type: "TOGGLE_EXPAND",
      id: "evt-1",
    });
    const b = heartbeatPanelReducer(a, { type: "SET_FILTER", filter: "urgent" });
    assert.equal(b.expandedId, null);
  });
});

describe("filterHeartbeatEvents", () => {
  const events = [
    ev("a", "pending"),
    ev("b", "mention"),
    ev("c", "escalated"),
    ev("d", "noted"),
    ev("e", "acted"),
    ev("f", "dismissed"),
    ev("g", null),
    ev("h", "calendar_urgent"),
  ];

  function ids(filter: HeartbeatFilter): string[] {
    return filterHeartbeatEvents(events as any, filter).map((e: any) => e.id);
  }

  it("all returns everything", () => {
    assert.deepEqual(ids("all"), ["a", "b", "c", "d", "e", "f", "g", "h"]);
  });

  it("urgent keeps mention, escalated, calendar_urgent", () => {
    assert.deepEqual(ids("urgent"), ["b", "c", "h"]);
  });

  it("pending keeps pending and null triage", () => {
    assert.deepEqual(ids("pending"), ["a", "g"]);
  });

  it("done keeps noted, acted, dismissed", () => {
    assert.deepEqual(ids("done"), ["d", "e", "f"]);
  });
});
