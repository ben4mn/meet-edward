// R/G red for Edward walking around the screensaver frame.
//
// The behaviour is ported from ben4mn/companion (docs/app.js `walk()`),
// which drives the same PaneSvg rigging. Pulled into a pure module so the
// stateful bits — bounds, target choice, easing, pacing — are testable
// without a DOM or a clock.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  pickTarget,
  walkDurationMs,
  easeInOutQuad,
  positionAt,
  MIN_WALK_DISTANCE,
  WALK_MS_PER_PX,
} from "../app/(display)/hub/lib/walkEngine.ts";

// Deterministic stand-in for Math.random.
const fixed = (v: number) => () => v;

describe("pickTarget", () => {
  it("stays inside the frame", () => {
    for (const r of [0, 0.25, 0.5, 0.75, 0.999]) {
      const t = pickTarget(100, 800, fixed(r));
      assert.ok(t !== null && t >= 0 && t <= 800, `target ${t} escaped 0..800`);
    }
  });

  it("returns null when the frame is too narrow to walk in", () => {
    assert.equal(pickTarget(0, 10, fixed(0.5)), null);
  });

  it("returns null rather than a pointless shuffle on the spot", () => {
    // rand 0.5 of 800 = 400, which is where he already is.
    assert.equal(pickTarget(400, 800, fixed(0.5)), null);
  });

  it("allows a target that is far enough to be worth walking to", () => {
    const t = pickTarget(0, 800, fixed(0.5));
    assert.ok(t !== null && Math.abs(t - 0) >= MIN_WALK_DISTANCE);
  });
});

describe("walkDurationMs", () => {
  it("scales with distance so his speed looks constant", () => {
    assert.ok(walkDurationMs(400) > walkDurationMs(200));
    assert.equal(walkDurationMs(400) / walkDurationMs(200), 2);
  });

  it("has a floor so a short hop is not a twitch", () => {
    assert.ok(walkDurationMs(1) >= 600);
  });

  it("uses the documented pace", () => {
    assert.equal(walkDurationMs(1000), 1000 * WALK_MS_PER_PX);
  });
});

describe("easeInOutQuad", () => {
  it("pins the endpoints", () => {
    assert.equal(easeInOutQuad(0), 0);
    assert.equal(easeInOutQuad(1), 1);
  });

  it("is symmetric about the midpoint", () => {
    assert.ok(Math.abs(easeInOutQuad(0.5) - 0.5) < 1e-9);
    assert.ok(Math.abs(easeInOutQuad(0.25) + easeInOutQuad(0.75) - 1) < 1e-9);
  });

  it("never overshoots", () => {
    for (let t = 0; t <= 1; t += 0.05) {
      const e = easeInOutQuad(t);
      assert.ok(e >= 0 && e <= 1, `ease(${t}) = ${e} out of range`);
    }
  });
});

describe("positionAt", () => {
  it("starts at the origin and ends exactly on target", () => {
    assert.equal(positionAt(100, 500, 0), 100);
    assert.equal(positionAt(100, 500, 1), 500);
  });

  it("clamps progress past the end so a late frame cannot overshoot", () => {
    assert.equal(positionAt(100, 500, 1.4), 500);
    assert.equal(positionAt(100, 500, -0.2), 100);
  });

  it("walks backwards just as happily", () => {
    assert.equal(positionAt(500, 100, 1), 100);
    assert.ok(positionAt(500, 100, 0.5) < 500);
  });
});

describe("pickTarget with a left bound (keeping clear of the clock)", () => {
  it("never targets a spot left of the bound", () => {
    for (const r of [0, 0.1, 0.5, 0.9, 0.999]) {
      const t = pickTarget(600, 800, fixed(r), 320);
      assert.ok(t === null || t >= 320, `target ${t} crossed the clock bound`);
    }
  });

  it("still respects the right edge", () => {
    for (const r of [0, 0.5, 0.999]) {
      const t = pickTarget(600, 800, fixed(r), 320);
      assert.ok(t === null || t <= 800, `target ${t} escaped the frame`);
    }
  });

  it("returns null when the clock leaves no room to walk", () => {
    // Clock bound at 790 on an 800px span: 10px of floor is not a walk.
    assert.equal(pickTarget(795, 800, fixed(0.5), 790), null);
  });

  it("uses the whole span when there is no bound", () => {
    const t = pickTarget(400, 800, fixed(0), 0);
    assert.ok(t !== null && t < MIN_WALK_DISTANCE);
  });

  it("maps the random draw across the reduced span, not the full one", () => {
    // rand 0 must land on the bound itself, not on 0.
    assert.equal(pickTarget(600, 800, fixed(0), 320), 320);
    // rand 1 must land on the right edge.
    assert.equal(pickTarget(320, 800, fixed(0.999999), 320), 800);
  });
});

