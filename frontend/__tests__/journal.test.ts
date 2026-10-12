// The weekly journal (Settings → Mind → Journal, 2026-09-26): what Edward
// noticed about himself this week, in words — never "tool_failure" or
// "triage_mismatch" on a Ellis-facing surface.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { observationKindLabel, observationMeta, outcomeSummary } from "../lib/journal.ts";

describe("observationKindLabel", () => {
  it("names each kind of observation in words", () => {
    assert.equal(observationKindLabel("tool_failure"), "Tools that failed");
    assert.equal(observationKindLabel("user_correction"), "You corrected me");
    assert.equal(observationKindLabel("triage_mismatch"), "Messages I misjudged");
    assert.equal(observationKindLabel("triage"), "Messages I misjudged");
    assert.equal(observationKindLabel("push_ignored"), "Pushes you didn't open");
    assert.equal(observationKindLabel("push_outcome"), "How my pushes landed");
    assert.equal(observationKindLabel("push_acted"), "Pushes you acted on");
    assert.equal(observationKindLabel("something_new"), "Something new");
  });
});

describe("observationMeta", () => {
  const now = new Date("2026-09-26T18:00:00Z");
  it("says how often, whether it is still open, and when last", () => {
    assert.equal(
      observationMeta({ occurrences: 4, status: "open", last_seen_at: "2026-09-26T05:00:00Z" }, now),
      "4 times · still happening · last 13h ago",
    );
    assert.equal(
      observationMeta({ occurrences: 1, status: "superseded", last_seen_at: null }, now),
      "once · settled",
    );
  });
});

describe("outcomeSummary", () => {
  it("reads the week's wakes as a sentence", () => {
    assert.equal(
      outcomeSummary([{ outcome: "acted", count: 5 }, { outcome: "left_alone", count: 14 }, { outcome: "failed", count: 1 }]),
      "Woke 20 times: acted on 5, left 14 alone, 1 failed.",
    );
    assert.equal(outcomeSummary([]), "I haven't woken this week.");
    assert.equal(outcomeSummary([{ outcome: "acted", count: 1 }]), "Woke once: acted on 1.");
  });
});
