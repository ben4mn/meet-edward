// Phase 11 of the mind rebuild: Ellis's window on what Edward thought,
// intended and did. The panel only draws what this module decides —
// grouping by day, folding quiet wakes, the one-line meta per wake, the
// next fire on an intention, and which verdict buttons are still open.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  describeIntention,
  describeWake,
  groupWakesByDay,
  VERDICT_OPTIONS,
  type MindIntention,
  type MindWake,
  wakeSourceLabel,
} from "../lib/mindLog.ts";

const NOW = new Date("2026-09-18T18:00:00Z");

function wake(over: Partial<MindWake> = {}): MindWake {
  return {
    id: 1,
    at: "2026-09-18T17:00:00Z",
    source: "evening",
    outcome: "acted",
    note: "Pushed Ellis about ACME.",
    tools: ["send_push_notification"],
    surfaces: [],
    delivered: true,
    turn_id: "t1",
    pushes: [],
    verdict: null,
    ...over,
  };
}

describe("groupWakesByDay", () => {
  it("groups newest day first and folds a run of left-alone wakes into one row", () => {
    const days = groupWakesByDay(
      [
        wake({ id: 5, at: "2026-09-18T17:00:00Z" }),
        wake({ id: 4, at: "2026-09-18T12:00:00Z", outcome: "left_alone", note: "Nothing." , tools: [] }),
        wake({ id: 3, at: "2026-09-18T07:00:00Z", outcome: "left_alone", note: "Quiet.", tools: [] }),
        wake({ id: 2, at: "2026-09-17T18:00:00Z", outcome: "acted" }),
        wake({ id: 1, at: "2026-09-17T07:00:00Z", outcome: "failed", note: "TimeoutError" }),
      ],
      { now: NOW, timeZone: "UTC" },
    );
    assert.deepEqual(days.map((d) => d.label), ["Today", "Yesterday"]);
    const today = days[0].rows;
    assert.equal(today.length, 2);
    assert.equal(today[0].kind, "wake");
    assert.equal(today[1].kind, "quiet");
    assert.equal(today[1].count, 2, "two left-alone wakes fold into one quiet row");
    assert.deepEqual(today[1].ids, [4, 3]);
    const yesterday = days[1].rows;
    assert.deepEqual(yesterday.map((r) => r.kind), ["wake", "wake"], "a failed wake never folds");
  });

  it("keeps a single left-alone wake as its own row", () => {
    const days = groupWakesByDay([wake({ outcome: "left_alone", tools: [] })], { now: NOW, timeZone: "UTC" });
    assert.equal(days[0].rows[0].kind, "wake");
  });

  it("returns nothing for nothing", () => {
    assert.deepEqual(groupWakesByDay([], { now: NOW, timeZone: "UTC" }), []);
  });
});

describe("describeWake", () => {
  it("names the trigger, what it did and what came of it", () => {
    const line = describeWake(
      wake({ pushes: [{ id: "p1", title: "ACME", opened: true }], verdict: { verdict: "right_call", note: null } }),
      { now: NOW, timeZone: "UTC" },
    );
    // The verdict is the Segment under the line now, not repeated in it.
    assert.equal(line, "evening wake · 1h ago · pushed, opened");
  });
  it("says what a failed or skipped wake was", () => {
    assert.equal(
      describeWake(wake({ outcome: "failed", note: "TimeoutError", tools: [] }), { now: NOW, timeZone: "UTC" }),
      "evening wake · 1h ago · failed",
    );
    assert.equal(
      describeWake(wake({ outcome: "skipped", note: "quota exhausted", tools: [] }), { now: NOW, timeZone: "UTC" }),
      "evening wake · 1h ago · skipped",
    );
  });
  it("counts tools when nothing was pushed", () => {
    assert.equal(
      describeWake(wake({ tools: ["remember", "leave_note"] }), { now: NOW, timeZone: "UTC" }),
      "evening wake · 1h ago · 2 tools",
    );
  });
});

