// Work's activity timeline — what Edward did on his own, newest first.
// Pure; pinned in __tests__/activityModel.test.ts.
//
//  * His own recurring maintenance (the wake ticks, the surfaces pulse, the
//    surplus tick, dreaming) folds into one "His rhythm" row: it fires every
//    few minutes and would otherwise bury everything else.
//  * Wakes that acted are listed in his own words, linking to the thinking
//    behind them (the thought entry) when there is one.
//  * Only what happened: a scheduled event that has not fired yet belongs to
//    the Events panel (and Today), not to the timeline.

import { formatRelative } from "../../lib/time";
import { reasonPhrase } from "../../lib/edwardMood";
import { resultText } from "../../lib/eventGroups";

export type ActivityKind = "rhythm" | "wake" | "event" | "worker" | "wish";
export type ActivityFilter = "all" | "wake" | "event" | "worker" | "wish";
export type ActivityTone = "neutral" | "ok" | "warn" | "bad" | "info" | "edward";

export interface Routine {
  id: string;
  title: string;
  detail: string;
  href: string;
}

export interface ActivityRow {
  id: string;
  kind: ActivityKind;
  /** ISO; when it happened. */
  at: string;
  title: string;
  detail: string;
  tone: ActivityTone;
  status: string;
  href: string;
  /** The thought entry behind a wake that acted. */
  thoughtHref?: string | null;
  /** The rhythm row's routines. */
  routines?: Routine[];
}

export interface EventLike {
  id: string;
  conversation_id: string | null;
  description: string;
  next_fire_at: string | null;
  recurrence_pattern: string | null;
  status: string;
  created_by: string;
  last_fired_at: string | null;
  fire_count: number;
  last_result: string | null;
}

export interface TaskLike {
  id: string;
  task_description: string;
  status: string;
  result_summary: string | null;
  error: string | null;
  task_type: string;
  model: string | null;
  started_at: string | null;
  completed_at: string | null;
  worker_conversation_id: string | null;
}

export interface WishLike {
  id: string;
  title: string;
  why: string;
  status: string;
  updated_at: string;
}

export interface DreamingLike {
  last_run: string | null;
  next_run: string | null;
  enabled: boolean;
  running: boolean;
  total_connections: number;
  total_flags: number;
}

export interface WakeLike {
  id: number;
  at: string | null;
  source: string;
  outcome: string;
  note: string | null;
  thought_id?: string | null;
}

export interface ActivityInput {
  events: EventLike[];
  tasks: TaskLike[];
  wishes: WishLike[];
  dreaming: DreamingLike | null;
  wakes: WakeLike[];
}

/** Edward's own recurring maintenance: recurring, made by him, and either
 *  in no conversation or in one of his own threads (edward-mind,
 *  edward-surfaces…). A recurring check he set up for the owner stays visible. */
export function isRhythmEvent(e: EventLike): boolean {
  if (!e.recurrence_pattern) return false;
  if (e.created_by !== "edward") return false;
  return !e.conversation_id || e.conversation_id.startsWith("edward-");
}

