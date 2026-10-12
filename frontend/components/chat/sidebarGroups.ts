// Pure config for the chat sidebar's two groups. "Yours" is what the owner
// started or what came in from outside; "Edward's" is everything Edward
// began on his own (heartbeat threads, scheduled thoughts, workers).

import type { Conversation } from "@/lib/api";
import { formatClock, formatRelative, parseIso } from "@/lib/time";
import { isMindAction, type ThoughtMeta } from "@/lib/thoughtEntry";
import { headerSource } from "@/lib/conversationHeader";

export type SidebarFilter = "yours" | "edwards";

export const SIDEBAR_FILTERS: readonly { value: SidebarFilter; label: string }[] = [
  { value: "yours", label: "Yours" },
  { value: "edwards", label: "Edward's" },
] as const;

const YOURS = new Set(["user", "external_message"]);

export function matchesSidebarFilter(c: { source?: string | null }, filter: SidebarFilter): boolean {
  const yours = YOURS.has(c.source || "user");
  return filter === "yours" ? yours : !yours;
}

/** Legacy chip values ("all", "heartbeat", …) persisted in localStorage
 *  map onto the pair; unknown or missing means Yours. */
export function normalizeSidebarFilter(v: unknown): SidebarFilter {
  if (v === "edwards") return "edwards";
  if (v === "yours" || v === "all" || v === "user" || v === "external_message" || v == null) return "yours";
  if (typeof v === "string" && ["heartbeat", "scheduled_event", "edward_thought", "orchestrator_worker"].includes(v)) return "edwards";
  return "yours";
}

// ---- Edward's group, sectioned -------------------------------------------

export type EdwardsSectionKey = "actions" | "surfaces" | "threads" | "workers" | "scheduled" | "thoughts";

export interface EdwardsSection {
  key: EdwardsSectionKey;
  label: string;
  items: Conversation[];
  /** Older thoughts are many and low-signal; they start folded. */
  collapsed: boolean;
  /** Actions are the point of the group: always shown, no fold. */
  collapsible: boolean;
}

// Actions first (2026-09-25: "it must be obvious when he is choosing
// to take action"): one conversation per wake that acted, carrying
// `thought.kind = "mind_action"`. Every other thought — whispers, old
// narratives — is "Older thoughts" (same key, still folded).
const SECTION_ORDER: { key: EdwardsSectionKey; label: string; collapsed: boolean; collapsible: boolean }[] = [
  { key: "actions", label: "Actions", collapsed: false, collapsible: false },
  { key: "surfaces", label: "Surfaces", collapsed: false, collapsible: true },
  { key: "threads", label: "Threads", collapsed: false, collapsible: true },
  { key: "workers", label: "Workers", collapsed: false, collapsible: true },
  { key: "scheduled", label: "Scheduled", collapsed: false, collapsible: true },
  { key: "thoughts", label: "Older thoughts", collapsed: true, collapsible: true },
];

/** Edward's long-lived threads (the mind, the surfaces pulse, research):
 *  pinned under Surfaces whatever else they carry. */
const PINNED_IDS = new Set(["edward-surfaces", "edward-mind", "edward-research", "edward-free-time"]);
const PINNED_TITLE = /^edward[’']?s (surfaces|mind|research|free time)$/i;

export function sectionFor(c: Conversation): EdwardsSectionKey {
  if (PINNED_IDS.has(c.id) || PINNED_TITLE.test((c.title || "").trim())) return "surfaces";
  if (isMindAction(c)) return "actions";
  switch (c.source) {
    case "heartbeat":
      return "threads";
    case "orchestrator_worker":
      return "workers";
    case "scheduled_event":
      return "scheduled";
    default:
      return "thoughts";
  }
}

/**
 * Did this action reach someone? (L3, 2026-09-27 follow-ups.) `outward` is
 * true for any outward-tier action, and that tier includes leave_note and a
 * send held for the owner's approval — neither reached anyone, and "reached
 * out" on them blurs the one thing the journal is for: that it is obvious
 * when Edward chose to act on someone. Only `thought.reached`
 * (thought_journal.reached_someone) says it; an entry written before the
 * field comes back null — unknown, so no chip, never a guess from
 * `outward` (integration review, 2026-09-27).
 */
export function reachedOut(t: Pick<ThoughtMeta, "reached"> | null | undefined): boolean {
  return t?.reached === true;
}

/** The small marker beside an action that reached someone (a message, a
 *  push) rather than only his own notes. */
export function actionMarker(c: Conversation): string | null {
  return isMindAction(c) && reachedOut(c.thought) ? "reached out" : null;
}

export function groupEdwards(conversations: Conversation[]): EdwardsSection[] {
  const buckets = new Map<EdwardsSectionKey, Conversation[]>();
  for (const c of conversations) {
    const k = sectionFor(c);
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k)!.push(c);
  }
  const stamp = (c: Conversation) => Date.parse(c.last_activity_at || c.updated_at) || 0;
  // An action sorts by when he acted: the owner replying "why?" to last night's
  // action must not lift it above this morning's.
  const acted = (c: Conversation) => (c.thought?.acted_at ? parseIso(c.thought.acted_at).getTime() || 0 : 0) || stamp(c);
  return SECTION_ORDER.filter((s) => (buckets.get(s.key) ?? []).length > 0).map((s) => {
    const when = s.key === "actions" ? acted : stamp;
    return {
      key: s.key,
      label: s.label,
      items: [...buckets.get(s.key)!].sort((a, b) => when(b) - when(a)),
      collapsed: s.collapsed,
      collapsible: s.collapsible,
    };
  });
}

