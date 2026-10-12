// R/G red (2026-09-26 review): the design contract (§2) promises ≥ 4.5:1
// for text on its surfaces. Two token pairs missed it: light "OK" pills
// (#2e7d32 on its own soft fill, 4.37) and dark muted text on surface-3
// (#7d8a9c on #1f2835, 4.24 — the selected option's helper line). This reads
// the tokens straight from globals.css so a later retune cannot regress them.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";

const css = fs.readFileSync(path.resolve(import.meta.dirname, "..", "app/globals.css"), "utf8");

function block(selector: string): string {
  const i = css.indexOf(`${selector} {`);
  assert.ok(i >= 0, selector);
  return css.slice(i, css.indexOf("\n}", i));
}

type RGB = [number, number, number];
function hex(block: string, name: string): RGB {
  const m = block.match(new RegExp(`--${name}:\\s*#([0-9a-fA-F]{6})`));
  assert.ok(m, name);
  const h = m![1];
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16)) as RGB;
}
function alpha(block: string): number {
  return Number(block.match(/--soft-alpha:\s*([\d.]+)/)![1]);
}
function over(fg: RGB, a: number, bg: RGB): RGB {
  return fg.map((c, i) => Math.round(c * a + bg[i] * (1 - a))) as RGB;
}
function lum([r, g, b]: RGB): number {
  const ch = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * ch(r) + 0.7152 * ch(g) + 0.0722 * ch(b);
}
function ratio(a: RGB, b: RGB): number {
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

describe("token contrast (≥ 4.5:1)", () => {
  for (const [name, sel] of [["light", ":root"], ["dark", ".dark"]] as const) {
    const b = block(sel);
    const a = alpha(b);
    for (const tone of ["ok", "warn", "bad", "info"]) {
      for (const ground of ["surface", "primary-bg"]) {
        it(`${name}: ${tone} text on its soft pill over ${ground}`, () => {
          const bg = over(hex(b, tone), a, hex(b, ground));
          const r = ratio(hex(b, tone), bg);
          assert.ok(r >= 4.5, `${r.toFixed(2)}:1`);
        });
      }
    }
    for (const ground of ["surface", "surface-elevated", "surface-3", "primary-bg"]) {
      it(`${name}: muted text on ${ground}`, () => {
        const r = ratio(hex(b, "text-muted"), hex(b, ground));
        assert.ok(r >= 4.5, `${r.toFixed(2)}:1`);
      });
    }
  }
});
