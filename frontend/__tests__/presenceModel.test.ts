// Edward's presence, the pure half (lib/presenceModel.ts): which real
// signals feed his mood, when the mind/now poll runs, and which one-shot
// beats a change earns. The provider (lib/presence.tsx) only owns timers,
// the DOM and fetch around these.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  presenceSignals,
  pollDelayMs,
  isOnline,
  turnBeat,
  mindBeat,
  admitBeat,
  releaseHeld,
  shouldPerk,
  MIND_NOW_POLL_MS,
  NEW_GATE,
  MIND_NOW_CACHE_KEY,
  MIND_NOW_CACHE_MAX_AGE_MS,
  encodeMindNowCache,
  hydrateMindNowCache,
  presenceAnnouncement,
} from "../lib/presenceModel.ts";
import { startTurn, reduceTurn, endTurn, type Turn } from "../lib/turnModel.ts";
import { normalizeMindNow, EMPTY_MIND_NOW } from "../lib/mindNowApi.ts";
import { deriveMood, statusLabel, statusLine } from "../lib/edwardMood.ts";

const T0 = 1_000;

function turnWith(events: Parameters<typeof reduceTurn>[1][]): Turn {
  let t = startTurn("a1", "c1", T0);
  events.forEach((e, i) => {
    t = reduceTurn(t, e, T0 + i + 1);
  });
  return t;
}

const MIND = normalizeMindNow({
  waking: null,
  last: { at: "2026-09-26T14:00:00Z", reason: "morning", outcome: "left_alone", line: null, thought_id: null },
  recent: [],
  approvals_waiting: 0,
  unread_thoughts: 0,
  quota: { used_percent: 40, exhausted: false, resets_at: "2026-09-29T09:00:00Z" },
  hands: { connected: true, down_for_s: null },
});

const base = { chat: null, online: true, mindNow: null, hour: 14 };

