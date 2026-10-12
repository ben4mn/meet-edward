// Settings → Work → Scheduled (2026-09-26, Jarvis polish). Pure; pinned in
// __tests__/eventGroups.test.ts.
//
// Three groups: the owner's reminders; Edward's own wake-ups (a `mind_wake` he
// set with wake_me, and the morning / evening / reflect ticks); and
// housekeeping — the screens pulse, the surplus tick and any other seeded
// maintenance — which the panel folds. The API may carry `event_metadata`
// (an object or its JSON text); without it the seeded rows are recognised
// by the descriptions and threads the scheduler really writes
// (scheduler_service._ensure_wakeup_events / _seed_extra_cron_events).

import { formatClock, parseIso } from "./time";

export type EventGroup = "yours" | "wakes" | "housekeeping";

export interface EventLike {
  id: string;
  description: string;
  conversation_id: string | null;
  recurrence_pattern: string | null;
  status: string;
  created_by: string;
  next_fire_at: string | null;
  last_fired_at?: string | null;
  updated_at?: string | null;
  event_metadata?: unknown;
  /** The API's own kind (2026-09-26): "mind_wake", "wakeup_tick",
   *  "expression_pulse", …; null for the owner's plain reminders. */
  event_type?: string | null;
}

function metadataType(meta: unknown): string | null | undefined {
  if (meta == null) return undefined;
  let obj: unknown = meta;
  if (typeof meta === "string") {
    try {
      obj = JSON.parse(meta);
    } catch {
      return null;
    }
  }
  if (obj && typeof obj === "object" && typeof (obj as { type?: unknown }).type === "string") {
    return (obj as { type: string }).type;
  }
  return null;
}

const TICK_RE = /^(nightly|morning|evening|reflect|midday)\s+wake\b/i;
const SEED_RE = /^([a-z]+(?:_[a-z]+)+)\s+—\s/;

/** The kind of scheduled row, or null for an ordinary reminder. */
export function eventKind(e: EventLike): string | null {
  // The API says what it is when it can (null: the owner's plain reminder).
  if (e.event_type !== undefined) return e.event_type ?? null;
  const meta = metadataType(e.event_metadata);
  if (meta !== undefined) return meta;
  const desc = (e.description || "").trim();
  if (desc.startsWith("expression_pulse") || e.conversation_id === "edward-surfaces") return "expression_pulse";
  if (desc.startsWith("surplus_tick")) return "surplus_tick";
  if (desc.startsWith("proactive_sweep")) return "proactive_sweep";
  if (TICK_RE.test(desc)) return "wakeup_tick";
  if (e.conversation_id === "edward-mind") return "mind_wake";
  if (e.created_by === "edward" && e.recurrence_pattern && SEED_RE.test(desc)) return "maintenance";
  return null;
}

const WAKE_KINDS = new Set(["mind_wake", "wakeup_tick"]);
const HOUSEKEEPING_KINDS = new Set(["expression_pulse", "proactive_sweep", "surplus_tick", "widget_refresh", "maintenance"]);

export function eventGroup(e: EventLike): EventGroup {
  const kind = eventKind(e);
  if (kind && WAKE_KINDS.has(kind)) return "wakes";
  if (kind && HOUSEKEEPING_KINDS.has(kind)) return "housekeeping";
  return "yours";
}

const GOAL_CHECK_IN_RE = /^\[goal check-in\]\s*(.+?)(?:\s*\(goal [0-9a-f-]+\))?$/i;

