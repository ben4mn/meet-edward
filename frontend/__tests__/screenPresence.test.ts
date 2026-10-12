// Edward on /screen and the hub tells the truth (2026-09-26, Jarvis polish).
//
// Before: a timer put "consulting · long-term memory" under him once a
// minute whether or not anything happened, a running worker read as
// "thinking · running deep retrieval", and the tile said "EDWARD · v2.7".
// Now his mood and his one line come from real signals only — the display
// signals (voice, chat streams, workers) and /api/mind/now (a running wake,
// what waits on Ellis, the quota, his hands) — through lib/edwardMood.ts, and
// a glance is motion only: it never changes the line.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import type { MindNow } from "../lib/mindNowApi.ts";
import { EMPTY_MIND_NOW } from "../lib/mindNowApi.ts";
import type { SignalsResponse } from "../app/(display)/display/lib/types.ts";
import type { PlacedTile } from "../app/(display)/display/lib/screenModel.ts";
import {
  glanceCue,
  gazeToward,
  lastWakeCard,
  screenPresence,
} from "../app/(display)/display/lib/screenPresence.ts";

const NOW = new Date("2026-09-26T00:30:00Z"); // 19:30 in Springfield

function sig(over: Partial<SignalsResponse> = {}): SignalsResponse {
  return {
    pending_interrupt: null,
    voice_state: "idle",
    active_chat_streams: 0,
    active_orchestrator_tasks: 0,
    recent_alert_at: null,
    calendar_near_fire_at: null,
    fetched_at: NOW.toISOString(),
    ...over,
  };
}

function mind(over: Partial<MindNow> = {}): MindNow {
  return { ...EMPTY_MIND_NOW, ...over, quota: { ...EMPTY_MIND_NOW.quota, ...(over.quota ?? {}) }, hands: { ...EMPTY_MIND_NOW.hands, ...(over.hands ?? {}) } };
}

const at = (over: Partial<Parameters<typeof screenPresence>[0]> = {}) =>
  screenPresence({ signals: sig(), signalFailures: 0, mindNow: mind(), hour: 19, ...over }, { now: NOW, tz: "America/Chicago" });

