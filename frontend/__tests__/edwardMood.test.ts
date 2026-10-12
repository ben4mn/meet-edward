// R/G red for Edward's mood model (lib/edwardMood.ts).
//
// The design contract: "the animation is a claim, so it must be true".
// Every mood is derived from a real signal, in a fixed priority
//   offline > speak > listen > think/work > attention > resting > sleepy > idle
// and every mood has one honest status line for the presence bar and
// aria-label. The life scheduling (blinks, glances, beat throttling) is
// pure here so the hook can stay a thin DOM shell.

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  BEAT_MIN_GAP_MS,
  BEAT_MS,
  REASON_PHRASES,
  beatForToolTransition,
  beatForTurnOutcome,
  blinkPattern,
  deriveMood,
  moodFromActivity,
  moodToActivity,
  nextBlinkMs,
  nextGlanceMs,
  pickGlance,
  pickPendingBeat,
  reasonPhrase,
  shouldWave,
  statusLabel,
  statusLine,
  throttleBeat,
  type EdwardSignals,
} from "../lib/edwardMood.ts";

const calm: EdwardSignals = {
  online: true,
  voice: null,
  turn: null,
  mindWaking: null,
  approvalsWaiting: 0,
  unreadThoughts: 0,
  quotaExhausted: false,
  quotaResetsAt: null,
  handsDown: false,
  hour: 14,
};
const s = (over: Partial<EdwardSignals>): EdwardSignals => ({ ...calm, ...over });
const seq = (...vals: number[]) => {
  let i = 0;
  return () => vals[i++ % vals.length];
};

describe("deriveMood", () => {
  it("is idle with nothing going on", () => {
    assert.equal(deriveMood(calm), "idle");
  });

  it("offline beats everything", () => {
    assert.equal(
      deriveMood(s({ online: false, voice: "speaking", unreadThoughts: 3, quotaExhausted: true })),
      "offline",
    );
  });

  it("voice phases map to speak / listen / think", () => {
    assert.equal(deriveMood(s({ voice: "speaking" })), "speak");
    assert.equal(deriveMood(s({ voice: "listening" })), "listen");
    assert.equal(deriveMood(s({ voice: "thinking" })), "think");
  });

  it("speak outranks listen, listen outranks a running turn", () => {
    assert.equal(
      deriveMood(s({ voice: "listening", turn: { phase: "working", runningTools: 2 } })),
      "listen",
    );
  });

  it("a turn running tools is work, recalling/thinking is think, writing is speak", () => {
    assert.equal(deriveMood(s({ turn: { phase: "thinking", runningTools: 1 } })), "work");
    assert.equal(deriveMood(s({ turn: { phase: "working", runningTools: 0 } })), "work");
    assert.equal(deriveMood(s({ turn: { phase: "recalling", runningTools: 0 } })), "think");
    assert.equal(deriveMood(s({ turn: { phase: "thinking", runningTools: 0 } })), "think");
    assert.equal(deriveMood(s({ turn: { phase: "writing", runningTools: 0 } })), "speak");
  });

  it("a turn with no phase and nothing running claims nothing", () => {
    assert.equal(deriveMood(s({ turn: { phase: null, runningTools: 0 } })), "idle");
  });

  it("a mind wake is think", () => {
    assert.equal(deriveMood(s({ mindWaking: { reason: "morning" } })), "think");
  });

  it("think/work outrank attention", () => {
    assert.equal(
      deriveMood(s({ mindWaking: { reason: "messages" }, approvalsWaiting: 1 })),
      "think",
    );
  });

  it("an unread thought or a held approval is attention", () => {
    assert.equal(deriveMood(s({ unreadThoughts: 1 })), "attention");
    assert.equal(deriveMood(s({ approvalsWaiting: 2 })), "attention");
  });

  it("attention outranks resting, resting outranks sleepy", () => {
    assert.equal(deriveMood(s({ unreadThoughts: 1, quotaExhausted: true })), "attention");
    assert.equal(deriveMood(s({ quotaExhausted: true, hour: 2 })), "resting");
  });

  it("is sleepy from 23:00 to 06:00 when idle", () => {
    assert.equal(deriveMood(s({ hour: 23 })), "sleepy");
    assert.equal(deriveMood(s({ hour: 0 })), "sleepy");
    assert.equal(deriveMood(s({ hour: 5 })), "sleepy");
    assert.equal(deriveMood(s({ hour: 6 })), "idle");
    assert.equal(deriveMood(s({ hour: 22 })), "idle");
  });
});

