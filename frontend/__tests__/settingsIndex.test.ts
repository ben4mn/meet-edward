// R/G red for the Edward tab's index — the light, render-free list of
// panels the rail, the landing, the rooms and the A–Z page read. The
// panels' code only loads on a panel page (settingsConfig), never with the
// rail on every settings route.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { SETTINGS_INDEX, indexItem, itemsInRoom } from "../components/settings/settingsIndex.ts";
import { ROOMS, ROOM_SLUGS } from "../components/settings/settingsRooms.ts";

describe("SETTINGS_INDEX", () => {
  it("has one entry for every slug in the rooms", () => {
    const slugs = SETTINGS_INDEX.map((i) => i.slug).sort();
    assert.deepEqual(slugs, ROOMS.flatMap((r) => ROOM_SLUGS[r.id]).sort());
  });
  it("speaks to Ellis without internal vocabulary in titles", () => {
    for (const i of SETTINGS_INDEX) {
      assert.doesNotMatch(i.title, /heartbeat|orchestrator|mcp|triage|pulse/i, i.slug);
      assert.ok(i.description.length > 0 && i.description.length <= 64, `${i.slug}: ${i.description}`);
    }
  });
  it("keeps the old names findable", () => {
    assert.match(indexItem("heartbeat")!.keywords ?? "", /heartbeat/);
    assert.match(indexItem("orchestrator")!.keywords ?? "", /orchestrator/);
    assert.match(indexItem("mcp")!.keywords ?? "", /mcp/i);
  });
});

describe("searching for a held send", () => {
  it("finds where it can be answered (Notifications shows what waits on you)", async () => {
    const { searchSettings } = await import("../components/settings/settingsRooms.ts");
    const { searchableIndex } = await import("../components/settings/settingsIndex.ts");
    for (const q of ["approval", "approve", "held"]) {
      assert.ok(searchSettings(searchableIndex(), q).some((i) => i.slug === "notifications"), q);
    }
  });
});

describe("itemsInRoom", () => {
  it("returns a room's panels in the room's order", () => {
    assert.deepEqual(itemsInRoom("mind").map((i) => i.slug), [...ROOM_SLUGS.mind]);
    assert.equal(indexItem("nope"), undefined);
  });
});
