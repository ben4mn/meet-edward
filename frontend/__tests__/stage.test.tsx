// The stage on /screen (2026-09-28), drawn: the pieces where the scene put
// them, one live Edward, his words beside him, his thinking only while a turn
// runs, the why receipt bottom-left, and night. Round 2: when no turn runs
// his mind drifts through his recent thoughts (labelled as past, quieter than
// live), thought bubbles rise once, he wanders now and then, the lane keeps
// clear of the compose bar, and Ellis at the desk gets the normal stage.

import { afterEach, describe, it, mock } from "node:test";
import assert from "node:assert/strict";
import React from "react";
import { act, cleanup, render } from "@testing-library/react";

import { Stage } from "../app/(display)/display/components/Stage.tsx";
import type { ScreenPresence } from "../app/(display)/display/lib/screenPresence.ts";
import { stageFromSnapshot, type StagePiece, type StageView } from "../app/(display)/display/lib/stageModel.ts";
import type { RecentThought, ScreenLive } from "../app/(display)/display/lib/thinkingModel.ts";

afterEach(() => cleanup());

const tz = "America/Chicago";
const NOW = Date.parse("2026-09-28T22:52:00Z"); // 5:52 pm
const ago = (s: number) => new Date(NOW - s * 1000).toISOString();

const presence: ScreenPresence = {
  known: true, mood: "idle", line: "Resting", label: "Edward · Resting", tone: "ok", active: false, handsDown: false,
  poke: { beat: "nod", say: null },
};

const p = (id: string, kind: StagePiece["kind"], x: number, y: number, w: number, h: number, extra: Partial<StagePiece> = {}): StagePiece => ({
  id, kind, size: "m", enter: "fade", place: "center", rect: { x, y, w, h }, title: null, ...extra,
});

const PIECES: StagePiece[] = [
  p("title", "words", 1, 1, 14, 2, { text: "A quiet Monday afternoon." }),
  p("thought", "note", 1, 7, 9, 4, { title: "A thought", body: "While it's quiet I'm reading up on Saturday." }),
  p("later", "list", 15, 6, 8, 5, { title: "Later today", items: [{ label: "Biscuit's evening meds", detail: "6:00 pm" }, "Wet food"] }),
  p("miles", "stat", 17, 1, 6, 3, { title: "This week", value: "19 mi", detail: "long run 8 mi" }),
];

function view(over: Partial<StageView> = {}): StageView {
  return {
    stage: { cols: 24, rows: 12 },
    scene: {
      pieces: PIECES,
      me: { at: "floor", rect: { x: 10, y: 7, w: 4, h: 5 }, on: null, side: "left", look: null },
      why: "You're out and nothing needs you.", source: "edward", updated_at: "2026-09-28T21:10:00+00:00", updated_by: "edward",
    },
    asked: null, lines: [], ownerAtMini: false, ...over,
  };
}

function props(over: Partial<React.ComponentProps<typeof Stage>> = {}): React.ComponentProps<typeof Stage> {
  return {
    view: view(), mode: "stage", presence, cue: null, beat: null, live: null, showThinking: true, speaking: false,
    accent: "#3ecf9e", avatarPx: 340, tz, nowMs: NOW, lifeEnabled: false, ...over,
  };
}

const THINKING: ScreenLive = {
  thinking: {
    turn: "t1", source: "mind", about: "messages", since: ago(120),
    lines: [{ text: "Parker asked about Saturday.", at: ago(60) }, { text: "I shouldn't answer for him.", at: ago(5) }],
    receipts: [{ tool: "read_imessage_thread", state: "ok", at: ago(50) }],
  },
  folded: [], now: new Date(NOW).toISOString(),
};

