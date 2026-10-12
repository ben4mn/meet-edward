// Settings → Work → Scheduled (2026-09-26, Jarvis polish). Pure.
//
// - Three groups: Ellis's reminders, Edward's own wake-ups (mind_wake and the
//   morning/evening/reflect ticks), and housekeeping (the screens pulse, the
//   surplus tick, other seeded maintenance), which the panel folds.
// - The API may carry event_metadata (object or JSON text); without it the
//   seeded rows are recognised by their real descriptions and threads.
// - Cron patterns are said in words ("Every day at 7 am").

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { describeCron, eventGroup, eventKind, eventTitle, groupEvents } from "../lib/eventGroups.ts";

function ev(over: Record<string, unknown> = {}) {
  return {
    id: "e",
    conversation_id: null as string | null,
    description: "Remind Ellis to call the vet",
    scheduled_at: "2026-09-26T18:00:00Z",
    next_fire_at: "2026-09-26T18:00:00Z",
    recurrence_pattern: null as string | null,
    status: "pending",
    created_by: "user",
    last_fired_at: null as string | null,
    updated_at: "2026-09-25T00:00:00Z",
    ...over,
  };
}

describe("eventKind", () => {
  it("prefers event_metadata, object or JSON text", () => {
    assert.equal(eventKind(ev({ event_metadata: { type: "mind_wake", why: "x" } })), "mind_wake");
    assert.equal(eventKind(ev({ event_metadata: '{"type":"wakeup_tick","kind":"morning"}' })), "wakeup_tick");
    assert.equal(eventKind(ev({ event_metadata: "not json" })), null);
  });

  it("trusts the API's event_type when it is there — null is Ellis's plain reminder (backend 2026-09-26)", () => {
    assert.equal(eventKind(ev({ event_type: "expression_pulse", description: "anything" })), "expression_pulse");
    assert.equal(eventKind(ev({ event_type: "mind_wake" })), "mind_wake");
    assert.equal(eventKind(ev({ event_type: null, conversation_id: "edward-mind" })), null);
  });

  it("a goal check-in reads as one, without its document id", () => {
    const e = ev({
      event_type: "mind_wake",
      event_metadata: { type: "mind_wake", goal_document_id: "1a2b3c4d-9", check_in: "weekly" },
      description: "[goal check-in] Run a sub-25 5k (goal 1a2b3c4d)",
      conversation_id: "edward-mind",
      created_by: "edward",
    });
    assert.equal(eventGroup(e), "wakes");
    assert.equal(eventTitle(e), "Check in on your goal: Run a sub-25 5k");
  });

  it("recognises the real seeded rows without metadata", () => {
    assert.equal(eventKind(ev({ description: "Morning wake — the mind decides", recurrence_pattern: "0 7 * * *", created_by: "edward" })), "wakeup_tick");
    assert.equal(eventKind(ev({ description: "Reflect wake — the mind decides", recurrence_pattern: "0 9 * * 0", created_by: "edward" })), "wakeup_tick");
    assert.equal(
      eventKind(ev({ description: "expression_pulse — Edward decides what his widget and the household screen show", recurrence_pattern: "*/15 * * * *", created_by: "edward" })),
      "expression_pulse",
    );
    assert.equal(eventKind(ev({ description: "surplus_tick — Edward spends the week's spare Codex quota on useful work", recurrence_pattern: "*/10 * * * *", created_by: "edward" })), "surplus_tick");
    assert.equal(eventKind(ev({ description: "Surfaces pulse", conversation_id: "edward-surfaces", created_by: "edward" })), "expression_pulse");
    assert.equal(eventKind(ev({ description: "Check whether Parker confirmed", conversation_id: "edward-mind", created_by: "edward" })), "mind_wake");
    assert.equal(eventKind(ev({ description: "widget_refresh — Edward's own maintenance", recurrence_pattern: "*/5 * * * *", created_by: "edward" })), "maintenance");
    assert.equal(eventKind(ev()), null);
  });
});

describe("eventGroup", () => {
  it("splits reminders, wake-ups and housekeeping", () => {
    assert.equal(eventGroup(ev()), "yours");
    assert.equal(eventGroup(ev({ created_by: "edward", description: "Rowan's flight lands", conversation_id: "th-flight" })), "yours");
    assert.equal(eventGroup(ev({ event_metadata: { type: "mind_wake" } })), "wakes");
    assert.equal(eventGroup(ev({ description: "Evening wake — the mind decides", recurrence_pattern: "0 18 * * *", created_by: "edward" })), "wakes");
    assert.equal(eventGroup(ev({ event_metadata: { type: "expression_pulse" } })), "housekeeping");
    assert.equal(eventGroup(ev({ event_metadata: { type: "surplus_tick" } })), "housekeeping");
  });
});

