// The Memory panel's words (2026-09-26, Jarvis polish). Pure.
//
// - A memory card leads with how he came by it, in plain words
//   ("he heard / he read / he thought / he did"), and whom it is about.
// - Tier and reinforcement are one muted meta line; importance and access
//   counts are not on the card at all.
// - The tier bar is one Segment with counts; the Filter chip counts only
//   what the sheet hides (type, nature, sort), not the visible tier.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  DEFAULT_FILTERS,
  MEMORY_TYPES,
  aboutLabel,
  activeFilterCount,
  compactCount,
  memoryEmpty,
  memoryMeta,
  normalizeQuery,
  originChip,
  statsLine,
  tierOptions,
} from "../lib/memoryView.ts";

const NOW = new Date("2026-09-26T12:00:00Z");

describe("originChip", () => {
  it("says how he came by it in plain words", () => {
    assert.deepEqual(originChip("heard"), { label: "he heard", tone: "neutral" });
    assert.deepEqual(originChip("read"), { label: "he read", tone: "neutral" });
    assert.deepEqual(originChip("thought"), { label: "he thought", tone: "edward" });
    assert.deepEqual(originChip("did"), { label: "he did", tone: "edward" });
  });

  it("treats a missing origin as heard (the backend default)", () => {
    assert.equal(originChip(undefined).label, "he heard");
    assert.equal(originChip(null).label, "he heard");
  });
});

describe("aboutLabel", () => {
  it("speaks to Ellis", () => {
    assert.equal(aboutLabel("owner"), "about you");
    assert.equal(aboutLabel("contact"), "about someone else");
    assert.equal(aboutLabel("self"), "about himself");
    assert.equal(aboutLabel("world"), "about the world");
    assert.equal(aboutLabel(undefined), "about you");
  });
});

describe("memoryMeta", () => {
  const base = {
    memory_type: "fact",
    tier: "knowledge",
    reinforcement_count: 14,
    created_at: "2026-09-19T12:00:00Z",
    importance: 0.95,
    access_count: 42,
  };

  it("is type, tier and reinforcement, then age — never importance or access", () => {
    const meta = memoryMeta(base, { now: NOW });
    assert.equal(meta, "Fact · Known · reinforced 14× · 1w ago");
    assert.doesNotMatch(meta, /95|42|Importance|Access/i);
  });

  it("leaves reinforcement out when it has none", () => {
    assert.equal(
      memoryMeta({ ...base, tier: "observation", reinforcement_count: 0, memory_type: "intention" }, { now: NOW }),
      "Intention · Noticed · 1w ago",
    );
  });

  it("copes with a missing date", () => {
    assert.equal(memoryMeta({ ...base, tier: "belief", reinforcement_count: 1, created_at: null }, { now: NOW }), "Fact · Believed · reinforced 1×");
  });
});

describe("filters", () => {
  it("offers intention as a type", () => {
    assert.ok(MEMORY_TYPES.includes("intention"));
  });

  it("counts only what the sheet hides", () => {
    assert.equal(activeFilterCount(DEFAULT_FILTERS), 0);
    assert.equal(activeFilterCount({ ...DEFAULT_FILTERS, tier: "knowledge" }), 0);
    assert.equal(activeFilterCount({ ...DEFAULT_FILTERS, type: "instruction" }), 1);
    assert.equal(activeFilterCount({ ...DEFAULT_FILTERS, type: "fact", nature: "temporary", sort: "reinforced" }), 3);
  });
});

describe("tierOptions", () => {
  it("is one segment, strongest first, with counts", () => {
    const opts = tierOptions({ total: 4400, live: 4410, by_tier: { knowledge: 1200, belief: 1000, observation: 2200 } });
    assert.deepEqual(
      opts.map((o) => [o.value, o.label, o.count]),
      [
        ["all", "All", 4410],
        ["knowledge", "Known", 1200],
        ["belief", "Believed", 1000],
        ["observation", "Noticed", 2200],
      ],
    );
  });

  it("works before stats arrive", () => {
    assert.deepEqual(tierOptions(null).map((o) => o.count), [undefined, undefined, undefined, undefined]);
  });
});

describe("small words", () => {
  it("compacts big counts", () => {
    assert.equal(compactCount(37), "37");
    assert.equal(compactCount(1200), "1.2k");
    assert.equal(compactCount(4410), "4.4k");
    assert.equal(compactCount(12000), "12k");
  });

  it("normalises a query", () => {
    assert.equal(normalizeQuery("  biscuit   meds "), "biscuit meds");
    assert.equal(normalizeQuery("   "), "");
  });

  it("says the store's size in one line", () => {
    assert.equal(statsLine({ total: 4843, live: 4432, created_last_24h: 37, unused_live: 1902 }), "4,432 memories · 37 new today");
    assert.equal(statsLine({ total: 12 }), "12 memories");
    assert.equal(statsLine(null), "");
  });

  it("speaks in his voice when there is nothing", () => {
    assert.match(memoryEmpty("", false).title, /haven't kept/i);
    assert.match(memoryEmpty("kayak", false).title, /Nothing I remember matches “kayak”/);
    assert.match(memoryEmpty("", true).title, /Nothing I remember fits/);
  });
});
