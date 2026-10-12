// R/G red: the conversation list's rows (components/chat/sidebarGroups.ts,
// 2026-09-26 chat chrome).
//
// Yours is grouped by date — Today / Yesterday / This week / Older, the
// last seven days open; each row carries a compact time, a quiet mark for
// where it came from, and can be deleted only when deleting it loses
// nothing of Edward's (his long-lived threads and his journal entries are
// never offered). Touch deletes by swiping the row left.

import { describe, it } from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import path from "node:path";

import {
  PROTECTED_IDS,
  canDelete,
  dateBucket,
  groupByDate,
  rowTime,
  sectionFor,
  sourceMark,
  swipeSettle,
  drawerDragCloses,
} from "../components/chat/sidebarGroups.ts";

const TZ = "America/Chicago";
// Sat 26 Sep 2026, 15:00 in Springfield.
const NOW = new Date("2026-09-26T20:00:00Z");

const c = (id: string, over: Record<string, unknown> = {}) =>
  ({
    id,
    source: "user",
    channel: "text",
    title: id,
    notified_user: false,
    created_at: "2026-09-26T19:00:00Z",
    updated_at: "2026-09-26T19:00:00Z",
    message_count: 2,
    ...over,
  }) as never;

describe("dateBucket", () => {
  it("today, in Ellis's zone (not UTC's)", () => {
    assert.equal(dateBucket("2026-09-26T13:00:00Z", NOW, TZ), "today");
    // 00:30 UTC on the 26th is still the 25th in Springfield.
    assert.equal(dateBucket("2026-09-26T00:30:00Z", NOW, TZ), "yesterday");
  });
  it("yesterday, this week (the rest of the last seven days), older", () => {
    assert.equal(dateBucket("2026-09-25T15:00:00Z", NOW, TZ), "yesterday");
    assert.equal(dateBucket("2026-09-21T15:00:00Z", NOW, TZ), "week");
    assert.equal(dateBucket("2026-09-20T15:00:00Z", NOW, TZ), "week");
    assert.equal(dateBucket("2026-09-19T15:00:00Z", NOW, TZ), "older");
    assert.equal(dateBucket("2026-06-01T15:00:00Z", NOW, TZ), "older");
  });
  it("a naive timestamp is UTC (lib/time contract)", () => {
    assert.equal(dateBucket("2026-09-26T13:00:00", NOW, TZ), "today");
  });
});

describe("groupByDate", () => {
  it("groups in order with the last seven days open and Older folded", () => {
    const g = groupByDate(
      [
        c("old", { updated_at: "2026-08-01T12:00:00Z" }),
        c("today", { updated_at: "2026-09-26T18:00:00Z" }),
        c("yday", { updated_at: "2026-09-25T18:00:00Z" }),
        c("week", { updated_at: "2026-09-22T18:00:00Z" }),
      ],
      NOW,
      TZ,
    );
    assert.deepEqual(g.map((s) => s.label), ["Today", "Yesterday", "This week", "Older"]);
    assert.deepEqual(g.map((s) => s.collapsed), [false, false, false, true]);
    assert.deepEqual(g.map((s) => s.items.map((x: { id: string }) => x.id)), [["today"], ["yday"], ["week"], ["old"]]);
  });
  it("drops empty groups", () => {
    const g = groupByDate([c("a", { updated_at: "2026-09-26T18:00:00Z" })], NOW, TZ);
    assert.deepEqual(g.map((s) => s.key), ["today"]);
  });
  it("orders a thread by its latest message when it has one", () => {
    const g = groupByDate(
      [
        c("a", { updated_at: "2026-09-26T19:30:00Z", last_activity_at: "2026-09-26T14:00:00Z" }),
        c("b", { updated_at: "2026-09-26T15:00:00Z" }),
      ],
      NOW,
      TZ,
    );
    assert.deepEqual(g[0].items.map((x: { id: string }) => x.id), ["b", "a"]);
  });
});

