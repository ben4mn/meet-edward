// R/G red for the chat sidebar's two-group filter.
//
// Seven source chips (All / Mine / Heartbeat / Scheduled / Inbound /
// Workers / Thoughts) became two: Yours and Edward's. Old persisted filter
// values in localStorage must map onto the new pair.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { SIDEBAR_FILTERS, matchesSidebarFilter, normalizeSidebarFilter } from "../components/chat/sidebarGroups.ts";

const conv = (source: string) => ({ source } as { source: string });

describe("SIDEBAR_FILTERS", () => {
  it("is Yours then Edward's", () => {
    assert.deepEqual(SIDEBAR_FILTERS.map((f) => f.value), ["yours", "edwards"]);
  });
});

describe("matchesSidebarFilter", () => {
  it("yours = your chats and inbound threads", () => {
    assert.equal(matchesSidebarFilter(conv("user"), "yours"), true);
    assert.equal(matchesSidebarFilter(conv("external_message"), "yours"), true);
    assert.equal(matchesSidebarFilter(conv("heartbeat"), "yours"), false);
    assert.equal(matchesSidebarFilter(conv("edward_thought"), "yours"), false);
  });
  it("edwards = everything he started", () => {
    for (const s of ["heartbeat", "scheduled_event", "edward_thought", "orchestrator_worker"]) {
      assert.equal(matchesSidebarFilter(conv(s), "edwards"), true, s);
    }
    assert.equal(matchesSidebarFilter(conv("user"), "edwards"), false);
  });
  it("treats a missing source as yours", () => {
    assert.equal(matchesSidebarFilter({} as { source?: string }, "yours"), true);
  });
});

describe("normalizeSidebarFilter", () => {
  it("maps legacy chip values onto the pair", () => {
    assert.equal(normalizeSidebarFilter("all"), "yours");
    assert.equal(normalizeSidebarFilter("user"), "yours");
    assert.equal(normalizeSidebarFilter("external_message"), "yours");
    assert.equal(normalizeSidebarFilter("heartbeat"), "edwards");
    assert.equal(normalizeSidebarFilter("edward_thought"), "edwards");
    assert.equal(normalizeSidebarFilter("edwards"), "edwards");
    assert.equal(normalizeSidebarFilter(null), "yours");
  });
});

import { groupEdwards, displayTitle } from "../components/chat/sidebarGroups.ts";

const c = (id: string, source: string, title: string, updated_at = "2026-09-06T01:00:00Z") =>
  ({ id, source, title, updated_at, channel: "text", notified_user: false, created_at: updated_at, message_count: 1 }) as never;

const action = (id: string, title: string, acted_at: string, over: Record<string, unknown> = {}) =>
  ({
    ...(c(id, "edward_thought", title) as object),
    thought: { kind: "mind_action", ledger_id: 1, reason: "messages", acted_at, outward: false, verdict: null },
    ...over,
  }) as never;

describe("groupEdwards", () => {
  const convs = [
    c("edward-surfaces", "edward_thought", "Edward's surfaces"),
    c("t1", "edward_thought", "Edward"),
    c("t2", "edward_thought", "3 pings while you were busy"),
    c("h1", "heartbeat", "iMessage: Ian Cho"),
    c("w1", "orchestrator_worker", "[CC] find address"),
    c("s1", "scheduled_event", "Remind Ellis about the vet"),
  ];
  // Updated deliberately 2026-09-25 (reading Edward's thinking): a wake
  // that acted is its own conversation with `thought.kind = "mind_action"`,
  // and those come first as "Actions". The old "Thoughts" section keeps its
  // key and its fold but reads "Older thoughts".
  it("sections in a fixed order: actions, surfaces, threads, workers, scheduled, older thoughts", () => {
    const g = groupEdwards([...convs, action("a1", "Sent Parker a note", "2026-09-06T00:30:00Z")]);
    assert.deepEqual(g.map((s) => s.key), ["actions", "surfaces", "threads", "workers", "scheduled", "thoughts"]);
    assert.deepEqual(g.map((s) => s.label), ["Actions", "Surfaces", "Threads", "Workers", "Scheduled", "Older thoughts"]);
    assert.deepEqual(g.find((s) => s.key === "thoughts")!.items.map((x) => x.id), ["t1", "t2"]);
    assert.deepEqual(g.find((s) => s.key === "surfaces")!.items.map((x) => x.id), ["edward-surfaces"]);
    assert.deepEqual(g.find((s) => s.key === "actions")!.items.map((x) => x.id), ["a1"]);
  });
  it("drops empty sections and marks thoughts collapsed by default", () => {
    const g = groupEdwards([c("h1", "heartbeat", "x")]);
    assert.deepEqual(g.map((s) => s.key), ["threads"]);
    const full = groupEdwards(convs);
    assert.equal(full.find((s) => s.key === "thoughts")!.collapsed, true);
    assert.equal(full.find((s) => s.key === "threads")!.collapsed, false);
  });
});

describe("displayTitle", () => {
  it("names a bare 'Edward' thought by its time instead", () => {
    assert.equal(displayTitle(c("t", "edward_thought", "Edward"), new Date("2026-09-06T03:00:00Z")), "Thought · 2h ago");
  });
  it("strips the iMessage: prefix from heartbeat threads", () => {
    assert.equal(displayTitle(c("h", "heartbeat", "iMessage: Ian Cho")), "Ian Cho");
  });
  it("leaves real titles alone", () => {
    assert.equal(displayTitle(c("u", "user", "Find the address")), "Find the address");
  });
});

