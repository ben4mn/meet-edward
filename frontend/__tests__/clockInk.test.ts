// R/G red for the screensaver's adaptive clock colour.
//
// The clock sits over arbitrary family photos. White-on-snow and
// black-on-night are both unreadable, so the overlay samples the region of
// the image actually behind the clock and picks its ink from that region's
// luminance — not from the photo as a whole, which averages out to mud.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  relativeLuminance,
  averageLuminance,
  inkForLuminance,
  LUMINANCE_SWITCH,
} from "../app/(display)/hub/lib/clockInk.ts";

// Build an RGBA buffer of a single repeated colour.
function rgba(r: number, g: number, b: number, count: number): Uint8ClampedArray {
  const out = new Uint8ClampedArray(count * 4);
  for (let i = 0; i < count; i++) {
    out[i * 4] = r;
    out[i * 4 + 1] = g;
    out[i * 4 + 2] = b;
    out[i * 4 + 3] = 255;
  }
  return out;
}

describe("relativeLuminance", () => {
  it("is 0 for black and 1 for white", () => {
    assert.equal(relativeLuminance(0, 0, 0), 0);
    // The sRGB coefficients sum to 0.9999999999999999 in binary floating
    // point, so white lands a hair under 1. Exact equality is the wrong
    // assertion for a weighted sum.
    assert.ok(Math.abs(relativeLuminance(255, 255, 255) - 1) < 1e-9);
  });

  it("clamps channel values outside 0..255", () => {
    assert.equal(relativeLuminance(999, 999, 999), 1);
    assert.equal(relativeLuminance(-50, -50, -50), 0);
  });

  it("weights green above red above blue, as the eye does", () => {
    const g = relativeLuminance(0, 255, 0);
    const r = relativeLuminance(255, 0, 0);
    const b = relativeLuminance(0, 0, 255);
    assert.ok(g > r, "green should read brighter than red");
    assert.ok(r > b, "red should read brighter than blue");
  });

  it("rates saturated yellow as bright and navy as dark", () => {
    assert.ok(relativeLuminance(255, 255, 0) > 0.7);
    assert.ok(relativeLuminance(16, 24, 64) < 0.15);
  });
});

describe("averageLuminance", () => {
  it("averages a uniform block to that block's luminance", () => {
    const mid = averageLuminance(rgba(128, 128, 128, 64));
    assert.ok(Math.abs(mid - relativeLuminance(128, 128, 128)) < 1e-6);
  });

  it("ignores fully transparent pixels rather than counting them as black", () => {
    const buf = rgba(255, 255, 255, 4);
    buf[3] = 0; // first pixel transparent
    assert.ok(averageLuminance(buf) > 0.99);
  });

  it("returns a mid value for an empty buffer instead of dividing by zero", () => {
    assert.equal(averageLuminance(new Uint8ClampedArray(0)), 0.5);
  });
});

describe("inkForLuminance", () => {
  it("puts dark ink on a bright photo", () => {
    assert.equal(inkForLuminance(0.9), "dark");
  });

  it("puts light ink on a dark photo", () => {
    assert.equal(inkForLuminance(0.05), "light");
  });

  it("defaults to light ink at the exact switch point", () => {
    // Ties go to light: the scrim under the clock is dark, so light ink is
    // the safer side of a coin-flip.
    assert.equal(inkForLuminance(LUMINANCE_SWITCH), "light");
  });
});
