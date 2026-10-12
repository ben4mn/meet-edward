// R/G red for the Pane avatar state machine.
//
// Edward exposes an EdwardState union (idle / thinking / speaking /
// listening / looking-{left,right,up}). The PaneSvg renders a single
// SVG and toggles behavior via an activity class. This module is the
// pure mapping between the two — kept separate from React so it can
// be tested cheaply.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { stateToActivityClass } from "../components/pane/paneActivity.ts";

describe("stateToActivityClass", () => {
  it("idle maps to act-stand", () => {
    assert.equal(stateToActivityClass("idle"), "act-stand");
  });

  it("thinking maps to act-think", () => {
    assert.equal(stateToActivityClass("thinking"), "act-think");
  });

  it("speaking maps to act-speak", () => {
    assert.equal(stateToActivityClass("speaking"), "act-speak");
  });

  it("listening maps to act-listen", () => {
    assert.equal(stateToActivityClass("listening"), "act-listen");
  });

  it("all three looking-* variants map to act-look", () => {
    assert.equal(stateToActivityClass("looking-left"), "act-look");
    assert.equal(stateToActivityClass("looking-right"), "act-look");
    assert.equal(stateToActivityClass("looking-up"), "act-look");
  });

  it("unknown state falls back to act-stand", () => {
    // @ts-expect-error — intentionally testing the fallback
    assert.equal(stateToActivityClass("nope"), "act-stand");
  });
});

import { stateToGaze, stateToMood } from "../components/pane/paneActivity.ts";

describe("stateToMood / stateToGaze", () => {
  it("routes the display states through Edward's moods", () => {
    assert.equal(stateToMood("idle"), "idle");
    assert.equal(stateToMood("thinking"), "think");
    assert.equal(stateToMood("speaking"), "speak");
    assert.equal(stateToMood("listening"), "listen");
    assert.equal(stateToMood("looking-left"), "idle");
    // @ts-expect-error — intentionally testing the fallback
    assert.equal(stateToMood("nope"), "idle");
  });

  it("looking-* states are gazes", () => {
    assert.equal(stateToGaze("looking-left"), "left");
    assert.equal(stateToGaze("looking-right"), "right");
    assert.equal(stateToGaze("looking-up"), "up");
    assert.equal(stateToGaze("idle"), null);
    assert.equal(stateToGaze("thinking"), null);
  });
});
