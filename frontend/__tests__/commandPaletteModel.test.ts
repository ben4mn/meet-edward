// R/G red for components/layout/commandPaletteModel.ts — the ⌘K palette.
//
// One list, grouped: with nothing typed, recent conversations first, then
// actions. With a query every source is matched (actions, conversations,
// settings) and the group with the best hit leads. Arrow keys move
// through the flattened list and wrap.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  scoreMatch,
  buildPalette,
  moveActive,
  type PaletteItem,
} from "../components/layout/commandPaletteModel.ts";

const actions: PaletteItem[] = [
  { id: "a:new", group: "actions", label: "New chat", keywords: ["start", "fresh"] },
  { id: "a:voice", group: "actions", label: "Talk live", keywords: ["voice", "speak"] },
  { id: "a:wake", group: "actions", label: "Wake Edward", keywords: ["mind", "look"] },
  { id: "a:theme", group: "actions", label: "Switch to light mode", keywords: ["theme", "dark", "appearance"] },
];
const conversations: PaletteItem[] = [
  { id: "c:1", group: "conversations", label: "Cabin weekend", detail: "2h ago" },
  { id: "c:2", group: "conversations", label: "Biscuit's vet visit", detail: "yesterday" },
  { id: "c:3", group: "conversations", label: "Morning voice", detail: "Mon" },
  { id: "c:4", group: "conversations", label: "Plan the week", detail: "Sun" },
  { id: "c:5", group: "conversations", label: "Taxes", detail: "Sat" },
  { id: "c:6", group: "conversations", label: "Newsletter", detail: "Fri" },
];
const settings: PaletteItem[] = [
  { id: "s:memories", group: "settings", label: "Memories", detail: "Search long-term memory" },
  { id: "s:thoughts", group: "settings", label: "Thoughts", detail: "What he did when nobody asked" },
];

describe("scoreMatch", () => {
  it("prefers a prefix, then a word start, then anywhere, then a keyword", () => {
    const prefix = scoreMatch({ id: "x", group: "actions", label: "New chat" }, "new");
    const word = scoreMatch({ id: "x", group: "actions", label: "Start new chat" }, "new");
    const inside = scoreMatch({ id: "x", group: "actions", label: "Renewal" }, "new");
    const keyword = scoreMatch({ id: "x", group: "actions", label: "Talk live", keywords: ["voice"] }, "voi");
    assert.ok(prefix > word && word > inside && inside > keyword && keyword > 0, `${prefix} ${word} ${inside} ${keyword}`);
    assert.equal(scoreMatch({ id: "x", group: "actions", label: "Talk live" }, "zebra"), 0);
  });
  it("is case-insensitive and matches every word of the query", () => {
    assert.ok(scoreMatch({ id: "x", group: "conversations", label: "Run a half marathon" }, "HALF mara") > 0);
    assert.equal(scoreMatch({ id: "x", group: "conversations", label: "Run a half marathon" }, "half zebra"), 0);
  });
});

describe("buildPalette", () => {
  it("with nothing typed: recent conversations (five), then actions", () => {
    const p = buildPalette({ query: "", actions, conversations, settings });
    assert.deepEqual(p.groups.map((g) => g.id), ["recent", "actions"]);
    assert.equal(p.groups[0].items.length, 5);
    assert.equal(p.groups[0].items[0].id, "c:1", "most recent first");
    assert.equal(p.groups[0].label, "Recent");
    assert.equal(p.flat.length, 5 + actions.length);
  });

  it("with a query: only matches, and the best group leads", () => {
    const p = buildPalette({ query: "vet", actions, conversations, settings });
    assert.deepEqual(p.groups.map((g) => g.id), ["conversations"]);
    assert.equal(p.flat[0].id, "c:2");

    const q = buildPalette({ query: "new", actions, conversations, settings });
    assert.equal(q.groups[0].id, "actions", "New chat (prefix) outranks Newsletter? tie → actions first");
    assert.ok(q.flat.some((i) => i.id === "c:6"));
  });

  it("finds settings (goals went with unit G, 2026-10-11)", () => {
    const p = buildPalette({ query: "marathon", actions, conversations, settings });
    assert.deepEqual(p.flat.map((i) => i.id), []);
    const s = buildPalette({ query: "memor", actions, conversations, settings });
    assert.equal(s.flat[0].id, "s:memories");
  });

  it("labels groups for Ellis", () => {
    const p = buildPalette({ query: "a", actions, conversations, settings });
    const labels = Object.fromEntries(p.groups.map((g) => [g.id, g.label]));
    assert.equal(labels.actions, "Actions");
    assert.equal(labels.conversations, "Conversations");
    if (labels.settings) assert.equal(labels.settings, "Settings");
  });

  it("an empty result is empty", () => {
    const p = buildPalette({ query: "zzzz", actions, conversations, settings });
    assert.equal(p.flat.length, 0);
    assert.equal(p.groups.length, 0);
  });
});

describe("moveActive", () => {
  it("wraps both ways and survives an empty list", () => {
    assert.equal(moveActive(0, 1, 3), 1);
    assert.equal(moveActive(2, 1, 3), 0);
    assert.equal(moveActive(0, -1, 3), 2);
    assert.equal(moveActive(0, 1, 0), 0);
  });
});
