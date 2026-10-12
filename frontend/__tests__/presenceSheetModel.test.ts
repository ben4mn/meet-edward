// R/G red for components/layout/presenceSheetModel.ts — what the presence
// sheet says (tap Edward anywhere). Plain words, no internal vocabulary
// (design system §6): no raw wake reasons, no exception names, no "triage".

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  outcomePill,
  wakeRowText,
  wakeRowMeta,
  doingNow,
  formatElapsed,
  nextWakeText,
  quotaText,
  handsText,
  approvalsText,
  wakeButton,
  offlineNow,
  nextWakeLine,
} from "../components/layout/presenceSheetModel.ts";
import { readFileSync } from "node:fs";

const NOW = new Date("2026-09-26T18:00:00Z");
const TZ = "America/Chicago";

describe("outcomePill", () => {
  it("names each outcome in plain words with an honest tone", () => {
    assert.deepEqual(outcomePill("acted"), { label: "Acted", tone: "edward" });
    assert.deepEqual(outcomePill("left_alone"), { label: "Left it", tone: "neutral" });
    assert.deepEqual(outcomePill("failed"), { label: "Didn't finish", tone: "bad" });
    assert.deepEqual(outcomePill("skipped"), { label: "Skipped", tone: "warn" });
    assert.deepEqual(outcomePill("something_new"), { label: "Something new", tone: "neutral" });
    assert.equal(outcomePill(null), null);
  });
});

