// The presence hero at the top of Today (UX pass 2026-09-26): the date,
// the live Edward and his greeting, then one quiet line that only says
// what real signals say — no "Watching iMessage…" template, no jargon,
// never an error message.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import { dateOverline, heroBeat, heroSubline, keepTogether, shortClock, softLine } from "../components/today/heroModel.ts";
import type { TodayPresence } from "../components/today/todayModel.ts";
import { EMPTY_MIND_NOW, type MindNow } from "../lib/mindNowApi.ts";

const tz = "America/Chicago";
const now = new Date("2026-09-26T23:30:00Z"); // Sat 6:30 pm Springfield

const presence = (over: Partial<TodayPresence["mind"]> = {}, listening: string[] | null = ["imessage", "calendar"]): TodayPresence => ({
  listening,
  last_look_at: "2026-09-26T23:20:00Z",
  mind: {
    last_wake_at: "2026-09-26T22:20:00Z",
    last_reason: "messages",
    last_outcome: "left_alone",
    last_line: "Left Theo's thread alone.",
    next_wake_at: "2026-09-27T12:00:00Z",
    next_wake_reason: "morning",
    ...over,
  },
});

const mind = (over: Partial<MindNow> = {}): MindNow => ({ ...EMPTY_MIND_NOW, ...over });

describe("shortClock", () => {
  it("soon is minutes, today is a clock, then tomorrow and weekdays", () => {
    assert.equal(shortClock("2026-09-27T00:08:00Z", { now, tz }), "in 38 min");
    assert.equal(shortClock("2026-09-27T01:00:00Z", { now, tz }), "8:00 pm");
    assert.equal(shortClock("2026-09-27T01:30:00Z", { now, tz }), "8:30 pm");
    assert.equal(shortClock("2026-09-27T12:00:00Z", { now, tz }), "tomorrow 7:00 am");
    assert.equal(shortClock("2026-09-29T23:00:00Z", { now, tz }), "Tue 6:00 pm");
    assert.equal(shortClock("2026-10-09T15:00:00Z", { now, tz }), "Oct 9");
  });
  it("a time already past is a clock too", () => {
    assert.equal(shortClock("2026-09-26T12:02:00Z", { now, tz }), "7:02 am");
  });
});

describe("softLine", () => {
  it("lower-cases an ordinary opening word and drops the full stop", () => {
    assert.equal(softLine("Left Theo's thread alone."), "left Theo's thread alone");
    assert.equal(softLine("Nothing changed that matters."), "nothing changed that matters");
  });
  it("keeps a name, I, or an acronym as written", () => {
    assert.equal(softLine("Parker asked about Saturday."), "Parker asked about Saturday");
    assert.equal(softLine("I left it."), "I left it");
    assert.equal(softLine("ORD flight moved."), "ORD flight moved");
  });
  it("keeps it short", () => {
    const long = "Replied to Parker about the flight and then checked the calendar for Saturday and the weather too.";
    const s = softLine(long);
    assert.ok(s.length <= 60, s);
    assert.ok(s.endsWith("…"));
  });
});