/** The row's title in plain words (no seeded snake_case). */
export function eventTitle(e: EventLike): string {
  const kind = eventKind(e);
  const desc = (e.description || "").trim();
  const goal = desc.match(GOAL_CHECK_IN_RE);
  if (goal) return `Check in on your goal: ${goal[1]}`;
  if (kind === "expression_pulse") return "Decide what my screens show";
  if (kind === "proactive_sweep") return "Run my checks";
  if (kind === "surplus_tick") return "Put spare time to use";
  if (kind === "wakeup_tick") {
    const m = desc.match(TICK_RE);
    if (m) return `${m[1].charAt(0).toUpperCase()}${m[1].slice(1).toLowerCase()} wake`;
  }
  const seed = desc.match(SEED_RE);
  if (seed) return desc.slice(seed[0].length).replace(/^Edward's own /, "").replace(/^./, (c) => c.toUpperCase());
  return desc || "Scheduled";
}

// ------------------------------------------------------------ cron in words
const DAY_PLURAL = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"];

function isInt(s: string): boolean {
  return /^\d+$/.test(s);
}

function clock(h: number, m: number): string {
  const suffix = h < 12 ? "am" : "pm";
  const h12 = h % 12 === 0 ? 12 : h % 12;
  return m === 0 ? `${h12} ${suffix}` : `${h12}:${String(m).padStart(2, "0")} ${suffix}`;
}

function joinAnd(parts: string[]): string {
  if (parts.length <= 1) return parts.join("");
  return `${parts.slice(0, -1).join(", ")} and ${parts[parts.length - 1]}`;
}

function ordinal(n: number): string {
  const s = n % 100;
  if (s >= 11 && s <= 13) return `${n}th`;
  return `${n}${({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th"}`;
}

function times(minute: string, hour: string): string | null {
  if (!isInt(minute)) return null;
  const m = Number(minute);
  const hours = hour.split(",");
  if (!hours.every(isInt) || m > 59) return null;
  const hs = hours.map(Number);
  if (hs.some((h) => h > 23)) return null;
  return joinAnd(hs.map((h) => clock(h, m)));
}

function days(dow: string): string | null {
  if (dow === "1-5") return "Weekdays";
  if (dow === "0,6" || dow === "6,0") return "Weekends";
  const list = dow.split(",");
  if (!list.every(isInt)) return null;
  const idx = list.map((d) => Number(d) % 7);
  if (idx.some((d) => d < 0 || d > 6)) return null;
  return joinAnd(idx.map((d) => DAY_PLURAL[d]));
}

/** "Every day at 7 am", or null when the pattern is not a common one. */
export function describeCron(pattern: string | null | undefined): string | null {
  if (!pattern) return null;
  const f = pattern.trim().split(/\s+/);
  if (f.length !== 5) return null;
  const [min, hour, dom, mon, dow] = f;
  if (mon !== "*") return null;

  if (hour === "*" && dom === "*" && dow === "*") {
    if (min === "*") return "Every minute";
    const step = min.match(/^\*\/(\d+)$/);
    if (step) return Number(step[1]) === 1 ? "Every minute" : `Every ${Number(step[1])} minutes`;
    if (min === "0") return "Every hour";
    if (isInt(min)) return `Every hour at :${min.padStart(2, "0")}`;
    return null;
  }
  const hourStep = hour.match(/^\*\/(\d+)$/);
  if (hourStep && dom === "*" && dow === "*" && min === "0") {
    const n = Number(hourStep[1]);
    return n === 1 ? "Every hour" : `Every ${n} hours`;
  }
  const at = times(min, hour);
  if (!at) return null;
  if (dom === "*" && dow === "*") return `Every day at ${at}`;
  if (dom === "*") {
    const d = days(dow);
    return d ? `${d} at ${at}` : null;
  }
  if (dow === "*" && isInt(dom)) return `Monthly on the ${ordinal(Number(dom))} at ${at}`;
  return null;
}

// ------------------------------------------------------------ grouping
const LIVE = new Set(["pending", "processing"]);

function ts(s: string | null | undefined): number {
  if (!s) return Number.POSITIVE_INFINITY;
  const t = Date.parse(s);
  return Number.isNaN(t) ? Number.POSITIVE_INFINITY : t;
}

export interface GroupedEvents<E> {
  yours: E[];
  wakes: E[];
  housekeeping: E[];
  past: E[];
}

export function groupEvents<E extends EventLike>(events: E[]): GroupedEvents<E> {
  const out: GroupedEvents<E> = { yours: [], wakes: [], housekeeping: [], past: [] };
  for (const e of events) {
    if (!LIVE.has(e.status)) out.past.push(e);
    else out[eventGroup(e)].push(e);
  }
  const soonest = (a: E, b: E) => ts(a.next_fire_at) - ts(b.next_fire_at);
  out.yours.sort(soonest);
  out.wakes.sort(soonest);
  out.housekeeping.sort(soonest);
  const latest = (e: E) => {
    const t = ts(e.last_fired_at ?? e.updated_at);
    return Number.isFinite(t) ? t : 0;
  };
  out.past.sort((a, b) => latest(b) - latest(a));
  return out;
}

// ------------------------------------------------------------ when
function dayKey(d: Date, tz: string | undefined): string {
  return new Intl.DateTimeFormat("en-US", { timeZone: tz || undefined, year: "numeric", month: "2-digit", day: "2-digit" }).format(d);
}

/** "Today at 3:45 pm" / "Tomorrow at 7:00 am" / "Sat, Oct 3 at 9:00 am",
 *  in the owner's zone — the app's one clock format (L1, 2026-09-27). */
export function whenLine(iso: string | null | undefined, opts: { now?: Date; tz?: string } = {}): string {
  if (!iso) return "";
  const d = parseIso(iso);
  if (Number.isNaN(d.getTime())) return "";
  const now = opts.now ?? new Date();
  const tz = opts.tz || undefined;
  const time = formatClock(d, { tz });
  const key = dayKey(d, tz);
  const day = 86400_000;
  if (key === dayKey(now, tz)) return `Today at ${time}`;
  if (key === dayKey(new Date(now.getTime() + day), tz)) return `Tomorrow at ${time}`;
  if (key === dayKey(new Date(now.getTime() - day), tz)) return `Yesterday at ${time}`;
  const sameYear =
    new Intl.DateTimeFormat("en-US", { timeZone: tz, year: "numeric" }).format(d) ===
    new Intl.DateTimeFormat("en-US", { timeZone: tz, year: "numeric" }).format(now);
  const date = new Intl.DateTimeFormat("en-US", {
    timeZone: tz,
    weekday: "short",
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  }).format(d);
  return `${date} at ${time}`;
}

/** A row's last result without internal prefixes ("Tool error: …"). */
export function resultText(s: string | null | undefined): string | null {
  const t = (s || "").trim().replace(/^(tool error|error|unknown tool):\s*/i, "");
  if (!t) return null;
  if (/^left alone\.?$/i.test(t)) return "Left it alone";
  return t.charAt(0).toUpperCase() + t.slice(1);
}
