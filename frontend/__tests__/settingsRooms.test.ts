// R/G red for the Edward tab's rooms.
//
// Twenty settings panels regrouped by the question you bring to Edward:
// Mind (how he thinks), Memory (what he knows), Attention (what he watches
// and how he reaches you), Work (what he does on his own), House (the
// machine and the household). Nothing is removed — every slug that exists
// today lives in exactly one room, and the All-settings index can search
// by title, description or the old category name.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  ROOMS,
  roomFor,
  ROOM_SLUGS,
  searchSettings,
  settingsLocation,
  settingDetail,
  type RoomId,
} from "../components/settings/settingsRooms.ts";

const ALL_SLUGS = [
  "model", "thoughts", "locale", "skills", "memories", "documents",
  "gmail", "alexa", "mcp", "proactive",
  "heartbeat", "events", "wishlist", "journal", "orchestrator", "dreaming",
  "notifications", "usage", "databases", "files", "hub-photos",
];

describe("ROOMS", () => {
  it("are Mind, Memory, Attention, Work, House in that order", () => {
    assert.deepEqual(ROOMS.map((r) => r.id), ["mind", "memory", "attention", "work", "house"]);
  });
  it("cover every existing slug exactly once", () => {
    const seen = new Map<string, RoomId>();
    for (const room of ROOMS) {
      for (const slug of ROOM_SLUGS[room.id]) {
        assert.ok(!seen.has(slug), `${slug} appears in two rooms`);
        seen.set(slug, room.id);
      }
    }
    assert.deepEqual([...seen.keys()].sort(), [...ALL_SLUGS].sort());
  });
});

describe("roomFor", () => {
  it("places the panels where the plan put them", () => {
    assert.equal(roomFor("model"), "mind");
    assert.equal(roomFor("thoughts"), "mind");
    assert.equal(roomFor("files"), "memory");
    assert.equal(roomFor("notifications"), "attention");
    assert.equal(roomFor("wishlist"), "work");
    assert.equal(roomFor("dreaming"), "memory");
    assert.equal(roomFor("evolution"), "house", "the evolution slug is retired; unknown → house");
  });
  it("falls back to house for an unknown slug rather than losing it", () => {
    assert.equal(roomFor("something-new"), "house");
  });
  // 2026-09-26 regroup (Edward tab frame): the journal sits beside his
  // thoughts, his servers beside his skills (both are what he can do),
  // token usage beside the Codex quota in Work ("Fuel"), and the timezone
  // with the household.
  it("puts the journal and his servers in Mind, usage in Work, locale in House", () => {
    assert.equal(roomFor("journal"), "mind");
    assert.equal(roomFor("mcp"), "mind");
    assert.equal(roomFor("usage"), "work");
    assert.equal(roomFor("locale"), "house");
  });
  it("leads Mind with his thoughts, then the journal", () => {
    assert.deepEqual(ROOM_SLUGS.mind.slice(0, 2), ["thoughts", "journal"]);
    assert.ok(ROOM_SLUGS.mind.indexOf("mcp") === ROOM_SLUGS.mind.indexOf("skills") + 1, "servers sit right after skills");
  });
  it("keeps every room non-empty", () => {
    for (const room of ROOMS) assert.ok(ROOM_SLUGS[room.id].length > 0, `${room.id} is empty`);
  });
});

describe("settingsLocation", () => {
  it("reads the landing, the A–Z index, a room and a panel from the path", () => {
    assert.deepEqual(settingsLocation("/settings"), { kind: "home" });
    assert.deepEqual(settingsLocation("/settings/"), { kind: "home" });
    assert.deepEqual(settingsLocation("/settings/all"), { kind: "all" });
    assert.deepEqual(settingsLocation("/settings/room/memory"), { kind: "room", room: "memory" });
    assert.deepEqual(settingsLocation("/settings/memories"), { kind: "panel", room: "memory", slug: "memories" });
    assert.deepEqual(settingsLocation("/settings/journal"), { kind: "panel", room: "mind", slug: "journal" });
  });
  it("is 'other' off the settings tree, for an unknown room, or with no path yet", () => {
    assert.deepEqual(settingsLocation("/chat"), { kind: "other" });
    assert.deepEqual(settingsLocation("/settingsx"), { kind: "other" });
    assert.deepEqual(settingsLocation("/settings/room/garage"), { kind: "other" });
    assert.deepEqual(settingsLocation(null), { kind: "other" });
  });
  it("ignores a query string or hash", () => {
    assert.deepEqual(settingsLocation("/settings/memories?id=m-1"), { kind: "panel", room: "memory", slug: "memories" });
    assert.deepEqual(settingsLocation("/settings/room/work#fuel"), { kind: "room", room: "work" });
  });
});

describe("settingDetail", () => {
  it("prefixes a panel's description with the room it lives in", () => {
    assert.equal(settingDetail("memories", "Search long-term memory"), "Memory · Search long-term memory");
    assert.equal(settingDetail("usage", "Tokens by model"), "Work · Tokens by model");
  });
  it("never mentions the pre-redesign category", () => {
    assert.doesNotMatch(settingDetail("heartbeat", "What he watched"), /was /);
  });
});

describe("searchSettings", () => {
  const items = [
    { slug: "heartbeat", title: "Heartbeat", description: "What he watched and decided", was: "Automation" },
    { slug: "usage", title: "Usage", description: "Token usage by provider", was: "System" },
  ];
  it("matches title, description and the old category, case-insensitively", () => {
    assert.deepEqual(searchSettings(items, "HEART").map((i) => i.slug), ["heartbeat"]);
    assert.deepEqual(searchSettings(items, "token").map((i) => i.slug), ["usage"]);
    assert.deepEqual(searchSettings(items, "system").map((i) => i.slug), ["usage"]);
  });
  it("returns everything for an empty query, sorted by title", () => {
    assert.deepEqual(searchSettings(items, "  ").map((i) => i.slug), ["heartbeat", "usage"]);
  });
});

describe("searchSettings keywords", () => {
  it("finds a renamed panel by its old or technical name without showing it", () => {
    const items = [
      { slug: "heartbeat", title: "Listening", description: "What he heard and did", was: "Automation", keywords: "heartbeat triage" },
      { slug: "mcp", title: "Servers", description: "Tools he installed", was: "Integrations", keywords: "mcp custom" },
    ];
    assert.deepEqual(searchSettings(items, "heartbeat").map((i) => i.slug), ["heartbeat"]);
    assert.deepEqual(searchSettings(items, "MCP").map((i) => i.slug), ["mcp"]);
  });
});
