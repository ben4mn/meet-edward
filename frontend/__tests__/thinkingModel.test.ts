// His thinking on /screen (2026-09-28): GET /api/screen/live, folded for the
// thought lane. Only while a mind or surfaces turn really runs; lines fade
// with age; receipts are tool names in his words; a finished turn folds into
// one "Earlier · 2:10 pm — …" line for ten minutes (round 2: after that his
// mind drifts through his recent thoughts, each labelled with its own time).

import { describe, it } from "node:test";
import assert from "node:assert/strict";

import {
  EMPTY_LIVE,
  FOLD_WITHIN_MS,
  LIVE_STALE_AFTER_MS,
  MIND_MAX_MS,
  MIND_MIN_MS,
  liveOnStage,
  mindKey,
  mindOverline,
  mindPool,
  nextMindDelayMs,
  pickMindThought,
  thoughtView as view,
  type RecentThought,
  type ScreenLive,
} from "../app/(display)/display/lib/thinkingModel.ts";
import { describeTool } from "../lib/toolLabels.ts";

/** The page loads the labels lazily (lib/toolLabels.ts); tests hand them in. */
const thoughtView = (live: Parameters<typeof view>[0], now: number, opts: { tz?: string } = {}) =>
  view(live, now, { ...opts, describe: describeTool });

const tz = "America/Chicago";
const NOW = Date.parse("2026-09-28T22:31:00Z"); // 5:31 pm in Springfield
const ago = (s: number) => new Date(NOW - s * 1000).toISOString();

const live = (over: Partial<ScreenLive> = {}): ScreenLive => ({
  thinking: {
    turn: "t1", source: "mind", about: "messages", since: ago(60),
    lines: [
      { text: "Parker asked about Saturday.", at: ago(50) },
      { text: "Ellis said last week he's free after two.", at: ago(30) },
      { text: "I shouldn't answer for him.", at: ago(5) },
    ],
    receipts: [
      { tool: "read_imessage_thread", state: "ok", at: ago(45) },
      { tool: "remember_search", state: "error", at: ago(25) },
      { tool: "leave_note", state: "running", at: ago(2) },
    ],
  },
  folded: [],
  now: new Date(NOW).toISOString(),
  ...over,
});

