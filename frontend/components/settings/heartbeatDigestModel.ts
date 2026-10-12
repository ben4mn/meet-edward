// Pure logic for the heartbeat panel: which events are *decisions* worth
// showing, a one-sentence reason per decision, human labels instead of
// snake_case, dismissals folded into per-person counts, the one-line status
// sentence, the sub-view in the URL, and "today" in the owner's zone.
//
// With the mind on (`mind_enabled`, 2026-09-20) an "escalated" message is
// not a push any more: it is handed to Edward's mind, which looks at it on
// its next wake. Every label below that depends on that takes `{mindEnabled}`.

import type { HeartbeatEvent, HeartbeatStatus } from "@/lib/api";
import type { Tone } from "@/components/ui";
import { formatRelative } from "@/lib/time";

export interface DigestOpts {
  /** The heartbeat hands escalations to the mind instead of pushing. */
  mindEnabled?: boolean;
}

export const DECISION_STATUSES = new Set([
  "noted",
  "watching",
  "escalated",
  "handled_by_owner",
  "mention",
  "follow_up",
  "calendar_urgent",
  "acted",
  // Someone asked Edward directly (an @mention, a reply to his message,
  // the owner's own chat with him): the listener stores it "ping" and the reply
  // path answers it in the chat, never triage (2026-10-11).
  "ping",
]);

const LABEL: Record<string, string> = {
  handled_by_owner: "Handled by you",
  watching: "Watching",
  noted: "Noted",
  escalated: "Told you",
  acted: "Acted",
  mention: "Mentioned Edward",
  ping: "Asked me",
  follow_up: "Following up",
  calendar_urgent: "Calendar, urgent",
  dismissed: "Set aside",
  pending: "Waiting for triage",
};

const MIND_LABEL: Record<string, string> = {
  escalated: "Handed to his mind",
  pending: "Waiting for his next look",
};

export function humanStatus(status: string, o: DigestOpts = {}): string {
  if (o.mindEnabled && MIND_LABEL[status]) return MIND_LABEL[status];
  return LABEL[status] ?? status.replace(/_/g, " ");
}

export function toneFor(status: string, o: DigestOpts = {}): Tone {
  if (o.mindEnabled && status === "escalated") return "edward";
  switch (status) {
    case "escalated":
    case "calendar_urgent":
      return "bad";
    case "watching":
    case "follow_up":
      return "warn";
    case "noted":
    case "mention":
    case "ping":
      return "info";
    // Emerald means Edward: his acts only. The owner's own reply is a plain "ok".
    case "handled_by_owner":
      return "ok";
    case "acted":
      return "edward";
    default:
      return "neutral";
  }
}

const REASON: Record<string, string> = {
  handled_by_owner: "You replied yourself. Nothing for me to do.",
  watching: "Still forming. I'll surface it if it firms up.",
  noted: "Saved as a note and an open loop.",
  escalated: "Looked urgent, so I told you.",
  acted: "I acted on it.",
  mention: "They @mentioned me, so I answered in the thread.",
  ping: "They asked me directly, so I answer in the chat.",
  follow_up: "A reply is owed. I'll nudge if it stays open.",
  calendar_urgent: "Starts soon. I flagged it.",
};

const MIND_REASON: Record<string, string> = {
  escalated: "I looked at this on my next wake.",
};

export function reasonFor(e: HeartbeatEvent, o: DigestOpts = {}): string {
  if (o.mindEnabled && MIND_REASON[e.triage_status]) return MIND_REASON[e.triage_status];
  return REASON[e.triage_status] ?? "";
}

/** Feedback choices per decision. The second option is the "you were
 *  wrong" direction that makes sense for that status. */
