// Phase 7 of the mind rebuild: the proactive panel no longer toggles five
// templated jobs. It shows the wakes Edward takes on his own (morning,
// evening, Sunday reflection) and keeps only the controls Ellis still owns.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { render, screen } from "@testing-library/react";

import { describeWake, wakeLabel } from "../components/settings/proactiveWakes.ts";
import { WakesNote } from "../components/settings/WakesNote.tsx";

describe("describeWake", () => {
  it("reads a daily cron as a time of day", () => {
    assert.equal(describeWake("0 7 * * *"), "07:00 every day");
    assert.equal(describeWake("30 18 * * *"), "18:30 every day");
  });

  it("reads a weekly cron as a weekday", () => {
    assert.equal(describeWake("0 9 * * 0"), "09:00 on Sundays");
    assert.equal(describeWake("0 9 * * 1"), "09:00 on Mondays");
  });

  it("falls back to the raw cron when it cannot read it", () => {
    assert.equal(describeWake("*/15 * * * *"), "*/15 * * * *");
    assert.equal(describeWake(""), "");
  });

  it("labels the known wakes", () => {
    assert.equal(wakeLabel("morning"), "Morning wake");
    assert.equal(wakeLabel("evening"), "Evening wake");
    assert.equal(wakeLabel("reflect"), "Sunday reflection");
    assert.equal(wakeLabel("custom_thing"), "Custom thing");
  });
});

describe("WakesNote", () => {
  it("renders every wake with its schedule and says Edward decides", () => {
    render(
      <WakesNote
        wakes={[
          { kind: "morning", cron: "0 7 * * *" },
          { kind: "evening", cron: "0 18 * * *" },
          { kind: "reflect", cron: "0 9 * * 0" },
        ]}
      />,
    );
    assert.ok(screen.getByText("Morning wake"));
    assert.ok(screen.getByText("07:00 every day"));
    assert.ok(screen.getByText("Sunday reflection"));
    assert.ok(screen.getByText("09:00 on Sundays"));
    assert.ok(screen.getByText(/nothing here to toggle/i));
  });

  it("renders an empty state when the wakes have not loaded", () => {
    render(<WakesNote wakes={[]} />);
    assert.ok(screen.getByText(/no wakes scheduled/i));
  });
});

// 2026-09-26: the wakes the surplus queue and free time added have names too.
describe("wakeLabel, the newer wakes", () => {
  it("names free time and the nightly reflection", () => {
    assert.equal(wakeLabel("free"), "Free time");
    assert.equal(wakeLabel("reflect_lite"), "Nightly reflection");
    assert.equal(wakeLabel("some_thing"), "Some thing");
  });
});
