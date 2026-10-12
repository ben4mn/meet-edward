// R/G red for Work's activity timeline ("what he does on his own").
//
//  - His own maintenance — the morning/evening wakes, the surfaces pulse,
//    the surplus tick, dreaming — is one "His rhythm" row, not forty.
//  - Wakes that acted are in it, first-person, with the thinking to read.
//  - What is still to come is the Events panel's job; the timeline is what
//    happened, newest first.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  isRhythmEvent,
  buildActivity,
  filterActivity,
  type ActivityInput,
} from "../components/settings/activityModel.ts";

const NOW = new Date("2026-09-26T18:00:00Z");

function ev(p: Record<string, unknown>) {
  return {
    id: "ev",
    conversation_id: null,
    description: "Something",
    next_fire_at: "2026-09-27T12:00:00Z",
    recurrence_pattern: null,
    status: "pending",
    created_by: "edward",
    last_fired_at: null,
    fire_count: 0,
    last_result: null,
    ...p,
  } as ActivityInput["events"][number];
}

const input: ActivityInput = {
  events: [
    ev({ id: "tick-m", description: "Morning wake — the mind decides", recurrence_pattern: "0 7 * * *", last_fired_at: "2026-09-26T12:00:00Z", fire_count: 41, next_fire_at: "2026-09-27T12:00:00Z" }),
    ev({ id: "pulse", description: "Surfaces pulse", conversation_id: "edward-surfaces", recurrence_pattern: "*/15 * * * *", last_fired_at: "2026-09-26T17:53:00Z", fire_count: 1920, next_fire_at: "2026-09-26T18:08:00Z" }),
    ev({ id: "bistro", description: "Check Bistro Lumen availability for the 29th", conversation_id: "c-rowan-bday", recurrence_pattern: "0 9,17 * * *", last_fired_at: "2026-09-25T23:00:00Z", fire_count: 3, last_result: "No 7pm table yet." }),
    ev({ id: "parker", description: "Confirm Parker's pick-up time", next_fire_at: "2026-09-28T08:00:00Z" }),
    ev({ id: "plants", description: "Remind Ellis to water the plants", status: "completed", last_fired_at: "2026-09-26T09:00:00Z", fire_count: 1, last_result: "Pushed Ellis." }),
    ev({ id: "user-weekly", description: "Weekly bins reminder", created_by: "user", recurrence_pattern: "0 19 * * 0", last_fired_at: "2026-09-20T00:00:00Z" }),
  ],
  tasks: [
    { id: "w-1", task_description: "Research flights", status: "completed", result_summary: "Found 3", error: null, task_type: "worker", model: null, started_at: "2026-09-26T10:00:00Z", completed_at: "2026-09-26T10:20:00Z", worker_conversation_id: "w-conv" },
  ],
  wishes: [{ id: "wish-1", title: "Read my Strava", why: "Runs", status: "open", updated_at: "2026-09-24T10:00:00Z" }],
  dreaming: { last_run: "2026-09-26T16:00:00Z", next_run: "2026-09-26T19:00:00Z", enabled: true, running: false, total_connections: 312, total_flags: 4 },
  wakes: [
    { id: 1, at: "2026-09-26T16:55:00Z", source: "mention", outcome: "acted", note: "Parker asked me directly; I answered.", thought_id: "t-reply-parker" },
    { id: 2, at: "2026-09-26T17:20:00Z", source: "messages", outcome: "left_alone", note: "Nothing for Ellis.", thought_id: null },
    { id: 3, at: "2026-09-26T08:00:00Z", source: "reflect_lite", outcome: "acted", note: "Rewrote my state page.", thought_id: null },
  ],
};

describe("isRhythmEvent", () => {
  it("is Edward's own recurring maintenance, not a recurring task for Ellis", () => {
    assert.ok(isRhythmEvent(input.events[0]), "the morning tick");
    assert.ok(isRhythmEvent(input.events[1]), "the pulse in edward-surfaces");
    assert.ok(!isRhythmEvent(input.events[2]), "Bistro Lumen is a task for Ellis");
    assert.ok(!isRhythmEvent(input.events[3]), "one-shot");
    assert.ok(!isRhythmEvent(input.events[5]), "Ellis made it");
  });
});

