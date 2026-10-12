// R/G red for the screensaver's slide advance.
//
// The bug this replaces: both layers derived their image from `index`, so
// advancing swapped the OUTGOING photo's image at the same moment its
// opacity started falling. The fade then ran on the wrong picture and the
// album appeared to skip — the "glitch" Ellis saw.
//
// The invariant that fixes it: a photo in a VISIBLE slot is never changed.
// New photos are only ever written into the hidden slot, and only then is
// visibility flipped.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  initSlideshow,
  advance,
  queueUpcoming,
  visiblePhoto,
  hiddenPhoto,
} from "../app/(display)/hub/lib/slideshow.ts";

const ALBUM = ["a.jpg", "b.jpg", "c.jpg", "d.jpg"];

describe("initSlideshow", () => {
  it("shows the first photo and preloads the second into the hidden slot", () => {
    const s = initSlideshow(ALBUM);
    assert.equal(visiblePhoto(s), "a.jpg");
    assert.equal(hiddenPhoto(s), "b.jpg");
  });

  it("handles a single photo without a hidden partner", () => {
    const s = initSlideshow(["only.jpg"]);
    assert.equal(visiblePhoto(s), "only.jpg");
    assert.equal(hiddenPhoto(s), null);
  });

  it("handles an empty album", () => {
    const s = initSlideshow([]);
    assert.equal(visiblePhoto(s), null);
    assert.equal(hiddenPhoto(s), null);
  });
});

describe("advance", () => {
  it("makes the previously-hidden photo the visible one", () => {
    const s = advance(initSlideshow(ALBUM), ALBUM);
    assert.equal(visiblePhoto(s), "b.jpg");
  });

  it("NEVER mutates the slot that is currently on screen", () => {
    // The whole point. Walk several advances and assert that at each step
    // the outgoing photo is still intact in its slot as it fades out.
    let s = initSlideshow(ALBUM);
    for (let i = 0; i < 6; i++) {
      const wasVisible = visiblePhoto(s);
      const next = advance(s, ALBUM);
      // After advancing, the old photo must still be somewhere — it is the
      // one fading out, so it is now the hidden slot's content... until the
      // NEXT preload replaces it.
      assert.equal(
        next.slots[s.active],
        wasVisible,
        `advance ${i} clobbered the outgoing photo`,
      );
      s = next;
    }
  });

  it("follows album order", () => {
    let s = initSlideshow(ALBUM);
    const seen = [visiblePhoto(s)];
    for (let i = 0; i < 3; i++) {
      s = advance(s, ALBUM);
      seen.push(visiblePhoto(s));
    }
    assert.deepEqual(seen, ["a.jpg", "b.jpg", "c.jpg", "d.jpg"]);
  });

  it("wraps around to the start", () => {
    let s = initSlideshow(ALBUM);
    for (let i = 0; i < 4; i++) s = advance(s, ALBUM);
    assert.equal(visiblePhoto(s), "a.jpg");
  });

  it("alternates slots so only two layers are ever needed", () => {
    let s = initSlideshow(ALBUM);
    const actives = [s.active];
    for (let i = 0; i < 4; i++) {
      s = advance(s, ALBUM);
      actives.push(s.active);
    }
    assert.deepEqual(actives, [0, 1, 0, 1, 0]);
  });

  it("is a no-op for a single-photo album", () => {
    const s = initSlideshow(["only.jpg"]);
    const after = advance(s, ["only.jpg"]);
    assert.equal(visiblePhoto(after), "only.jpg");
    assert.equal(after.active, s.active, "should not flip with nothing to flip to");
  });

  it("is safe on an empty album", () => {
    const s = initSlideshow([]);
    assert.equal(visiblePhoto(advance(s, [])), null);
  });

  it("preloads the upcoming photo into the newly hidden slot", () => {
    const s = advance(initSlideshow(ALBUM), ALBUM);
    // b is visible; c should be queued next.
    const s2 = advance(s, ALBUM);
    assert.equal(visiblePhoto(s2), "c.jpg");
  });
});

describe("slide direction", () => {
  it("marks the outgoing slot as exiting so it can leave to the left", () => {
    // Without this the CSS sends the outgoing photo back to its parked
    // position (off-screen RIGHT), which is the same side the incoming one
    // enters from — they slide over each other the wrong way.
    const s = advance(initSlideshow(ALBUM), ALBUM);
    assert.equal(s.exiting, 0, "slot 0 held the outgoing photo");
    assert.equal(s.active, 1);
  });

  it("clears the exit mark once the upcoming photo is queued there", () => {
    let s = advance(initSlideshow(ALBUM), ALBUM);
    s = queueUpcoming(s, ALBUM);
    assert.equal(s.exiting, null, "slot is parked for entry now, not exiting");
  });

  it("has nothing exiting on a fresh album", () => {
    assert.equal(initSlideshow(ALBUM).exiting, null);
  });

  it("alternates which slot is exiting", () => {
    let s = initSlideshow(ALBUM);
    const marks: (0 | 1 | null)[] = [];
    for (let i = 0; i < 3; i++) {
      s = advance(s, ALBUM);
      marks.push(s.exiting);
      s = queueUpcoming(s, ALBUM);
    }
    assert.deepEqual(marks, [0, 1, 0]);
  });

  it("does not mark an exit when there is nothing to advance to", () => {
    const s = advance(initSlideshow(["only.jpg"]), ["only.jpg"]);
    assert.equal(s.exiting, null);
  });
});