/** Sidebar label for a conversation: heartbeat threads lose the
 *  "iMessage:" prefix; a bare "Edward" thought is named by its time. */
export function displayTitle(c: Conversation, now?: Date): string {
  const t = (c.title || "").trim();
  if (c.source === "edward_thought" && /^(edward|edward's thought|thought)?$/i.test(t)) {
    return `Thought · ${formatRelative(c.updated_at, { now })}`;
  }
  if (c.source === "heartbeat" && /^iMessage:\s*/i.test(t)) return t.replace(/^iMessage:\s*/i, "");
  return t || "Untitled";
}

// ---- rows (2026-09-26 chat chrome) ----------------------------------------

/** Edward's long-lived threads: never offered for deletion. */
export const PROTECTED_IDS: ReadonlySet<string> = new Set([
  "edward-mind",
  "edward-surfaces",
  "edward-research",
  "edward-free-time",
]);

/** Deleting loses nothing of Edward's: not his long-lived threads, not a
 *  journal entry (a wake that acted — the record of what he chose to do). */
export function canDelete(c: Conversation): boolean {
  if (PINNED_IDS.has(c.id) || PROTECTED_IDS.has(c.id)) return false;
  if (PINNED_TITLE.test((c.title || "").trim())) return false;
  return !isMindAction(c);
}

export type SourceMarkKind = "inbound" | "edward" | "voice";
export interface SourceMark {
  kind: SourceMarkKind;
  label: string;
}

/** A quiet mark for where a thread came from (the header's words,
 *  lib/conversationHeader.ts). The owner's own text chats carry none. */
export function sourceMark(c: Pick<Conversation, "source" | "channel">): SourceMark | null {
  const src = headerSource(c);
  if (!src) return null;
  if (src.edward) return { kind: "edward", label: src.label };
  return { kind: src.label === "Voice" ? "voice" : "inbound", label: src.label };
}

export type DateBucket = "today" | "yesterday" | "week" | "older";

function resolveTz(tz?: string): string {
  if (tz) return tz;
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone;
  } catch {
    return "UTC";
  }
}

/** Days between two instants' calendar dates in `tz` (0 = same day). */
function dayDiff(d: Date, now: Date, tz: string): number {
  const fmt = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" });
  const day = (x: Date) => Date.parse(`${fmt.format(x)}T00:00:00Z`);
  return Math.round((day(now) - day(d)) / 86_400_000);
}

function activityStamp(c: Pick<Conversation, "updated_at" | "last_activity_at">): string {
  return c.last_activity_at || c.updated_at;
}

export function dateBucket(iso: string, now: Date = new Date(), tz?: string): DateBucket {
  const d = parseIso(iso);
  if (Number.isNaN(d.getTime())) return "older";
  const diff = dayDiff(d, now, resolveTz(tz));
  if (diff <= 0) return "today";
  if (diff === 1) return "yesterday";
  if (diff < 7) return "week";
  return "older";
}

export interface DateGroup {
  key: DateBucket;
  label: string;
  items: Conversation[];
  /** The last seven days start open; Older starts folded. */
  collapsed: boolean;
}

