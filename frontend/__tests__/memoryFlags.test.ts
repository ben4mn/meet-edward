import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { flagsNeedingReview, isChoosable } from "../lib/memoryFlags.ts";

const base = {
  flag_type: "contradiction",
  description: "d",
  resolved: false,
  resolved_at: null,
  related_memory_id: "b",
  memory_id: "a",
};

describe("flagsNeedingReview", () => {
  it("keeps only open flags, newest first", () => {
    const out = flagsNeedingReview([
      { ...base, id: "old", created_at: "2026-09-01T00:00:00Z" },
      { ...base, id: "done", created_at: "2026-09-05T00:00:00Z", resolved: true },
      { ...base, id: "new", created_at: "2026-09-04T00:00:00Z" },
    ]);
    assert.deepEqual(out.map((f) => f.id), ["new", "old"]);
  });
});

describe("isChoosable", () => {
  it("needs a contradiction with both texts present", () => {
    assert.equal(isChoosable({ ...base, id: "x", created_at: "", memory_content: "A", related_memory_content: "B" }), true);
    assert.equal(isChoosable({ ...base, id: "x", created_at: "", memory_content: "A" }), false);
    assert.equal(isChoosable({ ...base, id: "x", created_at: "", flag_type: "stale", memory_content: "A", related_memory_content: "B" }), false);
  });
});

// ---------------------------------------------------------------------------
// The "Which is right?" sheet (2026-09-26, Jarvis polish). Contradiction
// notes on Today link to /settings/dreaming?flag=<id>; `&ask=rewrite` marks a
// proposed rewrite, which is not a keep-one choice.

import { flagChoices, flagDeepLink, flagHeadline, questionFor, flagKind } from "../lib/memoryFlags.ts";

const params = (q: string) => new URLSearchParams(q);

describe("flagDeepLink", () => {
  it("opens a flag on the review tab", () => {
    assert.deepEqual(flagDeepLink(params("flag=fl-1")), { flagId: "fl-1", rewrite: false, tab: "review" });
  });

  it("marks a proposed rewrite", () => {
    assert.deepEqual(flagDeepLink(params("flag=fl-9&ask=rewrite")), { flagId: "fl-9", rewrite: true, tab: "review" });
  });

  it("reads ?tab=flags and ?tab=connections", () => {
    assert.deepEqual(flagDeepLink(params("tab=flags")), { flagId: null, rewrite: false, tab: "review" });
    assert.deepEqual(flagDeepLink(params("tab=connections")), { flagId: null, rewrite: false, tab: "links" });
  });

  it("is empty without params (tests, SSR)", () => {
    assert.deepEqual(flagDeepLink(null), { flagId: null, rewrite: false, tab: null });
    assert.deepEqual(flagDeepLink(params("")), { flagId: null, rewrite: false, tab: null });
  });
});

describe("flagChoices", () => {
  const flag = { ...base, id: "fl-1", created_at: "", memory_content: "Carprofen 100mg twice a day", related_memory_content: "Carprofen 300mg at night" };

  it("offers keep this / keep that, mapped to the resolve sides", () => {
    assert.deepEqual(flagChoices(flag), [
      { keep: "a", label: "Keep this", text: "Carprofen 100mg twice a day" },
      { keep: "b", label: "Keep that", text: "Carprofen 300mg at night" },
    ]);
  });

  it("offers nothing to keep for a rewrite or a one-sided flag", () => {
    assert.deepEqual(flagChoices(flag, { rewrite: true }), []);
    assert.deepEqual(flagChoices({ ...flag, related_memory_content: null }), []);
  });
});

describe("flagHeadline", () => {
  const f = (flag_type: string, extra: Record<string, unknown> = {}) => ({ ...base, id: "x", created_at: "", flag_type, ...extra });
  it("says what the question is, in plain words", () => {
    assert.equal(flagHeadline(f("contradiction", { memory_content: "A", related_memory_content: "B" })), "Which is right?");
    assert.equal(flagHeadline(f("contradiction")), "Two memories disagree");
    assert.equal(flagHeadline(f("contradiction", { memory_content: "A", related_memory_content: "B" }), { rewrite: true }), "Reword this?");
    assert.equal(flagHeadline(f("stale")), "Might be out of date");
    assert.equal(flagHeadline(f("low_quality")), "Too vague to keep?");
    assert.equal(flagHeadline(f("schema_confusion")), "Schema confusion");
  });

  it("names the kind for the list chip", () => {
    assert.equal(flagKind("contradiction"), "Disagree");
    assert.equal(flagKind("stale"), "Stale");
    assert.equal(flagKind("low_quality"), "Vague");
    assert.equal(flagKind("duplicate"), "Duplicate");
  });
});

describe("questionFor", () => {
  const notes = [
    { href: "/settings/dreaming?flag=fl-10", body: "Not this one" },
    { href: "/settings/dreaming?flag=fl-1&ask=rewrite", body: "  Is Biscuit's Carprofen 100mg twice a day, or 300mg in the evening?  " },
    { href: "/today", body: "Other" },
  ];
  it("finds the question Edward asked on Today, by exact flag id", () => {
    assert.equal(questionFor("fl-1", notes), "Is Biscuit's Carprofen 100mg twice a day, or 300mg in the evening?");
    assert.equal(questionFor("fl-10", notes), "Not this one");
    assert.equal(questionFor("fl-2", notes), null);
    assert.equal(questionFor("fl-1", []), null);
  });
});

import { flagNote } from "../lib/memoryFlags.ts";

describe("flagNote", () => {
  it("knows when Today's note asked for a rewrite", () => {
    const notes = [
      { href: "/settings/dreaming?flag=fl-3&ask=rewrite", body: "I have a memory that says “x”. Should it say “y” instead?" },
      { href: "/settings/dreaming?flag=fl-1", body: "Which one?" },
    ];
    assert.deepEqual(flagNote("fl-3", notes), { question: "I have a memory that says “x”. Should it say “y” instead?", rewrite: true });
    assert.deepEqual(flagNote("fl-1", notes), { question: "Which one?", rewrite: false });
    assert.equal(flagNote("fl-9", notes), null);
  });
});

describe("nextDreamText", () => {
  const now = new Date("2026-09-26T18:00:00Z");
  it("says when the next dream is due", async () => {
    const { nextDreamText } = await import("../lib/memoryFlags.ts");
    assert.equal(nextDreamText("2026-09-26T18:22:00Z", { now }), "next in 22m");
  });
  it("never says 'next 10m ago' when the loop is late", async () => {
    const { nextDreamText } = await import("../lib/memoryFlags.ts");
    assert.equal(nextDreamText("2026-09-26T17:50:00Z", { now }), "next any minute");
    assert.equal(nextDreamText("2026-09-26T18:00:30Z", { now }), "next any minute");
    assert.equal(nextDreamText(null, { now }), null);
  });
});

describe("connectionPhrase", () => {
  it("says what a link means in plain words", async () => {
    const { connectionPhrase } = await import("../lib/memoryFlags.ts");
    assert.equal(connectionPhrase("elaborates"), "adds detail to");
    assert.equal(connectionPhrase("same_person"), "is about the same person as");
    assert.equal(connectionPhrase("same_topic"), "is on the same topic as");
    assert.equal(connectionPhrase("same_event"), "is about the same event as");
    assert.equal(connectionPhrase("related"), "is related to");
    assert.equal(connectionPhrase("something_new"), "is linked to");
  });
});
