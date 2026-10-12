// R/G for the chat's stick-to-bottom scrolling (2026-09-26).
//
// While Edward streams, the transcript follows the newest line — unless Ellis
// scrolled up to read something, in which case it leaves him there and
// offers "New reply ↓". Opening a thread or sending lands at the bottom at
// once. The decisions are pure; ChatWindow only measures and writes
// scrollTop.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  INITIAL_STICK,
  STICK_PX,
  distanceFromBottom,
  reduceStick,
  showNewReplyPill,
  type StickState,
} from "../lib/scrollStick.ts";

const at = (s: StickState, top: number, distance: number) => reduceStick(s, { type: "scroll", top, distance });

describe("distanceFromBottom", () => {
  it("measures what is left below the viewport", () => {
    assert.equal(distanceFromBottom({ scrollTop: 600, scrollHeight: 1500, clientHeight: 800 }), 100);
    assert.equal(distanceFromBottom({ scrollTop: 800, scrollHeight: 1500, clientHeight: 800 }), 0);
  });
});

describe("reduceStick", () => {
  it("starts stuck to the bottom", () => {
    assert.equal(INITIAL_STICK.stuck, true);
    assert.equal(INITIAL_STICK.unseen, false);
  });

  it("follows growing content while stuck", () => {
    const r = reduceStick(INITIAL_STICK, { type: "grow", streaming: true });
    assert.equal(r.pin, true);
    assert.equal(r.state.stuck, true);
  });

  it("lets go when Ellis scrolls up, and marks new text unseen", () => {
    let s = at(INITIAL_STICK, 900, 0).state;
    const up = at(s, 700, 200);
    assert.equal(up.state.stuck, false);
    assert.equal(up.pin, false);
    s = up.state;
    const grow = reduceStick(s, { type: "grow", streaming: true });
    assert.equal(grow.pin, false, "never yank him back down");
    assert.equal(grow.state.unseen, true);
    assert.equal(showNewReplyPill(grow.state), true);
  });

  it("a small nudge up near the bottom still lets go", () => {
    const s = at(INITIAL_STICK, 900, 0).state;
    assert.equal(at(s, 880, 20).state.stuck, false);
  });

  it("content shrinking under him is not a scroll up", () => {
    const s = at(INITIAL_STICK, 900, 0).state;
    // the rail folded: the browser clamps scrollTop down but he is still at the bottom
    assert.equal(at(s, 760, 0).state.stuck, true);
  });

  it("growth after the stream ended is not a new reply", () => {
    const s = at(at(INITIAL_STICK, 900, 0).state, 500, 400).state;
    assert.equal(reduceStick(s, { type: "grow", streaming: false }).state.unseen, false);
  });

  it("scrolling back to the bottom sticks again and clears the pill", () => {
    let s = at(at(INITIAL_STICK, 900, 0).state, 500, 400).state;
    s = reduceStick(s, { type: "grow", streaming: true }).state;
    const back = at(s, 1200, STICK_PX - 1);
    assert.equal(back.state.stuck, true);
    assert.equal(showNewReplyPill(back.state), false);
  });

  it("a new conversation, a send and the pill all land at the bottom", () => {
    const loose: StickState = { stuck: false, unseen: true, lastTop: 300 };
    for (const type of ["reset", "send", "jump"] as const) {
      const r = reduceStick(loose, { type });
      assert.equal(r.pin, true, type);
      assert.equal(r.state.stuck, true, type);
      assert.equal(r.state.unseen, false, type);
    }
    assert.equal(reduceStick(loose, { type: "reset" }).state.lastTop, 0, "the old thread's position is forgotten");
  });
});

// 2026-09-26 review: only Ellis lets go. The composer's autosize collapses the
// box for a moment ("height: auto" before measuring), the transcript grows
// taller for that instant and the browser clamps scrollTop down a line —
// read as "moved up", it dropped the live turn behind a "New reply" pill the
// moment a queued follow-up wrapped to a second line. A scroll carries
// whether Ellis's hands made it (wheel, touch, a key, the scrollbar).
describe("reduceStick: only Ellis's own scroll lets go", () => {
  const stuckAt = (top: number) => at(INITIAL_STICK, top, 0).state;

  it("a layout clamp near the bottom (the composer re-measuring) stays stuck", () => {
    const s = stuckAt(564);
    const clamp = reduceStick(s, { type: "scroll", top: 540, distance: 24, user: false });
    assert.equal(clamp.state.stuck, true);
    assert.equal(showNewReplyPill(clamp.state), false);
    const grow = reduceStick(clamp.state, { type: "grow", streaming: true });
    assert.equal(grow.pin, true, "the next line of his answer still follows");
  });

  it("a bigger layout clamp is not Ellis either", () => {
    const s = stuckAt(900);
    const clamp = reduceStick(s, { type: "scroll", top: 736, distance: 164, user: false });
    assert.equal(clamp.state.stuck, true);
    assert.equal(reduceStick(clamp.state, { type: "grow", streaming: true }).pin, true);
  });

  it("the same move made by Ellis lets go", () => {
    const s = stuckAt(564);
    assert.equal(reduceStick(s, { type: "scroll", top: 540, distance: 24, user: true }).state.stuck, false);
  });

  it("a scroll that reaches the bottom sticks whoever made it", () => {
    const loose: StickState = { stuck: false, unseen: true, lastTop: 300 };
    assert.equal(reduceStick(loose, { type: "scroll", top: 700, distance: 0, user: false }).state.stuck, true);
  });
});

// 2026-09-26 review: tapping "Thought for 9s · …" or a pack card on the last
// message grew the content while stuck, and the pin threw the header he
// tapped off the top. Opening something in the transcript is Ellis reading:
// let go (no pill), so it opens downward under his finger.
describe("reduceStick: Ellis opens something in the transcript", () => {
  it("lets go without a pin, so the tapped header stays where it was", () => {
    const s = at(INITIAL_STICK, 900, 0).state;
    const t = reduceStick(s, { type: "toggle" });
    assert.equal(t.pin, false);
    assert.equal(t.state.stuck, false);
    const grow = reduceStick(t.state, { type: "grow", streaming: false });
    assert.equal(grow.pin, false, "the disclosure opening is not pinned to the bottom");
    assert.equal(showNewReplyPill(grow.state), false, "and nothing new arrived");
  });

  it("closing something at the bottom sticks again (the clamp lands at the bottom)", () => {
    const t = reduceStick(at(INITIAL_STICK, 900, 0).state, { type: "toggle" }).state;
    assert.equal(reduceStick(t, { type: "scroll", top: 700, distance: 0, user: false }).state.stuck, true);
  });
});

import { isScrollKey } from "../lib/scrollStick.ts";

describe("isScrollKey", () => {
  it("page keys scroll the transcript; typing in the composer never does", () => {
    assert.equal(isScrollKey("PageUp", false), true);
    assert.equal(isScrollKey("ArrowUp", false), true);
    assert.equal(isScrollKey("ArrowUp", true), false);
    assert.equal(isScrollKey(" ", true), false);
    assert.equal(isScrollKey("a", false), false);
  });
});