describe("statusLine", () => {
  const tz = "America/Chicago";
  // Thu 2026-09-24 14:00 in Springfield.
  const now = new Date("2026-09-24T19:00:00Z");

  it("idle is plainly resting", () => {
    assert.equal(statusLine(calm), "Resting");
  });

  it("offline says so", () => {
    assert.equal(statusLine(s({ online: false })), "Offline");
  });

  it("names the mind wake's reason in friendly words", () => {
    assert.equal(statusLine(s({ mindWaking: { reason: "morning" } })), "Thinking · morning wake");
    assert.equal(
      statusLine(s({ mindWaking: { reason: "messages" } })),
      "Thinking · reading your messages",
    );
    assert.equal(
      statusLine(s({ mindWaking: { reason: "reflect" } })),
      "Thinking · reflecting on the week",
    );
  });

  it("the surfaces wake reads as thinking about my screens — Edward speaks in first person (review)", () => {
    assert.equal(statusLine(s({ mindWaking: { reason: "surfaces" } })), "Thinking about my screens");
    assert.equal(statusLine(s({ mindWaking: { reason: "manual" } })), "Thinking · you woke me");
  });

  it("an unknown wake reason never leaks internal vocabulary", () => {
    assert.equal(statusLine(s({ mindWaking: { reason: "zz_internal" } })), "Thinking");
  });

  it("counts running tools", () => {
    assert.equal(statusLine(s({ turn: { phase: "working", runningTools: 3 } })), "Working · 3 tools");
    assert.equal(statusLine(s({ turn: { phase: "working", runningTools: 1 } })), "Working · 1 tool");
    assert.equal(statusLine(s({ turn: { phase: "working", runningTools: 0 } })), "Working");
  });

  it("names the turn's phase", () => {
    assert.equal(statusLine(s({ turn: { phase: "thinking", runningTools: 0 } })), "Thinking");
    assert.equal(statusLine(s({ turn: { phase: "recalling", runningTools: 0 } })), "Remembering");
    assert.equal(statusLine(s({ turn: { phase: "writing", runningTools: 0 } })), "Writing");
  });

  it("voice phases", () => {
    assert.equal(statusLine(s({ voice: "listening" })), "Listening");
    assert.equal(statusLine(s({ voice: "speaking" })), "Speaking");
    assert.equal(statusLine(s({ voice: "thinking" })), "Thinking");
  });

  it("wants Ellis, with the count", () => {
    assert.equal(statusLine(s({ unreadThoughts: 1 })), "Wants you · 1 new thought");
    assert.equal(statusLine(s({ unreadThoughts: 4 })), "Wants you · 4 new thoughts");
  });

  it("a held approval comes before unread thoughts", () => {
    assert.equal(statusLine(s({ approvalsWaiting: 1, unreadThoughts: 2 })), "Waiting on your approval");
    assert.equal(statusLine(s({ approvalsWaiting: 3 })), "Waiting on your approval · 3");
  });

  it("resting names when the quota comes back", () => {
    // Tue 2026-09-29 15:00 Springfield = 20:00Z
    assert.equal(
      statusLine(s({ quotaExhausted: true, quotaResetsAt: "2026-09-29T20:00:00Z" }), { now, tz }),
      "Resting until Tue 3:00 pm",
    );
  });

  it("resting later today and tomorrow read naturally", () => {
    assert.equal(
      statusLine(s({ quotaExhausted: true, quotaResetsAt: "2026-09-24T22:30:00Z" }), { now, tz }),
      "Resting until 5:30 pm",
    );
    assert.equal(
      statusLine(s({ quotaExhausted: true, quotaResetsAt: "2026-09-25T14:00:00Z" }), { now, tz }),
      "Resting until tomorrow 9:00 am",
    );
  });

  it("a naive reset time is UTC by contract", () => {
    assert.equal(
      statusLine(s({ quotaExhausted: true, quotaResetsAt: "2026-09-29T20:00:00" }), { now, tz }),
      "Resting until Tue 3:00 pm",
    );
  });

  it("resting without a reset time still says why", () => {
    assert.equal(statusLine(s({ quotaExhausted: true })), "Resting · quota spent");
  });

  it("is honest when his hands are down", () => {
    assert.equal(statusLine(s({ handsDown: true })), "Resting · Chrome link down");
    // Doing something still says what he is doing (the amber tip carries it).
    assert.equal(
      statusLine(s({ handsDown: true, turn: { phase: "working", runningTools: 1 } })),
      "Working · 1 tool",
    );
  });

  it("says his hands are down while something waits on Ellis, and while resting (review: hands-down-invisible)", () => {
    assert.equal(statusLine(s({ handsDown: true, approvalsWaiting: 1 })), "Waiting on your approval · Chrome link down");
    assert.equal(statusLine(s({ handsDown: true, unreadThoughts: 2 })), "Wants you · 2 new thoughts · Chrome link down");
    assert.equal(
      statusLine(s({ handsDown: true, quotaExhausted: true, quotaResetsAt: "2026-09-29T20:00:00Z" }), { now, tz }),
      "Resting until Tue 3:00 pm · Chrome link down",
    );
    assert.equal(statusLine(s({ handsDown: true, online: false })), "Offline");
  });

  it("a busy line keeps what he is doing, and the label still tells a screen reader his hands are down", () => {
    const busy = s({ handsDown: true, turn: { phase: "working", runningTools: 1 } });
    assert.equal(statusLabel(busy), "Edward · Working · 1 tool (Chrome link down)");
    assert.equal(statusLabel(s({ handsDown: true })), "Edward · Resting · Chrome link down");
  });

  it("counts the tools used this turn when the turn says so", () => {
    assert.equal(statusLine(s({ turn: { phase: "working", runningTools: 1, toolsUsed: 4 } })), "Working · 4 tools");
    assert.equal(statusLine(s({ turn: { phase: "working", runningTools: 0, toolsUsed: 2 } })), "Working · 2 tools");
  });

  it("sleepy is resting too", () => {
    assert.equal(statusLine(s({ hour: 2 })), "Resting");
  });
});

