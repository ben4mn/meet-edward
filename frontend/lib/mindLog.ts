// Pure logic for the Mind panel (Settings → Mind → Thoughts) — the owner's
// window on what Edward thought, intended and did (mind rebuild Phase 11).
// The panel only draws what this module decides: the day grouping, which
// quiet wakes fold, the one-line meta per wake and per intention, and the
// verdicts the owner can give. Pinned in __tests__/mindLog.test.ts.

import { formatDayHeader, formatRelative, type TimeOpts } from "./time";

export interface MindPush {
  id: string;
  title: string;
  opened: boolean;
  held?: string | null;
}

export interface MindVerdict {
  verdict: string | null;
  note: string | null;
}

export interface MindWake {
  id: number;
  at: string | null;
  source: string;
  outcome: string;
  note: string | null;
  tools: string[];
  surfaces: string[];
  delivered: boolean;
  turn_id: string | null;
  pushes: MindPush[];
  verdict: MindVerdict | null;
  /** The thought conversation a wake that acted left (2026-09-25). */
  thought_id?: string | null;
}

export interface MindIntention {
  id: string;
  content: string;
  created_at: string | null;
  valid_until: string | null;
  event_id: string | null;
  next_fire_at: string | null;
  next_fire_why: string | null;
}

export type Opts = { now?: Date; timeZone?: string };

export const VERDICT_OPTIONS: { value: "right_call" | "wrong_call"; label: string }[] = [
  { value: "right_call", label: "Right call" },
  { value: "wrong_call", label: "Wrong call" },
];

/** Only a wake that decided something can be judged: a failed or skipped
 *  wake decided nothing. */
export function canJudge(w: Pick<MindWake, "outcome">): boolean {
  return w.outcome === "acted" || w.outcome === "left_alone";
}

const OUTCOME_LABEL: Record<string, string> = {
  acted: "Acted",
  left_alone: "Left alone",
  failed: "Failed",
  skipped: "Skipped",
};

export function outcomeLabel(outcome: string): string {
  const known = OUTCOME_LABEL[outcome];
  if (known) return known;
  const words = outcome.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

export type LogRow =
  | { kind: "wake"; wake: MindWake }
  | { kind: "quiet"; count: number; ids: number[]; first: string | null; last: string | null };

export interface LogDay {
  label: string;
  rows: LogRow[];
}

function timeOpts(o: Opts): TimeOpts {
  return { now: o.now, tz: o.timeZone };
}

function isQuiet(w: MindWake): boolean {
  return w.outcome === "left_alone";
}

/** Newest day first; inside a day, a run of two or more left-alone wakes folds into one quiet row. */
export function groupWakesByDay(wakes: MindWake[], o: Opts = {}): LogDay[] {
  const sorted = [...wakes].sort((a, b) => (b.at ?? "").localeCompare(a.at ?? ""));
  const days: LogDay[] = [];
  for (const w of sorted) {
    const label = w.at ? formatDayHeader(w.at, timeOpts(o)) : "Undated";
    let day = days[days.length - 1];
    if (!day || day.label !== label) {
      day = { label, rows: [] };
      days.push(day);
    }
    const last = day.rows[day.rows.length - 1];
    if (isQuiet(w) && last && last.kind === "quiet") {
      last.count += 1;
      last.ids.push(w.id);
      last.last = w.at;
      continue;
    }
    if (isQuiet(w) && last && last.kind === "wake" && isQuiet(last.wake)) {
      day.rows[day.rows.length - 1] = { kind: "quiet", count: 2, ids: [last.wake.id, w.id], first: last.wake.at, last: w.at };
      continue;
    }
    day.rows.push({ kind: "wake", wake: w });
  }
  return days;
}

// A journal entry can come from a turn that was no wake
// (thought_journal.journal_unattended_turn: its reason is the turn's
// source). Named in words — never "heartbeat wake" (pre-deploy review,
// 2026-09-27; the backend's REASON_LABELS say the same in his voice).
const NOT_A_WAKE: Record<string, string> = {
  scheduled_event: "a scheduled job",
  heartbeat: "a message that came in",
  twilio: "a text to his number",
  worker: "a helper's task",
  briefing: "a briefing",
  background: "background work",
};

export function wakeSourceLabel(source: string): string {
  const known: Record<string, string> = {
    morning: "morning wake", evening: "evening wake", reflect: "Sunday reflection", self: "a wake he asked for",
    messages: "messages wake", mention: "mention wake", manual: "manual wake", surfaces: "surfaces wake",
    reflect_lite: "nightly reflection", prep: "meeting prep", research: "research", free: "free time",
    // The proactivity rebuild (2026-10-11): the 03:00 nightly and the wake
    // that says something he noticed. The older reasons stay for history.
    nightly: "nightly reflection", speak: "something he noticed",
    ...NOT_A_WAKE,
  };
  return known[source] ?? `${source.replace(/_/g, " ")} wake`;
}

/** "evening wake · 1h ago · pushed, opened". The verdict is not repeated
 *  here: the panel shows it as the selected option under this line. */
export function describeWake(w: MindWake, o: Opts = {}): string {
  const parts = [wakeSourceLabel(w.source)];
  if (w.at) parts.push(formatRelative(w.at, timeOpts(o)));
  if (w.outcome === "failed" || w.outcome === "skipped") {
    parts.push(w.outcome);
  } else if (w.pushes.length) {
    parts.push(w.pushes.some((p) => p.opened) ? "pushed, opened" : "pushed");
  } else if (w.outcome === "left_alone") {
    parts.push("left alone");
  } else {
    const n = w.tools.length;
    parts.push(n === 1 ? "1 tool" : `${n} tools`);
  }
  return parts.join(" · ");
}

/** "Read his thinking →": the wake's thought entry, as a chat URL (the chat
 *  loads from the URL; a click never calls loadConversation). */
export function thoughtHref(w: Pick<MindWake, "thought_id">): string | null {
  return w.thought_id ? `/chat?c=${encodeURIComponent(w.thought_id)}` : null;
}

function shortDate(iso: string, o: Opts): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return new Intl.DateTimeFormat("en-US", { month: "short", day: "numeric", timeZone: o.timeZone }).format(d);
}