describe("Stage", () => {
  it("draws every piece where the scene put it, and exactly one live Edward", () => {
    const { container, getByText } = render(<Stage {...props()} />);
    for (const piece of PIECES) assert.ok(container.querySelector(`[data-piece="${piece.id}"]`), piece.id);
    const title = container.querySelector('[data-piece="title"]') as HTMLElement;
    assert.equal(title.style.left, "4.1667%");
    getByText("A quiet Monday afternoon.");
    getByText("Biscuit's evening meds");
    getByText("19 mi");
    assert.equal(container.querySelectorAll(".pane-avatar.is-live").length, 1);
  });

  it("his first real scene is not news: no glance when it replaces the stock scene drawn before the first read", () => {
    const pane = (c: HTMLElement) => c.querySelector(".pane-avatar") as HTMLElement;
    const { container, rerender } = render(<Stage {...props({ view: stageFromSnapshot(null) })} />);
    rerender(<Stage {...props()} />);
    assert.equal(pane(container).dataset.glance, undefined, "arriving is not news");
    const more = view();
    more.scene = { ...more.scene, pieces: [...PIECES, p("new", "note", 17, 8, 6, 3, { body: "New." })] };
    rerender(<Stage {...props({ view: more })} />);
    assert.ok(pane(container).dataset.glance, "a piece that appears after that still earns a glance");
  });

  it("while the compose bar is up, the pieces under it fade back", () => {
    const low = view();
    low.scene = { ...low.scene, pieces: [...PIECES.filter((x) => x.id !== "thought"), p("thought", "note", 6, 8, 9, 4, { body: "Low." })] };
    const { container, rerender } = render(<Stage {...props({ view: low })} />);
    const note = () => container.querySelector('[data-piece="thought"]') as HTMLElement;
    assert.notEqual(note().dataset.dimmed, "true");
    rerender(<Stage {...props({ view: low, composeUp: true })} />);
    assert.equal(note().dataset.dimmed, "true", "the bar's band covers it");
    assert.notEqual((container.querySelector('[data-piece="miles"]') as HTMLElement).dataset.dimmed, "true", "the top of the stage stays");
  });

  it("the why receipt, bottom-left, in his words", () => {
    const { getByText } = render(<Stage {...props()} />);
    getByText("I arranged this at 4:10 pm — you're out and nothing needs you.");
  });

  it("Ellis's ask says so instead", () => {
    const { getByText } = render(<Stage {...props({ view: view({ asked: { at: "2026-09-28T23:10:00+00:00", until: "2026-09-29T01:10:00+00:00" } }) })} />);
    getByText("You asked at 6:10 pm · stays up until 8:10 pm");
  });

  it("a fresh line is said beside him, the one before it muted above", () => {
    const lines = [
      { id: "a", text: "I've read this afternoon's messages.", at: ago(300), aloud: false },
      { id: "b", text: "Biscuit's evening meds are due at 6.", at: ago(20), aloud: true },
    ];
    const { container } = render(<Stage {...props({ view: view({ lines }), playedAloud: new Set(["b"]) })} />);
    assert.equal(container.querySelector(".st-speech-over")?.textContent, "Said aloud · 5:51 pm");
    const lane = container.querySelector("[data-lane=speech]") as HTMLElement;
    assert.ok(lane);
    assert.match(lane.textContent ?? "", /I've read this afternoon's messages\.[\s\S]*Biscuit's evening meds are due at 6\./);
  });

  it("'Said aloud' only when it really played: asked for aloud but silent (switch off, autoplay refused) is 'Said'", () => {
    const lines = [{ id: "b", text: "Biscuit's evening meds are due at 6.", at: ago(20), aloud: true }];
    const { container } = render(<Stage {...props({ view: view({ lines }) })} />);
    const over = container.querySelector(".st-speech-over") as HTMLElement;
    assert.equal(over.textContent, "Said · 5:51 pm");
    assert.equal(over.querySelector("svg"), null, "no speaker icon");
  });

  it("the overline's clock keeps the one format (its capitals are only for words)", () => {
    const lines = [{ id: "b", text: "Biscuit's evening meds are due at 6.", at: ago(20), aloud: false }];
    const { container } = render(<Stage {...props({ view: view({ lines }) })} />);
    assert.equal(container.querySelector(".st-speech-over .st-overline-clock")?.textContent, "5:51 pm");
  });

  it("while a turn runs his thinking fills the lane and what it crosses fades back", async () => {
    const { container, getByText, findByText } = render(<Stage {...props({ live: THINKING })} />);
    getByText("Thinking · reading your messages");
    getByText("since 5:50 pm");
    getByText("I shouldn't answer for him.");
    await findByText("Read the thread"); // Chat's labels, loaded once a turn runs
    // Edward stands mid-floor; the left has more free room, so the lane is there.
    assert.equal((container.querySelector("[data-lane=thinking]") as HTMLElement).dataset.side, "left");
    const note = container.querySelector('[data-piece="thought"]') as HTMLElement;
    assert.equal(note.dataset.dimmed, "true", "the note under the lane fades back");
    const later = container.querySelector('[data-piece="later"]') as HTMLElement;
    assert.notEqual(later.dataset.dimmed, "true", "the list across the stage does not");
    assert.equal(container.querySelector("[data-edward-status] > span:last-child")?.textContent, "Thinking");
  });

  it("while his thinking fills the lane, the lane is his receipt: no why line under it", () => {
    const { container } = render(<Stage {...props({ live: THINKING })} />);
    assert.doesNotMatch(container.textContent ?? "", /I arranged this/);
  });

  it("while Ellis's ask holds, his words stay small beside him and nothing Ellis asked for fades", () => {
    const lines = [{ id: "b", text: "I'll say it out loud when it's done.", at: ago(20), aloud: false }];
    const asked = { at: ago(120), until: new Date(NOW + 600_000).toISOString() };
    const { container } = render(<Stage {...props({ view: view({ lines, asked }) })} />);
    const root = container.querySelector("[data-stage]") as HTMLElement;
    assert.equal(root.dataset.compact, "true");
    assert.ok(container.querySelector("[data-lane=speech]"));
    assert.equal(container.querySelectorAll('[data-piece][data-dimmed="true"]').length, 0);
  });

  it("the dot beside his line is his light while he thinks, whatever waits", () => {
    const waiting = { ...presence, mood: "attention" as const, tone: "warn" as const, line: "Waiting on your approval" };
    const { container } = render(<Stage {...props({ presence: waiting, live: THINKING })} />);
    const dot = container.querySelector("[data-edward-status] .mc-dot") as HTMLElement;
    assert.match(dot.style.background, /ed-accent/);
  });

  it("with thinking on screen turned off, no lane — but his line still says Thinking", () => {
    const { container } = render(<Stage {...props({ live: THINKING, showThinking: false })} />);
    assert.equal(container.querySelector("[data-lane=thinking]"), null);
    assert.equal(container.querySelector("[data-edward-status] > span:last-child")?.textContent, "Thinking");
  });

  it("no thinking is ever drawn when no turn runs", () => {
    const { container } = render(<Stage {...props({ live: { thinking: null, folded: [], now: "" } })} />);
    assert.equal(container.querySelector("[data-lane=thinking]"), null);
    assert.doesNotMatch(container.textContent ?? "", /Thinking/);
  });

  it("Ellis at the desk gets the normal stage: nothing dims, Edward is himself where the scene put him, the receipt stays", () => {
    const { container, getByText } = render(<Stage {...props({ view: view({ ownerAtMini: true }) })} />);
    const root = container.querySelector("[data-stage]") as HTMLElement;
    assert.equal(root.dataset.mode, "stage");
    assert.equal(root.dataset.compact, undefined);
    assert.equal(container.querySelector(".pane-avatar[data-variant=bust]"), null);
    assert.equal(container.querySelectorAll('[data-piece][data-dimmed="true"]').length, 0);
    assert.equal((container.querySelector("[data-stage-edward]") as HTMLElement).dataset.at, "floor");
    getByText("I arranged this at 4:10 pm — you're out and nothing needs you.");
  });

  it("at night the whole stage drifts a little", () => {
    const { container } = render(<Stage {...props({ mode: "night", drift: { x: 7, y: -3 } })} />);
    const root = container.querySelector("[data-stage]") as HTMLElement;
    assert.equal(root.dataset.mode, "night");
    assert.equal(root.style.transform, "translate(7px, -3px)");
  });

  it("an html piece is sandboxed; a video embeds or plays", () => {
    const pieces = [
      p("h", "html", 1, 1, 8, 5, { body: "<p>hi</p>" }),
      p("v", "video", 12, 1, 10, 6, { source: "https://youtu.be/x", embed_url: "https://www.youtube.com/embed/x" }),
      p("m", "video", 12, 7, 8, 4, { source: "https://example.com/clip.mp4", embed_url: null }),
    ];
    const v = view();
    const { container } = render(<Stage {...props({ view: { ...v, scene: { ...v.scene, pieces } } })} />);
    const html = container.querySelector('[data-piece="h"] iframe') as HTMLIFrameElement;
    assert.equal(html.getAttribute("sandbox"), "allow-scripts");
    const yt = container.querySelector('[data-piece="v"] iframe') as HTMLIFrameElement;
    assert.equal(yt.getAttribute("src"), "https://www.youtube.com/embed/x");
    assert.ok(container.querySelector('[data-piece="m"] video'));
  });

  it("a timer counts down to when it is done", () => {
    const pieces = [p("pasta", "timer", 1, 1, 12, 6, { title: "Pasta", started_at: ago(139), ends_at: new Date(NOW + 521_000).toISOString() })];
    const v = view();
    const { getByText } = render(<Stage {...props({ view: { ...v, scene: { ...v.scene, pieces } } })} />);
    getByText("08:41");
    getByText("Done at 6:00 pm");
  });

  it("while the compose bar is up his thinking ends above it, never under it", () => {
    const { container } = render(<Stage {...props({ live: THINKING, composeUp: true })} />);
    const lane = container.querySelector("[data-lane=thinking]") as HTMLElement;
    assert.equal(lane.style.top, "8.3333%");
    assert.equal(lane.style.height, "75%", "rows 1 to 10: the bar's band starts at row 10");
  });
});

// ---------------------------------------------------------------------------
// On his mind (round 2)
// ---------------------------------------------------------------------------

/** A rand that returns the given values in turn, then repeats the last. */
function seq(...values: number[]): () => number {
  let i = 0;
  return () => values[Math.min(i++, values.length - 1)];
}

const recent = (text: string, s: number, about: string | null = "morning", kind = "mind"): RecentThought => ({ text, at: ago(s), about, kind });
const MIND: ScreenLive = {
  thinking: null,
  folded: [],
  recent: [
    recent("Biscuit's evening meds are at six and nobody has logged them yet.", 42 * 60),
    recent("Parker's Saturday plan is still open; Ellis hasn't answered.", 30 * 60),
    recent("I read up on kayak rentals near the lake for Saturday.", 20 * 60, "free", "surplus"),
  ],
  now: new Date(NOW).toISOString(),
};
const mindText = (c: HTMLElement) => (c.querySelector("[data-lane=mind] .st-mind-item:not([data-leaving]) .st-mind-text") as HTMLElement | null)?.textContent ?? null;
const mindOver = (c: HTMLElement) => (c.querySelector("[data-lane=mind] .st-mind-item:not([data-leaving]) .st-mind-over") as HTMLElement | null)?.textContent ?? null;

describe("Stage — on his mind", () => {
  it("no turn and nothing just folded: one recent thought, labelled with its own time", () => {
    const { container } = render(<Stage {...props({ live: MIND, rand: seq(0) })} />);
    assert.ok(container.querySelector("[data-lane=mind]"), "the lane is not empty");
    assert.equal(mindText(container), "Biscuit's evening meds are at six and nobody has logged them yet.");
    assert.equal(mindOver(container), "On my mind · 5:10 pm");
    assert.equal(container.querySelector("[data-lane=mind] .st-mind-over .st-overline-clock")?.textContent, "5:10 pm");
  });

  it("quieter than live: no Thinking, no pip, no caret, and his line stays his presence line", () => {
    const { container } = render(<Stage {...props({ live: MIND, rand: seq(0) })} />);
    assert.doesNotMatch(container.textContent ?? "", /Thinking/);
    assert.equal(container.querySelector("[data-lane=mind] .st-pip"), null);
    assert.equal(container.querySelector("[data-lane=mind] .st-caret"), null);
    assert.equal(container.querySelector("[data-edward-status] > span:last-child")?.textContent, "Resting");
    assert.equal(container.querySelector("[data-lane=thinking]"), null);
  });

  it("free time is daydreaming; at night it is dreaming", () => {
    const day = render(<Stage {...props({ live: MIND, rand: seq(0.99) })} />);
    assert.equal(mindOver(day.container), "Daydreaming · 5:32 pm");
    cleanup();
    const night = render(<Stage {...props({ live: MIND, rand: seq(0), dreaming: true, mode: "night" })} />);
    assert.equal(mindOver(night.container), "Dreaming · 5:10 pm");
  });

  it("with thinking on screen turned off, nothing of his mind", () => {
    const { container } = render(<Stage {...props({ live: MIND, showThinking: false })} />);
    assert.equal(container.querySelector("[data-lane=mind]"), null);
    assert.doesNotMatch(container.textContent ?? "", /On my mind/);
  });

  it("a thought that just folded surfaces large, then his mind drifts on; live thinking wins; with no recent thoughts the lane may be empty", () => {
    // Round 2, after the gate: a finished mind turn is shown the way his mind is — large, "Just thought" —
    // not as a footnote under an empty lane (with 10–20 turns an hour the footnote was nearly always what showed).
    const open = view();
    open.scene = { ...open.scene, pieces: PIECES.filter((x) => x.id !== "thought") };
    const fresh = { ...MIND, folded: [{ text: "I went over the lunch messages.", about: "messages", at: ago(40), tools: 2 }] };
    const a = render(<Stage {...props({ view: open, live: fresh, rand: seq(0) })} />);
    assert.equal(mindOver(a.container), "Just thought · 5:51 pm");
    assert.equal(mindText(a.container), "I went over the lunch messages.");
    assert.equal(a.container.querySelector("[data-lane=fold]"), null, "no footnote while he has a thought to show");
    cleanup();
    // Past its moment the fold joins his mind's drift: a recent thought, not the footnote.
    const older = { ...MIND, folded: [{ text: "I went over the lunch messages.", about: "messages", at: ago(5 * 60), tools: 2 }] };
    const a2 = render(<Stage {...props({ view: open, live: older, rand: seq(0) })} />);
    assert.equal(mindOver(a2.container), "On my mind · 5:10 pm");
    assert.equal(a2.container.querySelector("[data-lane=fold]"), null);
    cleanup();
    // The pulse's note about his screens is not a thought to put up large.
    const pulse = { ...MIND, folded: [{ text: "Left all surfaces unchanged.", about: "surfaces", at: ago(40), tools: 1 }] };
    const a3 = render(<Stage {...props({ view: open, live: pulse, rand: seq(0) })} />);
    assert.equal(mindOver(a3.container), "On my mind · 5:10 pm");
    assert.doesNotMatch(a3.container.textContent ?? "", /Left all surfaces/);
    cleanup();
    // With nothing else of his mind to show, the fold's one line is still there.
    const alone = { thinking: null, folded: [{ text: "I went over the lunch messages.", about: "messages", at: ago(5 * 60), tools: 2 }], recent: [], now: "" };
    const a4 = render(<Stage {...props({ view: open, live: alone, rand: seq(0) })} />);
    assert.ok(a4.container.querySelector("[data-lane=fold]"), "the fold's one line on the lane's floor");
    cleanup();
    const b = render(<Stage {...props({ live: { ...THINKING, recent: MIND.recent }, rand: seq(0) })} />);
    assert.ok(b.container.querySelector("[data-lane=thinking]"));
    assert.equal(b.container.querySelector("[data-lane=mind]"), null);
    cleanup();
    const c = render(<Stage {...props({ live: { thinking: null, folded: [], recent: [], now: "" } })} />);
    assert.equal(c.container.querySelector("[data-lane]"), null);
  });

  it("the thought beside him fades nothing when there is free room", () => {
    const { container } = render(<Stage {...props({ live: MIND, rand: seq(0) })} />);
    assert.equal(container.querySelectorAll('[data-piece][data-dimmed="true"]').length, 0);
  });

  it("drifts to another thought at a random 25–70 s, never the last two, as a single cross-fade", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      // First pick: 0 → the first thought. Delay: 0.5 → 47.5 s. Next pick among the rest: 0.99 → the last.
      const { container } = render(<Stage {...props({ live: MIND, rand: seq(0, 0.5, 0.99, 0.5, 0) })} />);
      assert.equal(mindText(container), "Biscuit's evening meds are at six and nobody has logged them yet.");
      act(() => mock.timers.tick(47_000));
      assert.equal(mindText(container), "Biscuit's evening meds are at six and nobody has logged them yet.", "not yet");
      act(() => mock.timers.tick(1_000));
      assert.equal(mindText(container), "I read up on kayak rentals near the lake for Saturday.");
      const leaving = container.querySelector("[data-lane=mind] .st-mind-item[data-leaving]");
      assert.equal(leaving?.querySelector(".st-mind-text")?.textContent, "Biscuit's evening meds are at six and nobody has logged them yet.", "the old one fades out as the new one fades in");
      // Next: the last two were Biscuit and kayaks, so only Parker is left, whatever rand says.
      act(() => mock.timers.tick(70_000));
      assert.equal(mindText(container), "Parker's Saturday plan is still open; Ellis hasn't answered.");
    } finally {
      mock.timers.reset();
    }
  });

  it("thought bubbles rise once from his head when a thought surfaces and when a live turn starts, and he glances up", () => {
    const { container, rerender } = render(<Stage {...props({ live: MIND, rand: seq(0) })} />);
    const bubbles = () => container.querySelector(".st-bubbles") as HTMLElement | null;
    assert.ok(bubbles(), "a new thought on his mind");
    assert.equal(bubbles()!.querySelectorAll("circle").length, 3);
    assert.equal((container.querySelector(".pane-avatar") as HTMLElement).dataset.glance, "up");
    const first = bubbles()!.dataset.n;
    rerender(<Stage {...props({ live: THINKING, rand: seq(0) })} />);
    assert.notEqual(bubbles()?.dataset.n, first, "a live turn starting sends a fresh set");
  });
});

