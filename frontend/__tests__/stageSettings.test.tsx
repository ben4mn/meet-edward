// Two stage switches in /screen's gear panel (2026-09-28): his thinking on
// the screen, and his lines spoken aloud. Both on by default; both end to end
// (services/display_settings.py keeps the same keys).

import { afterEach, describe, it } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { cleanup, fireEvent, render } from "@testing-library/react";

import { DisplaySettingsPanel } from "../app/(display)/display/components/DisplaySettingsPanel.tsx";
import { DEFAULT_SETTINGS } from "../app/(display)/display/hooks/useDisplaySettings.ts";
import type { DisplaySettings } from "../app/(display)/display/lib/types.ts";

afterEach(() => cleanup());

describe("the stage's display settings", () => {
  it("thinking on screen and speaking aloud are on by default", () => {
    assert.equal(DEFAULT_SETTINGS.show_thinking, true);
    assert.equal(DEFAULT_SETTINGS.speak_aloud, true);
  });

  it("the gear panel turns each one off", () => {
    const patches: Partial<DisplaySettings>[] = [];
    const { getByLabelText, getByRole } = render(
      <DisplaySettingsPanel settings={DEFAULT_SETTINGS} update={(p) => patches.push(p)} />,
    );
    fireEvent.click(getByLabelText("Display settings"));
    const thinking = getByRole("group", { name: "thinking on screen" });
    fireEvent.click(thinking.querySelector("button[data-value=off]")!);
    const aloud = getByRole("group", { name: "speak aloud" });
    fireEvent.click(aloud.querySelector("button[data-value=off]")!);
    assert.deepEqual(patches, [{ show_thinking: false }, { speak_aloud: false }]);
  });

  it("time of day is what the stage has: auto (by the hour), day or night — each one changes the stage", () => {
    const patches: Partial<DisplaySettings>[] = [];
    const { getByLabelText, getByRole } = render(
      <DisplaySettingsPanel settings={{ ...DEFAULT_SETTINGS, time_of_day: "dusk" }} update={(p) => patches.push(p)} />,
    );
    fireEvent.click(getByLabelText("Display settings"));
    const tod = getByRole("group", { name: "time of day" });
    assert.deepEqual(Array.from(tod.querySelectorAll("button")).map((b) => b.dataset.value), ["auto", "day", "night"]);
    assert.equal(tod.querySelector("button[aria-pressed=true]")?.getAttribute("data-value"), "day", "an old daytime choice reads as day");
    fireEvent.click(tod.querySelector("button[data-value=night]")!);
    assert.deepEqual(patches, [{ time_of_day: "night" }]);
  });
});