describe("describeIntention", () => {
  const base: MindIntention = {
    id: "i1",
    content: "Watching ACME for Ellis's bonus",
    created_at: "2026-09-16T18:00:00Z",
    valid_until: "2026-10-18T00:00:00Z",
    event_id: "e1",
    next_fire_at: "2026-09-20T09:00:00Z",
    next_fire_why: "see whether ACME recovered",
  };
  it("shows the next fire and the horizon", () => {
    assert.equal(
      describeIntention(base, { now: NOW, timeZone: "UTC" }),
      "since 2d ago · next look in 2d: see whether ACME recovered · until Oct 18",
    );
  });
  it("says when nothing is scheduled", () => {
    assert.equal(
      describeIntention({ ...base, event_id: null, next_fire_at: null, next_fire_why: null, valid_until: null },
        { now: NOW, timeZone: "UTC" }),
      "since 2d ago · no look scheduled",
    );
  });
});

describe("VERDICT_OPTIONS", () => {
  it("offers right call and wrong call, in that order", () => {
    assert.deepEqual(VERDICT_OPTIONS.map((o) => o.value), ["right_call", "wrong_call"]);
  });
});

describe("wakeSourceLabel", () => {
  it("names the surplus wakes in words", () => {
    assert.equal(wakeSourceLabel("reflect_lite"), "nightly reflection");
    assert.equal(wakeSourceLabel("prep"), "meeting prep");
    assert.equal(wakeSourceLabel("research"), "research");
    assert.equal(wakeSourceLabel("someday"), "someday wake");
  });
});

// Reading Edward's thinking (2026-09-25): a wake that acted leaves a short
// thought conversation, and its log row carries `thought_id`. The panel
// links to it — a URL, never loadConversation from a click (CLAUDE.md
// "Chat URL is the source of truth").
import { thoughtHref } from "../lib/mindLog.ts";

describe("thoughtHref", () => {
  it("links a wake to his thinking when it left an entry", () => {
    assert.equal(thoughtHref(wake({ thought_id: "abc-123" })), "/chat?c=abc-123");
    assert.equal(thoughtHref(wake({ thought_id: "a b&c" })), "/chat?c=a%20b%26c");
  });
  it("has no link for a wake without one", () => {
    assert.equal(thoughtHref(wake()), null);
    assert.equal(thoughtHref(wake({ thought_id: null })), null);
    assert.equal(thoughtHref(wake({ thought_id: "" })), null);
  });
});

// 2026-09-26 (Jarvis polish): "Wake now" no longer blocks — POST
// /api/mind/wake answers 202 at once and the wake's row appears in the log
// when it is done. The panel shows an optimistic "Waking…" row and polls
// the log until a wake it has not seen lands. Ledger ids only grow, so
// "new" is "an id I did not have when you pressed", never a clock compare
// (the phone's clock and the mini's disagree).
import { canJudge, landedWake, outcomeLabel } from "../lib/mindLog.ts";

describe("landedWake", () => {
  const before = [wake({ id: 10, source: "manual" }), wake({ id: 9, source: "evening" })];
  const known = new Set(before.map((w) => w.id));
  it("is nothing while only the wakes you already had are in the log", () => {
    assert.equal(landedWake(before, known), null);
  });
  it("is the new manual wake once it lands", () => {
    const landed = landedWake([wake({ id: 11, source: "manual", outcome: "left_alone" }), ...before], known);
    assert.equal(landed?.id, 11);
  });
  it("ignores another reason's new wake unless the press joined a running one", () => {
    const log = [wake({ id: 11, source: "messages" }), ...before];
    assert.equal(landedWake(log, known), null);
    assert.equal(landedWake(log, known, { joined: true })?.id, 11);
  });
});

