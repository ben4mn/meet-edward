// R/G red for the verified forget in the Memory room (plan Phase 5).
//
// Preview first, then a delete of exactly what was shown, then counts and
// a re-check. The panel only has to say those numbers in words.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  forgetPreviewSummary,
  forgetResultSummary,
  hasAnythingToForget,
  linkedRowCount,
  type ForgetPlan,
  type ForgetResult,
} from "../lib/forget.ts";

function plan(over: Partial<ForgetPlan> = {}): ForgetPlan {
  return {
    id: "p1",
    subject: "Alex",
    terms: ["alex"],
    memories: [
      { id: "m1", content: "Alex loves bouldering", why: "mentions alex" },
      { id: "m2", content: "Alex's birthday is May 3", why: "mentions alex" },
    ],
    documents: [{ id: "d1", title: "Notes on Alex", why: "title" }],
    protected_documents: [],
    related: { enrichments: 1, connections: 1, flags: 0, entity_mentions: 2, entities: 1, graph_queue: 0 },
    conversations: { count: 1, titles: ["Planning Alex's party"] },
    ...over,
  };
}

describe("forget preview", () => {
  it("counts what would go", () => {
    assert.equal(linkedRowCount(plan().related), 5);
    assert.equal(forgetPreviewSummary(plan(), false), "2 memories and 5 linked rows would be deleted. 1 document mentions it (kept).");
    assert.equal(forgetPreviewSummary(plan(), true), "2 memories, 1 document and 5 linked rows would be deleted.");
  });
  it("says when nothing matches", () => {
    const empty = plan({ memories: [], documents: [], related: { enrichments: 0, connections: 0, flags: 0, entity_mentions: 0, entities: 0, graph_queue: 0 } });
    assert.equal(forgetPreviewSummary(empty, false), "Nothing matches “Alex”.");
    assert.equal(hasAnythingToForget(empty, false), false);
    assert.equal(hasAnythingToForget(plan({ memories: [] }), false), true, "linked rows alone are worth deleting");
    assert.equal(hasAnythingToForget(plan({ memories: [], related: empty.related }), true), true);
  });
});

describe("forget result", () => {
  const result = (over: Partial<ForgetResult> = {}): ForgetResult => ({
    subject: "Alex",
    plan_id: "p1",
    deleted: { memories: 2, documents: 0, enrichments: 1, connections: 1, flags: 0, entity_mentions: 2, entities: 1, graph_queue: 0 },
    still_matching: { memories: 0, documents: 1 },
    protected_documents: [],
    conversations_mentioning: 1,
    ...over,
  });
  it("reports counts and the re-check", () => {
    assert.equal(
      forgetResultSummary(result()),
      "Deleted 2 memories and 5 linked rows. Checked again: no memories still match; 1 document still mentions it. 1 conversation still mentions it — delete it from the sidebar if you want it gone.",
    );
  });
  it("is plain when everything went", () => {
    assert.equal(
      forgetResultSummary(result({ still_matching: { memories: 0, documents: 0 }, conversations_mentioning: 0, deleted: { memories: 1, documents: 1, enrichments: 0, connections: 0, flags: 0, entity_mentions: 0, entities: 0, graph_queue: 0 } })),
      "Deleted 1 memory and 1 document. Checked again: nothing still matches.",
    );
  });
});
