// What the presence sheet says (tap Edward anywhere) — pure, pinned in
// __tests__/presenceSheetModel.test.ts. Plain words only (design system §6):
// no raw wake reasons, no exception names, no internal vocabulary.

import { formatResetTime, reasonPhrase, SPEND_CAP_NOTE, type StatusOpts } from "@/lib/edwardMood";
import type { MindNowHands, MindNowQuota, MindWakeRow, MindWaking } from "@/lib/mindNowApi";
import { formatRelative, parseIso } from "@/lib/time";

export type SheetTone = "neutral" | "ok" | "warn" | "bad" | "edward";

function capitalise(s: string): string {
  return s ? s[0].toUpperCase() + s.slice(1) : s;
}

const OUTCOMES: Record<string, { label: string; tone: SheetTone }> = {
  acted: { label: "Acted", tone: "edward" },
  left_alone: { label: "Left it", tone: "neutral" },
  failed: { label: "Didn't finish", tone: "bad" },
  skipped: { label: "Skipped", tone: "warn" },
};

export function outcomePill(outcome: string | null | undefined): { label: string; tone: SheetTone } | null {
  if (!outcome) return null;
  return OUTCOMES[outcome] ?? { label: capitalise(outcome.replace(/_/g, " ")), tone: "neutral" };
}