describe("wakeRowText", () => {
  it("is his closing line when he has one", () => {
    assert.equal(
      wakeRowText({ at: "", reason: "messages", outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t" }),
      "Replied to Parker about the flight.",
    );
  });
  it("never shows an exception to Ellis", () => {
    assert.equal(
      wakeRowText({ at: "", reason: "evening", outcome: "failed", line: "ValueError: HTTP 503", thought_id: null }),
      "I couldn't finish — the model didn't answer.",
    );
    assert.equal(
      wakeRowText({ at: "", reason: "evening", outcome: "failed", line: null, thought_id: null }),
      "I couldn't finish — the model didn't answer.",
    );
  });
  it("says why a wake was skipped without jargon", () => {
    assert.equal(
      wakeRowText({ at: "", reason: "self", outcome: "skipped", line: "Codex quota exhausted", thought_id: null }),
      "Skipped — the week's quota is spent.",
    );
    assert.equal(
      wakeRowText({ at: "", reason: "self", outcome: "skipped", line: "today's Claude spend cap reached", thought_id: null }),
      "Skipped — today's budget is spent.",
    );
  });
  it("fills in a quiet look", () => {
    assert.equal(
      wakeRowText({ at: "", reason: "free", outcome: "left_alone", line: null, thought_id: null }),
      "Looked around; nothing needed you.",
    );
  });
});

describe("wakeRowMeta", () => {
  it("speaks of a finished wake in the past tense (2026-09-26)", () => {
    const row = (reason: string) => ({ at: "2026-09-26T17:00:00Z", reason, outcome: "acted", line: null, thought_id: null });
    assert.equal(wakeRowMeta(row("messages"), NOW), "Read your messages · 1h ago");
    assert.equal(wakeRowMeta(row("mention"), NOW), "Answered a mention · 1h ago");
    assert.equal(wakeRowMeta(row("self"), NOW), "Followed up on something · 1h ago");
    assert.equal(wakeRowMeta(row("manual"), NOW), "You woke me · 1h ago");
    assert.equal(wakeRowMeta(row("research"), NOW), "Researched something · 1h ago");
  });

  it("is the reason in words and how long ago", () => {
    assert.equal(
      wakeRowMeta({ at: "2026-09-26T16:00:00Z", reason: "morning", outcome: "acted", line: null, thought_id: null }, NOW),
      "Morning wake · 2h ago",
    );
    assert.equal(
      wakeRowMeta({ at: "2026-09-26T17:58:30Z", reason: "mystery", outcome: "acted", line: null, thought_id: null }, NOW),
      "Woke up · 2m ago",
    );
  });
});

describe("formatElapsed / doingNow", () => {
  it("formats elapsed time compactly", () => {
    assert.equal(formatElapsed("2026-09-26T17:59:40Z", NOW), "20s");
    assert.equal(formatElapsed("2026-09-26T17:57:00Z", NOW), "3m");
    assert.equal(formatElapsed("2026-09-26T16:55:00Z", NOW), "1h 5m");
    assert.equal(formatElapsed("nonsense", NOW), null);
  });
  it("says what he is doing now, or that nothing is running", () => {
    assert.deepEqual(
      doingNow({ reason: "morning", started_at: "2026-09-26T17:57:00Z", conversation_id: "edward-mind" }, NOW),
      { title: "Morning wake", detail: "For 3m", active: true },
    );
    assert.deepEqual(
      doingNow({ reason: "surfaces", started_at: null, conversation_id: "edward-surfaces" }, NOW),
      { title: "Tidying my screens", detail: null, active: true },
    );
    assert.deepEqual(doingNow(null, NOW), { title: "Nothing running right now", detail: null, active: false });
  });
});

describe("nextWakeText / quotaText / handsText / approvalsText", () => {
  it("next wake reads like a clock", () => {
    assert.equal(nextWakeText("2026-09-27T12:00:00Z", { now: NOW, tz: TZ }), "tomorrow 7:00 am");
    assert.equal(nextWakeText(null, { now: NOW, tz: TZ }), null);
  });
  it("quota: used share and when it resets; spent says so", () => {
    assert.deepEqual(
      quotaText({ used_percent: 41, exhausted: false, resets_at: "2026-09-27T02:30:00Z" }, { now: NOW, tz: TZ }),
      { percent: 41, text: "41% of the week used · resets 9:30 pm", tone: "neutral" },
    );
    assert.deepEqual(
      quotaText({ used_percent: 100, exhausted: true, resets_at: "2026-09-29T20:00:00Z" }, { now: NOW, tz: TZ }),
      { percent: 100, text: "The week's quota is spent · back Tue 3:00 pm", tone: "warn" },
    );
    assert.equal(quotaText({ used_percent: null, exhausted: false, resets_at: null }, { now: NOW, tz: TZ }), null);
    // The Claude API: the day's spend cap, back at Ellis's midnight.
    assert.deepEqual(
      quotaText({ used_percent: 34, exhausted: false, resets_at: "2026-09-27T05:00:00Z", period: "day" }, { now: NOW, tz: TZ }),
      { percent: 34, text: "34% of today's budget used · resets tomorrow 12:00 am", tone: "neutral" },
    );
    assert.equal(
      quotaText({ used_percent: 104, exhausted: true, resets_at: "2026-09-27T05:00:00Z", period: "day" }, { now: NOW, tz: TZ })?.text,
      "Today's budget is spent · back tomorrow 12:00 am",
    );
  });
  it("hands: connected, down for a while, or unknown (hidden)", () => {
    assert.deepEqual(handsText({ connected: true, down_for_s: null }), { text: "Connected to your Chrome", tone: "ok" });
    assert.deepEqual(handsText({ connected: false, down_for_s: 95 }), { text: "Link to your Chrome down for 2m", tone: "warn" });
    assert.deepEqual(handsText({ connected: false, down_for_s: null }), { text: "Not connected to your Chrome", tone: "warn" });
    assert.equal(handsText({ connected: null, down_for_s: null }), null);
  });
  it("approvals: nothing when none wait", () => {
    assert.equal(approvalsText(0), null);
    assert.equal(approvalsText(1), "1 message is waiting for your approval");
    assert.equal(approvalsText(3), "3 messages are waiting for your approval");
  });
});

describe("offline (2026-09-26)", () => {
  it("says it can't reach the Mac, and when it last heard, instead of claiming nothing runs", () => {
    assert.deepEqual(offlineNow("2026-09-26T17:52:00Z", NOW), {
      title: "I can't reach the Mac",
      detail: "Last heard from it 8m ago · trying again",
    });
    assert.deepEqual(offlineNow(null, NOW), { title: "I can't reach the Mac", detail: "Trying again" });
  });

  it("offers no wake it cannot deliver", () => {
    assert.deepEqual(wakeButton({ waking: false, pending: false, online: false }), {
      label: "Can't reach him",
      disabled: true,
      busy: false,
    });
  });
});

describe("wakeButton", () => {
  it("offers a wake when he is resting", () => {
    assert.deepEqual(wakeButton({ waking: false, pending: false }), { label: "Wake him now", disabled: false, busy: false });
  });
  it("is optimistic while the request is in flight", () => {
    assert.deepEqual(wakeButton({ waking: false, pending: true }), { label: "Waking…", disabled: true, busy: true });
  });
  it("says he is already awake while a wake runs", () => {
    assert.deepEqual(wakeButton({ waking: true, pending: false }), { label: "He's awake", disabled: true, busy: false });
  });
});

// Review 2026-09-26 (quota-rest-contradicts-next-look, extra-loops-in-transcript):
// a spent week has no next wake and no live Wake button, and only his
// breath loops on the sheet.
describe("a spent week (quota exhausted)", () => {
  const spent = { used_percent: 100, exhausted: true, resets_at: "2026-09-29T20:00:00Z" };
  it("the Wake button rests until the reset instead of a Wake that silently reverts", () => {
    assert.deepEqual(wakeButton({ waking: false, pending: false, online: true, quotaSpentUntil: "Tue 3:00 pm" }), {
      label: "Resting until Tue 3:00 pm",
      disabled: true,
      busy: false,
    });
    assert.deepEqual(wakeButton({ waking: false, pending: false, online: true, quotaSpentUntil: "" }), {
      label: "Resting · quota spent",
      disabled: true,
      busy: false,
    });
    // Offline still says the Mac can't be reached.
    assert.equal(wakeButton({ waking: false, pending: false, online: false, quotaSpentUntil: "Tue 3:00 pm" }).label, "Can't reach him");
  });
  it("no 'Next wake' while the week is spent or the Mac is unreachable", () => {
    const opts = { now: NOW, tz: TZ };
    assert.equal(nextWakeLine({ next_wake_at: "2026-09-26T20:00:00Z", quota: spent }, true, opts), null);
    assert.equal(nextWakeLine({ next_wake_at: "2026-09-26T20:00:00Z", quota: { ...spent, exhausted: false } }, false, opts), null);
    assert.equal(
      nextWakeLine({ next_wake_at: "2026-09-26T20:00:00Z", quota: { ...spent, exhausted: false } }, true, opts),
      "3:00 pm",
    );
    assert.equal(nextWakeLine(null, true, opts), null);
  });
  it("the 'Right now' dot is still: only his breath loops on the sheet", () => {
    const src = readFileSync(new URL("../components/layout/PresenceSheet.tsx", import.meta.url), "utf8");
    assert.equal(/animate-breathe|animate-pulse|animate-ping/.test(src), false);
  });
});