const SEED_SUFFIX = /\s+[—-]\s+(the mind decides|Edward's own maintenance)$/i;

/** His routines by what they do (the scheduler seeds them by internal id). */
const KNOWN_ROUTINES: Array<[RegExp, string]> = [
  [/^(expression_pulse\b|surfaces pulse\b)/i, "Tidying his screens"],
  [/^proactive_sweep\b/i, "Running his checks"],
  [/^surplus_tick\b/i, "Spending spare fuel"],
];

export function routineTitle(description: string): string {
  for (const [re, name] of KNOWN_ROUTINES) if (re.test(description.trim())) return name;
  const base = description.replace(SEED_SUFFIX, "").trim() || description;
  if (/^[a-z]+(?:_[a-z]+)+$/.test(base)) {
    const words = base.replace(/_/g, " ");
    return words.charAt(0).toUpperCase() + words.slice(1);
  }
  return base;
}

function capitalise(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

function ts(iso: string | null | undefined): number {
  const t = iso ? Date.parse(iso) : NaN;
  return Number.isNaN(t) ? NaN : t;
}

function count(n: number): string {
  return n.toLocaleString("en-US");
}

interface RoutineWithTimes extends Routine {
  last: number;
  next: number;
}

function rhythmRow(events: EventLike[], dreaming: DreamingLike | null, now: Date): ActivityRow | null {
  const routines: RoutineWithTimes[] = events.map((e) => {
    const next = ts(e.next_fire_at);
    const parts: string[] = [];
    if (!Number.isNaN(next) && next > now.getTime()) parts.push(`next ${formatRelative(e.next_fire_at!, { now })}`);
    if (e.fire_count > 0) parts.push(`ran ${count(e.fire_count)} ${e.fire_count === 1 ? "time" : "times"}`);
    return {
      id: `ev:${e.id}`,
      title: routineTitle(e.description),
      detail: parts.join(" · ") || "scheduled",
      href: `/settings/events?id=${encodeURIComponent(e.id)}`,
      last: ts(e.last_fired_at),
      next,
    };
  });
  if (dreaming && (dreaming.last_run || dreaming.enabled)) {
    const parts: string[] = [];
    if (dreaming.running) parts.push("running now");
    else if (dreaming.last_run) parts.push(`ran ${formatRelative(dreaming.last_run, { now })}`);
    if (!dreaming.enabled) parts.push("off");
    parts.push(`${count(dreaming.total_connections)} connections`);
    routines.push({
      id: "dream",
      title: "Dreaming",
      detail: parts.join(" · "),
      href: "/settings/dreaming",
      last: ts(dreaming.last_run),
      next: dreaming.enabled ? ts(dreaming.next_run) : NaN,
    });
  }
  if (routines.length === 0) return null;

  const upcoming = routines
    .filter((r) => !Number.isNaN(r.next) && r.next > now.getTime())
    .sort((a, b) => a.next - b.next)[0];
  const lasts = routines.map((r) => r.last).filter((t) => !Number.isNaN(t));
  const at = lasts.length ? new Date(Math.max(...lasts)).toISOString() : now.toISOString();
  const head = `${routines.length} ${routines.length === 1 ? "routine" : "routines"}`;
  const next = upcoming
    ? ` · next: ${upcoming.title} ${formatRelative(new Date(upcoming.next).toISOString(), { now })}`
    : "";
  return {
    id: "rhythm",
    kind: "rhythm",
    at,
    title: "His rhythm",
    detail: `${head}${next}`,
    tone: "neutral",
    status: "routine",
    href: "/settings/events",
    routines: routines
      .sort((a, b) => (Number.isNaN(a.next) ? Infinity : a.next) - (Number.isNaN(b.next) ? Infinity : b.next))
      .map(({ id, title, detail, href }) => ({ id, title, detail, href })),
  };
}

const EVENT_STATUS: Record<string, { status: string; tone: ActivityTone }> = {
  completed: { status: "done", tone: "ok" },
  failed: { status: "failed", tone: "bad" },
  cancelled: { status: "cancelled", tone: "neutral" },
};

function eventRow(e: EventLike): ActivityRow | null {
  const at = e.last_fired_at ?? (e.status === "failed" ? e.next_fire_at : null);
  if (!at) return null;
  const mapped = EVENT_STATUS[e.status] ?? {
    status: e.recurrence_pattern ? "recurring" : e.status,
    tone: "neutral" as ActivityTone,
  };
  return {
    id: `ev:${e.id}`,
    kind: "event",
    at,
    title: e.description,
    // "Tool error: blocked — …" reads as "Blocked — …" (lib/eventGroups).
    detail: resultText(e.last_result) || capitalise(mapped.status),
    tone: mapped.tone,
    status: mapped.status,
    href: `/settings/events?id=${encodeURIComponent(e.id)}`,
  };
}

function workerRow(t: TaskLike): ActivityRow | null {
  const at = t.completed_at || t.started_at;
  if (!at) return null;
  const tone: ActivityTone =
    t.status === "completed" ? "ok" : t.status === "failed" ? "bad" : t.status === "running" ? "info" : "neutral";
  return {
    id: `w:${t.id}`,
    kind: "worker",
    at,
    title: t.task_description,
    detail: t.result_summary || t.error || (t.status === "running" ? "Working on it" : "Worker"),
    tone,
    status: t.status,
    href: t.worker_conversation_id
      ? `/chat?c=${encodeURIComponent(t.worker_conversation_id)}`
      : `/settings/orchestrator?id=${encodeURIComponent(t.id)}`,
  };
}

const WISH_TONE: Record<string, ActivityTone> = {
  open: "neutral",
  planned: "info",
  building: "info",
  done: "ok",
  declined: "neutral",
};

function wishRow(w: WishLike): ActivityRow {
  return {
    id: `wish:${w.id}`,
    kind: "wish",
    at: w.updated_at,
    title: w.title,
    detail: w.why,
    tone: WISH_TONE[w.status] ?? "neutral",
    status: w.status === "open" ? "wished for" : w.status,
    href: `/settings/wishlist?id=${encodeURIComponent(w.id)}`,
  };
}

function wakeRow(w: WakeLike): ActivityRow | null {
  if (w.outcome !== "acted" || !w.at) return null;
  const thought = w.thought_id ? `/chat?c=${encodeURIComponent(w.thought_id)}` : null;
  return {
    id: `wake:${w.id}`,
    kind: "wake",
    at: w.at,
    title: w.note || "Acted on his own",
    detail: capitalise(reasonPhrase(w.source) ?? w.source.replace(/_/g, " ")),
    tone: "edward",
    status: "acted",
    href: thought ?? "/settings/thoughts",
    thoughtHref: thought,
  };
}

export function buildActivity(input: ActivityInput, opts: { now?: Date } = {}): ActivityRow[] {
  const now = opts.now ?? new Date();
  const rhythmEvents = input.events.filter(isRhythmEvent);
  const others = input.events.filter((e) => !isRhythmEvent(e));
  const rows = [
    ...input.wakes.map(wakeRow),
    ...others.map(eventRow),
    ...input.tasks.map(workerRow),
    ...input.wishes.slice(0, 15).map(wishRow),
  ].filter((r): r is ActivityRow => r !== null && !Number.isNaN(ts(r.at)));
  rows.sort((a, b) => ts(b.at) - ts(a.at));
  const rhythm = rhythmRow(rhythmEvents, input.dreaming, now);
  return rhythm ? [rhythm, ...rows] : rows;
}

export function filterActivity(rows: readonly ActivityRow[], filter: ActivityFilter): ActivityRow[] {
  if (filter === "all") return [...rows];
  return rows.filter((r) => r.kind === filter || (filter === "event" && r.kind === "rhythm"));
}
