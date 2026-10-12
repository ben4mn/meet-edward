// R/G red for the "in 58m" bug on the inbox and sidebar.
//
// lib/time.ts documents a contract: backends hand us UTC ISO strings
// ending in Z. The conversations API does not honour it — it emits
// naive `2026-09-06T02:30:42.820153`. `new Date()` reads a naive string
// as *local* time, so in Springfield a conversation updated five hours ago
// rendered as "in 58m".
//
// The fix is defensive on this side too: a naive ISO string is UTC,
// full stop. Every formatter parses through one helper.
//
// Run: node --test frontend/__tests__/timeParse.test.ts

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  parseIso,
  formatRelative,
  formatDayHeader,
  formatClock,
} from "../lib/time.ts";

const NOW = new Date("2026-04-21T18:00:00Z");

describe("parseIso", () => {
  it("treats a naive ISO string as UTC", () => {
    assert.equal(
      parseIso("2026-04-21T16:00:00").getTime(),
      Date.parse("2026-04-21T16:00:00Z"),
    );
  });

  it("keeps microsecond precision strings parseable", () => {
    const d = parseIso("2026-09-06T02:30:42.820153");
    assert.equal(Number.isNaN(d.getTime()), false);
    assert.equal(d.toISOString(), "2026-09-06T02:30:42.820Z");
  });

  it("leaves Z and offset strings alone", () => {
    assert.equal(parseIso("2026-04-21T16:00:00Z").getTime(), Date.parse("2026-04-21T16:00:00Z"));
    assert.equal(
      parseIso("2026-04-21T11:00:00-05:00").getTime(),
      Date.parse("2026-04-21T16:00:00Z"),
    );
  });

  it("returns an invalid date for garbage rather than throwing", () => {
    assert.equal(Number.isNaN(parseIso("not a date").getTime()), true);
  });
});

describe("formatters accept naive UTC", () => {
  it("formatRelative never says 'in …' about a past naive timestamp", () => {
    // 2h before NOW, written the way the conversations API writes it.
    const s = formatRelative("2026-04-21T16:00:00", { now: NOW });
    assert.equal(s, "2h ago");
  });

  it("formatDayHeader buckets a naive timestamp by its UTC instant", () => {
    // 01:30 UTC on Apr 22 is still Apr 21 evening in Springfield -> Today.
    assert.equal(
      formatDayHeader("2026-04-22T01:30:00", { now: NOW, tz: "America/Chicago" }),
      "Today",
    );
  });

  it("formatClock localises a naive timestamp once", () => {
    assert.equal(formatClock("2026-04-21T16:00:00", { tz: "America/Chicago" }), "11:00 am");
  });
});