describe("presenceSignals", () => {
  it("with nothing going on he is idle and resting", () => {
    const s = presenceSignals(base);
    assert.equal(s.online, true);
    assert.equal(s.turn, null);
    assert.equal(s.voice, null);
    assert.equal(s.mindWaking, null);
    assert.equal(deriveMood(s), "idle");
    assert.equal(statusLine(s), "Resting");
  });

  it("a live chat turn is his phase and running tools", () => {
    const turn = turnWith([{ type: "tool_start", tool_name: "web_search" }]);
    const s = presenceSignals({ ...base, chat: { turn, isVoiceMode: false, voicePhase: null } });
    assert.deepEqual(s.turn, { phase: "working", runningTools: 1, toolsUsed: 1 });
    assert.equal(deriveMood(s), "work");
    assert.equal(statusLine(s), "Working · 1 tool");
  });

  it("'Working · N tools' counts the tools used this turn, not only the one running (review: it always read 1)", () => {
    const turn = turnWith([
      { type: "tool_start", tool_name: "web_search" },
      { type: "tool_end", tool_name: "web_search", result: "3 results" },
      { type: "tool_start", tool_name: "browser_open" },
      { type: "tool_end", tool_name: "browser_open", result: "Tool error: link down" },
      { type: "tool_start", tool_name: "web_search" },
    ]);
    const s = presenceSignals({ ...base, chat: { turn, isVoiceMode: false, voicePhase: null } });
    assert.equal(s.turn?.runningTools, 1);
    assert.equal(s.turn?.toolsUsed, 3);
    assert.equal(statusLine(s), "Working · 3 tools");
  });

  it("an ended turn is no claim at all", () => {
    const turn = endTurn(turnWith([{ type: "content", content: "hi" }]), "done", T0 + 9);
    const s = presenceSignals({ ...base, chat: { turn, isVoiceMode: false, voicePhase: null } });
    assert.equal(s.turn, null);
    assert.equal(deriveMood(s), "idle");
  });

  it("voice mode is his voice phase; no claim while the phase is unknown", () => {
    const voice = (voicePhase: string | null) =>
      presenceSignals({ ...base, chat: { turn: null, isVoiceMode: true, voicePhase: voicePhase as never } }).voice;
    assert.equal(voice("speaking"), "speaking");
    assert.equal(voice("thinking"), "thinking");
    assert.equal(voice("listening"), "listening");
    assert.equal(voice(null), null, "the view has not reported: no claim (it reports connecting on mount)");
    assert.equal(voice("idle"), "listening");
    assert.equal(voice("connecting"), null, "not listening until the session is up");
    assert.equal(voice("error"), null);
    assert.equal(
      presenceSignals({ ...base, chat: { turn: null, isVoiceMode: false, voicePhase: "speaking" } }).voice,
      null,
      "a phase left over after voice mode ended is no claim",
    );
  });

  it("a wake running now is thinking with its reason", () => {
    const mindNow = normalizeMindNow({ ...MIND, waking: { reason: "morning", started_at: "x", conversation_id: "edward-mind" } });
    const s = presenceSignals({ ...base, mindNow });
    assert.deepEqual(s.mindWaking, { reason: "morning" });
    assert.equal(statusLine(s), "Thinking · morning wake");
  });

  it("what waits on Ellis, the quota and his hands come from mind/now", () => {
    const mindNow = normalizeMindNow({
      ...MIND,
      approvals_waiting: 2,
      unread_thoughts: 1,
      quota: { used_percent: 100, exhausted: true, resets_at: "2026-09-29T09:00:00Z" },
      hands: { connected: false, down_for_s: 300 },
    });
    const s = presenceSignals({ ...base, mindNow });
    assert.equal(s.approvalsWaiting, 2);
    assert.equal(s.unreadThoughts, 1);
    assert.equal(s.quotaExhausted, true);
    assert.equal(s.quotaResetsAt, "2026-09-29T09:00:00Z");
    assert.equal(s.handsDown, true);
    assert.equal(deriveMood(s), "attention");
  });

  it("unknown hands are not down", () => {
    const s = presenceSignals({ ...base, mindNow: EMPTY_MIND_NOW });
    assert.equal(s.handsDown, false);
  });

  it("offline outranks everything", () => {
    const turn = turnWith([{ type: "content", content: "hi" }]);
    const s = presenceSignals({ ...base, online: false, chat: { turn, isVoiceMode: false, voicePhase: null } });
    assert.equal(deriveMood(s), "offline");
  });

  it("carries the hour for the night pose", () => {
    assert.equal(deriveMood(presenceSignals({ ...base, hour: 2 })), "sleepy");
  });
});

describe("before the first answer", () => {
  it("makes no claim: no line, a bare name, not resting", () => {
    const s = presenceSignals({ ...base, known: false });
    assert.equal(s.known, false);
    assert.equal(deriveMood(s), "idle");
    assert.equal(statusLine(s), "");
    assert.equal(statusLabel(s), "Edward");
  });

  it("an answer (even an empty one) is known", () => {
    assert.equal(presenceSignals({ ...base, known: true }).known, true);
    assert.equal(statusLine(presenceSignals({ ...base, mindNow: EMPTY_MIND_NOW })), "Resting");
  });
});

describe("presenceAnnouncement (what the bar's live region says)", () => {
  it("names his mood, never a running-tool count that changes every second", () => {
    const turn = turnWith([{ type: "tool_start", tool_name: "web_search" }]);
    const s = presenceSignals({ ...base, chat: { turn, isVoiceMode: false, voicePhase: null } });
    assert.equal(statusLine(s), "Working · 1 tool");
    assert.equal(presenceAnnouncement(s), "Edward · Working");
  });
  it("is the status label otherwise; a bare name before the first answer", () => {
    assert.equal(presenceAnnouncement(presenceSignals(base)), "Edward · Resting");
    const mindNow = normalizeMindNow({ ...MIND, approvals_waiting: 2 });
    assert.equal(presenceAnnouncement(presenceSignals({ ...base, mindNow })), "Edward · Waiting on your approval · 2");
    assert.equal(presenceAnnouncement(presenceSignals({ ...base, known: false })), "Edward");
  });
});