const DATE_GROUPS: { key: DateBucket; label: string; collapsed: boolean }[] = [
  { key: "today", label: "Today", collapsed: false },
  { key: "yesterday", label: "Yesterday", collapsed: false },
  { key: "week", label: "This week", collapsed: false },
  { key: "older", label: "Older", collapsed: true },
];

export function groupByDate(conversations: Conversation[], now: Date = new Date(), tz?: string): DateGroup[] {
  const buckets = new Map<DateBucket, Conversation[]>();
  for (const c of conversations) {
    const k = dateBucket(activityStamp(c), now, tz);
    if (!buckets.has(k)) buckets.set(k, []);
    buckets.get(k)!.push(c);
  }
  const stamp = (c: Conversation) => parseIso(activityStamp(c)).getTime() || 0;
  return DATE_GROUPS.filter((g) => (buckets.get(g.key) ?? []).length > 0).map((g) => ({
    ...g,
    items: [...buckets.get(g.key)!].sort((a, b) => stamp(b) - stamp(a)),
  }));
}

/** The row's compact time: "9:05 AM" · "Yesterday" · "Tue" · "Sep 3" · "Dec 24, 2025". */
export function rowTime(iso: string, now: Date = new Date(), tz?: string): string {
  const d = parseIso(iso);
  if (Number.isNaN(d.getTime())) return "";
  const zone = resolveTz(tz);
  const diff = dayDiff(d, now, zone);
  if (diff <= 0) return formatClock(iso, { tz: zone, now });
  if (diff === 1) return "Yesterday";
  if (diff < 7) return new Intl.DateTimeFormat("en-US", { timeZone: zone, weekday: "short" }).format(d);
  const year = new Intl.DateTimeFormat("en-US", { timeZone: zone, year: "numeric" });
  const sameYear = year.format(d) === year.format(now);
  return new Intl.DateTimeFormat("en-US", {
    timeZone: zone,
    month: "short",
    day: "numeric",
    year: sameYear ? undefined : "numeric",
  }).format(d);
}

/** The row time for a conversation (its latest message when known). */
export function conversationTime(c: Conversation, now?: Date, tz?: string): string {
  return rowTime(activityStamp(c), now, tz);
}

// ---- gestures -------------------------------------------------------------

/** A full swipe runs at least this far past the open buttons… */
export const FULL_SWIPE_PAST_OPEN_PX = 24;
/** …and across this much of the row. */
export const FULL_SWIPE_ROW_FRACTION = 0.65;

/** Where a swiped row comes to rest (offset < 0 is left). The row opens to
 *  `actions` buttons of `actionWidth` each (on the phone: Rename and Delete
 *  since 2026-09-27 — rename used to be desktop-only). A long drag past
 *  them all is a full swipe: delete straight away.
 *
 *  How long: one action past the buttons, unless the row is measured
 *  (`rowWidth`). With two 88 px buttons that was 264 px, and a phone's
 *  drawer row is about 306 px with the screen's edge just left of it, so
 *  only a finger starting in the row's last ~50 px could reach it
 *  (2026-09-27 review). Measured, it is 65 % of the row but never within
 *  24 px of open (a swipe that overshoots opening is not a delete) and
 *  never further than before. */
export function swipeSettle(
  offsetX: number,
  velocityX: number,
  actionWidth: number,
  actions = 1,
  rowWidth?: number,
): "open" | "closed" | "delete" {
  const open = actionWidth * actions;
  const furthest = open + actionWidth;
  const full =
    rowWidth && rowWidth > 0
      ? Math.min(furthest, Math.max(open + FULL_SWIPE_PAST_OPEN_PX, FULL_SWIPE_ROW_FRACTION * rowWidth))
      : furthest;
  if (offsetX <= -full) return "delete";
  if (velocityX > 400) return "closed";
  if (velocityX < -400 || offsetX <= -open / 2) return "open";
  return "closed";
}

/** The history drawer closes on a drag left of a third of its width, or a flick. */
export function drawerDragCloses(offsetX: number, velocityX: number, width: number): boolean {
  if (offsetX >= 0 && velocityX >= 0) return false;
  return offsetX <= -width / 3 || velocityX < -500;
}

/** What the list can honestly show. Switching Yours ↔ Edward's refetches;
 *  until it lands the list still holds the other group's rows — that is
 *  loading, never "nothing here". */
export function listPhase(total: number, matching: number): "rows" | "loading" | "empty" {
  if (matching > 0) return "rows";
  return total > 0 ? "loading" : "empty";
}