export function feedbackOptions(status: string, _o: DigestOpts = {}): { verdict: string; label: string }[] {
  const right = { verdict: "right_call", label: "Right call" };
  switch (status) {
    case "noted":
    case "mention":
    case "ping":
    case "follow_up":
      return [right, { verdict: "not_worth_noting", label: "Not worth noting" }];
    case "handled_by_owner":
      return [right, { verdict: "should_have_noted", label: "Should have noted" }];
    case "escalated":
    case "calendar_urgent":
    case "acted":
      return [right, { verdict: "not_worth_noting", label: "Didn't need me" }];
    default:
      return [right, { verdict: "should_have_told_me", label: "Should have told me" }];
  }
}

export interface Digest {
  decisions: HeartbeatEvent[];
  setAside: { who: string; count: number; events: HeartbeatEvent[] }[];
  setAsideTotal: number;
  counts: Record<string, number>;
  waiting: number;
}

/** Who an event is about: you, the contact, the chat, a calendar event's
 *  title — never "Unknown" when there is anything better to say. */
export function whoOf(e: HeartbeatEvent): string {
  if (e.is_from_user) return "You";
  if (e.source === "calendar") {
    const title = e.raw_data && typeof e.raw_data.title === "string" ? e.raw_data.title.trim() : "";
    return title || "Calendar";
  }
  return e.contact_name || e.chat_name || e.sender || "Someone";
}

export function buildDigest(events: HeartbeatEvent[]): Digest {
  const counts: Record<string, number> = {};
  const decisions: HeartbeatEvent[] = [];
  const byWho = new Map<string, HeartbeatEvent[]>();
  for (const e of events) {
    counts[e.triage_status] = (counts[e.triage_status] ?? 0) + 1;
    // The owner's own outbound texts are not Edward's decisions about anyone;
    // they fold into "You" under set aside whatever their label.
    if (DECISION_STATUSES.has(e.triage_status) && !e.is_from_user) {
      decisions.push(e);
    } else if (e.triage_status === "dismissed" || e.is_from_user) {
      const w = whoOf(e);
      if (!byWho.has(w)) byWho.set(w, []);
      byWho.get(w)!.push(e);
    }
  }
  decisions.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
  const setAside = Array.from(byWho.entries())
    .map(([who, evs]) => ({ who, count: evs.length, events: evs }))
    .sort((a, b) => b.count - a.count || a.who.localeCompare(b.who));
  return {
    decisions,
    setAside,
    setAsideTotal: setAside.reduce((n, p) => n + p.count, 0),
    counts,
    waiting: counts.pending ?? 0,
  };
}

// ---- "today" in the owner's zone -------------------------------------------

/** The last midnight in `tz` (the user's configured zone, not the
 *  browser's), as an instant. */
export function startOfDayIn(tz: string | undefined, now: Date = new Date()): Date {
  try {
    const parts = new Intl.DateTimeFormat("en-US", {
      timeZone: tz,
      hour: "numeric",
      minute: "numeric",
      second: "numeric",
      hourCycle: "h23",
    }).formatToParts(now);
    const num = (t: string) => parseInt(parts.find((p) => p.type === t)?.value || "0", 10);
    const secs = num("hour") * 3600 + num("minute") * 60 + num("second");
    return new Date(now.getTime() - secs * 1000 - now.getMilliseconds());
  } catch {
    const d = new Date(now);
    d.setHours(0, 0, 0, 0);
    return d;
  }
}

export function todaysEvents<E extends { created_at: string }>(events: E[], tz: string | undefined, now: Date = new Date()): E[] {
  const start = startOfDayIn(tz, now).getTime();
  return events.filter((e) => Date.parse(e.created_at) >= start);
}

// ---- the status sentence -------------------------------------------------------

type StatusLike = Pick<HeartbeatStatus, "running" | "enabled" | "pending_count" | "last_triage_at"> & {
  tracks?: Record<string, { enabled?: boolean; status?: string }>;
};

const TRACK_WORD: Record<string, string> = {
  imessage: "messages",
  calendar: "calendar",
  email: "email",
  gmail: "Gmail",
};
const TRACK_ORDER = ["imessage", "calendar", "email", "gmail"];