describe("the mind/now cache (the last answer, for a cold open)", () => {
  const NOW = Date.parse("2026-09-26T15:00:00Z");
  const withApproval = normalizeMindNow({ ...MIND, approvals_waiting: 2 });

  it("has a versioned key and a ten-minute shelf life", () => {
    assert.equal(MIND_NOW_CACHE_KEY, "edward:mindNow:v1");
    assert.equal(MIND_NOW_CACHE_MAX_AGE_MS, 10 * 60_000);
  });

  it("round-trips a fresh answer", () => {
    const raw = encodeMindNowCache(withApproval, NOW - 60_000);
    assert.equal(hydrateMindNowCache(raw, NOW)?.approvals_waiting, 2);
  });

  it("is nothing when missing, garbage, too old, or from the future", () => {
    assert.equal(hydrateMindNowCache(null, NOW), null);
    assert.equal(hydrateMindNowCache("{not json", NOW), null);
    assert.equal(hydrateMindNowCache(JSON.stringify({ at: "yesterday", data: withApproval }), NOW), null);
    assert.equal(hydrateMindNowCache(encodeMindNowCache(withApproval, NOW - 11 * 60_000), NOW), null);
    assert.equal(hydrateMindNowCache(encodeMindNowCache(withApproval, NOW + 5 * 60_000), NOW), null);
  });

  it("never claims a wake is still running from an answer more than a minute old", () => {
    const waking = normalizeMindNow({ ...MIND, waking: { reason: "morning", started_at: null, conversation_id: null } });
    assert.equal(hydrateMindNowCache(encodeMindNowCache(waking, NOW - 30_000), NOW)?.waking?.reason, "morning");
    const old = hydrateMindNowCache(encodeMindNowCache(waking, NOW - 3 * 60_000), NOW);
    assert.equal(old?.waking, null);
    assert.equal(old?.last?.reason, "morning", "the rest of the answer stays");
  });
});

describe("polling", () => {
  it("every 20 s while it answers", () => {
    assert.equal(MIND_NOW_POLL_MS, 20_000);
    assert.equal(pollDelayMs(0), 20_000);
  });

  it("backs off exponentially after failures, from a quick first retry, to at most five minutes", () => {
    const delays = [1, 2, 3, 4, 5, 6, 7, 8, 12].map(pollDelayMs);
    assert.equal(delays[0], 5_000, "one hiccup is retried soon");
    for (let i = 1; i < delays.length; i++) assert.ok(delays[i] >= delays[i - 1]);
    assert.equal(delays[1], 10_000);
    assert.equal(delays[delays.length - 1], 300_000);
  });

  it("takes other timings (tests, a slower screen)", () => {
    assert.equal(pollDelayMs(0, { pollMs: 50, retryMs: 10 }), 50);
    assert.equal(pollDelayMs(1, { pollMs: 50, retryMs: 10 }), 10);
    assert.equal(pollDelayMs(3, { pollMs: 50, retryMs: 10 }), 40);
    assert.equal(pollDelayMs(20, { pollMs: 50, retryMs: 10, maxMs: 100 }), 100);
  });

  it("offline when the browser says so, or after two failed polls in a row", () => {
    assert.equal(isOnline(true, 0), true);
    assert.equal(isOnline(true, 1), true, "one failed poll is a hiccup, not a claim");
    assert.equal(isOnline(true, 2), false);
    assert.equal(isOnline(false, 0), false);
  });
});

