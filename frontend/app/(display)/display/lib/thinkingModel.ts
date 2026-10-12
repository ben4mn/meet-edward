/** His thinking on /screen (2026-09-28) — pure, tested in __tests__/thinkingModel.test.ts.
 *
 * GET /api/screen/live (services/screen_thoughts.py) carries a mind or
 * surfaces turn while it really runs: its reasoning summaries as short lines
 * and its tool receipts (names only, never arguments or results). When the
 * turn ends it folds into one line. This turns that into the thought lane:
 * what it is about in his words (never a raw reason), lines that fade with
 * age, receipts labelled the way Chat labels them, and the last fold.
 *
 * Round 2 (2026-09-28: "it should always show what he is thinking or
 * dreaming"): when no turn runs and nothing folded in the last ten minutes,
 * the lane drifts through his own recent thoughts (`recent`, from durable
 * rows: wakes' closing notes, free time's notes) — one at a time, chosen at
 * random, each labelled with its own time so a past thought never reads as
 * live. Randomness is injected (`rand`); nothing here calls Math.random.
 */

import { reasonPhrase } from "@/lib/edwardMood";
import { formatClock, parseIso } from "@/lib/time";

/** lib/toolLabels.ts::describeTool — loaded lazily by the page (it is Chat's
 *  whole label table, too heavy for /screen's first load), handed in here. */
export type DescribeTool = (name: string) => { title: string; live: string; failed: string };

export interface LiveLine {
  text: string;
  at: string;
  /** Per-turn sequence number, when the backend stamps one: the item's identity. */
  n?: number;
}

export type ReceiptState = "running" | "ok" | "error";

export interface LiveReceipt {
  tool: string;
  state: ReceiptState;
  at: string;
  n?: number;
}

export interface LiveThinking {
  turn: string;
  source: string;
  /** The wake's reason ("messages", "surfaces"…), or null. */
  about: string | null;
  since: string;
  lines: LiveLine[];
  receipts: LiveReceipt[];
}

export interface ThoughtFold {
  text: string;
  about: string | null;
  at: string;
  tools: number;
}

/** One of his own recent thoughts (services/screen_thoughts.py `recent`). */
export interface RecentThought {
  text: string;
  at: string;
  /** The wake's reason, or the surplus job ("free_time"…), or null. */
  about: string | null;
  kind?: string | null;
}

export interface ScreenLive {
  thinking: LiveThinking | null;
  folded: ThoughtFold[];
  /** His thoughts from the last twelve hours, newest last (an old backend has none). */
  recent?: RecentThought[];
  now: string;
}

export const EMPTY_LIVE: ScreenLive = { thinking: null, folded: [], recent: [], now: "" };

/** A `speak` wake is about something he noticed in the owner's messages; the
 *  stage is a screen anyone in the room can read. The backend never sends its
 *  words (services/wake_state.py::SPEAK_LINE, which services/screen_thoughts.py
 *  serves); this holds the line on this side too: no lines while it runs, one
 *  fixed line after (2026-10-11). It must equal the backend's exactly. */
export const PRIVATE_ABOUTS: ReadonlySet<string> = new Set(["speak"]);
export const SPEAK_LINE = "Something came up — I left a note.";

function isPrivate(about: string | null | undefined): boolean {
  return !!about && PRIVATE_ABOUTS.has(about);
}

/** services/screen_thoughts.py::STALE_AFTER: a live turn older than this is
 *  a crashed one, and a crashed turn must not look like thinking. The backend
 *  drops it too, but only a poll that gets through can say so. */
export const LIVE_STALE_AFTER_MS = 20 * 60_000;
/** screenPresence.SCREEN_OFFLINE_AFTER: two failed reads in a row. */
export const LIVE_FAILED_AFTER = 2;

/** What the stage may believe about his thinking. The shared poll keeps the
 *  last good answer through a failure; past two failed reads in a row (or
 *  while he is offline) that answer is stale, and stale thinking is not
 *  thinking — the lane must not say "Thinking" beside an "Offline" line. */
export function liveOnStage<T extends ScreenLive>(
  poll: { data: T | null | undefined; failures: number },
  { offline }: { offline: boolean },
): T | null {
  if (offline || poll.failures >= LIVE_FAILED_AFTER) return null;
  return poll.data ?? null;
}

/** A stable identity for each item: the backend keeps a rolling window (8
 *  lines, 12 receipts), so a position shifts for every survivor when the
 *  oldest drops — and a changed key replays every fade. Its own sequence
 *  number when stamped, else what it is and when, counted among twins. */
function stableKeys<T>(items: T[], base: (x: T) => string): string[] {
  const seen = new Map<string, number>();
  return items.map((x) => {
    const b = base(x);
    const k = seen.get(b) ?? 0;
    seen.set(b, k + 1);
    return k ? `${b}|${k}` : b;
  });
}