/** "since 2d ago · next look in 2d: why · until Oct 18" */
export function describeIntention(i: MindIntention, o: Opts = {}): string {
  const parts: string[] = [];
  if (i.created_at) parts.push(`since ${formatRelative(i.created_at, timeOpts(o))}`);
  if (i.next_fire_at) {
    const when = formatRelative(i.next_fire_at, timeOpts(o));
    parts.push(i.next_fire_why ? `next look ${when}: ${i.next_fire_why}` : `next look ${when}`);
  } else {
    parts.push("no look scheduled");
  }
  if (i.valid_until) parts.push(`until ${shortDate(i.valid_until, o)}`);
  return parts.join(" · ");
}

/** "Wake now" answers 202 at once; the wake's row lands in the log when it
 *  is done. It has landed when the log holds a wake whose id was not there
 *  when the owner pressed — ledger ids only grow, and comparing clocks would
 *  trust the phone's clock against the host Mac's. A press that `joined` a
 *  wake already running waits for that wake, whatever its reason. */
export function landedWake(
  wakes: readonly MindWake[],
  knownIds: ReadonlySet<number>,
  o: { joined?: boolean } = {},
): MindWake | null {
  for (const w of wakes) {
    if (knownIds.has(w.id)) continue;
    if (o.joined || w.source === "manual") return w;
  }
  return null;
}

/** The headline of a wake's row: his closing line, or — for a wake that
 *  failed or was skipped, whose "note" is an exception or a gate reason —
 *  what happened, in his words. The raw note stays available under it. */
export function wakeTitle(w: Pick<MindWake, "outcome" | "note">): string {
  const note = (w.note ?? "").trim();
  if (w.outcome === "failed") return "I couldn't finish this wake.";
  if (w.outcome === "skipped") {
    // "spend cap": the Claude day's cap (services/llm_spend.QUOTA_SKIP_NOTE).
    if (/spend cap/i.test(note)) return "I skipped this wake — today's budget was spent.";
    return /quota|limit/i.test(note) ? "I skipped this wake — the week's quota was spent." : "I skipped this wake.";
  }
  if (note) return note;
  return w.outcome === "left_alone" ? "Nothing needed me." : "I acted.";
}

/** The meta line under a wake whose outcome is already a pill:
 *  "evening wake · 1h ago · 2 tools". */
export function wakeMeta(w: MindWake, o: Opts = {}): string {
  const parts = [wakeSourceLabel(w.source)];
  if (w.at) parts.push(formatRelative(w.at, timeOpts(o)));
  // A push is shown under the line with its title, so only tools count here.
  if (w.outcome === "acted" && !w.pushes.length && w.tools.length) {
    parts.push(w.tools.length === 1 ? "1 tool" : `${w.tools.length} tools`);
  }
  return parts.join(" · ");
}

/** "Next look in 2d" — when he will look at it again. */
export function intentionNextLook(i: MindIntention, o: Opts = {}): string {
  if (!i.next_fire_at) return "No look scheduled";
  const when = formatRelative(i.next_fire_at, timeOpts(o));
  return when.startsWith("in ") ? `Next look ${when}` : `Next look due (${when})`;
}

/** "since 2d ago · until Oct 18" */
export function intentionMeta(i: MindIntention, o: Opts = {}): string {
  const parts: string[] = [];
  if (i.created_at) parts.push(`since ${formatRelative(i.created_at, timeOpts(o))}`);
  if (i.valid_until) parts.push(`until ${shortDate(i.valid_until, o)}`);
  return parts.join(" · ");
}