describe("buildActivity", () => {
  const rows = buildActivity(input, { now: NOW });

  it("folds the rhythm into one row, first, saying what comes next", () => {
    assert.equal(rows[0].kind, "rhythm");
    assert.equal(rows[0].title, "His rhythm");
    assert.equal(rows[0].routines?.length, 3, "two ticks + dreaming");
    assert.match(rows[0].detail, /^3 routines · next: Tidying his screens in 8m$/);
    assert.equal(rows.filter((r) => r.id.startsWith("ev:tick") || r.id === "ev:pulse").length, 0);
    const morning = rows[0].routines!.find((r) => r.id === "ev:tick-m")!;
    assert.equal(morning.title, "Morning wake", "the seed's ' — the mind decides' is internal");
    assert.equal(morning.href, "/settings/events?id=tick-m");
  });

  it("includes only wakes that acted, in his words, with the thinking to read", () => {
    const wakes = rows.filter((r) => r.kind === "wake");
    assert.deepEqual(wakes.map((w) => w.title), ["Parker asked me directly; I answered.", "Rewrote my state page."]);
    assert.equal(wakes[0].thoughtHref, "/chat?c=t-reply-parker");
    assert.equal(wakes[0].href, "/chat?c=t-reply-parker");
    assert.equal(wakes[0].detail, "Answering a mention");
    assert.equal(wakes[0].tone, "edward");
    assert.equal(wakes[1].thoughtHref, null);
    assert.equal(wakes[1].href, "/settings/thoughts");
  });

  it("lists what happened newest first and leaves what is still to come to Events", () => {
    const rest = rows.slice(1);
    const ats = rest.map((r) => Date.parse(r.at));
    assert.deepEqual([...ats].sort((a, b) => b - a), ats);
    assert.ok(!rows.some((r) => r.id === "ev:parker"), "an unfired one-shot is not activity");
    const bistro = rows.find((r) => r.id === "ev:bistro")!;
    assert.equal(bistro.detail, "No 7pm table yet.");
    assert.equal(bistro.href, "/settings/events?id=bistro");
    assert.ok(rows.find((r) => r.id === "w:w-1"));
    assert.equal(rows.find((r) => r.id === "wish:wish-1")?.href, "/settings/wishlist?id=wish-1");
  });
});

describe("filterActivity", () => {
  const rows = buildActivity(input, { now: NOW });
  it("keeps the rhythm under All and Events only", () => {
    assert.equal(filterActivity(rows, "all")[0].kind, "rhythm");
    assert.equal(filterActivity(rows, "event")[0].kind, "rhythm");
    assert.ok(filterActivity(rows, "wake").every((r) => r.kind === "wake"));
    assert.ok(filterActivity(rows, "wish").every((r) => r.kind === "wish"));
  });
});

describe("routineTitle", () => {
  it("names his routines for Ellis, never by their internal ids", async () => {
    const { routineTitle } = await import("../components/settings/activityModel.ts");
    assert.equal(routineTitle("Morning wake — the mind decides"), "Morning wake");
    assert.equal(routineTitle("Reflect wake — the mind decides"), "Reflect wake");
    assert.equal(routineTitle("expression_pulse — Edward decides what his widget and the household screen show"), "Tidying his screens");
    assert.equal(routineTitle("Surfaces pulse"), "Tidying his screens");
    assert.equal(routineTitle("surplus_tick — Edward spends the week's spare Codex quota on useful work"), "Spending spare fuel");
    assert.equal(routineTitle("widget_refresh — Edward's own maintenance"), "Widget refresh");
  });
});

describe("worker rows", () => {
  it("say a running worker is working, not 'Worker'", () => {
    const rows = buildActivity(
      { ...input, tasks: [{ ...input.tasks[0], id: "w-2", status: "running", result_summary: null, completed_at: null }] },
      { now: NOW },
    );
    assert.equal(rows.find((r) => r.id === "w:w-2")?.detail, "Working on it");
  });
});

describe("a failed event's result", () => {
  it("reads without the internal 'Tool error:' prefix", () => {
    const rows = buildActivity(
      {
        ...input,
        events: [ev({ id: "jordan", description: "Text Jordan the cabin address", status: "failed", last_fired_at: "2026-09-26T09:00:00Z", last_result: "Tool error: blocked — stood down in that chat" })],
      },
      { now: NOW },
    );
    const row = rows.find((r) => r.id === "ev:jordan")!;
    assert.equal(row.detail, "Blocked — stood down in that chat");
  });
});