describe("rowTime", () => {
  it("a clock time today, 'Yesterday', a weekday this week, a date before that", () => {
    assert.equal(rowTime("2026-09-26T14:05:00Z", NOW, TZ), "9:05 am");
    assert.equal(rowTime("2026-09-25T14:05:00Z", NOW, TZ), "Yesterday");
    assert.equal(rowTime("2026-09-22T14:05:00Z", NOW, TZ), "Tue");
    assert.equal(rowTime("2026-09-03T14:05:00Z", NOW, TZ), "Sep 3");
  });
  it("another year says so", () => {
    assert.equal(rowTime("2025-12-24T14:05:00Z", NOW, TZ), "Dec 24, 2025");
  });
  it("garbage in, nothing out", () => {
    assert.equal(rowTime("not a date", NOW, TZ), "");
  });
});

describe("canDelete", () => {
  it("Ellis's own chats and inbound threads can go", () => {
    assert.equal(canDelete(c("u1")), true);
    assert.equal(canDelete(c("i1", { source: "external_message" })), true);
    assert.equal(canDelete(c("h1", { source: "heartbeat" })), true);
  });
  it("never Edward's long-lived threads", () => {
    for (const id of ["edward-mind", "edward-surfaces", "edward-research", "edward-free-time"]) {
      assert.equal(PROTECTED_IDS.has(id), true, id);
      assert.equal(canDelete(c(id, { source: "edward_thought" })), false, id);
    }
  });
  it("never a thought-journal entry (a wake that acted)", () => {
    const entry = c("t-reply-parker", {
      source: "edward_thought",
      thought: { kind: "mind_action", ledger_id: 3, reason: "mention", acted_at: "2026-09-26T18:00:00Z", outward: true, verdict: null },
    });
    assert.equal(canDelete(entry), false);
  });
  it("his free-time thread is pinned with the others", () => {
    assert.equal(sectionFor(c("edward-free-time", { source: "edward_thought", title: "Edward's free time" })), "surfaces");
    assert.equal(sectionFor(c("x", { source: "edward_thought", title: "Edward's free time" })), "surfaces");
  });
});

describe("sourceMark", () => {
  it("Ellis's own chats carry no mark", () => {
    assert.equal(sourceMark(c("u")), null);
  });
  it("inbound threads are a neutral mark, labelled by channel", () => {
    assert.deepEqual(sourceMark(c("i", { source: "external_message", channel: "sms" })), { kind: "inbound", label: "Text" });
    assert.deepEqual(sourceMark(c("w", { source: "external_message", channel: "whatsapp" })), { kind: "inbound", label: "WhatsApp" });
    assert.deepEqual(sourceMark(c("h", { source: "heartbeat", channel: "imessage" })), { kind: "inbound", label: "iMessage" });
  });
  it("what Edward started is his (emerald) mark", () => {
    assert.deepEqual(sourceMark(c("t", { source: "edward_thought" })), { kind: "edward", label: "Edward" });
    assert.deepEqual(sourceMark(c("s", { source: "scheduled_event" })), { kind: "edward", label: "Scheduled" });
    assert.deepEqual(sourceMark(c("o", { source: "orchestrator_worker" })), { kind: "edward", label: "Worker" });
  });
  it("a voice chat is Ellis's, marked as voice", () => {
    assert.deepEqual(sourceMark(c("v", { channel: "voice" })), { kind: "voice", label: "Voice" });
  });
});

