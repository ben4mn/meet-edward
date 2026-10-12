// R/G red for Edward's CSS state machine (the Pane section of globals.css).
//
// The character's behaviour lives in CSS driven by data attributes that
// PaneSvg and useEdwardLife set: data-mood, data-beat, data-gaze /
// data-glance, data-blink, .is-live, .pane-static. These tests pin the
// contract the design system states (§4 and §7):
//   * only the live Edward breathes, and mood loops run only on him;
//   * beats are ≤1s and animate nothing but transform / opacity / fill;
//   * reduced motion stops every loop and leaves readable static poses;
//   * .pane-static is fully still;
//   * no [class*=] selectors.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { BEAT_MS, BEATS, MOODS } from "../lib/edwardMood.ts";

const globalsCss = readFileSync(new URL("../app/globals.css", import.meta.url), "utf8");

const START = "/* === Pane buddy";
const END = "/* === end Pane buddy === */";

/** The section with its comments removed (a comment is never a selector). */
function paneSection(): string {
  const a = globalsCss.indexOf(START);
  const b = globalsCss.indexOf(END);
  assert.ok(a >= 0, "pane section start marker missing");
  assert.ok(b > a, "pane section end marker missing");
  return globalsCss.slice(a, b + END.length).replace(/\/\*[\s\S]*?\*\//g, "");
}

/** Top-level-ish [selector, body] pairs, ignoring @keyframes contents. */
function rules(css: string): Array<[string, string]> {
  const noFrames = css.replace(/@keyframes[^{]+\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, "");
  const out: Array<[string, string]> = [];
  const re = /([^{}]+)\{([^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(noFrames)) !== null) out.push([m[1].trim(), m[2]]);
  return out;
}

function keyframes(css: string): Map<string, string> {
  const out = new Map<string, string>();
  const re = /@keyframes\s+([\w-]+)\s*\{((?:[^{}]*\{[^{}]*\})*[^{}]*)\}/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(css)) !== null) out.set(m[1], m[2]);
  return out;
}

function reducedMotionBlock(css: string): string {
  const i = css.indexOf("@media (prefers-reduced-motion: reduce)");
  assert.ok(i >= 0, "no reduced-motion block in the pane section");
  // Balanced-brace scan from the @media's opening brace.
  let depth = 0;
  let start = -1;
  for (let j = i; j < css.length; j++) {
    if (css[j] === "{") {
      if (depth === 0) start = j;
      depth++;
    } else if (css[j] === "}") {
      depth--;
      if (depth === 0) return css.slice(start + 1, j);
    }
  }
  assert.fail("unbalanced reduced-motion block");
}

/** Rules outside @media blocks (the always-on rules). */
function outsideMedia(css: string): string {
  let out = "";
  let i = 0;
  while (i < css.length) {
    const at = css.indexOf("@media", i);
    if (at < 0) {
      out += css.slice(i);
      break;
    }
    out += css.slice(i, at);
    let depth = 0;
    let j = css.indexOf("{", at);
    for (; j < css.length; j++) {
      if (css[j] === "{") depth++;
      else if (css[j] === "}") {
        depth--;
        if (depth === 0) break;
      }
    }
    i = j + 1;
  }
  return out;
}

describe("pane section", () => {
  it("is delimited so other agents can leave it alone", () => {
    paneSection();
  });

  it("has no [class*=] selectors", () => {
    assert.ok(!/\[class\*=/.test(paneSection()));
  });

  it("styles every non-idle mood", () => {
    const css = paneSection();
    for (const mood of MOODS) {
      if (mood === "idle") continue;
      assert.ok(css.includes(`[data-mood="${mood}"]`), `no rules for mood ${mood}`);
    }
  });

  it("styles every beat", () => {
    const css = paneSection();
    for (const beat of BEATS) {
      assert.ok(css.includes(`[data-beat="${beat}"]`), `no rules for beat ${beat}`);
    }
  });

  it("styles blinks and all four gaze directions", () => {
    const css = paneSection();
    assert.ok(css.includes("[data-blink]"));
    for (const g of ["left", "right", "up", "down"]) {
      assert.ok(css.includes(`[data-gaze="${g}"]`), `no gaze ${g}`);
    }
  });
});

describe("loops", () => {
  it("only the live Edward runs an infinite animation", () => {
    for (const [sel, body] of rules(outsideMedia(paneSection()))) {
      if (!/infinite/.test(body)) continue;
      for (const s of sel.split(",")) {
        assert.ok(s.includes(".is-live"), `"${s.trim()}" loops without .is-live`);
      }
    }
  });

  it("he breathes at 5.2s", () => {
    const breath = rules(paneSection()).find(
      ([sel, body]) => sel.includes(".is-live") && /robot-upper/.test(sel) && /animation:/.test(body),
    );
    assert.ok(breath, "no breathing rule on .is-live .robot-upper");
    assert.ok(/5\.2s/.test(breath![1]));
  });
});

describe("motion budget", () => {
  const frames = keyframes(paneSection());

  it("keyframes animate only transform, opacity and fill", () => {
    assert.ok(frames.size > 0, "no keyframes found");
    for (const [name, body] of frames) {
      const props = [...body.matchAll(/([a-z-]+)\s*:/g)].map((m) => m[1]);
      for (const p of props) {
        assert.ok(
          ["transform", "opacity", "fill"].includes(p),
          `@keyframes ${name} animates ${p}`,
        );
      }
    }
  });

  it("every beat's animations finish within BEAT_MS", () => {
    const css = outsideMedia(paneSection());
    for (const beat of BEATS) {
      const blocks = rules(css).filter(([sel]) => sel.includes(`[data-beat="${beat}"]`));
      const anims = blocks.filter(([, b]) => /animation:/.test(b));
      assert.ok(anims.length > 0, `beat ${beat} has no animation`);
      for (const [sel, body] of anims) {
        const decl = body.match(/animation:\s*([^;]+)/)![1];
        const times = [...decl.matchAll(/([\d.]+)(ms|s)\b/g)].map((m) =>
          m[2] === "s" ? Number(m[1]) * 1000 : Number(m[1]),
        );
        const total = times.slice(0, 2).reduce((a, b) => a + b, 0); // duration + delay
        assert.ok(
          total <= BEAT_MS[beat],
          `${sel} runs ${total}ms, over ${beat}'s ${BEAT_MS[beat]}ms`,
        );
        assert.ok(!/infinite/.test(decl), `${sel} loops`);
      }
    }
  });
});

describe("reduced motion", () => {
  it("stops every pane animation", () => {
    const rm = reducedMotionBlock(paneSection());
    assert.ok(/\.pane-avatar\s+\*[^{]*\{[^}]*animation:\s*none\s*!important/.test(rm));
  });

  it("keeps readable static poses (thinking, acted, wave)", () => {
    const rm = reducedMotionBlock(paneSection());
    assert.ok(rm.includes(`[data-mood="think"]`), "no static thinking pose");
    assert.ok(rm.includes(`[data-beat="acted"]`), "no static acted pose");
    assert.ok(rm.includes(`[data-beat="wave"]`), "no static wave pose");
  });
});

describe("still avatars", () => {
  it(".pane-static turns off every animation and transition", () => {
    const hit = rules(paneSection()).find(
      ([sel, body]) =>
        sel.includes(".pane-static") && /animation:\s*none/.test(body) && /transition:\s*none/.test(body),
    );
    assert.ok(hit, "no .pane-static rule");
  });
});

describe("drawing contract", () => {
  it("face-left mirrors only the outer svg (a bust nests one, which would flip back)", () => {
    const css = paneSection();
    assert.ok(/\.pane-avatar\.face-left\s*>\s*svg/.test(css), "no .face-left > svg rule");
    assert.ok(!/\.pane-avatar\.face-left\s+svg/.test(css), "a descendant face-left rule double-flips the bust");
  });

  it("paints the icon's palette through tokens", () => {
    const css = paneSection();
    assert.ok(/--pane-shell:\s*#e9e4d8/i.test(css), "cream shell");
    assert.ok(/--pane-shade:\s*#c9c3b4/i.test(css), "underside");
    assert.ok(/--pane-visor:\s*#20262f/i.test(css), "visor");
  });

  it("light mode outlines him so the cream never vanishes on white", () => {
    const css = paneSection();
    const base = rules(css).find(([sel]) => sel.split(",").some((s) => s.trim() === ".pane-avatar"));
    assert.ok(base && /--pane-outline:\s*rgba/.test(base[1]), "no light outline on .pane-avatar");
    assert.ok(/\.dark\s+\.pane-avatar[^{]*\{[^}]*--pane-outline/.test(css), "dark mode must restate the outline");
  });

  it("the antenna tip is his trust light: amber waiting on Ellis or resting, grey offline", () => {
    const css = paneSection();
    for (const mood of ["attention", "resting", "offline"]) {
      const hit = rules(css).find(
        ([sel, body]) => sel.includes(`[data-mood="${mood}"]`) && /--pane-tip:/.test(body),
      );
      assert.ok(hit, `${mood} does not recolour the tip`);
    }
  });

  it("the mouth follows --mouth when lip-synced and the listening ring follows --level", () => {
    const css = paneSection();
    assert.ok(/\[data-lipsync\][^{]*\{[^}]*var\(--mouth/.test(css), "no --mouth lip-sync rule");
    assert.ok(/\[data-mood="listen"\][^{]*robot-antenna-ring[^{]*\{[^}]*var\(--level/.test(css), "no --level ring rule");
  });

  it("offline never breathes, even when live", () => {
    const css = outsideMedia(paneSection());
    const hit = rules(css).find(
      ([sel, body]) =>
        sel.includes('[data-mood="offline"]') && /robot-upper/.test(sel) && /animation:\s*none/.test(body),
    );
    assert.ok(hit, "an offline Edward still breathes");
  });
});

describe("the wave", () => {
  it("swaps the hanging hand for the raised arm and back, inside the beat", () => {
    const css = outsideMedia(paneSection());
    const base = rules(css).find(
      ([sel, body]) => sel.split(",").some((s) => s.trim() === ".pane-avatar .robot-wave-arm") && /opacity:\s*0/.test(body),
    );
    assert.ok(base, "the raised arm must be hidden at rest");
    for (const part of ["robot-wave-arm", "robot-arm-r"]) {
      const hit = rules(css).find(
        ([sel, body]) => sel.includes('[data-beat="wave"]') && sel.includes(`.${part}`) && /animation:/.test(body),
      );
      assert.ok(hit, `wave does not animate .${part}`);
    }
  });

  it("reduced motion holds the raised arm up", () => {
    const rm = reducedMotionBlock(paneSection());
    assert.ok(/\[data-beat="wave"\]\s*\.robot-wave-arm\s*\{[^}]*opacity:\s*1/.test(rm));
  });
});
