// Edward on the stage (2026-09-28) — the contracts EdwardCenter carried,
// moved to where he now stands:
//
// - one honest line under him, from real presence — nothing before the first answer;
// - poked, he answers what is true, and nods only at rest;
// - news is a glance toward the piece it concerns; a wake that acted is the acted beat;
// - his hands down marks the avatar so display.css turns the tip amber;
// - he is sized from where he stands, capped by the avatar-size setting;
// - a wander is a one-shot walk (or a fade across); thought bubbles earn a glance up.
// (Round 2 removed the desk bust: Ellis at the desk gets the normal stage.)

import { afterEach, describe, it, mock } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import React from "react";
import { act, cleanup, fireEvent, render } from "@testing-library/react";

import { StageEdward } from "../app/(display)/display/components/StageEdward.tsx";
import type { ScreenPresence } from "../app/(display)/display/lib/screenPresence.ts";
import type { StageMe, StagePiece } from "../app/(display)/display/lib/stageModel.ts";

afterEach(() => cleanup());

const STAGE = { cols: 24, rows: 12 };
const presence = (over: Partial<ScreenPresence> = {}): ScreenPresence => ({
  known: true, mood: "idle", line: "Resting", label: "Edward · Resting",
  tone: "ok", active: false, handsDown: false, poke: { beat: "nod", say: null }, ...over,
});
const floor: StageMe = { at: "floor", rect: { x: 10, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null };
const piece = (id: string, kind: StagePiece["kind"], x: number, y: number, w: number, h: number): StagePiece => ({
  id, kind, size: "m", enter: "fade", place: "center", rect: { x, y, w, h }, title: null,
});
const PIECES = [piece("a", "alerts", 8, 0, 8, 2), piece("c", "calendar", 1, 6, 6, 5)];

function base(over: Partial<React.ComponentProps<typeof StageEdward>> = {}): React.ComponentProps<typeof StageEdward> {
  return {
    me: floor, stage: STAGE, pieces: PIECES, presence: presence(), statusLine: "Resting", mode: "stage",
    laneSide: "right", busy: false, accent: "#3ecf9e", avatarPx: 340, lifeEnabled: false, ...over,
  };
}

const pane = (root: HTMLElement) => root.querySelector(".pane-avatar") as HTMLElement;

describe("StageEdward", () => {
  it("shows his one honest line and is the one live Edward", () => {
    const { container, getByText } = render(<StageEdward {...base({ statusLine: "Thinking · morning wake", presence: presence({ mood: "think", label: "Edward · Thinking · morning wake" }) })} />);
    getByText("Thinking · morning wake");
    assert.equal(container.querySelectorAll(".pane-avatar.is-live").length, 1);
    assert.equal(pane(container).dataset.mood, "think");
    assert.equal(pane(container).getAttribute("aria-label"), "Edward · Thinking · morning wake");
  });

  it("claims nothing before the first answer", () => {
    const { container } = render(<StageEdward {...base({ presence: presence({ known: false, line: "" }), statusLine: "" })} />);
    assert.equal(container.querySelector("[data-edward-status]"), null);
  });

  it("poked, he answers and nods", () => {
    const { container, getByTestId } = render(<StageEdward {...base()} />);
    fireEvent.click(pane(container));
    assert.ok(getByTestId("buddy-speech-bubble").textContent);
    assert.equal(pane(container).dataset.beat, "nod");
  });

  it("poked offline: a wince and the truth", () => {
    const off = presence({ mood: "offline", line: "Offline", tone: "mute", poke: { beat: "wince", say: "I can't reach the server right now. I'll keep trying." } });
    const { container, getByTestId } = render(<StageEdward {...base({ presence: off, statusLine: "Offline" })} />);
    fireEvent.click(pane(container));
    assert.equal(pane(container).dataset.beat, "wince");
    assert.match(getByTestId("buddy-speech-bubble").textContent ?? "", /can't reach/);
  });

  it("glances toward the piece the news is about", () => {
    const { container, rerender } = render(<StageEdward {...base({ cue: null })} />);
    rerender(<StageEdward {...base({ cue: { target: "alerts", key: 1 } })} />);
    assert.equal(pane(container).dataset.glance, "up");
    rerender(<StageEdward {...base({ cue: { target: "calendar", key: 2 } })} />);
    assert.equal(pane(container).dataset.glance, "left");
  });

  it("glances toward a piece that just appeared", () => {
    const { container, rerender } = render(<StageEdward {...base()} />);
    rerender(<StageEdward {...base({ pieces: [...PIECES, piece("new", "note", 17, 3, 6, 4)] })} />);
    assert.equal(pane(container).dataset.glance, "right");
  });

  it("plays the acted beat when a wake reached outward", () => {
    const { container } = render(<StageEdward {...base({ beat: { beat: "acted", key: 1 } })} />);
    assert.equal(pane(container).dataset.beat, "acted");
  });

  it("his hands down marks the avatar for the amber tip", () => {
    const { container } = render(<StageEdward {...base({ presence: presence({ handsDown: true }) })} />);
    assert.equal((container.querySelector("[data-edward-anchor]") as HTMLElement).dataset.hands, "down");
  });

  it("display.css keeps the amber-tip rule (unless offline)", () => {
    const css = readFileSync(new URL("../app/(display)/display/display.css", import.meta.url), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    assert.match(
      css,
      /\[data-edward-anchor\]\[data-hands="down"\]\s+\.pane-avatar:not\(\[data-mood="offline"\]\)\s*\{[^}]*--pane-tip:\s*var\(--pane-amber\)/,
    );
  });

  it("stands where the scene puts him, and never bigger than Ellis's size setting", () => {
    const { container } = render(<StageEdward {...base({ avatarPx: 180 })} />);
    const root = container.querySelector("[data-stage-edward]") as HTMLElement;
    assert.equal(root.dataset.at, "floor");
    assert.equal(root.style.left, "41.6667%");
    const anchor = container.querySelector("[data-edward-anchor]") as HTMLElement;
    assert.ok(parseInt(anchor.style.width, 10) <= 180);
  });

  it("there is no desk figure any more: no bust, no desk rules in the stylesheet", () => {
    const { container } = render(<StageEdward {...base()} />);
    assert.equal(container.querySelector(".pane-avatar[data-variant=bust]"), null);
    const css = readFileSync(new URL("../app/(display)/display/display.css", import.meta.url), "utf8");
    assert.doesNotMatch(css, /desk/, "the desk mode and its CSS are gone");
  });

  it("a wander is a one-shot walk over the time it takes, or a fade across", () => {
    const { container, rerender } = render(<StageEdward {...base()} />);
    const box = () => container.querySelector("[data-stage-edward]") as HTMLElement;
    assert.equal(box().dataset.walk, undefined);
    rerender(<StageEdward {...base({ me: { ...floor, rect: { ...floor.rect, x: 16 } }, walk: { kind: "walk", ms: 1920, n: 1 } })} />);
    assert.equal(box().dataset.walk, "walk");
    assert.equal(box().style.getPropertyValue("--st-walk-ms"), "1920ms");
    assert.equal(box().style.left, "66.6667%");
    rerender(<StageEdward {...base({ me: { ...floor, rect: { ...floor.rect, x: 2 } }, walk: { kind: "fade", ms: 0, n: 2 } })} />);
    assert.equal(box().dataset.walk, "fade");
    assert.equal(box().dataset.arrive, "b", "each arrival replays the fade");
  });

  it("a new spot never carries an old step's offset (no jump back as the walk starts)", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      const props = base({ lifeEnabled: true, rand: () => 0.1, laneSide: null });
      const { container, rerender } = render(<StageEdward {...props} />);
      const box = () => container.querySelector("[data-stage-edward]") as HTMLElement;
      act(() => mock.timers.tick(10_000));
      assert.match(box().style.transform, /translateX/, "an idle step moved him");
      rerender(<StageEdward {...props} me={{ ...floor, rect: { ...floor.rect, x: 16 } }} walk={{ kind: "walk", ms: 1920, n: 1 }} />);
      assert.equal(box().style.transform, "", "the first frame at the new spot has no stale offset");
    } finally {
      mock.timers.reset();
    }
  });

  it("thought bubbles earn a glance up", () => {
    const { container, rerender } = render(<StageEdward {...base()} />);
    assert.equal(pane(container).dataset.glance, undefined);
    rerender(<StageEdward {...base({ glanceUp: 1 })} />);
    assert.equal(pane(container).dataset.glance, "up");
  });

  it("while busy he looks at his lane", () => {
    const { container } = render(<StageEdward {...base({ busy: true, laneSide: "right" })} />);
    assert.equal(pane(container).dataset.gaze, "right");
  });

  it("a line or a thought brings him back to his spot: the lane and the joint are laid out from there", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      const props = base({ lifeEnabled: true, rand: () => 0.1, laneSide: null });
      const { container, rerender } = render(<StageEdward {...props} />);
      const box = () => container.querySelector("[data-stage-edward]") as HTMLElement;
      act(() => mock.timers.tick(10_000));
      assert.match(box().style.transform, /translateX/, "an idle step moved him");
      rerender(<StageEdward {...props} busy laneSide="right" />);
      assert.equal(box().style.transform, "", "busy, he stands where the scene put him");
    } finally {
      mock.timers.reset();
    }
  });

  it("standing on a piece his feet touch its card, and his line is beside him, never over his head", () => {
    const on: StageMe = { at: "on", rect: { x: 1, y: 4, w: 3, h: 4 }, on: "thought", side: "left", look: null };
    const { container } = render(<StageEdward {...base({ me: on, avatarPx: 180, statusSide: "left" })} />);
    const box = container.querySelector("[data-stage-edward]") as HTMLElement;
    const anchor = container.querySelector("[data-edward-anchor]") as HTMLElement;
    const status = container.querySelector("[data-edward-status]") as HTMLElement;
    const px = parseInt(anchor.style.width, 10);
    // The 36×38 rig's feet end at y=34: 4/38 of him is air under his feet,
    // and the card sits inside the piece's 0.6u padding.
    assert.equal(anchor.style.transform, `translateY(calc(${Math.round((px * 4) / 38)}px + var(--u) * 0.6))`);
    assert.ok(anchor.compareDocumentPosition(status) & Node.DOCUMENT_POSITION_FOLLOWING, "his line comes after him, not over his head");
    assert.equal(box.dataset.statusSide, "left");
  });
});