describe("turnBeat", () => {
  it("a tool that succeeded nods; one that failed winces", () => {
    const running = turnWith([{ type: "tool_start", tool_name: "web_search" }]);
    const ok = reduceTurn(running, { type: "tool_end", tool_name: "web_search", result: "3 results" }, T0 + 50);
    const bad = reduceTurn(running, { type: "tool_end", tool_name: "web_search", result: "Tool error: timeout" }, T0 + 50);
    assert.equal(turnBeat(running, ok), "nod");
    assert.equal(turnBeat(running, bad), "wince");
  });

  it("a send that went out is acted", () => {
    const running = turnWith([{ type: "tool_start", tool_name: "send_imessage" }]);
    const sent = reduceTurn(running, { type: "tool_end", tool_name: "send_imessage", result: "Sent to Parker" }, T0 + 50);
    assert.equal(turnBeat(running, sent), "acted");
  });

  it("a turn that ends well nods; one that errors winces; a stop is quiet", () => {
    const live = turnWith([{ type: "content", content: "hi" }]);
    assert.equal(turnBeat(live, endTurn(live, "done", T0 + 9)), "nod");
    assert.equal(turnBeat(live, endTurn(live, "error", T0 + 9)), "wince");
    assert.equal(turnBeat(live, endTurn(live, "stopped", T0 + 9)), null);
  });

  it("a turn that ran out of tool iterations is no nod", () => {
    const live = turnWith([{ type: "progress", step: "tool_loop", status: "exhausted" }]);
    assert.equal(turnBeat(live, endTurn(live, "done", T0 + 9)), null);
  });

  it("nothing new, no beat (reasoning deltas, the same object, no turn)", () => {
    const live = turnWith([{ type: "content", content: "hi" }]);
    assert.equal(turnBeat(live, live), null);
    assert.equal(turnBeat(live, reduceTurn(live, { type: "reasoning", content: "**Hm**" }, T0 + 9)), null);
    assert.equal(turnBeat(null, null), null);
    assert.equal(turnBeat(live, null), null);
  });

  it("a new turn starts from zero; an ended turn seen for the first time is not replayed", () => {
    const fresh = startTurn("a2", "c1", T0);
    const previous = endTurn(turnWith([{ type: "tool_start", tool_name: "x" }, { type: "tool_end", tool_name: "x", result: "ok" }]), "done", T0 + 5);
    assert.equal(turnBeat(previous, fresh), null);
    assert.equal(turnBeat(null, previous), null, "mounting after a turn ended is not a nod");
  });
});

describe("mindBeat", () => {
  const acted = normalizeMindNow({ ...MIND, last: { at: "2026-09-26T15:00:00Z", reason: "messages", outcome: "acted", line: "Replied to Parker", thought_id: "t-9" } });

  it("the last wake changing to one that acted is the acted beat", () => {
    assert.equal(mindBeat(MIND, acted), "acted");
  });

  it("the first poll is a baseline, never a beat", () => {
    assert.equal(mindBeat(null, acted), null);
  });

  it("the same acted wake polled again is not replayed", () => {
    assert.equal(mindBeat(acted, normalizeMindNow(acted)), null);
  });

  it("a wake that left things alone is no beat", () => {
    const quiet = normalizeMindNow({ ...MIND, last: { at: "2026-09-26T16:00:00Z", reason: "self", outcome: "left_alone" } });
    assert.equal(mindBeat(acted, quiet), null);
  });
});

describe("beat gate", () => {
  it("plays a beat at once, then holds the next one inside 1.2 s", () => {
    const a = admitBeat(NEW_GATE, "nod", 10_000);
    assert.equal(a.play, "nod");
    const b = admitBeat(a.gate, "nod", 10_300);
    assert.equal(b.play, null);
    assert.equal(b.waitMs, 900);
    assert.equal(b.gate.held, "nod");
  });

  it("of two held beats the one that matters more plays", () => {
    const a = admitBeat(NEW_GATE, "nod", 0);
    const b = admitBeat(a.gate, "acted", 100);
    const c = admitBeat(b.gate, "nod", 200);
    assert.equal(c.gate.held, "acted");
    const r = releaseHeld(c.gate, 1_300);
    assert.equal(r.play, "acted");
    assert.equal(r.gate.held, null);
    assert.equal(r.gate.lastAt, 1_300);
  });

  it("releasing too early waits again; releasing nothing plays nothing", () => {
    const a = admitBeat(NEW_GATE, "wince", 0);
    const b = admitBeat(a.gate, "nod", 100);
    const early = releaseHeld(b.gate, 500);
    assert.equal(early.play, null);
    assert.equal(early.waitMs, 700);
    assert.equal(releaseHeld(NEW_GATE, 5).play, null);
  });
});

describe("shouldPerk", () => {
  it("perks when the app returns after a while away, not after a flick", () => {
    assert.equal(shouldPerk(null, 50_000), false);
    assert.equal(shouldPerk(40_000, 50_000), false);
    assert.equal(shouldPerk(10_000, 50_000), true);
  });
});