describe("before the first answer (known: false)", () => {
  // 2026-09-26: the provider's signals defaulted approvals/unread to 0, so
  // the bar and heroes read "Resting" for the first seconds even while an
  // approval waited. Before any answer he makes no claim.
  it("is plain idle with no line and a bare name", () => {
    const unknown = s({ known: false });
    assert.equal(deriveMood(unknown), "idle");
    assert.equal(statusLine(unknown), "");
    assert.equal(statusLabel(unknown), "Edward");
  });

  it("is not sleepy at night: sleepy is a claim that nothing waits", () => {
    assert.equal(deriveMood(s({ known: false, hour: 2 })), "idle");
  });

  it("still says what the chat and voice really show", () => {
    assert.equal(statusLine(s({ known: false, turn: { phase: "working", runningTools: 1 } })), "Working · 1 tool");
    assert.equal(statusLine(s({ known: false, voice: "listening" })), "Listening");
    assert.equal(statusLine(s({ known: false, online: false })), "Offline");
  });
});

describe("statusLabel", () => {
  it("names Edward for screen readers", () => {
    assert.equal(statusLabel(calm), "Edward · Resting");
  });

  it("explains a quota rest", () => {
    const tz = "America/Chicago";
    const now = new Date("2026-09-24T19:00:00Z");
    assert.equal(
      statusLabel(s({ quotaExhausted: true, quotaResetsAt: "2026-09-29T20:00:00Z" }), { now, tz }),
      "Edward · Resting until Tue 3:00 pm (week's quota spent)",
    );
  });

  it("explains a rest at the Claude day's cap as the day's budget", () => {
    const tz = "America/Chicago";
    const now = new Date("2026-09-24T19:00:00Z");
    assert.equal(
      statusLabel(s({ quotaExhausted: true, quotaResetsAt: "2026-09-25T05:00:00Z", quotaPeriod: "day" }), { now, tz }),
      "Edward · Resting until tomorrow 12:00 am (today's budget spent)",
    );
  });
});