describe("heroSubline", () => {
  const base = { presence: presence(), mindNow: null, wakes: 6, mood: "idle" as const, status: "Resting", now, tz };

  it("counts his wakes, says what the last one did and when he looks next", () => {
    assert.deepEqual(heroSubline(base), ["6 wakes", "last: left Theo's thread alone", "next look tomorrow 7:00 am"]);
  });

  it("an acted look is named by what he did (the newest 'Edward did' row), not his whole closing line", () => {
    const p = presence({ last_outcome: "acted", last_wake_at: "2026-09-26T22:20:00Z", last_line: "Jordan asked about dinner; drafted a reply and held it for Ellis." });
    const actions = [
      { id: "a1", kind: "action", title: "Drafted a reply to Jordan", detail: "", at: "2026-09-26T22:10:00Z", href: "/chat?c=t" },
      { id: "a0", kind: "action", title: "Pushed you: dog food", detail: "", at: "2026-09-26T19:00:00Z", href: "/chat?c=u" },
    ];
    assert.equal(heroSubline({ ...base, presence: p, actions })[1], "last: drafted a reply to Jordan");
    // No action near that look: the first clause of his line, kept short.
    assert.equal(heroSubline({ ...base, presence: p, actions: [actions[1]] })[1], "last: Jordan asked about dinner");
  });

  it("prefers the live mind/now over the feed", () => {
    const m = mind({
      last: { at: "2026-09-26T23:25:00Z", reason: "mention", outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t" },
      next_wake_at: "2026-09-27T00:08:00Z",
    });
    assert.deepEqual(heroSubline({ ...base, mindNow: m }), ["6 wakes", "last: replied to Parker about the flight", "next look in 38 min"]);
  });

  it("while he is thinking, says so first (its own separator a dash) and drops the last and next look", () => {
    const m = mind({ waking: { reason: "morning", started_at: "2026-09-26T23:29:40Z", conversation_id: "edward-mind" } });
    assert.deepEqual(heroSubline({ ...base, mindNow: m, mood: "think", status: "Thinking · morning wake" }), [
      "Thinking — morning wake",
      "6 wakes",
    ]);
  });

  it("any status with its own dot reads as one clause", () => {
    assert.equal(heroSubline({ ...base, mood: "attention", status: "Wants you · 1 new thought" })[0], "Wants you — 1 new thought");
  });

  it("never shows an error: a failed look is just a look that didn't finish", () => {
    const p = presence({ last_outcome: "failed", last_line: "ValueError: Codex API error (HTTP 503)" });
    assert.deepEqual(heroSubline({ ...base, presence: p }), ["6 wakes", "last look didn't finish", "next look tomorrow 7:00 am"]);
  });

  it("a look skipped for quota says why, plainly", () => {
    const p = presence({ last_outcome: "skipped", last_line: "Codex quota exhausted" });
    assert.equal(heroSubline({ ...base, presence: p })[1], "skipped a look (quota spent)");
  });

  it("a quiet look without a closing line", () => {
    const p = presence({ last_line: null });
    assert.equal(heroSubline({ ...base, presence: p })[1], "last: left things alone");
  });

  it("says when he is not reading messages, and nothing when it is unknown", () => {
    assert.equal(heroSubline({ ...base, presence: presence({}, []) })[0], "Not reading messages");
    assert.deepEqual(heroSubline({ ...base, presence: presence({}, null), wakes: 0 }), [
      "last: left Theo's thread alone",
      "next look tomorrow 7:00 am",
    ]);
  });

  it("says nothing it does not know", () => {
    assert.deepEqual(heroSubline({ ...base, presence: null, wakes: null }), []);
  });

  it("one wake is singular; a past next-wake is left out", () => {
    const p = presence({ next_wake_at: "2026-09-26T20:00:00Z" });
    assert.deepEqual(heroSubline({ ...base, presence: p, wakes: 1 }), ["1 wake", "last: left Theo's thread alone"]);
  });
});

describe("heroBeat", () => {
  const mounted = 1_000_000;
  it("plays a beat that happened as the page opened (the day's wave)", () => {
    assert.equal(heroBeat({ beat: "wave", key: 1, at: mounted - 500 }, mounted), "wave");
  });
  it("never replays an old beat from another screen", () => {
    assert.equal(heroBeat({ beat: "nod", key: 3, at: mounted - 60_000 }, mounted), null);
    assert.equal(heroBeat(null, mounted), null);
  });
  it("plays every beat after the page opened", () => {
    assert.equal(heroBeat({ beat: "acted", key: 4, at: mounted + 5_000 }, mounted), "acted");
  });
});

describe("dateOverline", () => {
  it("weekday, month and day in his zone", () => {
    assert.equal(dateOverline(now, tz), "Saturday, September 26");
    assert.equal(dateOverline(new Date("2026-09-27T04:30:00Z"), tz), "Saturday, September 26");
  });
});

import { clockOnly, isContinuation } from "../components/today/heroModel.ts";

describe("clockOnly", () => {
  it("a lower-case clock for list rows", () => {
    assert.equal(clockOnly("2026-09-26T22:10:00Z", tz), "5:10 pm");
    assert.equal(clockOnly("2026-09-26T14:00:00Z", tz), "9:00 am");
    assert.equal(clockOnly("nope", tz), "");
  });
});

describe("isContinuation", () => {
  it("a streamed line that grows keeps its words; a new line starts over", () => {
    assert.equal(isContinuation("Evening, ", "Evening, Ellis."), true);
    assert.equal(isContinuation("", "Evening"), true);
    assert.equal(isContinuation("Morning, Ellis.", "Evening, Ellis."), false);
  });
});

// ---------------------------------------------------------------------------
// Review 2026-09-26 (hands-down-invisible, quota-rest-contradicts-next-look):
// "never 'fine' when his hands are down", and a spent week is not also
// "next look in 38 min".
// ---------------------------------------------------------------------------
describe("heroSubline: what is wrong is said whatever the mood", () => {
  const base = { presence: presence(), mindNow: null, wakes: 6, mood: "idle" as const, status: "Resting", now, tz };
  const handsDown = mind({ hands: { connected: false, down_for_s: 600 }, last: null, next_wake_at: "2026-09-27T00:08:00Z" });
  it("says his hands in Chrome are down while he rests …", () => {
    assert.equal(heroSubline({ ...base, mindNow: handsDown })[0], "My hands in your Chrome are down");
  });
  it("… and while something waits on Ellis", () => {
    const parts = heroSubline({ ...base, mindNow: handsDown, mood: "attention", status: "Waiting on your approval" });
    assert.deepEqual(parts.slice(0, 2), ["Waiting on your approval", "My hands in your Chrome are down"]);
  });
  it("says nothing about Chrome when the link is up or unknown", () => {
    for (const connected of [true, null]) {
      const parts = heroSubline({ ...base, mindNow: mind({ hands: { connected, down_for_s: null } }) });
      assert.equal(parts.some((p) => /Chrome/.test(p)), false);
    }
  });
  it("a spent week has no next look: the looks resume when the quota does", () => {
    const spent = mind({
      quota: { used_percent: 100, exhausted: true, resets_at: "2026-09-28T03:59:00Z" },
      next_wake_at: "2026-09-27T00:08:00Z",
      last: null,
    });
    const parts = heroSubline({ ...base, mindNow: spent, mood: "resting", status: "Resting until tomorrow 10:59 pm" });
    assert.equal(parts.some((p) => p.startsWith("next look")), false, parts.join(" · "));
    assert.equal(parts[0], "Resting until tomorrow 10:59 pm");
    // Attention outranks resting, so the status does not say it: the subline does.
    const busy = heroSubline({ ...base, mindNow: spent, mood: "attention", status: "Waiting on your approval" });
    assert.equal(busy.includes("looks resume tomorrow 10:59 pm"), true, busy.join(" · "));
    assert.equal(busy.some((p) => p.startsWith("next look")), false);
  });
});

// Review 2026-09-26 (greeting-stale-after-action): the greeting on screen
// must not go on claiming something Ellis has just done.
import { greetingWithout } from "../components/today/heroModel.ts";

describe("greetingWithout", () => {
  const line = "Evening, Ellis. Biscuit's 6 o'clock meds are still waiting, and Jordan wants an answer about Sunday.";
  const carprofen = { kind: "dose", title: "Biscuit · Carprofen 100mg", detail: "Due 18:00, 42 min late." };
  const probiotic = { kind: "dose", title: "Biscuit · Probiotic", detail: "Due 18:00." };
  const jordan = { kind: "approval", title: "Send this to Jordan?", detail: "" };
  const vetMail = { kind: "email", title: "Patel Vet emailed you: Can Biscuit come in Thursday at 9?", detail: "" };
  it("keeps a clause while any need it names is still there", () => {
    assert.equal(greetingWithout(line, [carprofen], [probiotic, jordan]), line);
  });
  it("drops the clause once every need it names is done", () => {
    assert.equal(greetingWithout(line, [carprofen, probiotic], [jordan]), "Evening, Ellis. Jordan wants an answer about Sunday.");
    assert.equal(greetingWithout(line, [jordan], [carprofen, probiotic]), "Evening, Ellis. Biscuit's 6 o'clock meds are still waiting.");
    assert.equal(greetingWithout(line, [carprofen, probiotic, jordan], []), "Evening, Ellis.");
  });
  it("a need of another kind naming the same pet does not keep the meds clause", () => {
    assert.equal(greetingWithout(line, [carprofen, probiotic], [jordan, vetMail]), "Evening, Ellis. Jordan wants an answer about Sunday.");
  });
  it("never touches the salutation, and leaves a line that names nothing gone", () => {
    assert.equal(greetingWithout("Morning, Ellis.", [carprofen], []), "Morning, Ellis.");
    assert.equal(greetingWithout("Quiet one today. The rain stops at noon.", [carprofen], []), "Quiet one today. The rain stops at noon.");
    assert.equal(greetingWithout(line, [], []), line);
  });
});

// ---------------------------------------------------------------------------
// Integration pass 2026-09-26: one clock format ("7:34 pm", the backend's
// too), a subline part never wraps a lone "min", and an offline Edward
// makes no claim about now.
// ---------------------------------------------------------------------------
describe("heroSubline: the integration pass", () => {
  const base = { presence: presence(), mindNow: null, wakes: 6, mood: "idle" as const, status: "Resting", now, tz };
  const handsDown = mind({ hands: { connected: false, down_for_s: 600 }, last: null, next_wake_at: "2026-09-27T00:08:00Z" });

  it("keepTogether joins a part's words with non-breaking spaces", () => {
    assert.equal(keepTogether("next look in 38 min"), "next\u00a0look\u00a0in\u00a038\u00a0min");
    assert.equal(keepTogether(""), "");
  });

  it("the status part does not repeat 'Chrome link down' beside the full sentence about his hands", () => {
    const parts = heroSubline({
      ...base,
      mindNow: handsDown,
      mood: "attention",
      status: "Waiting on your approval · Chrome link down",
    });
    assert.deepEqual(parts.slice(0, 2), ["Waiting on your approval", "My hands in your Chrome are down"]);
    assert.equal(parts.filter((p) => /Chrome/.test(p)).length, 1, parts.join(" · "));
  });

  it("offline: says it, keeps what is past, and claims nothing about now (review: offline-sheet-claims)", () => {
    const m = mind({
      hands: { connected: false, down_for_s: 600 },
      quota: { used_percent: 100, exhausted: true, resets_at: "2026-09-28T03:59:00Z" },
      next_wake_at: "2026-09-27T00:08:00Z",
      last: { at: "2026-09-26T23:25:00Z", reason: "mention", outcome: "acted", line: "Replied to Parker about the flight.", thought_id: "t" },
    });
    const parts = heroSubline({ ...base, presence: presence({}, []), mindNow: m, mood: "offline", status: "Offline" });
    assert.deepEqual(parts, ["Offline", "6 wakes", "last: replied to Parker about the flight"]);
  });
});
