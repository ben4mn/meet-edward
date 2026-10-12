// Reading Edward's thinking (2026-09-25). Ellis: "Edward shouldn't need my
// approval, but it must be obvious when he is choosing to take action."
// A wake that acts gets its own short thought conversation carrying
// `thought: {kind: "mind_action", ledger_id, reason, acted_at, outward,
// verdict}`. Opened, it reads as an entry — "Edward acted · messages wake ·
// 2h ago" — with a "Right call?" that posts /api/mind/verdict optimistically
// and rolls back when the post fails. Pure bits pinned here.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  beginVerdict,
  describeThought,
  initialVerdict,
  isMindAction,
  settleVerdict,
  shownVerdict,
  verdictLabel,
  verdictView,
  withThought,
  type ThoughtMeta,
} from "../lib/thoughtEntry.ts";

const NOW = new Date("2026-09-25T12:00:00Z");

function thought(over: Partial<ThoughtMeta> = {}): ThoughtMeta {
  return {
    kind: "mind_action",
    ledger_id: 42,
    reason: "messages",
    acted_at: "2026-09-25T10:00:00Z",
    outward: true,
    verdict: null,
    ...over,
  };
}

describe("isMindAction", () => {
  it("is true only for a mind_action thought", () => {
    assert.equal(isMindAction({ thought: thought() }), true);
    assert.equal(isMindAction({ thought: null }), false);
    assert.equal(isMindAction({}), false);
    assert.equal(isMindAction(null), false);
    assert.equal(isMindAction({ thought: { ...thought(), kind: "something_else" } as unknown as ThoughtMeta }), false);
  });
});

describe("describeThought", () => {
  it("says he acted, why he looked and when, with the mind panel's reason labels", () => {
    assert.equal(describeThought(thought(), { now: NOW }), "Edward acted · messages wake · 2h ago");
    assert.equal(describeThought(thought({ reason: "reflect" }), { now: NOW }), "Edward acted · Sunday reflection · 2h ago");
    assert.equal(describeThought(thought({ reason: "self" }), { now: NOW }), "Edward acted · a wake he asked for · 2h ago");
  });

  it("drops a missing time rather than printing it", () => {
    assert.equal(describeThought(thought({ acted_at: "" }), { now: NOW }), "Edward acted · messages wake");
  });

  // Entries from turns that are not wakes (pre-deploy review, 2026-09-27):
  // thought_journal.journal_unattended_turn sets the reason to the turn's
  // source, and the header read "Edward acted · heartbeat wake" — wrong
  // (it was no wake) and internal vocabulary on the one header meant to
  // make acting obvious.
  it("names a turn that was not a wake in words, never as a wake", () => {
    const expected: Record<string, string> = {
      scheduled_event: "a scheduled job",
      heartbeat: "a message that came in",
      twilio: "a text to his number",
      worker: "a helper's task",
      briefing: "a briefing",
      background: "background work",
    };
    for (const [reason, label] of Object.entries(expected)) {
      const line = describeThought(thought({ reason }), { now: NOW });
      assert.equal(line, `Edward acted · ${label} · 2h ago`);
      assert.doesNotMatch(line, /wake|_|heartbeat|twilio/i, reason);
    }
  });

  it("never shows a raw reason with underscores", () => {
    assert.doesNotMatch(describeThought(thought({ reason: "codex_exec" }), { now: NOW }), /_/);
  });
});

describe("verdictLabel", () => {
  it("reads back what Ellis said", () => {
    assert.equal(verdictLabel("right_call"), "You said: right call");
    assert.equal(verdictLabel("wrong_call"), "You said: wrong call");
  });
});

describe("the verdict, optimistic with rollback", () => {
  it("starts from what the backend already knows", () => {
    assert.equal(shownVerdict(initialVerdict(thought())), null);
    assert.equal(shownVerdict(initialVerdict(thought({ verdict: "wrong_call" }))), "wrong_call");
  });

  it("shows the new verdict at once and keeps it when the post succeeds", () => {
    const s1 = beginVerdict(initialVerdict(thought()), "right_call");
    assert.equal(shownVerdict(s1), "right_call");
    const view = verdictView(s1, false);
    assert.deepEqual(view, { mode: "said", label: "You said: right call", saving: true });
    const s2 = settleVerdict(s1, true);
    assert.equal(shownVerdict(s2), "right_call");
    assert.deepEqual(verdictView(s2, false), { mode: "said", label: "You said: right call", saving: false });
    assert.equal(s2.error, null);
  });

  it("rolls back to the previous verdict and says so when the post fails", () => {
    const s1 = beginVerdict(initialVerdict(thought({ verdict: "right_call" })), "wrong_call");
    assert.equal(shownVerdict(s1), "wrong_call");
    const s2 = settleVerdict(s1, false, "Failed to record the verdict");
    assert.equal(shownVerdict(s2), "right_call");
    assert.equal(s2.error, "Failed to record the verdict");
    const fresh = settleVerdict(beginVerdict(initialVerdict(thought()), "right_call"), false);
    assert.equal(shownVerdict(fresh), null);
    assert.ok(fresh.error, "a failure without a message still says it failed");
  });

  it("asks when there is no verdict yet, or when Ellis wants to change it", () => {
    assert.deepEqual(verdictView(initialVerdict(thought()), false), { mode: "ask" });
    assert.deepEqual(verdictView(initialVerdict(thought({ verdict: "right_call" })), true), { mode: "ask" });
  });
});

describe("withThought", () => {
  const listed = { id: "c1", title: "Sent Parker a note", thought: null as ThoughtMeta | null };
  const loaded = { id: "c1", title: "old title", thought: thought() };

  it("keeps the list's fields and takes the thought from the loaded conversation", () => {
    const merged = withThought(listed, loaded, "c1");
    assert.equal(merged?.title, "Sent Parker a note");
    assert.equal(merged?.thought?.ledger_id, 42);
  });

  it("falls back to the loaded conversation when the list does not have it", () => {
    // Opened from Today or the Mind panel while the sidebar shows "Yours".
    assert.equal(withThought(null, loaded, "c1")?.title, "old title");
  });

  it("ignores a loaded conversation that is not the one on screen", () => {
    assert.equal(withThought(null, { ...loaded, id: "other" }, "c1"), null);
    assert.equal(withThought(listed, { ...loaded, id: "other" }, "c1")?.thought, null);
    assert.equal(withThought(null, loaded, undefined), null);
  });
});