describe("screenPresence: mood and line from real signals", () => {
  it("claims nothing before the first answer", () => {
    const p = screenPresence({ signals: null, signalFailures: 0, mindNow: null, hour: 19 });
    assert.equal(p.known, false);
    assert.equal(p.line, "");
    assert.equal(p.mood, "idle");
  });

  it("at rest he is resting — no timer-driven 'consulting memory'", () => {
    const p = at();
    assert.equal(p.mood, "idle");
    assert.equal(p.line, "Resting");
    assert.equal(p.active, false);
    assert.ok(!/consult|memory|retrieval|listening for context/i.test(p.line));
  });

  it("a running mind wake reads 'Thinking · morning wake'", () => {
    const p = at({ mindNow: mind({ waking: { reason: "morning", started_at: null, conversation_id: "edward-mind" } }) });
    assert.equal(p.mood, "think");
    assert.equal(p.line, "Thinking · morning wake");
    assert.equal(p.active, true);
    assert.equal(p.tone, "ok");
  });

  it("the surfaces pulse reads as thinking about my screens (first person)", () => {
    const p = at({ mindNow: mind({ waking: { reason: "surfaces", started_at: null, conversation_id: "edward-surfaces" } }) });
    assert.equal(p.line, "Thinking about my screens");
  });

  it("a chat turn streaming is thinking in a chat, not 'deep retrieval'", () => {
    const p = at({ signals: sig({ active_chat_streams: 1 }) });
    assert.equal(p.mood, "think");
    assert.equal(p.line, "Thinking · in a chat");
  });

  it("voice wins: speaking, then listening", () => {
    assert.equal(at({ signals: sig({ voice_state: "speaking", active_chat_streams: 1 }) }).line, "Speaking");
    assert.equal(at({ signals: sig({ voice_state: "listening" }) }).mood, "listen");
  });

  it("a running worker is work, counted honestly", () => {
    assert.equal(at({ signals: sig({ active_orchestrator_tasks: 1 }) }).line, "Working · 1 worker");
    const two = at({ signals: sig({ active_orchestrator_tasks: 2 }) });
    assert.equal(two.mood, "work");
    assert.equal(two.line, "Working · 2 workers");
  });

  it("a wake outranks a background worker, and so does something waiting on Ellis", () => {
    const waking = at({
      signals: sig({ active_orchestrator_tasks: 1 }),
      mindNow: mind({ waking: { reason: "mention", started_at: null, conversation_id: "edward-mind" } }),
    });
    assert.equal(waking.line, "Thinking · answering a mention");
    const waiting = at({ signals: sig({ active_orchestrator_tasks: 1 }), mindNow: mind({ approvals_waiting: 1 }) });
    assert.equal(waiting.mood, "attention");
    assert.equal(waiting.line, "Waiting on your approval");
    assert.equal(waiting.tone, "warn");
  });

  it("unread thoughts are attention", () => {
    const p = at({ mindNow: mind({ unread_thoughts: 2 }) });
    assert.equal(p.line, "Wants you · 2 new thoughts");
  });

  it("a spent quota is resting until the reset, amber not alarming", () => {
    const p = at({ mindNow: mind({ quota: { used_percent: 100, exhausted: true, resets_at: "2026-09-27T03:00:00Z" } }) });
    assert.equal(p.mood, "resting");
    assert.equal(p.line, "Resting until tomorrow 10:00 pm");
    assert.equal(p.tone, "warn");
    assert.match(p.label, /week's quota spent/);
  });

  it("his hands down is said plainly", () => {
    const p = at({ mindNow: mind({ hands: { connected: false, down_for_s: 240 } }) });
    assert.equal(p.line, "Resting · Chrome link down");
    assert.equal(p.tone, "warn");
  });

  it("late at night he is sleepy", () => {
    assert.equal(at({ hour: 2 }).mood, "sleepy");
  });

  it("two failed signal reads in a row is offline; one is not", () => {
    assert.equal(at({ signalFailures: 1 }).mood, "idle");
    const off = at({ signalFailures: 2 });
    assert.equal(off.mood, "offline");
    assert.equal(off.line, "Offline");
    assert.equal(off.tone, "mute");
  });

  it("a glance-worthy signal never changes the line", () => {
    const p = at({ signals: sig({ recent_alert_at: NOW.toISOString(), calendar_near_fire_at: NOW.toISOString() }) });
    assert.equal(p.line, "Resting");
    assert.equal(p.mood, "idle");
  });

  it("the label names him for screen readers", () => {
    assert.equal(at().label, "Edward · Resting");
  });
});

describe("glanceCue: something new arrived", () => {
  const t = NOW.getTime();

  it("never on the first read", () => {
    assert.equal(glanceCue(null, sig({ recent_alert_at: NOW.toISOString() }), t), null);
  });

  it("a fresh alert is a glance at the alerts", () => {
    const prev = sig();
    const next = sig({ recent_alert_at: new Date(t - 5_000).toISOString() });
    assert.equal(glanceCue(prev, next, t), "alerts");
  });

  it("an old alert that merely reappears is not", () => {
    const iso = new Date(t - 30 * 60_000).toISOString();
    assert.equal(glanceCue(sig(), sig({ recent_alert_at: iso }), t), null);
    assert.equal(glanceCue(sig({ recent_alert_at: iso }), sig({ recent_alert_at: iso }), t), null);
  });

  it("an event about to start is a glance at the calendar", () => {
    const soon = new Date(t + 4 * 60_000).toISOString();
    assert.equal(glanceCue(sig(), sig({ calendar_near_fire_at: soon }), t), "calendar");
    assert.equal(glanceCue(sig({ calendar_near_fire_at: soon }), sig({ calendar_near_fire_at: soon }), t), null);
  });
});

describe("gazeToward: he looks at the tile, wherever Edward put it", () => {
  const tile = (type: PlacedTile["type"], col: number, row: number, w: number, h: number): PlacedTile =>
    ({ type, size: "s", col, row, w, h });
  const edward = tile("edward", 2, 1, 4, 2);

  it("up at an alert strip above him", () => {
    assert.equal(gazeToward(edward, tile("alerts", 2, 0, 4, 1)), "up");
  });

  it("left at a calendar beside him", () => {
    assert.equal(gazeToward(edward, tile("calendar", 0, 1, 2, 2)), "left");
  });

  it("right and down too", () => {
    assert.equal(gazeToward(tile("edward", 0, 0, 2, 2), tile("calendar", 4, 0, 2, 2)), "right");
    assert.equal(gazeToward(tile("edward", 0, 0, 2, 2), tile("recents", 0, 3, 2, 1)), "down");
  });

  it("nowhere when either tile is missing", () => {
    assert.equal(gazeToward(edward, undefined), null);
    assert.equal(gazeToward(undefined, tile("alerts", 0, 0, 1, 1)), null);
  });
});

describe("lastWakeCard: what he did last, in his words", () => {
  const row = (over: Partial<MindNow["recent"][number]>) => ({
    at: "2026-09-26T00:18:00Z", reason: "messages", outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t-parker", ...over,
  });

  it("an acted wake shows his closing line and when", () => {
    const c = lastWakeCard(row({}), NOW);
    assert.ok(c);
    assert.equal(c!.body, "Replied to Parker about the flight.");
    assert.equal(c!.when, "12m ago");
  });

  it("a failed wake never shows the raw error", () => {
    const c = lastWakeCard(row({ outcome: "failed", line: "ValueError: Codex API error (HTTP 503)" }), NOW);
    assert.ok(c && !/Error|HTTP|Codex/.test(c.body), c?.body);
  });

  it("a skipped wake says why in plain words", () => {
    const c = lastWakeCard(row({ outcome: "skipped", line: "Codex quota exhausted" }), NOW);
    assert.ok(c && !/Codex|exhausted/.test(c.body), c?.body);
    const day = lastWakeCard(row({ outcome: "skipped", line: "today's Claude spend cap reached" }), NOW);
    assert.equal(day?.body, "I sat that one out — today's budget is spent.");
  });

  it("left alone without a line still says something true", () => {
    const c = lastWakeCard(row({ outcome: "left_alone", line: null }), NOW);
    assert.ok(c && c.body.length > 0);
  });

  it("nothing to show without a row", () => {
    assert.equal(lastWakeCard(null, NOW), null);
  });
});

// ---------------------------------------------------------------------------
// Review fixes (2026-09-26)
// ---------------------------------------------------------------------------

describe("his hands down are never hidden behind a busy line", () => {
  const handsDown = { hands: { connected: false, down_for_s: 240 } };

  it("a wake with the Chrome link dead says so, amber", () => {
    const p = at({ mindNow: mind({ ...handsDown, waking: { reason: "morning", started_at: null, conversation_id: "edward-mind" } }) });
    assert.equal(p.mood, "think");
    assert.equal(p.line, "Thinking · morning wake · Chrome link down");
    assert.equal(p.tone, "warn");
    assert.equal(p.handsDown, true);
  });

  it("a worker running with the link down says so too", () => {
    const p = at({ signals: sig({ active_orchestrator_tasks: 1 }), mindNow: mind(handsDown) });
    assert.equal(p.line, "Working · 1 worker · Chrome link down");
    assert.equal(p.tone, "warn");
  });

  it("at rest it is said once, not twice", () => {
    assert.equal(at({ mindNow: mind(handsDown) }).line, "Resting · Chrome link down");
  });

  it("offline outranks it: grey, and nothing about Chrome", () => {
    const p = at({ signalFailures: 2, mindNow: mind(handsDown) });
    assert.equal(p.line, "Offline");
    assert.equal(p.tone, "mute");
    assert.equal(p.handsDown, false);
  });

  it("with the link up there is nothing to flag", () => {
    assert.equal(at().handsDown, false);
  });
});

describe("the household hub never wears Ellis's private signals", () => {
  const house = (over: Partial<Parameters<typeof screenPresence>[0]> = {}) =>
    screenPresence(
      { signals: sig(), signalFailures: 0, mindNow: mind(), hour: 19, audience: "household", ...over },
      { now: NOW, tz: "America/Chicago" },
    );

  it("unread thoughts for Ellis are no amber 'wants you' at the pet station", () => {
    const p = house({ mindNow: mind({ unread_thoughts: 3 }) });
    assert.equal(p.mood, "idle");
    assert.doesNotMatch(p.label, /wants you|thought/i);
  });

  it("a send held for Ellis's approval is not the household's to see", () => {
    const p = house({ mindNow: mind({ approvals_waiting: 1 }) });
    assert.equal(p.mood, "idle");
    assert.doesNotMatch(p.label, /approval/i);
  });

  it("his Chrome link is not the household's business either", () => {
    const p = house({ mindNow: mind({ hands: { connected: false, down_for_s: 240 } }) });
    assert.equal(p.line, "Resting");
    assert.equal(p.handsDown, false);
  });

  it("what he is really doing still shows: a wake, speaking, a spent quota", () => {
    assert.equal(house({ mindNow: mind({ unread_thoughts: 2, waking: { reason: "morning", started_at: null, conversation_id: "edward-mind" } }) }).mood, "think");
    assert.equal(house({ signals: sig({ voice_state: "speaking" }) }).mood, "speak");
    assert.equal(house({ mindNow: mind({ quota: { used_percent: 100, exhausted: true, resets_at: null } }) }).mood, "resting");
  });

  it("Ellis's own screen still sees them", () => {
    assert.equal(at({ mindNow: mind({ unread_thoughts: 3 }) }).mood, "attention");
  });
});

describe("poking him: what he says is true", () => {
  it("idle: a nod and small talk", () => {
    assert.deepEqual(at().poke, { beat: "nod", say: null });
    assert.deepEqual(at({ hour: 2 }).poke, { beat: "nod", say: null });
  });

  it("offline: a wince, and he says he can't reach the server", () => {
    const p = at({ signalFailures: 2 }).poke;
    assert.equal(p.beat, "wince");
    assert.match(p.say ?? "", /can't reach/i);
    assert.doesNotMatch(p.say ?? "", /ready|still here/i);
  });

  it("before the first answer he claims nothing", () => {
    const p = screenPresence({ signals: null, signalFailures: 0, mindNow: null, hour: 19 }).poke;
    assert.notEqual(p.beat, "nod");
    assert.ok(p.say && !/ready|still here/i.test(p.say));
  });

  it("resting: when the quota comes back, and why", () => {
    const p = at({ mindNow: mind({ quota: { used_percent: 100, exhausted: true, resets_at: "2026-09-27T03:00:00Z" } }) }).poke;
    assert.equal(p.say, "Resting until tomorrow 10:00 pm — the week's quota is spent.");
    assert.notEqual(p.beat, "nod");
    const noReset = at({ mindNow: mind({ quota: { used_percent: 100, exhausted: true, resets_at: null } }) }).poke;
    assert.equal(noReset.say, "Resting — the week's quota is spent.");
  });

  it("resting at the Claude day's cap says the day's budget, back at midnight", () => {
    const p = at({ mindNow: mind({ quota: { used_percent: 104, exhausted: true, resets_at: "2026-09-26T05:00:00Z",
      period: "day" } }) }).poke;
    assert.equal(p.say, "Resting until tomorrow 12:00 am — today's budget is spent.");
    const noReset = at({ mindNow: mind({ quota: { used_percent: 104, exhausted: true, resets_at: null, period: "day" } }) }).poke;
    assert.equal(noReset.say, "Resting — today's budget is spent.");
  });

  it("waiting on Ellis: the waiting thing, not small talk", () => {
    assert.equal(at({ mindNow: mind({ approvals_waiting: 1 }) }).poke.say, "1 send is waiting for your approval.");
    assert.equal(at({ mindNow: mind({ approvals_waiting: 3 }) }).poke.say, "3 sends are waiting for your approval.");
    assert.equal(at({ mindNow: mind({ unread_thoughts: 1 }) }).poke.say, "1 new thought for you — it's in Chat.");
    assert.equal(at({ mindNow: mind({ unread_thoughts: 2 }) }).poke.say, "2 new thoughts for you — they're in Chat.");
    assert.notEqual(at({ mindNow: mind({ approvals_waiting: 1 }) }).poke.beat, "nod");
  });

  it("busy: what he is doing, in his own words, never 'Ready when you are'", () => {
    const wake = at({ mindNow: mind({ waking: { reason: "morning", started_at: null, conversation_id: "edward-mind" } }) }).poke;
    assert.equal(wake.say, "Morning wake — one moment.");
    assert.equal(wake.beat, null);
    const screens = at({ mindNow: mind({ waking: { reason: "surfaces", started_at: null, conversation_id: "edward-surfaces" } }) }).poke;
    assert.equal(screens.say, "Tidying my screens — one moment.");
    assert.equal(at({ signals: sig({ active_chat_streams: 1 }) }).poke.say, "In the middle of a chat — one moment.");
    assert.equal(at({ signals: sig({ voice_state: "speaking" }) }).poke.say, "Mid-sentence — one moment.");
    assert.equal(at({ signals: sig({ voice_state: "listening" }) }).poke.say, "I'm listening.");
  });

  it("a background worker leaves him free to answer", () => {
    const p = at({ signals: sig({ active_orchestrator_tasks: 2 }) }).poke;
    assert.equal(p.say, "2 workers are on it — I'm here.");
    assert.equal(p.beat, "nod");
  });

  it("hands down at rest: he says the browser is out, with a wince", () => {
    const p = at({ mindNow: mind({ hands: { connected: false, down_for_s: 240 } }) }).poke;
    assert.equal(p.beat, "wince");
    assert.match(p.say ?? "", /Chrome link is down/);
  });
});
