// The proactivity rebuild (2026-10-11): proactivity is noticing, not a
// schedule. One tick is left — the silent 03:00 `nightly` — and a new wake,
// `speak`, says something Edward noticed. The old reasons (morning, evening,
// reflect, free …) keep their labels so history still reads; the new ones
// get words everywhere a reason is shown, never a raw id. The `*/5`
// `proactive_sweep` is his housekeeping, never one of Ellis's events. And the
// stage (the Mac mini screen, which anyone in the room can read) never shows
// what a `speak` wake noticed: its lines are dropped and its fold and recent
// thought read one fixed line.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen } from "@testing-library/react";

import { wakeSourceLabel } from "../lib/mindLog.ts";
import { describeWake, wakeLabel } from "../components/settings/proactiveWakes.ts";
import { WakesNote } from "../components/settings/WakesNote.tsx";
import { reasonPhrase } from "../lib/edwardMood.ts";
import { wakeRowMeta } from "../components/layout/presenceSheetModel.ts";
import { routineTitle } from "../components/settings/activityModel.ts";
import { eventGroup, eventKind, eventTitle } from "../lib/eventGroups.ts";
import {
  SPEAK_LINE,
  mindPool,
  thoughtView,
  type ScreenLive,
} from "../app/(display)/display/lib/thinkingModel.ts";

const NOW = Date.parse("2026-10-11T21:30:00Z");

describe("the new reasons have words", () => {
  it("names nightly and speak in the mind log", () => {
    assert.equal(wakeSourceLabel("nightly"), "nightly reflection");
    assert.equal(wakeSourceLabel("speak"), "something he noticed");
    // History still reads.
    assert.equal(wakeSourceLabel("morning"), "morning wake");
    assert.equal(wakeSourceLabel("free"), "free time");
  });

  it("names them in the live line and the presence sheet", () => {
    assert.equal(reasonPhrase("nightly"), "thinking over the day");
    assert.equal(reasonPhrase("speak"), "looking into something I noticed");
    const row = { reason: "nightly", at: "2026-10-11T08:00:00Z", outcome: "left_alone", line: "A quiet day." };
    assert.match(wakeRowMeta(row as never, new Date(NOW)), /^Thought over the day · /);
    const spoke = { reason: "speak", at: "2026-10-11T20:00:00Z", outcome: "acted", line: "x" };
    assert.match(wakeRowMeta(spoke as never, new Date(NOW)), /^Looked into something I noticed · /);
  });
});

describe("the wakes panel", () => {
  it("labels the nightly and reads its cron", () => {
    assert.equal(wakeLabel("nightly"), "Nightly reflection");
    assert.equal(describeWake("0 3 * * *"), "03:00 every day");
  });

  it("renders the one wake on a clock and says the rest happens when something does", () => {
    render(<WakesNote wakes={[{ kind: "nightly", cron: "0 3 * * *" }]} />);
    assert.ok(screen.getByText("Nightly reflection"));
    assert.ok(screen.getByText("03:00 every day"));
    assert.ok(screen.getByText(/when something happens/i));
    assert.ok(screen.getByText(/nothing here to toggle/i));
  });
});

describe("the sweep is his housekeeping", () => {
  const sweep = {
    description: "proactive_sweep — Edward's checks: a late dose, things he's holding, what came of what he said",
    recurrence_pattern: "*/5 * * * *",
    created_by: "edward",
  };

  it("is housekeeping in the events browser, by its type or its description", () => {
    assert.equal(eventGroup({ ...sweep, event_type: "proactive_sweep" } as never), "housekeeping");
    assert.equal(eventKind(sweep as never), "proactive_sweep");
    assert.equal(eventGroup(sweep as never), "housekeeping");
    assert.equal(eventTitle({ ...sweep, event_type: "proactive_sweep" } as never), "Run my checks");
  });

  it("reads as a routine in the activity view", () => {
    assert.equal(routineTitle(sweep.description), "Running his checks");
  });

  it("titles the nightly tick", () => {
    const tick = { description: "Nightly wake — the mind decides", event_type: "wakeup_tick" };
    assert.equal(eventTitle(tick as never), "Nightly wake");
    assert.equal(eventKind({ description: "Nightly wake — the mind decides" } as never), "wakeup_tick");
  });
});

describe("the stage never shows what a speak wake noticed", () => {
  it("drops a live speak turn's lines and keeps its tool receipts", () => {
    const live: ScreenLive = {
      thinking: {
        turn: "t1", source: "mind", about: "speak", since: "2026-10-11T21:29:00Z",
        lines: [{ text: "BA's check-in closes at 10 pm and Ellis is en route.", at: "2026-10-11T21:29:10Z" }],
        receipts: [{ tool: "tell_owner", state: "ok", at: "2026-10-11T21:29:20Z" }],
      },
      folded: [], recent: [], now: "2026-10-11T21:30:00Z",
    };
    const v = thoughtView(live, NOW);
    assert.equal(v.lines.length, 0);
    assert.ok(!JSON.stringify(v).includes("check-in"));
    assert.equal(v.receipts.length, 1);
    assert.equal(v.about, "looking into something I noticed");
  });

  it("folds a speak turn and reads a speak thought as the fixed line", () => {
    const live: ScreenLive = {
      thinking: null,
      folded: [{ text: "Told Ellis about UA958.", about: "speak", at: "2026-10-11T21:28:00Z", tools: 2 }],
      recent: [{ text: "Told Ellis about UA958.", at: "2026-10-11T21:00:00Z", about: "speak", kind: "mind" }],
      now: "2026-10-11T21:30:00Z",
    };
    const v = thoughtView(live, NOW);
    assert.equal(v.fold?.text, SPEAK_LINE);
    assert.ok(!v.fold?.line.includes("UA958"));
    assert.deepEqual(mindPool(live, NOW).map((t) => t.text), [SPEAK_LINE]);
  });

  it("leaves every other turn as it was", () => {
    const live: ScreenLive = {
      thinking: {
        turn: "t2", source: "mind", about: "nightly", since: "2026-10-11T21:29:00Z",
        lines: [{ text: "Reading the day's verdicts.", at: "2026-10-11T21:29:10Z" }], receipts: [],
      },
      folded: [], recent: [{ text: "A quiet day.", at: "2026-10-11T19:00:00Z", about: "nightly", kind: "mind" }],
      now: "2026-10-11T21:30:00Z",
    };
    assert.equal(thoughtView(live, NOW).lines[0].text, "Reading the day's verdicts.");
    assert.deepEqual(mindPool(live, NOW).map((t) => t.text), ["A quiet day."]);
  });
});