describe("reasonPhrase", () => {
  it("covers every wake reason the mind uses", () => {
    for (const r of [
      "messages", "mention", "morning", "evening", "reflect", "self",
      "surfaces", "free", "research", "prep", "reflect_lite", "manual",
    ]) {
      assert.ok(REASON_PHRASES[r], `no phrase for ${r}`);
    }
  });

  it("maps the vocabulary", () => {
    assert.equal(reasonPhrase("mention"), "answering a mention");
    assert.equal(reasonPhrase("evening"), "evening wake");
    assert.equal(reasonPhrase("self"), "following up on something");
    assert.equal(reasonPhrase("surfaces"), "tidying my screens");
    assert.equal(reasonPhrase("free"), "free time");
    assert.equal(reasonPhrase("research"), "researching");
    assert.equal(reasonPhrase("prep"), "preparing for a meeting");
    assert.equal(reasonPhrase("reflect_lite"), "thinking over the day");
    assert.equal(reasonPhrase("nope"), null);
  });
});

describe("beatForToolTransition", () => {
  it("nods when a tool succeeded", () => {
    assert.equal(beatForToolTransition({ ok: 0, failed: 0 }, { ok: 1, failed: 0 }), "nod");
  });

  it("winces when a tool failed, even if another succeeded", () => {
    assert.equal(beatForToolTransition({ ok: 0, failed: 0 }, { ok: 1, failed: 1 }), "wince");
  });

  it("pings when he reached outward", () => {
    assert.equal(
      beatForToolTransition({ ok: 1, failed: 0, outward: 0 }, { ok: 2, failed: 0, outward: 1 }),
      "acted",
    );
  });

  it("stays still when nothing finished", () => {
    assert.equal(beatForToolTransition({ ok: 1, failed: 0 }, { ok: 1, failed: 0 }), null);
    assert.equal(beatForToolTransition(null, { ok: 0, failed: 0 }), null);
  });

  it("the first snapshot of a finished tool counts as a transition", () => {
    assert.equal(beatForToolTransition(null, { ok: 1, failed: 0 }), "nod");
  });
});

describe("beatForTurnOutcome", () => {
  it("nods on a finished turn, winces on an error, stays still otherwise", () => {
    assert.equal(beatForTurnOutcome("completed"), "nod");
    assert.equal(beatForTurnOutcome("error"), "wince");
    assert.equal(beatForTurnOutcome("stopped"), null);
  });
});

describe("beat throttle", () => {
  it("lets the first beat through", () => {
    assert.deepEqual(throttleBeat(null, 1000), { ok: true, waitMs: 0 });
  });

  it("holds a beat that comes too soon, saying how long", () => {
    assert.deepEqual(throttleBeat(1000, 1500), { ok: false, waitMs: BEAT_MIN_GAP_MS - 500 });
  });

  it("gaps beats at least 1.2s apart", () => {
    assert.ok(BEAT_MIN_GAP_MS >= 1200);
    assert.deepEqual(throttleBeat(1000, 1000 + BEAT_MIN_GAP_MS), { ok: true, waitMs: 0 });
  });

  it("keeps the more important beat when two wait", () => {
    assert.equal(pickPendingBeat("nod", "acted"), "acted");
    assert.equal(pickPendingBeat("wince", "nod"), "wince");
    assert.equal(pickPendingBeat(null, "perk"), "perk");
    assert.equal(pickPendingBeat("acted", "wince"), "acted");
  });

  it("no beat lasts longer than a second", () => {
    for (const [beat, ms] of Object.entries(BEAT_MS)) {
      assert.ok(ms <= 1000, `${beat} lasts ${ms}ms`);
    }
  });
});

