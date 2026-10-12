// R/G red for how a photo is fitted to the screensaver frame.
//
// 9 of Ellis's 36 photos are portrait. `cover` on a 16:9 panel crops a tall
// photo to a middle band, which on family photos means cutting off heads.
// So the slide uses `contain` over a blurred `cover` backdrop, and the
// clock's ink sampler has to know which of those two layers is actually
// behind the clock.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  containRect,
  layerBehind,
  BACKDROP_BRIGHTNESS,
} from "../app/(display)/hub/lib/photoLayout.ts";

describe("containRect", () => {
  it("fills the frame exactly when aspects match", () => {
    const r = containRect(1920, 1080, 1280, 720);
    assert.ok(Math.abs(r.x0 - 0) < 1e-9);
    assert.ok(Math.abs(r.x1 - 1) < 1e-9);
    assert.ok(Math.abs(r.y0 - 0) < 1e-9);
    assert.ok(Math.abs(r.y1 - 1) < 1e-9);
  });

  it("pillarboxes a portrait photo, leaving the sides empty", () => {
    const r = containRect(1440, 1920, 1280, 720);
    assert.ok(r.x0 > 0.2, "expected wide empty margins at the sides");
    assert.ok(r.x1 < 0.8);
    // Full height used.
    assert.ok(Math.abs(r.y0) < 1e-9);
    assert.ok(Math.abs(r.y1 - 1) < 1e-9);
  });

  it("letterboxes an ultra-wide photo", () => {
    const r = containRect(3000, 1000, 1280, 720);
    assert.ok(r.y0 > 0.1);
    assert.ok(r.y1 < 0.9);
    assert.ok(Math.abs(r.x0) < 1e-9);
  });

  it("stays centred", () => {
    const r = containRect(1440, 1920, 1280, 720);
    assert.ok(Math.abs((r.x0 + r.x1) / 2 - 0.5) < 1e-9);
  });
});

describe("layerBehind", () => {
  // The clock sits bottom-left.
  const clock = { x: 0.12, y: 0.88 };

  it("reads the backdrop for a 4:3 photo, whose pillarbox reaches past the clock", () => {
    // 4:3 on 16:9 contains to 960x720 -> margins of 0.125 each side, so the
    // bottom-left clock sits just outside the photo. This is the common
    // case: 27 of the 36 photos in the album are 4:3.
    assert.equal(layerBehind(clock, containRect(1920, 1440, 1280, 720)), "backdrop");
  });

  it("reads the photo for a point well inside a 4:3 photo", () => {
    assert.equal(
      layerBehind({ x: 0.5, y: 0.88 }, containRect(1920, 1440, 1280, 720)),
      "photo",
    );
  });

  it("reads the blurred backdrop when a tall photo does not reach the clock", () => {
    assert.equal(layerBehind(clock, containRect(1033, 1920, 1280, 720)), "backdrop");
  });

  it("reads the photo when it fills the frame", () => {
    assert.equal(layerBehind(clock, containRect(1920, 1080, 1280, 720)), "photo");
  });
});

describe("BACKDROP_BRIGHTNESS", () => {
  it("is dimmed, so ink sampled off the backdrop must be scaled", () => {
    assert.ok(BACKDROP_BRIGHTNESS > 0 && BACKDROP_BRIGHTNESS < 1);
  });
});
