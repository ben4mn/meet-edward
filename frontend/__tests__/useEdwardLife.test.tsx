// R/G red for useEdwardLife — Edward's blinks, glances and beats.
//
// Contract (design system §4 "Life"): everything is a DOM attribute on the
// avatar root (data-blink, data-glance, data-beat), never React state, so a
// blink costs zero renders. Blinks and glances pause while the tab is
// hidden and never run under reduced motion; beats are throttled ≥1.2s
// apart, the more important one wins a queue, and each clears itself.
// Scheduling decisions are pure (lib/edwardMood.ts); this drives the DOM.

import { afterEach, beforeEach, describe, it, mock } from "node:test";
import assert from "node:assert/strict";
import React, { useRef } from "react";
import { cleanup, render } from "@testing-library/react";

import { useEdwardLife, type EdwardLife, type EdwardLifeOptions } from "../lib/useEdwardLife.ts";
import { BEAT_MIN_GAP_MS, BEAT_MS, type Mood } from "../lib/edwardMood.ts";

let life: EdwardLife | null = null;
let renders = 0;

function Probe(props: EdwardLifeOptions & { gazeAttr?: string }) {
  renders++;
  const ref = useRef<HTMLSpanElement>(null);
  const { gazeAttr, ...opts } = props;
  life = useEdwardLife(ref, opts);
  return <span data-testid="edward" ref={ref} data-gaze={gazeAttr} />;
}

function el(): HTMLElement {
  return document.querySelector('[data-testid="edward"]') as HTMLElement;
}

// rand fixed at 0.5: blink wait = 2200 + 0.5*3800 = 4100ms, a single 120ms
// close; glance wait = 8000 + 0.5*12000 = 14000ms, "right", held 1200ms.
const half = () => 0.5;

function setHidden(hidden: boolean) {
  Object.defineProperty(document, "hidden", { configurable: true, get: () => hidden });
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    get: () => (hidden ? "hidden" : "visible"),
  });
  document.dispatchEvent(new window.Event("visibilitychange"));
}

beforeEach(() => {
  mock.timers.enable({ apis: ["setTimeout", "Date"], now: 1_000_000 });
  renders = 0;
  life = null;
});

afterEach(() => {
  cleanup();
  mock.timers.reset();
  setHidden(false);
  delete (window as any).matchMedia;
});

describe("blinks", () => {
  it("blinks on the schedule and opens again, with no re-render", () => {
    render(<Probe rand={half} mood="idle" />);
    const before = renders;
    mock.timers.tick(4099);
    assert.equal(el().hasAttribute("data-blink"), false);
    mock.timers.tick(1);
    assert.equal(el().hasAttribute("data-blink"), true);
    mock.timers.tick(120);
    assert.equal(el().hasAttribute("data-blink"), false);
    assert.equal(renders, before, "a blink must not re-render");
  });

  it("keeps blinking", () => {
    render(<Probe rand={half} mood="idle" />);
    mock.timers.tick(4100 + 120 + 4100);
    assert.equal(el().hasAttribute("data-blink"), true);
  });

  it("does nothing when disabled", () => {
    render(<Probe rand={half} mood="idle" enabled={false} />);
    mock.timers.tick(60_000);
    assert.equal(el().hasAttribute("data-blink"), false);
    assert.equal(el().hasAttribute("data-glance"), false);
  });

  it("never blinks under reduced motion", () => {
    (window as any).matchMedia = (q: string) => ({
      matches: q.includes("reduce"),
      addEventListener() {},
      removeEventListener() {},
    });
    render(<Probe rand={half} mood="idle" />);
    mock.timers.tick(60_000);
    assert.equal(el().hasAttribute("data-blink"), false);
  });

  it("pauses while the tab is hidden and resumes when it returns", () => {
    render(<Probe rand={half} mood="idle" />);
    setHidden(true);
    mock.timers.tick(60_000);
    assert.equal(el().hasAttribute("data-blink"), false);
    setHidden(false);
    mock.timers.tick(4100);
    assert.equal(el().hasAttribute("data-blink"), true);
  });

  it("stops cleanly on unmount", () => {
    const { unmount } = render(<Probe rand={half} mood="idle" />);
    unmount();
    assert.doesNotThrow(() => mock.timers.tick(60_000));
  });
});

