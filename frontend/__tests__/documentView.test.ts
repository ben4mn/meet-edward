// Settings → Memory → Documents (2026-09-26, Jarvis polish). Pure.
//
// - "Edward: state" and "Goal: …" documents are pinned above the rest.
// - A list preview is prose: markdown syntax stripped, whitespace folded.
// - Tag chips filter; the most used tags come first.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { docDisplay, docKind, docPreview, splitPinned, tagList, tagOptions } from "../lib/documentView.ts";

const d = (id: string, title: string) => ({ id, title });

describe("docKind", () => {
  it("knows the state document and goals", () => {
    assert.equal(docKind("Edward: state"), "state");
    assert.equal(docKind("Goal: Run the Springfield half"), "goal");
    assert.equal(docKind("Cabin weekend"), "doc");
    assert.equal(docKind("Goals for 2027"), "doc");
  });
});

describe("splitPinned", () => {
  it("pins the state document first, then goals, keeping order otherwise", () => {
    const { pinned, rest } = splitPinned([d("1", "Cabin"), d("2", "Goal: Half"), d("3", "Edward: state"), d("4", "Biscuit"), d("5", "Goal: Garage")]);
    assert.deepEqual(pinned.map((x) => x.id), ["3", "2", "5"]);
    assert.deepEqual(rest.map((x) => x.id), ["1", "4"]);
  });
});

describe("docDisplay", () => {
  it("drops the goal prefix and names the state page plainly", () => {
    assert.deepEqual(docDisplay("Goal: Run the Springfield half"), { title: "Run the Springfield half", badge: "Goal" });
    assert.deepEqual(docDisplay("Edward: state"), { title: "My state", badge: null });
    assert.deepEqual(docDisplay("Cabin weekend"), { title: "Cabin weekend", badge: null });
  });
});

describe("docPreview", () => {
  it("reads as prose", () => {
    assert.equal(
      docPreview("# Cabin weekend\n\nFri 17th – **Sun 19th**. [Map](https://x.y) `code`\n\n| Item | $ |\n|---|---:|\n| Cabin | 412 |\n- one\n> quote"),
      "Cabin weekend: Fri 17th – Sun 19th. Map code Item $ Cabin 412 one. quote",
    );
  });

  it("keeps headings and lists readable", () => {
    assert.equal(docPreview("## Why\nSub-2:00.\n\n## Plan\n- Long run Sunday\n- Tempo Wednesday\n"), "Why: Sub-2:00. Plan: Long run Sunday; Tempo Wednesday");
    assert.equal(docPreview("## Done?\nYes"), "Done? Yes");
    assert.equal(docPreview("## Watching\n- Biscuit's doses\n- Parker\n## Learned\nNotes after 9pm."), "Watching: Biscuit's doses; Parker. Learned: Notes after 9pm.");
  });

  it("clips on a word with an ellipsis", () => {
    const out = docPreview("word ".repeat(80), 40);
    assert.ok(out.length <= 41);
    assert.ok(out.endsWith("…"));
    assert.ok(!out.includes("  "));
    assert.equal(docPreview("alpha beta. gamma delta. epsilon zeta.", 30), "alpha beta. gamma delta…");
  });
});

describe("tags", () => {
  it("splits and trims", () => {
    assert.deepEqual(tagList(" trip, plans ,,pets"), ["trip", "plans", "pets"]);
    assert.deepEqual(tagList(null), []);
  });

  it("orders chips by use, then name, and drops housekeeping tags (state/self, goal statuses)", () => {
    assert.deepEqual(
      tagOptions({ trip: 2, pets: 5, biscuit: 2, state: 1, self: 1, goal: 3, active: 2, paused: 1 }).map((t) => t.tag),
      ["pets", "goal", "biscuit", "trip"],
    );
  });
});

import { opensWithTitle } from "../lib/documentView.ts";

describe("opensWithTitle", () => {
  it("is true when the document starts with its own top heading", () => {
    assert.equal(opensWithTitle("# Cabin weekend\n\nFri"), true);
    assert.equal(opensWithTitle("\n  # Cabin"), true);
    assert.equal(opensWithTitle("## Why\nSub-2"), false);
    assert.equal(opensWithTitle("I'm Edward."), false);
    assert.equal(opensWithTitle("#hashtag"), false);
  });
});