const LOOKS_INTERNAL = /(error|exception|traceback|http \d{3}|timeout|\w+\.\w+\(|codex)/i;

/** The row's one line: his closing line, or plain words for a quiet look,
 *  a skip or a failure (never an exception). */
export function wakeRowText(row: MindWakeRow): string {
  const line = (row.line ?? "").trim();
  switch (row.outcome) {
    case "failed":
      return "I couldn't finish — the model didn't answer.";
    case "skipped":
      if (SPEND_CAP_NOTE.test(line)) return "Skipped — today's budget is spent.";
      if (/quota|exhaust|limit/i.test(line) || !line) return "Skipped — the week's quota is spent.";
      return LOOKS_INTERNAL.test(line) ? "Skipped this one." : line;
    case "left_alone":
      return line && !LOOKS_INTERNAL.test(line) ? line : "Looked around; nothing needed you.";
    default:
      return line && !LOOKS_INTERNAL.test(line) ? line : "Did something — read his thinking for the detail.";
  }
}

/** A finished wake, in his words and the past tense (REASON_PHRASES is
 *  the live line's present tense: "Reading your messages · 1h ago" read as
 *  if he still was). Reasons that name a moment stay nouns. */
const PAST_REASON: Readonly<Record<string, string>> = {
  messages: "Read your messages",
  mention: "Answered a mention",
  morning: "Morning wake",
  evening: "Evening wake",
  reflect: "Reflected on the week",
  reflect_lite: "Thought over the day",
  nightly: "Thought over the day",
  speak: "Looked into something I noticed",
  self: "Followed up on something",
  free: "Free time",
  research: "Researched something",
  prep: "Prepared for a meeting",
  manual: "You woke me",
};

/** "Read your messages · 2h ago", "Morning wake · 2h ago". */
export function wakeRowMeta(row: MindWakeRow, now: Date = new Date()): string {
  const past = row.reason && Object.prototype.hasOwnProperty.call(PAST_REASON, row.reason) ? PAST_REASON[row.reason] : null;
  const why = past ?? capitalise(reasonPhrase(row.reason) ?? "woke up");
  const when = row.at ? formatRelative(row.at, { now }) : "";
  return when ? `${why} · ${when}` : why;
}

/** "20s", "3m", "1h 5m". */
export function formatElapsed(iso: string, now: Date = new Date()): string | null {
  const t = parseIso(iso).getTime();
  if (Number.isNaN(t)) return null;
  const s = Math.max(0, Math.round((now.getTime() - t) / 1000));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  const rest = m % 60;
  return rest ? `${h}h ${rest}m` : `${h}h`;
}

export function doingNow(
  waking: MindWaking | null | undefined,
  now: Date = new Date(),
): { title: string; detail: string | null; active: boolean } {
  if (!waking) return { title: "Nothing running right now", detail: null, active: false };
  const title = capitalise(reasonPhrase(waking.reason) ?? "thinking");
  const elapsed = waking.started_at ? formatElapsed(waking.started_at, now) : null;
  return { title, detail: elapsed ? `For ${elapsed}` : null, active: true };
}

export function nextWakeText(iso: string | null | undefined, opts: StatusOpts = {}): string | null {
  if (!iso) return null;
  return formatResetTime(iso, opts);
}

export function quotaText(
  q: MindNowQuota | null | undefined,
  opts: StatusOpts = {},
): { percent: number; text: string; tone: SheetTone } | null {
  if (!q || q.used_percent == null) return null;
  const percent = Math.max(0, Math.min(100, Math.round(q.used_percent)));
  const when = q.resets_at ? formatResetTime(q.resets_at, opts) : null;
  if (q.period === "day") {
    // The Claude API: the day's spend cap, back at the owner's midnight.
    if (q.exhausted) {
      return { percent, text: when ? `Today's budget is spent · back ${when}` : "Today's budget is spent", tone: "warn" };
    }
    return { percent, text: when ? `${percent}% of today's budget used · resets ${when}` : `${percent}% of today's budget used`, tone: "neutral" };
  }
  if (q.exhausted) {
    return { percent, text: when ? `The week's quota is spent · back ${when}` : "The week's quota is spent", tone: "warn" };
  }
  return { percent, text: when ? `${percent}% of the week used · resets ${when}` : `${percent}% of the week used`, tone: "neutral" };
}

export function handsText(h: MindNowHands | null | undefined): { text: string; tone: SheetTone } | null {
  if (!h || h.connected === null) return null;
  if (h.connected) return { text: "Connected to your Chrome", tone: "ok" };
  if (h.down_for_s && h.down_for_s > 0) {
    const mins = Math.max(1, Math.round(h.down_for_s / 60));
    const span = mins < 60 ? `${mins}m` : `${Math.round(mins / 60)}h`;
    return { text: `Link to your Chrome down for ${span}`, tone: "warn" };
  }
  return { text: "Not connected to your Chrome", tone: "warn" };
}

export function approvalsText(n: number): string | null {
  if (!n || n < 1) return null;
  return n === 1 ? "1 message is waiting for your approval" : `${n} messages are waiting for your approval`;
}

/** Offline: what the sheet can honestly say — he can't reach the host Mac,
 *  and when it last answered (mind/now's `now`), never "nothing running". */
export function offlineNow(
  lastHeard: string | null | undefined,
  now: Date = new Date(),
): { title: string; detail: string } {
  const when = lastHeard ? formatRelative(lastHeard, { now }) : "";
  return {
    title: "I can't reach the Mac",
    detail: when ? `Last heard from it ${when} · trying again` : "Trying again",
  };
}

/** "Next wake …" only when it will happen: never offline (nothing about
 *  now is known) and never while the week's quota is spent (the wake
 *  would be skipped — review 2026-09-26, quota-rest-contradicts-next-look). */
export function nextWakeLine(
  mind: { next_wake_at?: string | null; quota?: MindNowQuota | null } | null | undefined,
  online: boolean,
  opts: StatusOpts = {},
): string | null {
  if (!online || !mind || mind.quota?.exhausted) return null;
  return nextWakeText(mind.next_wake_at, opts);
}

export function wakeButton(s: {
  waking: boolean;
  pending: boolean;
  online?: boolean;
  /** Set while the week's quota is spent: when it comes back ("Tue 3:00 pm"),
   *  or "" when unknown. A manual wake would only be recorded as skipped. */
  quotaSpentUntil?: string | null;
}): {
  label: string;
  disabled: boolean;
  busy: boolean;
} {
  if (s.online === false) return { label: "Can't reach him", disabled: true, busy: false };
  if (s.quotaSpentUntil != null && !s.waking) {
    return {
      label: s.quotaSpentUntil ? `Resting until ${s.quotaSpentUntil}` : "Resting · quota spent",
      disabled: true,
      busy: false,
    };
  }
  if (s.pending) return { label: "Waking…", disabled: true, busy: true };
  if (s.waking) return { label: "He's awake", disabled: true, busy: false };
  return { label: "Wake him now", disabled: false, busy: false };
}