describe("Stage — around the compose bar's reply card", () => {
  const cells = (el: HTMLElement) => {
    const n = (v: string) => parseFloat(v);
    return { x: Math.round((n(el.style.left) / 100) * 24), y: Math.round((n(el.style.top) / 100) * 12), w: Math.round((n(el.style.width) / 100) * 24), h: Math.round((n(el.style.height) / 100) * 12) };
  };
  const card = { x: 5, y: 5, w: 14, h: 7 };
  const overlaps = (a: typeof card, b: typeof card) => a.x < b.x + b.w && b.x < a.x + a.w && a.y < b.y + b.h && b.y < a.y + a.h;

  it("a thought on his mind moves clear of the card instead of hiding under it", () => {
    const { container } = render(<Stage {...props({ live: MIND, rand: seq(0), composeUp: true, composeRect: card })} />);
    const lane = container.querySelector("[data-lane=mind]") as HTMLElement;
    assert.ok(lane, "still on his mind");
    assert.equal(overlaps(cells(lane), card), false, JSON.stringify(cells(lane)));
  });

  it("the bar never covers him: with no room beside it he stands up on it, and goes back down when it goes", () => {
    const edward = (c: HTMLElement) => c.querySelector("[data-stage-edward]") as HTMLElement;
    const { container, rerender } = render(<Stage {...props({ live: MIND, rand: seq(0) })} />);
    assert.deepEqual(cells(edward(container)), { x: 10, y: 7, w: 4, h: 5 });
    rerender(<Stage {...props({ live: MIND, rand: seq(0), composeUp: true })} />);
    const up = cells(edward(container));
    assert.equal(overlaps(up, { x: 3, y: 10, w: 18, h: 2 }), false, JSON.stringify(up));
    assert.deepEqual(up, { x: 10, y: 5, w: 4, h: 5 });
    assert.equal(edward(container).dataset.walk, "fade", "a lift is a fade, not a slide");
    const lane = container.querySelector("[data-lane=mind]") as HTMLElement;
    assert.ok(lane, "his mind stays");
    assert.equal(overlaps(cells(lane), up), false, "and is laid out from where he stands");
    rerender(<Stage {...props({ live: MIND, rand: seq(0) })} />);
    assert.deepEqual(cells(edward(container)), { x: 10, y: 7, w: 4, h: 5 });
  });

  it("with room beside the bar on the floor he walks aside, and back", () => {
    const edward = (c: HTMLElement) => c.querySelector("[data-stage-edward]") as HTMLElement;
    const v = view();
    const openView = { ...v, scene: { ...v.scene, pieces: [p("title", "words", 1, 1, 14, 2, { text: "A quiet Monday afternoon." })], me: { ...v.scene.me, rect: { x: 4, y: 7, w: 4, h: 5 } } } };
    const bar = { x: 4, y: 10, w: 16, h: 2 };
    const { container, rerender } = render(<Stage {...props({ view: openView })} />);
    rerender(<Stage {...props({ view: openView, composeUp: true, composeRect: bar })} />);
    assert.deepEqual(cells(edward(container)), { x: 0, y: 7, w: 4, h: 5 });
    assert.equal(edward(container).dataset.walk, "walk");
    rerender(<Stage {...props({ view: openView })} />);
    assert.deepEqual(cells(edward(container)), { x: 4, y: 7, w: 4, h: 5 });
    assert.equal(edward(container).dataset.walk, "walk", "the floor is clear on the way back too");
  });

  it("his live thinking ends above the card", () => {
    const { container } = render(<Stage {...props({ live: THINKING, composeUp: true, composeRect: card })} />);
    const lane = container.querySelector("[data-lane=thinking]") as HTMLElement;
    assert.equal(lane.style.height, "33.3333%", "rows 1 to 5");
  });
});

