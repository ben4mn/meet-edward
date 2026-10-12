// R/G red for the Wishlist panel.
//
// Edward logs features he wishes he could do; Ellis reads and manages the
// list in Settings (Work room). The panel only draws what this module
// decides: which section an item sits in, the order inside a section, and
// the one-line meta under each title.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  cyclePriority,
  describeWish,
  groupWishes,
  PRIORITY_LABEL,
  STATUS_OPTIONS,
  type WishItem,
} from "../lib/wishlist.ts";

const now = new Date("2026-09-12T12:00:00Z");

function wish(over: Partial<WishItem> = {}): WishItem {
  return {
    id: "w1",
    title: "Read my calendar without asking",
    why: "I keep guessing at Ellis's day",
    detail: null,
    status: "open",
    priority: 2,
    source: "edward",
    conversation_id: null,
    times_wanted: 1,
    created_at: "2026-09-10T12:00:00Z",
    updated_at: "2026-09-10T12:00:00Z",
    ...over,
  };
}

describe("groupWishes", () => {
  it("returns Open, Planned, Building, then a folded Done & declined — only sections that have items", () => {
    const sections = groupWishes([
      wish({ id: "a", status: "done" }),
      wish({ id: "b", status: "open" }),
      wish({ id: "c", status: "building" }),
      wish({ id: "d", status: "declined" }),
      wish({ id: "e", status: "planned" }),
    ]);
    assert.deepEqual(sections.map((s) => s.id), ["open", "planned", "building", "closed"]);
    assert.deepEqual(sections.map((s) => s.label), ["Open", "Planned", "Building", "Done & declined"]);
    assert.deepEqual(sections.map((s) => s.folded), [false, false, false, true]);
    assert.deepEqual(sections[3].items.map((i) => i.id), ["a", "d"]);
    assert.deepEqual(groupWishes([wish({ status: "planned" })]).map((s) => s.id), ["planned"]);
    assert.deepEqual(groupWishes([]), []);
  });

  it("orders Open by priority, then how often he wanted it, then freshest first", () => {
    const sections = groupWishes([
      wish({ id: "old-high", priority: 1, times_wanted: 1, updated_at: "2026-09-01T00:00:00Z" }),
      wish({ id: "low", priority: 3, times_wanted: 9 }),
      wish({ id: "wanted-often", priority: 2, times_wanted: 4, updated_at: "2026-09-02T00:00:00Z" }),
      wish({ id: "wanted-once-new", priority: 2, times_wanted: 1, updated_at: "2026-09-11T00:00:00Z" }),
      wish({ id: "wanted-once-old", priority: 2, times_wanted: 1, updated_at: "2026-09-03T00:00:00Z" }),
    ]);
    assert.deepEqual(
      sections[0].items.map((i) => i.id),
      ["old-high", "wanted-often", "wanted-once-new", "wanted-once-old", "low"],
    );
  });

  it("keeps the closed section freshest first regardless of priority", () => {
    const [closed] = groupWishes([
      wish({ id: "x", status: "done", priority: 1, updated_at: "2026-09-01T00:00:00Z" }),
      wish({ id: "y", status: "declined", priority: 3, updated_at: "2026-09-09T00:00:00Z" }),
    ]);
    assert.deepEqual(closed.items.map((i) => i.id), ["y", "x"]);
  });
});

describe("describeWish", () => {
  it("says how often he wanted it, where it came from, and when", () => {
    assert.equal(
      describeWish(wish({ times_wanted: 3, conversation_id: "c1" }), now),
      "wanted 3× · from a chat · 2d ago",
    );
  });
  it("drops the source when there is no conversation, and says once for once", () => {
    assert.equal(describeWish(wish({ times_wanted: 1 }), now), "wanted once · 2d ago");
  });
  it("marks Ellis's own entries as his", () => {
    assert.equal(describeWish(wish({ source: "owner", conversation_id: null }), now), "your idea · 2d ago");
  });
});

describe("priority + status vocab", () => {
  it("cycles high → normal → low → high", () => {
    assert.equal(cyclePriority(1), 2);
    assert.equal(cyclePriority(2), 3);
    assert.equal(cyclePriority(3), 1);
    assert.deepEqual(PRIORITY_LABEL, { 1: "high", 2: "normal", 3: "low" });
  });
  it("offers the five statuses in workflow order", () => {
    assert.deepEqual(STATUS_OPTIONS.map((o) => o.value), ["open", "planned", "building", "done", "declined"]);
  });
});