describe("eventTitle", () => {
  it("says the seeded rows in plain words", () => {
    assert.equal(eventTitle(ev({ description: "Morning wake — the mind decides", recurrence_pattern: "0 7 * * *", created_by: "edward" })), "Morning wake");
    assert.equal(eventTitle(ev({ event_metadata: { type: "expression_pulse" }, description: "expression_pulse — Edward decides…" })), "Decide what my screens show");
    assert.equal(eventTitle(ev({ event_metadata: { type: "surplus_tick" }, description: "surplus_tick — …" })), "Put spare time to use");
    assert.equal(eventTitle(ev({ description: "Remind Ellis to call the vet" })), "Remind Ellis to call the vet");
  });
});

describe("describeCron", () => {
  it("says common patterns in words", () => {
    assert.equal(describeCron("*/15 * * * *"), "Every 15 minutes");
    assert.equal(describeCron("* * * * *"), "Every minute");
    assert.equal(describeCron("0 * * * *"), "Every hour");
    assert.equal(describeCron("0 */2 * * *"), "Every 2 hours");
    assert.equal(describeCron("0 7 * * *"), "Every day at 7 am");
    assert.equal(describeCron("30 18 * * *"), "Every day at 6:30 pm");
    assert.equal(describeCron("0 9,17 * * *"), "Every day at 9 am and 5 pm");
    assert.equal(describeCron("0 12 * * *"), "Every day at 12 pm");
    assert.equal(describeCron("0 0 * * *"), "Every day at 12 am");
    assert.equal(describeCron("0 9 * * 0"), "Sundays at 9 am");
    assert.equal(describeCron("0 19 * * 0"), "Sundays at 7 pm");
    assert.equal(describeCron("30 8 * * 1-5"), "Weekdays at 8:30 am");
    assert.equal(describeCron("0 10 * * 0,6"), "Weekends at 10 am");
    assert.equal(describeCron("0 8 * * 1,3"), "Mondays and Wednesdays at 8 am");
    assert.equal(describeCron("0 9 1 * *"), "Monthly on the 1st at 9 am");
  });

  it("gives up honestly on what it cannot say", () => {
    assert.equal(describeCron("0 9 1 1 *"), null);
    assert.equal(describeCron("garbage"), null);
    assert.equal(describeCron(null), null);
  });
});

describe("groupEvents", () => {
  it("puts upcoming rows in their groups, soonest first, and the rest under past", () => {
    const g = groupEvents([
      ev({ id: "late", next_fire_at: "2026-09-27T09:00:00Z" }),
      ev({ id: "soon", next_fire_at: "2026-09-26T13:00:00Z" }),
      ev({ id: "tick", event_metadata: { type: "wakeup_tick" }, next_fire_at: "2026-09-27T12:00:00Z" }),
      ev({ id: "pulse", event_metadata: { type: "expression_pulse" } }),
      ev({ id: "done", status: "completed", last_fired_at: "2026-09-25T09:00:00Z" }),
      ev({ id: "failed", status: "failed", last_fired_at: "2026-09-26T09:00:00Z" }),
      ev({ id: "running", status: "processing" }),
    ]);
    assert.deepEqual(g.yours.map((e) => e.id), ["soon", "running", "late"]);
    assert.deepEqual(g.wakes.map((e) => e.id), ["tick"]);
    assert.deepEqual(g.housekeeping.map((e) => e.id), ["pulse"]);
    assert.deepEqual(g.past.map((e) => e.id), ["failed", "done"]);
  });
});

import { whenLine } from "../lib/eventGroups.ts";

describe("whenLine", () => {
  const now = new Date("2026-09-26T15:00:00Z"); // 10:00 am in Springfield
  const tz = "America/Chicago";
  it("says today, tomorrow, yesterday, then the date", () => {
    // The app's one clock format, "7:34 pm" (L1, 2026-09-27).
    assert.equal(whenLine("2026-09-26T20:45:00Z", { now, tz }), "Today at 3:45 pm");
    assert.equal(whenLine("2026-09-27T12:00:00Z", { now, tz }), "Tomorrow at 7:00 am");
    assert.equal(whenLine("2026-09-25T23:00:00Z", { now, tz }), "Yesterday at 6:00 pm");
    assert.equal(whenLine("2026-10-03T14:00:00Z", { now, tz }), "Sat, Oct 3 at 9:00 am");
    assert.equal(whenLine("", { now, tz }), "");
  });
});

import { resultText } from "../lib/eventGroups.ts";

describe("resultText", () => {
  it("drops internal prefixes and speaks plainly", () => {
    assert.equal(resultText("Tool error: blocked — stood down in that chat"), "Blocked — stood down in that chat");
    assert.equal(resultText("acted: left a note"), "Acted: left a note");
    assert.equal(resultText("left alone"), "Left it alone");
    assert.equal(resultText("  "), null);
    assert.equal(resultText(null), null);
  });
});