describe("the wake vocabulary", () => {
  it("names free time", () => {
    assert.equal(wakeSourceLabel("free"), "free time");
  });
  it("labels each outcome in words", () => {
    assert.equal(outcomeLabel("acted"), "Acted");
    assert.equal(outcomeLabel("left_alone"), "Left alone");
    assert.equal(outcomeLabel("failed"), "Failed");
    assert.equal(outcomeLabel("skipped"), "Skipped");
  });
  it("asks for a verdict only on a wake that decided something", () => {
    assert.equal(canJudge(wake({ outcome: "acted" })), true);
    assert.equal(canJudge(wake({ outcome: "left_alone" })), true);
    assert.equal(canJudge(wake({ outcome: "failed" })), false);
    assert.equal(canJudge(wake({ outcome: "skipped" })), false);
  });
});

// A failed wake's note is an exception string ("ValueError: HTTP 503");
// the title says what happened in his words and the raw text goes under it.
import { wakeTitle } from "../lib/mindLog.ts";

describe("wakeTitle", () => {
  it("is his closing line when he left one", () => {
    assert.equal(wakeTitle(wake({ note: "Replied to Parker." })), "Replied to Parker.");
  });
  it("says what happened, in his words, when there is no line or it is an error", () => {
    assert.equal(wakeTitle(wake({ outcome: "failed", note: "ValueError: HTTP 503" })), "I couldn't finish this wake.");
    assert.equal(wakeTitle(wake({ outcome: "skipped", note: "Codex quota exhausted" })), "I skipped this wake — the week's quota was spent.");
    assert.equal(wakeTitle(wake({ outcome: "skipped", note: null })), "I skipped this wake.");
    assert.equal(wakeTitle(wake({ outcome: "skipped", note: "today's Claude spend cap reached" })),
      "I skipped this wake — today's budget was spent.");
    assert.equal(wakeTitle(wake({ outcome: "left_alone", note: null })), "Nothing needed me.");
    assert.equal(wakeTitle(wake({ outcome: "acted", note: "" })), "I acted.");
  });
});

// The row's outcome is a pill now, so the meta line under it must not say
// it again ("Left alone · … · left alone"); and an intention leads with
// when he will look next, not with a paraphrase of itself.
import { intentionMeta, intentionNextLook, wakeMeta } from "../lib/mindLog.ts";

describe("wakeMeta", () => {
  const o = { now: NOW, timeZone: "UTC" };
  it("names the trigger and when, without repeating the outcome", () => {
    assert.equal(wakeMeta(wake({ outcome: "left_alone", tools: [] }), o), "evening wake · 1h ago");
    assert.equal(wakeMeta(wake({ outcome: "failed", tools: [] }), o), "evening wake · 1h ago");
    assert.equal(wakeMeta(wake({ source: "free", outcome: "skipped" }), o), "free time · 1h ago");
  });
  it("says what an acting wake did", () => {
    assert.equal(wakeMeta(wake({ tools: ["remember", "leave_note"] }), o), "evening wake · 1h ago · 2 tools");
    // A wake that pushed shows the push itself under the meta line.
    assert.equal(wakeMeta(wake({ pushes: [{ id: "p", title: "t", opened: false }] }), o), "evening wake · 1h ago");
  });
});

describe("intentionNextLook / intentionMeta", () => {
  const base: MindIntention = {
    id: "i1", content: "Watching ACME", created_at: "2026-09-16T18:00:00Z", valid_until: "2026-10-18T00:00:00Z",
    event_id: "e1", next_fire_at: "2026-09-20T09:00:00Z", next_fire_why: "see whether ACME recovered",
  };
  const o = { now: NOW, timeZone: "UTC" };
  it("leads with the next look", () => {
    assert.equal(intentionNextLook(base, o), "Next look in 2d");
    assert.equal(intentionNextLook({ ...base, next_fire_at: null }, o), "No look scheduled");
  });
  it("keeps since and until as quiet meta", () => {
    assert.equal(intentionMeta(base, o), "since 2d ago · until Oct 18");
    assert.equal(intentionMeta({ ...base, valid_until: null }, o), "since 2d ago");
  });
});