describe("glances", () => {
  it("glances while idle, then looks back", () => {
    render(<Probe rand={half} mood="idle" />);
    mock.timers.tick(14_000);
    assert.equal(el().getAttribute("data-glance"), "right");
    mock.timers.tick(1200);
    assert.equal(el().hasAttribute("data-glance"), false);
  });

  it("doesn't wander while thinking", () => {
    render(<Probe rand={half} mood={"think" as Mood} />);
    mock.timers.tick(30_000);
    assert.equal(el().hasAttribute("data-glance"), false);
  });

  it("an explicit gaze wins over idle glances", () => {
    render(<Probe rand={half} mood="idle" gazeAttr="left" />);
    mock.timers.tick(30_000);
    assert.equal(el().hasAttribute("data-glance"), false);
  });

  it("can be asked to glance (e.g. down at new content)", () => {
    render(<Probe rand={half} mood="think" />);
    life!.glance("down", 900);
    assert.equal(el().getAttribute("data-glance"), "down");
    mock.timers.tick(900);
    assert.equal(el().hasAttribute("data-glance"), false);
  });
});

describe("beats", () => {
  it("fire() sets data-beat and it clears itself", () => {
    render(<Probe rand={half} mood="idle" />);
    assert.equal(life!.fire("nod"), true);
    assert.equal(el().getAttribute("data-beat"), "nod");
    mock.timers.tick(BEAT_MS.nod + 200);
    assert.equal(el().hasAttribute("data-beat"), false);
  });

  it("clears early on the beat's own animationend", () => {
    render(<Probe rand={half} mood="idle" />);
    life!.fire("wave");
    const other = new window.Event("animationend", { bubbles: true });
    Object.assign(other, { animationName: "ed-breathe" });
    el().dispatchEvent(other);
    assert.equal(el().getAttribute("data-beat"), "wave");
    const done = new window.Event("animationend", { bubbles: true });
    Object.assign(done, { animationName: "ed-beat-wave" });
    el().dispatchEvent(done);
    assert.equal(el().hasAttribute("data-beat"), false);
  });

  it("holds a beat that comes too soon, then plays it", () => {
    render(<Probe rand={half} mood="idle" />);
    life!.fire("nod");
    assert.equal(life!.fire("acted"), false);
    assert.equal(el().getAttribute("data-beat"), "nod");
    mock.timers.tick(BEAT_MIN_GAP_MS);
    assert.equal(el().getAttribute("data-beat"), "acted");
  });

  it("of two held beats the more important plays", () => {
    render(<Probe rand={half} mood="idle" />);
    life!.fire("nod");
    life!.fire("acted");
    life!.fire("perk");
    mock.timers.tick(BEAT_MIN_GAP_MS);
    assert.equal(el().getAttribute("data-beat"), "acted");
  });

  it("a disabled Edward ignores beats", () => {
    render(<Probe rand={half} mood="idle" enabled={false} />);
    assert.equal(life!.fire("nod"), false);
    assert.equal(el().hasAttribute("data-beat"), false);
  });

  it("beats still show (as static poses) under reduced motion", () => {
    (window as any).matchMedia = (q: string) => ({
      matches: q.includes("reduce"),
      addEventListener() {},
      removeEventListener() {},
    });
    render(<Probe rand={half} mood="idle" />);
    life!.fire("acted");
    assert.equal(el().getAttribute("data-beat"), "acted");
    mock.timers.tick(BEAT_MS.acted + 200);
    assert.equal(el().hasAttribute("data-beat"), false);
  });

  it("perks when the app comes back after a while away", () => {
    render(<Probe rand={half} mood="idle" perkOnReturn />);
    setHidden(true);
    mock.timers.tick(30_000);
    setHidden(false);
    assert.equal(el().getAttribute("data-beat"), "perk");
  });

  it("doesn't perk after a quick flick away", () => {
    render(<Probe rand={half} mood="idle" perkOnReturn />);
    setHidden(true);
    mock.timers.tick(2000);
    setHidden(false);
    assert.equal(el().hasAttribute("data-beat"), false);
  });

  it("returns the same handle every render", () => {
    const { rerender } = render(<Probe rand={half} mood="idle" />);
    const first = life;
    rerender(<Probe rand={half} mood="think" />);
    assert.equal(life, first);
  });
});