describe("thoughtView", () => {
  it("while a turn runs: what it is about in his words, and since when", () => {
    const v = thoughtView(live(), NOW, { tz });
    assert.equal(v.active, true);
    assert.equal(v.about, "reading your messages");
    assert.equal(v.since, "5:30 pm");
  });

  it("older lines fade: the oldest ~0.35, the newest fully there", () => {
    const v = thoughtView(live(), NOW, { tz });
    assert.deepEqual(v.lines.map((l) => l.text), [
      "Parker asked about Saturday.", "Ellis said last week he's free after two.", "I shouldn't answer for him.",
    ]);
    assert.equal(v.lines[0].opacity, 0.35);
    assert.equal(v.lines[2].opacity, 1);
    assert.ok(v.lines[1].opacity > 0.35 && v.lines[1].opacity < 1);
    assert.equal(v.lines[2].newest, true);
  });

  it("one line alone is fully there", () => {
    const one = live();
    one.thinking!.lines = [{ text: "Looking.", at: ago(1) }];
    assert.equal(thoughtView(one, NOW, { tz }).lines[0].opacity, 1);
  });

  it("receipts are tool names in his words — live, done or failed", () => {
    const v = thoughtView(live(), NOW, { tz });
    assert.deepEqual(v.receipts.map((r) => r.state), ["ok", "error", "running"]);
    for (const r of v.receipts) {
      assert.doesNotMatch(r.label, /_/, `no raw tool name: ${r.label}`);
      assert.ok(r.label.length > 0);
    }
    assert.match(v.receipts[1].label, /^Couldn't /);
    assert.match(v.receipts[2].label, /…$/, "a running tool reads as in progress");
  });

  it("the stream interleaves lines and receipts in the order they happened", () => {
    const v = thoughtView(live(), NOW, { tz });
    assert.deepEqual(v.stream.map((x) => x.type), ["line", "receipt", "line", "receipt", "line", "receipt"]);
    const chips = v.stream.filter((x) => x.type === "receipt");
    assert.ok(chips.every((c) => c.opacity > 0 && c.opacity <= 1));
    assert.equal(v.stream[v.stream.length - 1].opacity, 1, "the newest thing is fully there");
  });

  it("only the last few moments stay in the stream", () => {
    const l = live();
    l.thinking!.lines = Array.from({ length: 8 }, (_, i) => ({ text: `line ${i}`, at: ago(50 - i) }));
    l.thinking!.receipts = Array.from({ length: 12 }, (_, i) => ({ tool: "web_search", state: "ok" as const, at: ago(100 - i) }));
    const v = thoughtView(l, NOW, { tz });
    assert.ok(v.stream.length <= 9, String(v.stream.length));
    assert.equal(v.stream[v.stream.length - 1].type, "line", "the newest thing is kept");
  });

  it("before the labels load, a receipt is still a readable name, never snake_case", () => {
    const v = view(live(), NOW, { tz });
    assert.deepEqual(v.receipts.map((r) => r.label), ["Read imessage thread", "Couldn't finish: Remember search", "Leave note…"]);
  });

  it("a raw wake reason never shows; an unknown one says nothing", () => {
    const l = live();
    l.thinking!.about = "some_internal_reason";
    assert.equal(thoughtView(l, NOW, { tz }).about, null);
    l.thinking!.about = null;
    assert.equal(thoughtView(l, NOW, { tz }).about, null);
  });

  it("the surfaces turn reads as tidying his screens", () => {
    const l = live();
    l.thinking!.source = "pulse";
    l.thinking!.about = "surfaces";
    assert.equal(thoughtView(l, NOW, { tz }).about, "tidying my screens");
  });

  it("nothing running: not active, and the last fold under ten minutes old is one line", () => {
    const v = thoughtView(live({
      thinking: null,
      folded: [
        { text: "Older.", about: "morning", at: ago(3 * 3600), tools: 1 },
        { text: "I went over the lunch messages. Nothing needed you.", about: "messages", at: ago(9 * 60), tools: 3 },
      ],
    }), NOW, { tz });
    assert.equal(v.active, false);
    assert.deepEqual(v.lines, []);
    assert.equal(v.fold?.line, "Earlier · 5:22 pm — I went over the lunch messages. Nothing needed you.");
  });

  it("a fold over ten minutes old is gone (his mind drifts on instead)", () => {
    assert.equal(FOLD_WITHIN_MS, 10 * 60_000);
    const v = thoughtView(live({ thinking: null, folded: [{ text: "Old.", about: null, at: ago(11 * 60), tools: 0 }] }), NOW, { tz });
    assert.equal(v.fold, null);
  });

  it("an empty or missing snapshot shows nothing", () => {
    for (const x of [null, undefined, EMPTY_LIVE]) {
      const v = thoughtView(x, NOW, { tz });
      assert.equal(v.active, false);
      assert.equal(v.fold, null);
      assert.deepEqual(v.receipts, []);
    }
  });

  it("a turn older than the backend's 20 minutes is not thinking (a crashed turn must not look like thinking)", () => {
    const l = live();
    l.thinking!.since = new Date(NOW - LIVE_STALE_AFTER_MS - 60_000).toISOString();
    const v = thoughtView(l, NOW, { tz });
    assert.equal(v.active, false);
    assert.deepEqual(v.stream, []);
    assert.equal(LIVE_STALE_AFTER_MS, 20 * 60_000);
  });
});

describe("stream keys survive the backend's rolling window", () => {
  // screen_thoughts keeps the last 8 lines and 12 receipts; once full, each
  // new one drops the oldest. A key built from the position would change for
  // every survivor and replay every fade.
  const rolled = (from: number, to: number, receipts = false) => {
    const l = live();
    l.thinking!.lines = receipts ? [] : Array.from({ length: to - from }, (_, k) => ({ text: `line ${from + k}`, at: ago(200 - (from + k) * 5) }));
    l.thinking!.receipts = receipts
      ? Array.from({ length: to - from }, (_, k) => ({ tool: "web_search", state: "ok" as const, at: ago(200 - (from + k) * 5) }))
      : [];
    return thoughtView(l, NOW, { tz });
  };

  it("lines: every line still there keeps its key when the ninth arrives", () => {
    const a = rolled(0, 8);
    const b = rolled(1, 9);
    const keyOf = (v: ReturnType<typeof rolled>, text: string) => v.stream.find((x) => x.type === "line" && x.text === text)?.key;
    for (let k = 1; k < 8; k++) assert.equal(keyOf(b, `line ${k}`), keyOf(a, `line ${k}`), `line ${k}`);
    const la = new Map(a.lines.map((x) => [x.text, x.key]));
    for (const x of b.lines) if (la.has(x.text)) assert.equal(x.key, la.get(x.text), x.text);
  });

  it("receipts: every receipt still there keeps its key when the thirteenth arrives", () => {
    const a = rolled(0, 12, true);
    const b = rolled(1, 13, true);
    const aKeys = new Set(a.stream.map((x) => x.key));
    const kept = b.stream.slice(0, -1).filter((x) => aKeys.has(x.key)).length;
    assert.equal(kept, b.stream.length - 1, "all but the new receipt keep their keys");
    const ra = new Set(a.receipts.map((x) => x.key));
    assert.equal(b.receipts.filter((x) => ra.has(x.key)).length, 11);
  });

  it("a receipt keeps its key when it finishes (running → ok)", () => {
    const a = live();
    const b = live();
    b.thinking!.receipts = b.thinking!.receipts.map((r) => (r.state === "running" ? { ...r, state: "ok" as const } : r));
    assert.deepEqual(thoughtView(a, NOW, { tz }).stream.map((x) => x.key), thoughtView(b, NOW, { tz }).stream.map((x) => x.key));
  });

  it("keys stay unique when two items match", () => {
    const l = live();
    l.thinking!.lines = [{ text: "Same.", at: ago(5) }, { text: "Same.", at: ago(5) }];
    l.thinking!.receipts = [{ tool: "web_search", state: "ok", at: ago(4) }, { tool: "web_search", state: "ok", at: ago(4) }];
    const keys = thoughtView(l, NOW, { tz }).stream.map((x) => x.key);
    assert.equal(new Set(keys).size, keys.length);
  });
});

describe("liveOnStage", () => {
  const thinking = live();
  it("a good poll passes his thinking through", () => {
    assert.equal(liveOnStage({ data: thinking, failures: 0 }, { offline: false }), thinking);
    assert.equal(liveOnStage({ data: thinking, failures: 1 }, { offline: false }), thinking, "one blip is not an outage");
  });
  it("two failed reads in a row drop the last good answer: stale thinking is not thinking", () => {
    assert.equal(liveOnStage({ data: thinking, failures: 2 }, { offline: false }), null);
  });
  it("while he is offline, the lane never claims thinking", () => {
    assert.equal(liveOnStage({ data: thinking, failures: 0 }, { offline: true }), null);
  });
  it("nothing yet is nothing", () => {
    assert.equal(liveOnStage({ data: null, failures: 0 }, { offline: false }), null);
  });
});

// ---------------------------------------------------------------------------
// On his mind (round 2): when no turn runs and nothing just folded, the lane
// drifts through his own recent thoughts — one at a time, at random, each
// labelled with its own time, so a past thought never reads as live.
// ---------------------------------------------------------------------------

/** A rand that returns the given values in turn, then repeats the last. */
function seq(...values: number[]): () => number {
  let i = 0;
  return () => values[Math.min(i++, values.length - 1)];
}

const thought = (text: string, s: number, about: string | null = "morning", kind = "mind"): RecentThought => ({ text, at: ago(s), about, kind });

describe("mindPool", () => {
  it("his recent thoughts, oldest first, without blanks, twins or anything unreadable", () => {
    const pool = mindPool(live({
      thinking: null,
      recent: [
        thought("Biscuit's meds are at six; nobody has logged them.", 3 * 3600),
        thought("   ", 2 * 3600),
        { text: "No time.", at: "not a time", about: null, kind: "mind" },
        thought("Parker's Saturday plan is still open.", 3600),
        thought("Parker's Saturday plan is still open.", 1800),
        thought("I read up on kayak rentals for Saturday.", 600, "free", "surplus"),
      ],
    }), NOW);
    assert.deepEqual(pool.map((t) => t.text), [
      "Biscuit's meds are at six; nobody has logged them.",
      "Parker's Saturday plan is still open.",
      "I read up on kayak rentals for Saturday.",
    ]);
  });

  it("nothing older than twelve hours, and an old backend without recent is an empty pool", () => {
    assert.deepEqual(mindPool(live({ thinking: null, recent: [thought("Yesterday.", 13 * 3600)] }), NOW), []);
    assert.deepEqual(mindPool(live({ thinking: null }), NOW), []);
    assert.deepEqual(mindPool(null, NOW), []);
    assert.deepEqual(mindPool(EMPTY_LIVE, NOW), []);
  });
});

describe("mindKey", () => {
  it("a thought is who it is by its words, not by a timestamp a poll may restamp", () => {
    assert.equal(mindKey(thought("Same words.", 60)), mindKey(thought("Same words.", 61)));
    assert.notEqual(mindKey(thought("One.", 60)), mindKey(thought("Two.", 60)));
  });
});

describe("pickMindThought", () => {
  const pool = ["One.", "Two.", "Three.", "Four."].map((t, i) => thought(t, 4000 - i * 600));
  const keys = pool.map(mindKey);

  it("chooses at random from the pool", () => {
    assert.equal(pickMindThought(pool, [], seq(0))?.text, "One.");
    assert.equal(pickMindThought(pool, [], seq(0.99))?.text, "Four.");
  });

  it("never the last two he showed", () => {
    for (const r of [0, 0.2, 0.4, 0.6, 0.8, 0.999]) {
      const t = pickMindThought(pool, [keys[1], keys[3]], seq(r))!;
      assert.ok(t.text === "One." || t.text === "Three.", `${r}: ${t.text}`);
    }
  });

  it("with two thoughts it alternates; with one it keeps it; with none there is nothing", () => {
    const two = pool.slice(0, 2);
    for (const r of [0, 0.5, 0.99]) assert.equal(pickMindThought(two, [keys[0]], seq(r))?.text, "Two.");
    assert.equal(pickMindThought(pool.slice(0, 1), [keys[0]], seq(0.5))?.text, "One.");
    assert.equal(pickMindThought([], [], seq(0.5)), null);
  });
});

describe("nextMindDelayMs", () => {
  it("a random 25 to 70 seconds", () => {
    assert.equal(MIND_MIN_MS, 25_000);
    assert.equal(MIND_MAX_MS, 70_000);
    assert.equal(nextMindDelayMs(() => 0), 25_000);
    assert.equal(nextMindDelayMs(() => 0.999999), 69_999);
  });
});

describe("mindOverline", () => {
  it("'On my mind' with the thought's own time", () => {
    assert.deepEqual(mindOverline(thought("x", 21 * 60), { tz, dreaming: false }), { label: "On my mind", time: "5:10 pm" });
  });
  it("his free time is daydreaming — in the shapes the backend really sends (screen_thoughts.recent_lines)", () => {
    // The surplus free_time job's note, and a free wake's own mind row: both about "free".
    assert.deepEqual(mindOverline(thought("x", 21 * 60, "free", "surplus"), { tz, dreaming: false }), { label: "Daydreaming", time: "5:10 pm" });
    assert.deepEqual(mindOverline(thought("x", 21 * 60, "free", "mind"), { tz, dreaming: false }), { label: "Daydreaming", time: "5:10 pm" });
    // An older payload that named the job itself still reads as free time.
    assert.deepEqual(mindOverline(thought("x", 21 * 60, "free_time", "surplus"), { tz, dreaming: false }), { label: "Daydreaming", time: "5:10 pm" });
    // Other surplus work is not free time.
    assert.deepEqual(mindOverline(thought("x", 21 * 60, "research", "surplus"), { tz, dreaming: false }), { label: "On my mind", time: "5:10 pm" });
  });
  it("at night it is dreaming", () => {
    assert.deepEqual(mindOverline(thought("x", 21 * 60, "free", "surplus"), { tz, dreaming: true }), { label: "Dreaming", time: "5:10 pm" });
    assert.deepEqual(mindOverline(thought("x", 21 * 60), { tz, dreaming: true }), { label: "Dreaming", time: "5:10 pm" });
  });
});
