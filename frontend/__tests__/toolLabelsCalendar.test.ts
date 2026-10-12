// Ellis's calendar and Reminders on the work rail (2026-10-11).
//
// `schedule` reads Ellis's Reminders and, when he asks, adds, moves and deletes
// events and adds, checks off and deletes reminders. Each action reads as
// what Edward did ("Added “Dentist” to your calendar", "Checked off a
// reminder"), its failure as what he couldn't do ("Couldn't add …"), and
// every action the backend's SCHEDULE_ACTIONS names has its own label —
// never the bare "Checked the schedule".

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

import { describeTool } from "../lib/toolLabels.ts";

const FALLBACK = describeTool("schedule").title;

function scheduleActions(): string[] {
  const src = fs.readFileSync(path.resolve(import.meta.dirname, "../../backend/services/toolset/schedule.py"), "utf8");
  const m = /SCHEDULE_ACTIONS\s*=\s*\(([\s\S]*?)\)/.exec(src);
  assert.ok(m, "SCHEDULE_ACTIONS is in schedule.py");
  return [...m[1].matchAll(/"([a-z_]+)"/g)].map((x) => x[1]);
}

describe("every schedule action has its own label", () => {
  const actions = scheduleActions();
  it("reads the backend's tuple", () => {
    for (const a of ["reminders", "event_add", "event_update", "event_delete", "reminder_add", "reminder_complete", "reminder_delete"]) {
      assert.ok(actions.includes(a), a);
    }
  });
  for (const action of actions) {
    it(action, () => assert.notEqual(describeTool("schedule", action).title, FALLBACK, action));
  }
});

describe("the calendar writes", () => {
  it("adding an event names it", () => {
    const d = describeTool("schedule", "event_add: Dentist");
    assert.equal(d.title, "Added “Dentist” to your calendar");
    assert.equal(d.live, "Adding “Dentist” to your calendar…");
    assert.equal(d.failed, "Couldn't add “Dentist” to your calendar");
    assert.equal(d.icon, "calendar-plus");
    assert.equal(describeTool("schedule", "event_add").title, "Added an event to your calendar");
  });
  it("changing and deleting an event", () => {
    assert.equal(describeTool("schedule", "event_update: Dentist").title, "Changed “Dentist” on your calendar");
    assert.equal(describeTool("schedule", "event_update").title, "Changed an event");
    assert.equal(describeTool("schedule", "event_update: Dentist").failed, "Couldn't change “Dentist” on your calendar");
    const del = describeTool("schedule", "event_delete: cal-U1-813456000");
    assert.equal(del.title, "Deleted an event from your calendar");
    assert.equal(del.failed, "Couldn't delete an event from your calendar");
    assert.equal(del.icon, "calendar-x");
  });
});

describe("the reminders", () => {
  it("reading them", () => {
    assert.equal(describeTool("schedule", "reminders: vet").title, "Checked your reminders");
    assert.equal(describeTool("schedule", "reminders").live, "Checking your reminders…");
  });
  it("adding, checking off and deleting one", () => {
    assert.equal(describeTool("schedule", "reminder_add: Call the vet").title, "Added the reminder “Call the vet”");
    assert.equal(describeTool("schedule", "reminder_add").title, "Added a reminder");
    assert.equal(describeTool("schedule", "reminder_add: Call the vet").failed, "Couldn't add the reminder “Call the vet”");
    const done = describeTool("schedule", "reminder_complete: rem-CK1");
    assert.equal(done.title, "Checked off a reminder");
    assert.equal(done.failed, "Couldn't check off a reminder");
    const del = describeTool("schedule", "reminder_delete: rem-CK1");
    assert.equal(del.title, "Deleted a reminder");
    assert.equal(del.icon, "trash");
  });
});