describe("Stage — wandering", () => {
  const OPEN: StagePiece[] = [p("title", "words", 1, 1, 14, 2, { text: "A quiet Monday afternoon." })];
  const open = (x: number) => {
    const v = view();
    return { ...v, scene: { ...v.scene, pieces: OPEN, me: { ...v.scene.me, rect: { x, y: 7, w: 4, h: 5 } } } };
  };

  it("every one to four minutes he walks somewhere else on the floor, and his lane is laid out from there", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      const { container } = render(<Stage {...props({ view: open(4), live: MIND, rand: seq(0.99), wanderEnabled: true })} />);
      const edward = () => container.querySelector("[data-stage-edward]") as HTMLElement;
      const root = () => container.querySelector("[data-stage]") as HTMLElement;
      assert.equal(edward().style.left, "16.6667%");
      assert.equal(root().dataset.laneSide, "right");
      act(() => mock.timers.tick(59_000));
      assert.equal(edward().style.left, "16.6667%", "not before a minute");
      act(() => mock.timers.tick(180_000));
      assert.equal(edward().style.left, "83.3333%", "the far end of the free floor");
      assert.equal(edward().dataset.walk, "walk", "the floor is clear: he walks");
      assert.match(edward().getAttribute("style") ?? "", /--st-walk-ms:\s*4000ms/);
      assert.equal(root().dataset.laneSide, "left", "his lane follows him");
      assert.equal(container.querySelector("[data-lane=mind]"), null, "his mind waits while he walks");
      act(() => mock.timers.tick(4_000));
      assert.ok(container.querySelector("[data-lane=mind]"), "and comes back beside him when he arrives");
    } finally {
      mock.timers.reset();
    }
  });

  it("a new scene puts him back where it says", () => {
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      const { container, rerender } = render(<Stage {...props({ view: open(4), rand: seq(0.99), wanderEnabled: true })} />);
      act(() => mock.timers.tick(240_000));
      const edward = () => container.querySelector("[data-stage-edward]") as HTMLElement;
      assert.notEqual(edward().style.left, "16.6667%");
      rerender(<Stage {...props({ view: open(6), rand: seq(0.99), wanderEnabled: true })} />);
      assert.equal(edward().style.left, "25%");
    } finally {
      mock.timers.reset();
    }
  });

  it("a new scene that keeps his spot but lays a piece where he wandered puts him back too, never on the piece", () => {
    // screen_service.place_scene stands him first at the floor's default spot, so most new
    // scenes keep his spot and lay their pieces anywhere else — including where he wandered.
    mock.timers.enable({ apis: ["setTimeout"] });
    try {
      const { container, rerender } = render(<Stage {...props({ view: open(4), rand: seq(0.99), wanderEnabled: true })} />);
      const edward = () => container.querySelector("[data-stage-edward]") as HTMLElement;
      act(() => mock.timers.tick(240_000));
      assert.equal(edward().style.left, "83.3333%", "he wandered to x 20");
      const next = open(4);
      next.scene = {
        ...next.scene,
        updated_at: "2026-09-28T22:40:00+00:00",
        pieces: [...OPEN, p("note", "note", 18, 5, 6, 6, { body: "Where he was standing." })],
      };
      rerender(<Stage {...props({ view: next, rand: seq(0.99), wanderEnabled: true })} />);
      assert.equal(edward().style.left, "16.6667%", "back on the scene's spot");
    } finally {
      mock.timers.reset();
    }
  });
});