describe("groupEdwards ordering", () => {
  it("orders threads by last message activity, newest first, not by updated_at", () => {
    const rows = [
      { ...c("old", "heartbeat", "iMessage: Quinn"), updated_at: "2026-09-06T03:18:00Z", last_activity_at: "2026-07-10T23:58:00Z" },
      { ...c("new", "heartbeat", "iMessage: Trivia night"), updated_at: "2026-09-06T02:44:00Z", last_activity_at: "2026-09-06T03:06:00Z" },
      { ...c("mid", "heartbeat", "iMessage: Sasha"), updated_at: "2026-09-06T03:29:00Z", last_activity_at: "2026-09-05T21:33:00Z" },
    ] as never[];
    const g = groupEdwards(rows as never);
    assert.deepEqual(g[0].items.map((x: { id: string }) => x.id), ["new", "mid", "old"]);
  });
  it("falls back to updated_at when there is no activity stamp", () => {
    const rows = [
      { ...c("a", "edward_thought", "A"), updated_at: "2026-09-06T01:00:00Z" },
      { ...c("b", "edward_thought", "B"), updated_at: "2026-09-06T02:00:00Z" },
    ] as never[];
    const g = groupEdwards(rows as never);
    assert.deepEqual(g[0].items.map((x: { id: string }) => x.id), ["b", "a"]);
  });
});

// Reading Edward's thinking (2026-09-25). Ellis: "it must be obvious when he
// is choosing to take action." Actions lead Edward's group, never folded,
// newest action first; one that reached someone says so.
import { actionMarker, sectionFor } from "../components/chat/sidebarGroups.ts";

describe("Actions", () => {
  it("puts a mind_action thought in Actions and leaves other thoughts in Older thoughts", () => {
    assert.equal(sectionFor(action("a", "Sent Parker a note", "2026-09-06T00:30:00Z")), "actions");
    assert.equal(sectionFor(c("t", "edward_thought", "3 pings while you were busy")), "thoughts");
  });

  it("is never collapsed and cannot be folded; older thoughts start folded", () => {
    const g = groupEdwards([action("a", "x", "2026-09-06T00:30:00Z"), c("t", "edward_thought", "y")]);
    const actions = g.find((s) => s.key === "actions")!;
    assert.equal(actions.collapsed, false);
    assert.equal(actions.collapsible, false);
    const older = g.find((s) => s.key === "thoughts")!;
    assert.equal(older.collapsed, true);
    assert.equal(older.collapsible, true);
  });

  it("orders actions by when he acted, newest first, not by a later reply", () => {
    const g = groupEdwards([
      action("early", "a", "2026-09-06T00:10:00Z", { updated_at: "2026-09-06T09:00:00Z" }),
      action("late", "b", "2026-09-06T00:50:00Z"),
      action("mid", "c", "2026-09-06T00:30:00Z"),
    ]);
    assert.deepEqual(g[0].items.map((x: { id: string }) => x.id), ["late", "mid", "early"]);
  });

  it("marks an action that reached someone", () => {
    assert.equal(actionMarker(action("a", "x", "2026-09-06T00:30:00Z")), null);
    const outward = action("b", "x", "2026-09-06T00:30:00Z", {
      thought: { kind: "mind_action", ledger_id: 2, reason: "mention", acted_at: "2026-09-06T00:30:00Z", outward: true, reached: true, verdict: null },
    });
    assert.equal(actionMarker(outward), "reached out");
    assert.equal(actionMarker(c("t", "edward_thought", "y")), null);
  });

  // L3 (2026-09-27 follow-ups): `outward` is true for any outward-tier
  // action, and that tier includes leave_note and a send held for Ellis's
  // approval — neither reached anyone. Journal entries now carry
  // thought.reached (thought_journal.reached_someone); the chip reads it.
  // Older entries come back with `reached: null` (or none): unknown, so no
  // chip — never a guess from `outward`, which is the very reading that
  // put "reached out" on notes (integration review, 2026-09-27).
  it("reads thought.reached: a note or a held send is not 'reached out'", () => {
    const meta = (extra: Record<string, unknown>) => ({
      thought: { kind: "mind_action", ledger_id: 3, reason: "morning", acted_at: "2026-09-06T00:30:00Z", verdict: null, ...extra },
    });
    assert.equal(actionMarker(action("n", "Left a note", "2026-09-06T00:30:00Z", meta({ outward: true, reached: false }))), null);
    assert.equal(actionMarker(action("s", "Replied to Parker", "2026-09-06T00:30:00Z", meta({ outward: true, reached: true }))), "reached out");
    assert.equal(actionMarker(action("o", "Older entry", "2026-09-06T00:30:00Z", meta({ outward: true }))), null, "unknown is not a guess from outward");
    assert.equal(actionMarker(action("u", "Older entry", "2026-09-06T00:30:00Z", meta({ outward: true, reached: null }))), null, "the API's null is unknown");
    assert.equal(actionMarker(action("q", "Older quiet entry", "2026-09-06T00:30:00Z", meta({ outward: false }))), null);
  });
});

describe("Surfaces", () => {
  it("keeps the mind, surfaces and research threads under Surfaces", () => {
    assert.equal(sectionFor(c("edward-mind", "edward_thought", "Edward's mind")), "surfaces");
    assert.equal(sectionFor(c("edward-research", "edward_thought", "whatever")), "surfaces");
    assert.equal(sectionFor(c("r2", "edward_thought", "Edward's research")), "surfaces");
    assert.equal(sectionFor(c("r3", "edward_thought", "Edward’s research")), "surfaces");
  });
});