// ---------------------------------------------------------------------------
// Standing on the floor
// ---------------------------------------------------------------------------
//
// R/G red: on the Pi he read as hovering rather than walking. Two reasons,
// both invisible from the CSS alone:
//
//  * PaneSvg's viewBox is "0 0 36 38" but his foot pads stop at y=34, so
//    the bottom ~10% of the box is empty air. Positioning the *box* at the
//    floor leaves his feet floating that far above it.
//  * The box is square while the viewBox is not, so `meet` scales by
//    height (96/38) — the vertical unit is size/38, not size/36.
//
// Deriving the offset from the rig means a change to PaneSvg's viewBox
// can't silently un-ground him.

import {
  PANE_VIEWBOX_HEIGHT,
  PANE_FOOT_Y,
  feetGapPx,
  groundedBottomPx,
} from "../app/(display)/hub/lib/walkEngine.ts";

describe("feetGapPx", () => {
  it("measures the dead space under his feet in the rendered box", () => {
    // 4 of 38 viewBox units, scaled to a 96px box.
    assert.ok(Math.abs(feetGapPx(96) - (96 * 4) / 38) < 1e-9);
  });

  it("scales with the avatar", () => {
    assert.ok(Math.abs(feetGapPx(192) - 2 * feetGapPx(96)) < 1e-9);
  });

  it("is derived from the rig, not hard-coded", () => {
    const expected = (96 * (PANE_VIEWBOX_HEIGHT - PANE_FOOT_Y)) / PANE_VIEWBOX_HEIGHT;
    assert.ok(Math.abs(feetGapPx(96) - expected) < 1e-9);
  });

  it("uses the taller viewBox axis, since `meet` scales by height here", () => {
    // Scaling by width (36) instead of height (38) is the tempting bug and
    // puts him ~0.6px off at 96 — enough to look wrong on a 7" panel.
    assert.ok(Math.abs(feetGapPx(96) - (96 * 4) / 36) > 0.5);
  });
});

describe("groundedBottomPx", () => {
  it("plants his feet exactly on the floor line", () => {
    for (const size of [64, 96, 140]) {
      for (const floor of [0, 6, 26]) {
        const bottom = groundedBottomPx(size, floor);
        assert.ok(
          Math.abs(bottom + feetGapPx(size) - floor) < 1e-9,
          `feet landed at ${bottom + feetGapPx(size)}, wanted ${floor}`,
        );
      }
    }
  });

  it("hangs the box below the floor line, because the box is bigger than he is", () => {
    assert.ok(groundedBottomPx(96, 0) < 0);
  });

  it("defaults to the very bottom of the frame", () => {
    assert.equal(groundedBottomPx(96), groundedBottomPx(96, 0));
  });
});

// The constants above are only honest if they match the drawing. Render the
// real PaneSvg and measure: a redraw that moves his soles or changes the
// canvas fails here, not on the Pi as a floating robot.
import React from "react";
import { render } from "@testing-library/react";
import { PaneSvg } from "../components/pane/PaneSvg.tsx";

describe("walkEngine constants match the rendered rig", () => {
  it("PANE_VIEWBOX_HEIGHT is the full-body viewBox height", () => {
    const { container } = render(
      React.createElement(PaneSvg, { size: 96, activity: "act-stand" }),
    );
    const vb = container.querySelector("svg")!.getAttribute("viewBox")!;
    assert.equal(Number(vb.split(/\s+/)[3]), PANE_VIEWBOX_HEIGHT);
  });

  it("PANE_FOOT_Y is where the lowest foot pad ends", () => {
    const { container } = render(
      React.createElement(PaneSvg, { size: 96, activity: "act-stand" }),
    );
    const rects = Array.from(
      container.querySelectorAll(".robot-leg-left rect, .robot-leg-right rect"),
    );
    const bottom = Math.max(
      ...rects.map((r) => Number(r.getAttribute("y")) + Number(r.getAttribute("height"))),
    );
    assert.equal(bottom, PANE_FOOT_Y);
  });
});

// ---------------------------------------------------------------------------
// Moving without React (2026-09-26): the companion writes his position as a
// transform straight to the DOM each frame and commits x to state only on
// arrival. Under prefers-reduced-motion he never glides across the frame —
// he fades out and back in where he was going.
// ---------------------------------------------------------------------------

import { FADE_MOVE_MS, planMove, trackTransform } from "../app/(display)/hub/lib/walkEngine.ts";

describe("planMove", () => {
  it("walks at the usual pace with motion allowed", () => {
    assert.deepEqual(planMove(220, false), { kind: "walk", durationMs: walkDurationMs(220) });
  });

  it("fades instead of gliding under reduced motion", () => {
    const p = planMove(600, true);
    assert.equal(p.kind, "fade");
    assert.ok(FADE_MOVE_MS <= 120, "reduced motion: ≤120ms opacity fades only");
  });
});

describe("trackTransform", () => {
  it("is a compositor-only translate, rounded to a device pixel", () => {
    assert.equal(trackTransform(93.4567), "translate3d(93.46px, 0, 0)");
    assert.equal(trackTransform(320), "translate3d(320px, 0, 0)");
  });
});