/** A finished turn's fold is kept for ten minutes: the one line on the lane's floor when nothing else of his mind shows. */
export const FOLD_WITHIN_MS = 10 * 60_000;

/**
 * A mind turn that just finished surfaces large, "Just thought", for its moment
 * (then his mind drifts on). With 10–20 turns an hour a ten-minute "just thought"
 * footnote was nearly always what the lane showed. The pulse's own notes about his
 * screens (about "surfaces") are not thoughts to put up large.
 */
export const JUST_THOUGHT_MS = 90_000;

export function justThoughtOf(fold: ThoughtView["fold"], nowMs: number): RecentThought | null {
  if (!fold || fold.about === "surfaces") return null;
  const at = timeOf(fold.at);
  if (!at || nowMs - at > JUST_THOUGHT_MS) return null;
  return { text: fold.text, at: fold.at, about: fold.about };
}
/** The oldest line on screen is this faint; the newest is fully there. */
export const OLDEST_OPACITY = 0.35;

export interface ThoughtLine {
  key: string;
  text: string;
  opacity: number;
  newest: boolean;
}

export interface ThoughtReceipt {
  key: string;
  label: string;
  state: ReceiptState;
}

/** Lines and receipts in the order they happened: what the lane draws. */
export type StreamItem =
  | { type: "line"; key: string; text: string; opacity: number; newest: boolean }
  | { type: "receipt"; key: string; label: string; state: ReceiptState; opacity: number };

/** The lane keeps the last few moments; older ones have scrolled away. */
export const MAX_STREAM = 9;

export interface ThoughtView {
  active: boolean;
  /** "reading your messages" — his words for the reason, or null. */
  about: string | null;
  /** "5:30 pm" */
  since: string | null;
  lines: ThoughtLine[];
  receipts: ThoughtReceipt[];
  stream: StreamItem[];
  /** The last finished turn, "Earlier · 2:10 pm — …" (`at`: when it ended). */
  fold: { text: string; line: string; at: string; about: string | null } | null;
}

function fade(i: number, n: number): number {
  return n <= 1 ? 1 : Number((OLDEST_OPACITY + ((1 - OLDEST_OPACITY) * i) / (n - 1)).toFixed(2));
}

function timeOf(iso: string): number {
  const t = parseIso(iso).getTime();
  return Number.isFinite(t) ? t : 0;
}

/** Until the labels load: the tool's name as words, never snake_case. */
function plainName(tool: string): string {
  const words = tool.replace(/^mcp_[a-z0-9]+_/, "").split(/[_\s]+/).filter(Boolean).join(" ");
  return words ? words[0].toUpperCase() + words.slice(1) : "A step";
}

function receiptLabel(r: LiveReceipt, describe?: DescribeTool): string {
  if (!describe) {
    const name = plainName(r.tool);
    if (r.state === "running") return `${name}…`;
    return r.state === "error" ? `Couldn't finish: ${name}` : name;
  }
  const d = describe(r.tool);
  if (r.state === "running") return d.live;
  if (r.state === "error") return d.failed;
  return d.title;
}

export function thoughtView(
  live: ScreenLive | null | undefined,
  nowMs: number,
  { tz, describe }: { tz?: string; describe?: DescribeTool } = {},
): ThoughtView {
  const raw = live?.thinking ?? null;
  const stale = !!raw?.since && nowMs - timeOf(raw.since) > LIVE_STALE_AFTER_MS && timeOf(raw.since) > 0;
  const t = stale ? null : raw;
  const lines: ThoughtLine[] = [];
  const receipts: ThoughtReceipt[] = [];
  let stream: StreamItem[] = [];
  if (t) {
    const texts = isPrivate(t.about) ? [] : (t.lines ?? []).filter((l) => l && typeof l.text === "string" && l.text.trim());
    const n = texts.length;
    const lineKeys = stableKeys(texts, (l) => (typeof l.n === "number" ? `#${l.n}` : `${l.at}|${l.text}`));
    texts.forEach((l, i) => {
      const opacity = fade(i, n);
      lines.push({ key: lineKeys[i], text: l.text, opacity, newest: i === n - 1 });
    });
    const valid = (t.receipts ?? []).filter((r) => r && typeof r.tool === "string" && r.tool);
    const receiptKeys = stableKeys(valid, (r) => (typeof r.n === "number" ? `#${r.n}` : `${r.tool}|${r.at}`));
    valid.forEach((r, i) => {
      receipts.push({ key: receiptKeys[i], label: receiptLabel(r, describe), state: r.state });
    });

    const timed: { at: number; order: number; item: StreamItem }[] = [];
    texts.forEach((l, i) =>
      timed.push({ at: timeOf(l.at), order: i, item: { type: "line", key: `l|${lineKeys[i]}`, text: l.text, opacity: 1, newest: false } }),
    );
    valid.forEach((r, i) => {
      timed.push({ at: timeOf(r.at), order: 100 + i, item: { type: "receipt", key: `r|${receiptKeys[i]}`, label: receiptLabel(r, describe), state: r.state, opacity: 1 } });
    });
    timed.sort((a, b) => a.at - b.at || a.order - b.order);
    stream = timed.slice(-MAX_STREAM).map((x, i, all) => ({ ...x.item, opacity: fade(i, all.length) }));
    const lastLine = [...stream].reverse().find((x) => x.type === "line");
    stream = stream.map((x) => (x === lastLine && x.type === "line" ? { ...x, newest: true } : x));
  }

  let fold: ThoughtView["fold"] = null;
  if (!t) {
    const newest = (live?.folded ?? [])
      .map((f) => ({ f, at: parseIso(f.at).getTime() }))
      .filter((x) => Number.isFinite(x.at) && x.f.text)
      .sort((a, b) => b.at - a.at)[0];
    if (newest && nowMs - newest.at <= FOLD_WITHIN_MS) {
      const text = isPrivate(newest.f.about) ? SPEAK_LINE : newest.f.text;
      fold = {
        text,
        line: `Earlier · ${formatClock(newest.f.at, { tz })} — ${text}`,
        at: newest.f.at,
        about: newest.f.about ?? null,
      };
    }
  }

  return {
    active: !!t,
    about: t ? reasonPhrase(t.about) : null,
    since: t?.since ? formatClock(t.since, { tz }) : null,
    lines,
    receipts,
    stream,
    fold,
  };
}