describe("swipeSettle (touch delete)", () => {
  const W = 88; // the Delete action's width
  it("a short drag springs back closed", () => {
    assert.equal(swipeSettle(-20, 0, W), "closed");
  });
  it("past half the action's width, or a flick left, stays open", () => {
    assert.equal(swipeSettle(-50, 0, W), "open");
    assert.equal(swipeSettle(-10, -600, W), "open");
  });
  it("a flick right closes even from open", () => {
    assert.equal(swipeSettle(-70, 700, W), "closed");
  });
  it("a long drag past twice the width is a full swipe: delete", () => {
    assert.equal(swipeSettle(-2.2 * W, 0, W), "delete");
  });

  // L14 (2026-09-27 follow-ups): rename was desktop-only (the "⋯" menu).
  // On touch the swipe now shows Rename beside Delete, so the row opens to
  // both actions and a full swipe to delete runs one action further.
  it("with two actions it opens to both, and a full swipe still deletes", () => {
    assert.equal(swipeSettle(-60, 0, W, 2), "closed", "short of half the two actions");
    assert.equal(swipeSettle(-100, 0, W, 2), "open");
    assert.equal(swipeSettle(-2.2 * W, 0, W, 2), "open", "opening both actions is not a delete");
    assert.equal(swipeSettle(-3.1 * W, 0, W, 2), "delete");
    assert.equal(swipeSettle(-10, -600, W, 2), "open");
    assert.equal(swipeSettle(-150, 700, W, 2), "closed");
  });

  // 2026-09-27 review: with Rename beside Delete a full swipe needed 264 px
  // (both buttons plus one more). The drawer is 86vw, at most 340 px, so a
  // row is about 306 px on a 375 pt iPhone and a leftward drag stops at the
  // screen's edge: only a finger starting in the row's last ~50 px could
  // still delete in one gesture. The full swipe now scales with the row —
  // past both buttons by at least 24 px and 65 % of the row, never further
  // than it was.
  it("on a phone-width row a full swipe fits under the finger", () => {
    assert.equal(swipeSettle(-205, 0, W, 2, 306), "delete", "a 375 pt iPhone's row");
    assert.equal(swipeSettle(-190, 0, W, 2, 306), "open", "just past both buttons still only opens");
    assert.equal(swipeSettle(-215, 0, W, 2, 319), "delete", "a 390 pt iPhone's row");
    assert.equal(swipeSettle(-205, 0, W, 2, 319), "open");
    // From about two thirds of the way across any phone row.
    for (let row = 300; row <= 340; row += 5) {
      assert.equal(swipeSettle(-Math.ceil(0.7 * row), 0, W, 2, row), "delete", `a ${row}px row`);
    }
  });

  it("the full swipe is never within 24 px of open, and never further than one button past it", () => {
    assert.equal(swipeSettle(-199, 0, W, 2, 120), "open", "a narrow row: 24 px past both buttons");
    assert.equal(swipeSettle(-200, 0, W, 2, 120), "delete");
    assert.equal(swipeSettle(-263, 0, W, 2, 900), "open", "a wide row: capped");
    assert.equal(swipeSettle(-264, 0, W, 2, 900), "delete");
    assert.equal(swipeSettle(-263, 0, W, 2), "open", "no width measured: as before");
    assert.equal(swipeSettle(-264, 0, W, 2), "delete");
  });

  it("the drawer's rows pass their measured width", () => {
    const src = readFileSync(path.resolve(__dirname, "../components/chat/ChatSidebar.tsx"), "utf8");
    assert.match(src, /swipeSettle\(\s*x\.get\(\),\s*info\.velocity\.x,\s*ACTION_W,\s*SWIPE_ACTIONS,\s*rowRef\.current\?\.offsetWidth/);
  });
});

describe("drawerDragCloses", () => {
  it("a drag a third of the way or a flick left closes the drawer", () => {
    assert.equal(drawerDragCloses(-120, 0, 320), true);
    assert.equal(drawerDragCloses(-20, -700, 320), true);
  });
  it("a small drag, or a drag right, does not", () => {
    assert.equal(drawerDragCloses(-40, -100, 320), false);
    assert.equal(drawerDragCloses(30, 800, 320), false);
  });
});

// Switching Yours ↔ Edward's refetches the list; until it lands the list
// still holds the other group's rows. That in-between state is loading,
// not "nothing here" — the empty line is a claim and must be true.
import { listPhase } from "../components/chat/sidebarGroups.ts";

describe("listPhase", () => {
  it("rows of this group: rows", () => {
    assert.equal(listPhase(12, 5), "rows");
  });
  it("rows, but none of this group yet: loading (the switch is in flight)", () => {
    assert.equal(listPhase(12, 0), "loading");
  });
  it("nothing at all: empty", () => {
    assert.equal(listPhase(0, 0), "empty");
  });
});
