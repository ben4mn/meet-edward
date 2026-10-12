// R/G TDD red for Bucket A (frontend half).
//
// The backend now emits UTC ISO strings ending in "Z". The frontend must be
// the single place that localises. `lib/time.ts` provides helpers that:
//   - Parse the Z string as UTC
//   - Render in an explicitly-provided tz (from user settings) or fall back
//     to the browser's resolved tz
//   - Never double-convert
//
// Run: node --test frontend/__tests__/time.test.ts

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { formatClock, formatDateTime, formatRelative } from "../lib/time.ts";

describe("formatDateTime", () => {
  it("renders in provided tz regardless of host tz", () => {
    const iso = "2026-04-21T14:00:00Z"; // 14:00 UTC
    const chicago = formatDateTime(iso, { tz: "America/Chicago" });
    // CDT in April → 09:00. Must contain "9" hour and clearly not 14.
    assert.match(chicago, /(^|\D)9(:|\s)/, `expected 9-ish local hour, got ${chicago}`);
  });

  it("includes the year when the date is in a different calendar year", () => {
    const iso = "2024-03-05T20:00:00Z";
    const out = formatDateTime(iso, {
      tz: "America/Chicago",
      now: new Date("2026-04-21T14:00:00Z"),
    });
    assert.match(out, /2024/, `expected the year to be shown, got ${out}`);
  });

  it("omits the year for today in the same year", () => {
    const iso = "2026-04-21T14:00:00Z";
    const out = formatDateTime(iso, {
      tz: "America/Chicago",
      now: new Date("2026-04-21T15:00:00Z"),
    });
    assert.doesNotMatch(out, /2026/, `today should not show year, got ${out}`);
  });
});

describe("formatRelative", () => {
  it("renders '2h ago', never '-2h'", () => {
    const past = new Date("2026-04-21T12:00:00Z").toISOString();
    const now = new Date("2026-04-21T14:00:00Z");
    const out = formatRelative(past, { now });
    assert.match(out, /2h ago/, `expected '2h ago', got ${out}`);
    assert.doesNotMatch(out, /-\d/, `must not contain a negative number, got ${out}`);
  });

  it("renders 'in 30m' for future", () => {
    const future = new Date("2026-04-21T14:30:00Z").toISOString();
    const now = new Date("2026-04-21T14:00:00Z");
    const out = formatRelative(future, { now });
    assert.match(out, /in 30m/, `expected 'in 30m', got ${out}`);
  });

  it("renders 'just now' under 60s", () => {
    const t = new Date("2026-04-21T14:00:15Z").toISOString();
    const now = new Date("2026-04-21T14:00:30Z");
    assert.equal(formatRelative(t, { now }), "just now");
  });
});

describe("formatClock", () => {
  it("renders a clock time in the given tz", () => {
    const iso = "2026-04-21T14:00:00Z";
    const out = formatClock(iso, { tz: "America/Chicago" });
    // In CDT, 14:00 UTC = 9:00 am local.
    assert.match(out, /9:00\s?AM/i, `got ${out}`);
  });
});

// L1 (2026-09-27 follow-ups): the app had two clock formats — lib/time.ts
// said Intl's "9:00 AM" (the sidebar, the Listening log, Skills, Usage,
// Memories, Files) while Today, presence and the backend say "7:34 pm"
// (lib/edwardMood.formatClock, today_service._clock). One format now.
import { formatClock as moodClock } from "../lib/edwardMood.ts";
import { joinClockParts } from "../lib/time.ts";

describe("one clock format: \"7:34 pm\"", () => {
  it("formatClock says am/pm in lower case with a plain space", () => {
    assert.equal(formatClock("2026-04-21T14:00:00Z", { tz: "America/Chicago" }), "9:00 am");
    assert.equal(formatClock("2026-04-22T00:34:00Z", { tz: "America/Chicago" }), "7:34 pm");
  });

  it("formatDateTime carries the same clock", () => {
    const out = formatDateTime("2026-04-21T14:00:00Z", { tz: "America/Chicago", now: new Date("2026-04-21T15:00:00Z") });
    assert.equal(out, "Apr 21, 9:00 am");
    assert.doesNotMatch(out, /AM|PM/);
  });

  it("agrees with the clock Today and presence use", () => {
    assert.equal(formatClock("2026-04-22T00:34:00Z", { tz: "America/Chicago" }), moodClock(19, 34));
    assert.equal(formatClock("2026-04-21T05:05:00Z", { tz: "America/Chicago" }), moodClock(0, 5));
  });

  // The hub's clocks and the calendar's times hold a Date (a naive calendar
  // time is the mini's local time, so it must not be re-read as UTC).
  it("formatClock takes a Date as well as an ISO string", () => {
    assert.equal(formatClock(new Date("2026-04-22T00:34:00Z"), { tz: "America/Chicago" }), "7:34 pm");
    assert.equal(formatClock(new Date(NaN), { tz: "America/Chicago" }), "");
  });

  it("a browser's narrow no-break space before the day period becomes a plain space", () => {
    const parts: Intl.DateTimeFormatPart[] = [
      { type: "hour", value: "9" },
      { type: "literal", value: ":" },
      { type: "minute", value: "00" },
      { type: "literal", value: " " },
      { type: "dayPeriod", value: "AM" },
    ];
    assert.equal(joinClockParts(parts), "9:00 am");
  });
});