// ---------------------------------------------------------------------------
// On his mind: the drift through his recent thoughts
// ---------------------------------------------------------------------------

export type Rand = () => number;

/** The backend sends twelve hours; a thought older than that has had its turn. */
export const MIND_WITHIN_MS = 12 * 3600_000;
/** A thought stays up a random 25–70 s before another takes its place. */
export const MIND_MIN_MS = 25_000;
export const MIND_MAX_MS = 70_000;
/** He never shows either of the last two again straight away. */
export const MIND_AVOID_LAST = 2;

/** A thought is who it is by its words (the pool has no twins): a restamped time is the same thought. */
export function mindKey(t: RecentThought): string {
  return t.text.replace(/\s+/g, " ").trim().toLowerCase();
}

/** His recent thoughts worth drifting through: readable, within twelve hours, no twins; oldest first. */
export function mindPool(live: ScreenLive | null | undefined, nowMs: number): RecentThought[] {
  const seen = new Set<string>();
  const out: { t: RecentThought; at: number }[] = [];
  for (const r of live?.recent ?? []) {
    if (!r || typeof r.text !== "string" || typeof r.at !== "string") continue;
    const text = isPrivate(r.about) ? SPEAK_LINE : r.text.replace(/\s+/g, " ").trim();
    const at = timeOf(r.at);
    if (!text || !at || nowMs - at > MIND_WITHIN_MS) continue;
    const twin = text.toLowerCase();
    if (seen.has(twin)) continue;
    seen.add(twin);
    out.push({ t: { ...r, text }, at });
  }
  return out.sort((a, b) => a.at - b.at).map((x) => x.t);
}

/** One thought at random, never one of the last two shown (unless nothing else is left). */
export function pickMindThought(pool: RecentThought[], avoid: readonly string[], rand: Rand): RecentThought | null {
  if (!pool.length) return null;
  const recent = new Set(avoid.slice(-MIND_AVOID_LAST));
  let choices = pool.filter((t) => !recent.has(mindKey(t)));
  if (!choices.length) {
    const last = avoid[avoid.length - 1];
    choices = pool.filter((t) => mindKey(t) !== last);
  }
  if (!choices.length) choices = pool;
  return choices[Math.min(choices.length - 1, Math.floor(rand() * choices.length))];
}

export function nextMindDelayMs(rand: Rand): number {
  return Math.floor(MIND_MIN_MS + rand() * (MIND_MAX_MS - MIND_MIN_MS));
}

// The backend's `about` for free time was "free" (the surplus queue's free_time job mapped to it,
// and a free wake's own reason is "free"); "free_time" is kept for older payloads. The queue is
// retired (2026-10-11), so these only label thoughts from before it.
const FREE_TIME = new Set(["free", "free_time", "free time"]);

/** "On my mind · 3:10 pm", "Daydreaming · …" for his free time, "Dreaming · …" at night. */
export function mindOverline(
  t: RecentThought,
  { tz, dreaming }: { tz?: string; dreaming: boolean },
): { label: string; time: string } {
  const time = formatClock(t.at, { tz });
  if (dreaming) return { label: "Dreaming", time };
  const free = FREE_TIME.has((t.about ?? "").toLowerCase()) || FREE_TIME.has((t.kind ?? "").toLowerCase());
  return { label: free ? "Daydreaming" : "On my mind", time };
}
