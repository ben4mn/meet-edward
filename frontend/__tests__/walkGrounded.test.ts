// Edward keeps his feet on the floor on the hub screensaver (review 2026-09-26).
//
// Measured frame by frame at 1280×720: every walk step lifted both feet
// 2.5 px (the bob animated .robot-body, which holds the legs); arriving
// popped him 5 px up and then 2.8 px into the floor (a 2-unit settle, and
// pivots at `center bottom` = y 38, not the soles at y 34); the shove's lean
// sank him and its 40 px slide had no step; and the body moved at 91 px/s on
// average (182 px/s at the ease's peak) while the legs' swing covers about
// 30 px/s — he skated.
//
// Now: the bob moves only the upper body; every body pivot is the soles
// (18px 34px); the settle is a squash, never a pop; the shove leans the
// upper body and stays within a stride; and he walks at a steady, slower
// pace with short ramps.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import {
  PANE_FOOT_Y,
  WALK_MS_PER_PX,
  positionAt,
} from "../app/(display)/hub/lib/walkEngine.ts";

const hubCss = readFileSync(new URL("../app/(display)/hub/hub.css", import.meta.url), "utf8").replace(
  /\/\*[\s\S]*?\*\//g,
  "",
);

/** Every [selector, declarations] pair (nested @media included). */
function rules(css: string): Array<[string, string]> {
  const out: Array<[string, string]> = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css)) !== null) out.push([m[1].trim(), m[2]]);
  return out;
}

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

/** The rules that run `animation: <name>` (not the reduced-motion nulls). */
function animated(name: string): Array<[string, string]> {
  return rules(hubCss).filter(([, body]) => new RegExp(`animation:\\s*${name}\\b`).test(body));
}

describe("the walk keeps his feet planted", () => {
  it("the step bob moves his upper body, never his legs", () => {
    const users = animated("hub-walk-bob");
    assert.ok(users.length > 0, "the walk bob exists");
    for (const [sel] of users) {
      assert.match(sel, /\.robot-upper\b/, `hub-walk-bob animates ${sel}`);
      assert.doesNotMatch(sel, /\.robot-body\b/, `hub-walk-bob moves the legs via ${sel}`);
    }
  });

  it("the bob wins over the breath on the live companion", () => {
    // globals.css: `.pane-avatar.is-live .robot-upper { animation: ed-breathe … }` (0,3,0).
    for (const [sel] of animated("hub-walk-bob")) {
      const classes = (sel.match(/\.[a-z-]+/g) ?? []).length;
      assert.ok(classes >= 4, `${sel} is not more specific than the breath rule`);
    }
  });
});

describe("every body pivot is his soles", () => {
  const SOLES = new RegExp(`transform-origin:\\s*18px\\s+${PANE_FOOT_Y}px`);

  for (const name of ["hub-walk-crouch", "hub-walk-settle"]) {
    it(`${name} squashes about the soles (18px ${PANE_FOOT_Y}px), not the viewport's bottom`, () => {
      const users = animated(name);
      assert.ok(users.length > 0, `${name} is used`);
      for (const [sel, body] of users) {
        assert.match(body, SOLES, `${sel}: ${body}`);
        assert.doesNotMatch(body, /transform-origin:\s*center/, sel);
      }
    });
  }

  it("no hub rule pivots the body on `center bottom` (y 38, below his feet)", () => {
    for (const [sel, body] of rules(hubCss)) {
      if (!/\.robot-(body|upper)\b/.test(sel)) continue;
      assert.doesNotMatch(body, /transform-origin:\s*center\s+bottom/, sel);
    }
  });
});

describe("arriving is a settle, never a pop or a sink", () => {
  it("the settle lifts at most half a unit and never pushes him into the floor", () => {
    const kf = keyframes("hub-walk-settle");
    for (const [, v] of kf.matchAll(/translateY\((-?[\d.]+)px\)/g)) {
      const y = Number(v);
      assert.ok(y <= 0, `settle sinks ${y} units into the floor`);
      assert.ok(y >= -0.5, `settle pops ${-y} units off the floor`);
    }
  });
});

describe("the shove", () => {
  it("leans his upper body, so his feet stay flat", () => {
    const users = animated("hub-push-lean");
    assert.ok(users.length > 0, "the lean exists");
    for (const [sel] of users) {
      assert.doesNotMatch(sel, /\.robot-body\b/, `the lean tilts his legs via ${sel}`);
      assert.match(sel, /\.robot-upper\b/, sel);
    }
  });

  it("slides no further than one stride (≤ 8 px) without a step", () => {
    for (const [, v] of keyframes("hub-push-step").matchAll(/translateX\((-?[\d.]+)px\)/g)) {
      assert.ok(Math.abs(Number(v)) <= 8, `the shove slides ${v}px`);
    }
  });
});

describe("the walk's pace", () => {
  const SIZE = 96;
  // Each half of the 0.5 s leg cycle swings a foot 3 viewBox units.
  const legPxPerS = (3 * (SIZE / 38)) / 0.25;

  it("on average he walks no faster than about twice his legs carry him", () => {
    const avg = 1000 / WALK_MS_PER_PX;
    assert.ok(avg <= 2.2 * legPxPerS, `body ${avg.toFixed(0)} px/s vs legs ${legPxPerS.toFixed(0)} px/s`);
  });

  it("steady in the middle: the peak speed is within 1.25× the average (short ramps, no sprint)", () => {
    const from = 0;
    const to = 1000;
    let peak = 0;
    const steps = 1000;
    for (let i = 0; i < steps; i++) {
      const v = (positionAt(from, to, (i + 1) / steps) - positionAt(from, to, i / steps)) * steps;
      peak = Math.max(peak, v);
    }
    const avg = to - from;
    assert.ok(peak <= 1.25 * avg, `peak ${peak.toFixed(0)} vs average ${avg}`);
  });

  it("still starts and stops gently", () => {
    assert.ok(positionAt(0, 1000, 0.02) < 20, "no jump off the mark");
    assert.ok(positionAt(0, 1000, 0.98) > 980, "no lurch at the end");
    assert.equal(positionAt(0, 1000, 1), 1000);
  });
});