function listWords(words: string[]): string {
  if (words.length <= 1) return words.join("");
  return `${words.slice(0, -1).join(", ")} and ${words[words.length - 1]}`;
}

/** "I'm listening to messages, calendar and email — last looked 3m ago, 1 waiting." */
export function heartbeatStatusLine(status: StatusLike | null | undefined, now: Date = new Date()): string {
  if (!status) return "";
  if (!status.enabled) return "I'm not watching your messages right now.";
  if (!status.running) return "Paused — I'm not listening right now.";
  const tracks = status.tracks ?? {};
  const keys = [...TRACK_ORDER, ...Object.keys(tracks).filter((k) => !TRACK_ORDER.includes(k))];
  const live: string[] = [];
  const broken: string[] = [];
  for (const k of keys) {
    const t = tracks[k];
    if (!t || !t.enabled) continue;
    const word = TRACK_WORD[k] ?? k;
    if (t.status === "error") broken.push(word);
    else live.push(word);
  }
  let head = live.length ? `I'm listening to ${listWords(live)}` : "I'm listening";
  if (broken.length) head += ` (${listWords(broken)} ${broken.length === 1 ? "isn't" : "aren't"} answering)`;
  const tail: string[] = [];
  if (status.last_triage_at) tail.push(`last looked ${formatRelative(status.last_triage_at, { now })}`);
  if (status.pending_count > 0) tail.push(`${status.pending_count} waiting`);
  return tail.length ? `${head} — ${tail.join(", ")}.` : `${head}.`;
}

// ---- the sub-view in the URL ----------------------------------------------------

export type HeartbeatView = "digest" | "log" | "config";

/** `?view=log` is the full log, `?view=config` the configuration. */
export function heartbeatView(params: { get(name: string): string | null } | null | undefined): HeartbeatView {
  const v = params?.get("view");
  return v === "log" || v === "config" ? v : "digest";
}

// ---- interval options -------------------------------------------------------------

/** "10s", "2m", "1h" — whole units only ("90s", "90m" otherwise). */
export function secondsLabel(seconds: number): string {
  if (seconds >= 3600 && seconds % 3600 === 0) return `${seconds / 3600}h`;
  if (seconds >= 60 && seconds % 60 === 0) return `${seconds / 60}m`;
  return `${seconds}s`;
}

/** The presets, plus the stored value in its place when no preset matches,
 *  so a Segment never shows nothing selected. */
export function withCurrentOption<T extends { label: string; value: string }>(
  presets: T[],
  current: number | null | undefined,
  label: (n: number) => string = secondsLabel,
): { label: string; value: string }[] {
  if (current == null || !Number.isFinite(current) || presets.some((p) => Number(p.value) === current)) return presets;
  return [...presets, { label: label(current), value: String(current) }].sort((a, b) => Number(a.value) - Number(b.value));
}

/** The quiet line under a blocked sender's name: what the handle is, in
 *  words the owner reads ("Group chat", "(555) 555-0142"), never a raw
 *  "chat874412" in mono. Null when the name already is the handle. */
export function senderSubline(identifier: string, label: string): string | null {
  const id = (identifier || "").trim();
  if (!id || id === (label || "").trim()) return null;
  if (/^chat[\w-]*$/i.test(id)) return "Group chat";
  const digits = id.replace(/[^\d]/g, "");
  const phoneLike = /^\+?[\d\s().-]+$/.test(id);
  if (phoneLike) {
    const us = digits.length === 11 && digits.startsWith("1") ? digits.slice(1) : digits.length === 10 ? digits : null;
    if (us && (!id.startsWith("+") || id.startsWith("+1"))) return `(${us.slice(0, 3)}) ${us.slice(3, 6)}-${us.slice(6)}`;
  }
  return id;
}

