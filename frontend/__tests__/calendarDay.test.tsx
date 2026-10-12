// All-day events on Edward's screen (review 2026-09-26).
//
// The /screen calendar never read `all_day`: "Rowan's sister in town"
// took the NEXT UP slot as "in now · 12:00 am – 12:00 am" and pushed the
// real next meeting down. The backend's calendar normaliser drops apple-mcp's
// isAllDay, so the screen also recognises one by its shape (local midnight
// to local midnight), the way the surplus jobs do.

import { afterEach, beforeEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, render, waitFor } from "@testing-library/react";

import { isAllDayEvent, nextUpEvent } from "../app/(display)/display/lib/calendarDay.ts";
import { CalendarPanel } from "../app/(display)/display/components/CalendarPanel.tsx";

const TZ = "America/Chicago";
// 2026-09-26 in Springfield is UTC-5 (CDT): local midnight = 05:00Z.
const MIDNIGHT = "2026-09-26T05:00:00Z";
const NEXT_MIDNIGHT = "2026-09-27T05:00:00Z";

const ev = (over: Record<string, unknown>) => ({
  id: "e", title: "t", start: MIDNIGHT, end: NEXT_MIDNIGHT, where: "", kind: "personal" as const, ...over,
});

describe("isAllDayEvent", () => {
  it("honours the flag when the backend sends one", () => {
    assert.equal(isAllDayEvent(ev({ all_day: true, start: "2026-09-26T15:00:00Z", end: "2026-09-26T16:00:00Z" }), TZ), true);
    assert.equal(isAllDayEvent(ev({ all_day: false }), TZ), false);
  });

  it("recognises midnight-to-midnight in the user's zone without a flag", () => {
    assert.equal(isAllDayEvent(ev({}), TZ), true);
  });

  it("a multi-day all-day event counts too", () => {
    assert.equal(isAllDayEvent(ev({ start: "2026-09-25T05:00:00Z", end: "2026-09-28T05:00:00Z" }), TZ), true);
  });

  it("a long timed event is not all-day, nor is an hour at midnight", () => {
    assert.equal(isAllDayEvent(ev({ start: "2026-09-26T15:00:00Z", end: "2026-09-27T17:00:00Z" }), TZ), false);
    assert.equal(isAllDayEvent(ev({ end: "2026-09-26T06:00:00Z" }), TZ), false);
  });
});

describe("nextUpEvent", () => {
  const now = new Date("2026-09-26T19:00:00Z"); // 2pm in Springfield
  const allDay = ev({ id: "a", title: "Sister in town" });
  const standup = ev({ id: "s", title: "Standup", start: "2026-09-26T14:00:00Z", end: "2026-09-26T14:30:00Z" });
  const oneOnOne = ev({ id: "o", title: "1:1 with Priya", start: "2026-09-26T20:00:00Z", end: "2026-09-26T20:30:00Z" });

  it("skips all-day events and past ones: the next real meeting", () => {
    assert.equal(nextUpEvent([allDay, standup, oneOnOne], now, TZ)?.id, "o");
  });

  it("nothing next when only all-day and past events remain — never a past event 'in now'", () => {
    assert.equal(nextUpEvent([allDay, standup], now, TZ), null);
  });
});

// ---------------------------------------------------------------------------
// The panel
// ---------------------------------------------------------------------------

const originalFetch = globalThis.fetch;
let body: unknown = { source: "apple_calendar", fetched_at: null, events: [] };

beforeEach(() => {
  globalThis.fetch = (async () =>
    new Response(JSON.stringify(body), { status: 200, headers: { "Content-Type": "application/json" } })) as typeof fetch;
});

afterEach(() => {
  cleanup();
  globalThis.fetch = originalFetch;
});

describe("CalendarPanel", () => {
  it("an all-day event reads 'all day' and never takes NEXT UP from the real next meeting", async () => {
    // Built in the process's own zone (the panel falls back to it).
    const midnight = new Date();
    midnight.setHours(0, 0, 0, 0);
    const nextMidnight = new Date(midnight);
    nextMidnight.setDate(midnight.getDate() + 1);
    const soon = new Date(Date.now() + 2 * 3_600_000);
    body = {
      source: "apple_calendar",
      fetched_at: new Date().toISOString(),
      events: [
        { id: "a", title: "Rowan's sister in town", start: midnight.toISOString(), end: nextMidnight.toISOString(), where: "", kind: "personal" },
        { id: "b", title: "Vet call (Biscuit)", start: soon.toISOString(), end: new Date(soon.getTime() + 15 * 60_000).toISOString(), where: "", kind: "personal" },
      ],
    };
    const { container, findAllByText } = render(<CalendarPanel />);
    await findAllByText("Vet call (Biscuit)");
    await waitFor(() => assert.match(container.textContent ?? "", /all day/i));

    const text = container.textContent ?? "";
    assert.doesNotMatch(text, /12:00 am – 12:00 am/);
    const nextUp = text.slice(text.indexOf("NEXT UP"));
    assert.ok(nextUp.startsWith("NEXT UPVet call (Biscuit)"), `NEXT UP is ${nextUp.slice(0, 60)}`);
  });
});

describe("the hub's Today page", () => {
  it("uses the same all-day rule, so it is right in production too (the backend sends no flag yet)", async () => {
    const { readFileSync } = await import("node:fs");
    const src = readFileSync(new URL("../app/(display)/hub/today/page.tsx", import.meta.url), "utf8");
    assert.match(src, /isAllDayEvent\(/);
    assert.doesNotMatch(src, /e\.all_day \?/);
  });
});