describe("blinks and glances", () => {
  it("waits 2.2-6s between ordinary blinks", () => {
    assert.equal(nextBlinkMs(seq(0.5, 0)), 2200);
    assert.equal(nextBlinkMs(seq(0.5, 0.999999)) <= 6000, true);
    assert.ok(nextBlinkMs(seq(0.5, 0.999999)) > 5900);
  });

  it("sometimes stares (about 12% of the time)", () => {
    const stare = nextBlinkMs(seq(0.05, 0.5));
    assert.ok(stare > 6000, `stare was ${stare}`);
    assert.ok(stare <= 10000);
    assert.ok(nextBlinkMs(seq(0.13, 0.5)) <= 6000);
  });

  it("blinks slower when sleepy or resting", () => {
    assert.ok(nextBlinkMs(seq(0.5, 0.5), "sleepy") > nextBlinkMs(seq(0.5, 0.5)));
  });

  it("never blinks while offline", () => {
    assert.equal(nextBlinkMs(seq(0.5, 0.5), "offline"), null);
  });

  it("a blink is one close, or a double 15% of the time", () => {
    assert.equal(blinkPattern(seq(0.9)).length, 1);
    assert.equal(blinkPattern(seq(0.1)).length, 3);
    assert.equal(blinkPattern(seq(0.16)).length, 1);
  });

  it("a sleepy blink is slow", () => {
    const [close] = blinkPattern(seq(0.9), "sleepy");
    const [quick] = blinkPattern(seq(0.9));
    assert.ok(close > quick);
  });

  it("glances every 8-20s", () => {
    assert.equal(nextGlanceMs(seq(0)), 8000);
    assert.ok(nextGlanceMs(seq(0.999999)) <= 20000);
  });

  it("glances left, right or down and comes back", () => {
    const seen = new Set<string>();
    for (const r of [0.05, 0.4, 0.8]) {
      const g = pickGlance(seq(r, 0.5));
      seen.add(g.gaze);
      assert.ok(g.holdMs >= 600 && g.holdMs <= 1800);
    }
    assert.deepEqual([...seen].sort(), ["down", "left", "right"]);
  });
});

describe("activity back-compat", () => {
  it("maps moods onto the legacy act-* classes", () => {
    assert.equal(moodToActivity("idle"), "act-stand");
    assert.equal(moodToActivity("think"), "act-think");
    assert.equal(moodToActivity("work"), "act-think");
    assert.equal(moodToActivity("speak"), "act-speak");
    assert.equal(moodToActivity("listen"), "act-listen");
    assert.equal(moodToActivity("offline"), "act-stand");
  });

  it("reads the legacy act-* classes as moods", () => {
    assert.equal(moodFromActivity("act-stand"), "idle");
    assert.equal(moodFromActivity("act-think"), "think");
    assert.equal(moodFromActivity("act-speak"), "speak");
    assert.equal(moodFromActivity("act-listen"), "listen");
    assert.equal(moodFromActivity("act-look"), "idle");
  });
});

describe("shouldWave", () => {
  const tz = "America/Chicago";
  it("waves on the first open of a local day", () => {
    assert.equal(shouldWave(null, new Date("2026-09-24T19:00:00Z"), tz), true);
    assert.equal(
      shouldWave("2026-09-24T13:00:00Z", new Date("2026-09-24T19:00:00Z"), tz),
      false,
    );
    // 23:30 Springfield the night before is a different local day.
    assert.equal(
      shouldWave("2026-09-24T04:30:00Z", new Date("2026-09-24T19:00:00Z"), tz),
      true,
    );
  });

  it("an unreadable stamp means wave", () => {
    assert.equal(shouldWave("garbage", new Date("2026-09-24T19:00:00Z"), tz), true);
  });
});
