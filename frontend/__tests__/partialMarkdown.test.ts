// A table still being written (2026-09-26 review): while Edward streams, a
// table's header row arrives before its separator, and until the separator
// is complete GFM renders the row as literal pipes ("| Week | Miles | Long").
// A stopped answer kept them forever. The half-built table is held back
// while live and dropped once he was stopped.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { withoutUnfinishedTable } from "../lib/partialMarkdown.ts";

describe("withoutUnfinishedTable", () => {
  it("holds back a header row whose separator has not arrived", () => {
    const md = "Here is the plan.\n\n| Week | Miles | Long";
    assert.equal(withoutUnfinishedTable(md), "Here is the plan.");
  });

  it("and one whose separator is still incomplete", () => {
    assert.equal(withoutUnfinishedTable("Plan:\n\n| Week | Miles |\n|---|--"), "Plan:");
  });

  it("keeps a table once its separator is whole, even with a half row", () => {
    const md = "Plan:\n\n| Week | Miles |\n|---|---|\n| 1 | 2";
    assert.equal(withoutUnfinishedTable(md), md);
  });

  it("leaves everything else alone", () => {
    assert.equal(withoutUnfinishedTable("Just text."), "Just text.");
    assert.equal(withoutUnfinishedTable("a | b in a sentence"), "a | b in a sentence");
    assert.equal(withoutUnfinishedTable(""), "");
  });
});
