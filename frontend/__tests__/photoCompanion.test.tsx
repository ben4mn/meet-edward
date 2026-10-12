// Edward walking along the bottom of the hub screensaver (2026-09-26).
//
// - While he walks, his position is written straight to the DOM as a
//   transform each frame; React commits only when he sets off and arrives
//   (it used to setState on every requestAnimationFrame — ~60 renders a
//   second on a Raspberry Pi).
// - Under prefers-reduced-motion he never glides: he fades out and back in
//   at his new spot.
// - The shove choreography animates an inner element, so its keyframed
//   transform never fights the track's translate.

import { afterEach, beforeEach, describe, it, mock } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import React, { Profiler } from "react";
import { act, cleanup, render } from "@testing-library/react";

import { PhotoCompanion } from "../app/(display)/hub/components/PhotoCompanion.tsx";

const hubCss = readFileSync(new URL("../app/(display)/hub/hub.css", import.meta.url), "utf8")
  .replace(/\/\*[\s\S]*?\*\//g, "");

let frames: Array<(t: number) => void> = [];
let reduced = false;
const realRandom = Math.random;
const g = globalThis as unknown as { requestAnimationFrame: unknown; cancelAnimationFrame: unknown };
const realRaf = window.requestAnimationFrame;
const realCaf = window.cancelAnimationFrame;
const realGlobalRaf = g.requestAnimationFrame;
const realGlobalCaf = g.cancelAnimationFrame;
const realMatchMedia = window.matchMedia;

beforeEach(() => {
  frames = [];
  reduced = false;
  mock.timers.enable({ apis: ["setTimeout"] });
  // 0.1: a walk (< 0.6), the shortest wait (2860 ms), target x = 0.1 × 928 ≈ 93.
  Math.random = () => 0.1;
  window.requestAnimationFrame = ((cb: (t: number) => void) => {
    frames.push(cb);
    return frames.length;
  }) as typeof window.requestAnimationFrame;
  window.cancelAnimationFrame = (() => {}) as typeof window.cancelAnimationFrame;
  // global-jsdom copies window's props onto globalThis; the component calls
  // the bare globals, so stub both.
  g.requestAnimationFrame = window.requestAnimationFrame;
  g.cancelAnimationFrame = window.cancelAnimationFrame;
  window.matchMedia = ((q: string) => ({
    matches: reduced && /prefers-reduced-motion:\s*reduce/.test(q),
    media: q,
    onchange: null,
    addListener() {},
    removeListener() {},
    addEventListener() {},
    removeEventListener() {},
    dispatchEvent() {
      return false;
    },
  })) as typeof window.matchMedia;
});

afterEach(() => {
  cleanup();
  mock.timers.reset();
  Math.random = realRandom;
  window.requestAnimationFrame = realRaf;
  window.cancelAnimationFrame = realCaf;
  g.requestAnimationFrame = realGlobalRaf;
  g.cancelAnimationFrame = realGlobalCaf;
  window.matchMedia = realMatchMedia;
});

function track(container: HTMLElement): HTMLElement {
  return container.querySelector(".hub-companion") as HTMLElement;
}

/** Wait out the first pause and the anticipation crouch. */
function setOff() {
  act(() => mock.timers.tick(2860));
  act(() => mock.timers.tick(180));
}

describe("PhotoCompanion walking", () => {
  it("moves by transform each frame without a React commit per frame", () => {
    let commits = 0;
    const { container } = render(
      <Profiler id="companion" onRender={() => commits++}>
        <PhotoCompanion />
      </Profiler>,
    );
    assert.equal(track(container).style.transform, "translate3d(320px, 0, 0)");
    setOff();
    assert.ok(frames.length > 0, "the walk runs on requestAnimationFrame");
    const before = commits;
    const t0 = performance.now();

    for (let k = 1; k <= 10; k++) {
      const cb = frames.shift()!;
      act(() => cb(t0 + k * 100));
    }
    assert.equal(commits, before, "no React commit while he walks");
    const mid = Number(/translate3d\(([\d.]+)px/.exec(track(container).style.transform)?.[1]);
    assert.ok(mid < 320 && mid > 93, `mid-walk x ${mid} should be between 93 and 320`);

    const last = frames.shift()!;
    act(() => last(t0 + 60_000));
    assert.ok(commits > before, "arrival commits x once");
    assert.equal(track(container).style.transform, "translate3d(93px, 0, 0)");
  });

  it("under reduced motion he fades to his new spot instead of gliding", () => {
    reduced = true;
    const { container } = render(<PhotoCompanion />);
    act(() => mock.timers.tick(2860));
    assert.equal(frames.length, 0, "no frame-by-frame glide");
    assert.equal(track(container).style.opacity, "0");
    act(() => mock.timers.tick(120));
    assert.equal(track(container).style.transform, "translate3d(93px, 0, 0)");
    assert.equal(track(container).style.opacity, "1");
  });
});

describe("PhotoCompanion presenting mid-walk", () => {
  it("stops to shove the photo, then carries on where he was", () => {
    const { container, rerender } = render(<PhotoCompanion />);
    setOff();
    const t0 = performance.now();
    for (let k = 1; k <= 5; k++) {
      const cb = frames.shift()!;
      act(() => cb(t0 + k * 100));
    }
    const before = track(container).style.transform;

    rerender(<PhotoCompanion presenting />);
    for (let k = 6; k <= 20; k++) {
      const cb = frames.shift()!;
      act(() => cb(t0 + k * 100));
    }
    assert.equal(track(container).style.transform, before, "he stands still while he shoves");

    rerender(<PhotoCompanion />);
    const cb = frames.shift()!;
    act(() => cb(t0 + 21 * 100));
    assert.notEqual(track(container).style.transform, before, "then walks on");
    const x = Number(/translate3d\(([\d.]+)px/.exec(track(container).style.transform)?.[1]);
    assert.ok(x > 93, "resumed, not jumped to the end");
  });
});

describe("PhotoCompanion presenting", () => {
  it("the shove animates an inner element, not the translated track", () => {
    const { container } = render(<PhotoCompanion presenting />);
    assert.equal(track(container).getAttribute("data-pushing"), null);
    assert.ok(container.querySelector(".hub-companion-body[data-pushing]"));
  });

  it("hub.css drives the shove on the body and sizes only the outer svg", () => {
    assert.match(hubCss, /\.hub-companion-body\[data-pushing\]\s*\{[^}]*animation:\s*hub-push-step/);
    assert.doesNotMatch(hubCss, /\.hub-companion\[data-pushing\]\s*\{[^}]*animation/);
    assert.match(hubCss, /\.hub-companion \.pane-avatar > svg\s*\{/);
    assert.doesNotMatch(hubCss, /\.hub-companion \.pane-avatar svg\s*\{/);
  });
});

// The shove's arms pivot at the shoulders now (globals.css, Phase A), and the
// old shared keyframe slid both arms 5 viewBox units right: the left arm sank
// into his torso and the right hand floated off on its own. Each arm now has
// its own keyframes, stays attached, and keeps the shoulder pivot.
describe("hub.css shove arms", () => {
  function keyframes(name: string): string {
    const m = new RegExp(`@keyframes\\s+${name}\\s*\\{`).exec(hubCss);
    assert.ok(m, `missing @keyframes ${name}`);
    let depth = 1;
    let i = m!.index + m![0].length;
    for (; i < hubCss.length && depth > 0; i++) {
      if (hubCss[i] === "{") depth++;
      else if (hubCss[i] === "}") depth--;
    }
    return hubCss.slice(m!.index, i);
  }

  it("each arm has its own shove", () => {
    assert.match(hubCss, /\.pane-avatar\.hub-pushing \.robot-arm-r\s*\{[^}]*animation:\s*hub-push-arm-r\b/);
    assert.match(hubCss, /\.pane-avatar\.hub-pushing \.robot-arm-l\s*\{[^}]*animation:\s*hub-push-arm-l\b/);
  });

  it("no arm slides off his body (translate ≤ 2 units)", () => {
    for (const name of ["hub-push-arm-r", "hub-push-arm-l"]) {
      for (const [, v] of keyframes(name).matchAll(/translateX\((-?[\d.]+)px\)/g)) {
        assert.ok(Math.abs(Number(v)) <= 2, `${name} translates ${v}px`);
      }
    }
  });

  it("the right arm reaches out toward the shove (a negative turn about the shoulder)", () => {
    const turns = [...keyframes("hub-push-arm-r").matchAll(/rotate\((-?[\d.]+)deg\)/g)].map((m) => Number(m[1]));
    assert.ok(Math.min(...turns) <= -30, `right arm turns ${turns.join(", ")}`);
  });

  it("the shove keeps the shoulder pivots from globals.css", () => {
    const re = /([^{}]*\.hub-pushing[^{}]*\.robot-arm-[lr][^{}]*)\{([^}]*)\}/g;
    let m: RegExpExecArray | null;
    while ((m = re.exec(hubCss)) !== null) {
      assert.doesNotMatch(m[2], /transform-origin/, m[1]);
    }
  });
});

// Review 2026-09-26: a third of his idle picks were "act-think", which the
// rig reads as the real thinking mood (visor scan, antenna pulse) — the
// household saw Edward "thinking" while nothing ran. Idle play is never a
// mood claim.
describe("PhotoCompanion idle play", () => {
  it("never plays the thinking mood while nothing is running", () => {
    for (let r = 0.6; r < 1; r += 0.02) {
      Math.random = () => r;
      const { container, unmount } = render(<PhotoCompanion />);
      // The first pause (rand(2600, 5200) at this r), then the idle starts.
      act(() => mock.timers.tick(Math.floor(r * 2601) + 2600));
      const pane = container.querySelector(".pane-avatar") as HTMLElement;
      assert.notEqual(pane.dataset.mood, "think", `idle pick at r=${r.toFixed(2)} claims he is thinking`);
      assert.ok(!pane.classList.contains("act-think"), `idle pick at r=${r.toFixed(2)} uses act-think`);
      unmount();
    }
  });
});

// L7 (2026-09-27 follow-ups): in slide mode the photo leaves to the left
// (hub.css .hub-slide[data-exiting] → translateX(-100%)), but Edward faced
// right and shoved right (+7px) — he pushed the photo the wrong way. In
// slide mode he faces left and the step is mirrored (his lean and arms are
// drawn inside the flipped svg, so they mirror with it).
import { pushDirection } from "../app/(display)/hub/components/PhotoCompanion.tsx";

describe("PhotoCompanion shoves the way the photo goes", () => {
  function keyframeShifts(name: string): number[] {
    const m = new RegExp(`@keyframes\\s+${name}\\s*\\{`).exec(hubCss);
    assert.ok(m, `missing @keyframes ${name}`);
    let depth = 1;
    let i = m!.index + m![0].length;
    for (; i < hubCss.length && depth > 0; i++) {
      if (hubCss[i] === "{") depth++;
      else if (hubCss[i] === "}") depth--;
    }
    return [...hubCss.slice(m!.index, i).matchAll(/translateX\((-?[\d.]+)px\)/g)].map((x) => Number(x[1]));
  }

  it("slide mode pushes left; the other transitions keep pushing right", () => {
    assert.equal(pushDirection("slide"), "left");
    assert.equal(pushDirection("kenburns"), "right");
    assert.equal(pushDirection("crossfade"), "right");
  });

  it("pushing left, he faces left and the body carries the direction", () => {
    const { container } = render(<PhotoCompanion presenting pushToward="left" />);
    const pane = container.querySelector(".pane-avatar") as HTMLElement;
    assert.ok(pane.classList.contains("face-left"), "faces the way the photo leaves");
    assert.equal(container.querySelector(".hub-companion-body")?.getAttribute("data-push"), "left");
  });

  it("pushing right (the default), he faces right", () => {
    const { container } = render(<PhotoCompanion presenting />);
    const pane = container.querySelector(".pane-avatar") as HTMLElement;
    assert.ok(!pane.classList.contains("face-left"));
  });

  it("hub.css mirrors the step for a push to the left", () => {
    assert.match(hubCss, /\.hub-companion-body\[data-pushing\]\[data-push="left"\]\s*\{[^}]*animation-name:\s*hub-push-step-left/);
    const right = keyframeShifts("hub-push-step");
    const left = keyframeShifts("hub-push-step-left");
    assert.deepEqual(left, right.map((v) => (v === 0 ? 0 : -v)), "the left step is the right one mirrored");
    assert.ok(Math.min(...left) < 0, "the shove goes left");
  });

  it("reduced motion stills the left shove too", () => {
    const reducedBlock = /@media \(prefers-reduced-motion: reduce\)\s*\{([^{}]*)\{\s*animation:\s*none;?\s*\}/g;
    const blocks = [...hubCss.matchAll(reducedBlock)].map((m) => m[1]);
    assert.ok(
      blocks.some((b) => b.includes('.hub-companion-body[data-pushing][data-push="left"]')),
      "the more specific left rule would otherwise out-rank animation: none",
    );
  });

  it("the screensaver passes the direction from its transition", () => {
    const page = readFileSync(new URL("../app/(display)/hub/idle/page.tsx", import.meta.url), "utf8");
    assert.match(page, /<PhotoCompanion\s+presenting=\{presenting\}\s+pushToward=\{pushDirection\(settings\.transition\)\}\s*\/>/);
  });
});
