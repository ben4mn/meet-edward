// Guard for a specific bug class: transform-origin copied between SVGs.
//
// The walk/push animations were ported from ben4mn/companion, whose mascot
// SVG has different geometry. Its antenna rules use `transform-origin:
// center bottom`, which on THIS SVG is wrong twice over — the antenna is a
// <g> with no transform-box, so keywords resolve against the whole SVG
// viewport rather than the element, putting the pivot at Edward's feet.
// The antenna then swung in a huge arc around his ankles.
//
// globals.css pivots it correctly at the joint (18px 6.5px), which is
// exactly where the antenna path starts: "M18 6.5". Any rule that rotates
// the antenna must use that same pivot.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

const hubCss = readFileSync(
  new URL("../app/(display)/hub/hub.css", import.meta.url),
  "utf8",
);
const globalsCss = readFileSync(
  new URL("../app/globals.css", import.meta.url),
  "utf8",
);

/** The declaration block following each selector mentioning .robot-antenna. */
function antennaBlocks(css: string): string[] {
  const out: string[] = [];
  const re = /([^{}]*\.robot-antenna[^{}]*)\{([^}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css)) !== null) {
    // Skip the reduced-motion overrides, which only null animations out.
    if (/animation:\s*none/.test(m[2])) continue;
    out.push(m[2]);
  }
  return out;
}

describe("antenna pivot", () => {
  it("globals.css pins the joint origin the SVG path starts at", () => {
    assert.ok(
      /transform-origin:\s*18px\s+6\.5px/.test(globalsCss),
      "expected the canonical joint origin in globals.css",
    );
  });

  it("no hub rule pivots the antenna with viewport-relative keywords", () => {
    for (const block of antennaBlocks(hubCss)) {
      assert.ok(
        !/transform-origin:\s*(center|top|bottom|left|right)/.test(block),
        `antenna rule uses a keyword origin, which resolves against the SVG viewport:\n${block}`,
      );
    }
  });

  it("every hub rule that animates the antenna sets the joint origin", () => {
    const blocks = antennaBlocks(hubCss).filter((b) => /animation:/.test(b));
    assert.ok(blocks.length > 0, "expected the walk/push antenna rules to exist");
    for (const block of blocks) {
      assert.ok(
        /transform-origin:\s*18px\s+6\.5px/.test(block),
        `antenna animation without the joint pivot:\n${block}`,
      );
    }
  });
});

// ---------------------------------------------------------------------------
// Pivots are viewBox px, never element-relative px
// ---------------------------------------------------------------------------
//
// `transform-box: fill-box` makes px origins relative to the element's own
// bounding box, so "18px 17px" on the speaking grin pivoted a point far
// outside the mouth and the grin swung off his face (the speak-grin bug).
// The rig's rule: every pivot is written in viewBox px with the default
// transform-box, so it can be read straight off the SVG's coordinates.

/** Every [selector, declarations] pair in a stylesheet (nested @media too). */
function ruleBlocks(raw: string): Array<[string, string]> {
  const css = raw.replace(/\/\*[\s\S]*?\*\//g, ""); // a comment is never a selector
  const out: Array<[string, string]> = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css)) !== null) out.push([m[1].trim(), m[2]]);
  return out;
}

const PX_ORIGIN = /transform-origin:\s*-?[\d.]+px/;
const FILL_BOX = /transform-box:\s*fill-box/;

describe("no fill-box with px pivots", () => {
  for (const [name, css] of [
    ["globals.css", globalsCss],
    ["hub.css", hubCss],
  ] as const) {
    it(`${name} never combines transform-box: fill-box with a px origin`, () => {
      for (const [sel, body] of ruleBlocks(css)) {
        assert.ok(
          !(FILL_BOX.test(body) && PX_ORIGIN.test(body)),
          `${name}: "${sel}" uses fill-box with a px origin`,
        );
      }
    });
  }

  it("the pane rules never use fill-box at all", () => {
    for (const [sel, body] of ruleBlocks(globalsCss)) {
      if (!sel.includes(".pane-avatar")) continue;
      assert.ok(!FILL_BOX.test(body), `"${sel}" uses transform-box: fill-box`);
    }
  });
});

describe("every moving part has its pivot pinned", () => {
  // part → the joint it turns about, in viewBox units.
  const PIVOTS: Record<string, [number, number]> = {
    "robot-head": [18, 21.5], // the neck
    "robot-neck": [18, 21.5],
    "robot-eyes": [18, 11.2], // the eye line, so moods squint both eyes in place
    "robot-antenna-glow": [17, 1.2],
    "robot-antenna": [18, 6.5], // where the stalk leaves the head
    "robot-antenna-tip": [17, 1.2],
    "robot-antenna-ring": [17, 1.2],
    "robot-eye-l": [14.5, 11.2],
    "robot-eye-r": [21.5, 11.2],
    "robot-mouth-open": [18, 16.6],
    "robot-arm-r": [25.2, 23.4], // the shoulder, so a wave stays attached
    "robot-arm-l": [10.8, 23.4],
    "robot-wave-arm": [24.8, 23.4], // the raised arm wags about the shoulder it grows from
    "robot-core": [18, 25],
    "robot-shadow": [18, 35],
  };

  for (const [part, [x, y]] of Object.entries(PIVOTS)) {
    it(`.${part} pivots at ${x}px ${y}px`, () => {
      const base = ruleBlocks(globalsCss).find(([sel, body]) =>
        sel.split(",").some((s) => s.trim() === `.pane-avatar .${part}`) &&
        /transform-origin/.test(body),
      );
      assert.ok(base, `no base rule pins .pane-avatar .${part}'s transform-origin`);
      const m = base![1].match(/transform-origin:\s*(-?[\d.]+)px\s+(-?[\d.]+)px/);
      assert.ok(m, `.${part}'s origin is not in viewBox px: ${base![1]}`);
      assert.equal(Number(m![1]), x, `.${part} x pivot`);
      assert.equal(Number(m![2]), y, `.${part} y pivot`);
    });
  }
});
