// Pure logic for the Wishlist panel (Settings → Work → Wishlist).
//
// Edward logs features he wishes he could do — from a chat, with the
// conversation he wished in — and the owner reads and manages the list. This
// replaced the self-evolution UI in Sep 2026: Edward no longer codes
// himself, he asks. The panel only draws what this module decides:
// which section an item sits in, the order inside a section, and the
// one-line meta under each title. Pinned in __tests__/wishlist.test.ts.

import { formatRelative } from "./time";

export type WishStatus = "open" | "planned" | "building" | "done" | "declined";
/** 1 is high. */
export type WishPriority = 1 | 2 | 3;
export type WishSource = "edward" | "owner";

export interface WishItem {
  id: string;
  title: string;
  why: string;
  detail: string | null;
  status: WishStatus;
  priority: WishPriority;
  source: WishSource;
  conversation_id: string | null;
  times_wanted: number;
  created_at: string;
  updated_at: string;
}

export const STATUS_OPTIONS: { value: WishStatus; label: string }[] = [
  { value: "open", label: "Open" },
  { value: "planned", label: "Planned" },
  { value: "building", label: "Building" },
  { value: "done", label: "Done" },
  { value: "declined", label: "Declined" },
];

export const PRIORITY_LABEL: Record<WishPriority, string> = { 1: "high", 2: "normal", 3: "low" };

/** The priority toggle walks high → normal → low → high. */
export function cyclePriority(p: WishPriority): WishPriority {
  return p === 3 ? 1 : ((p + 1) as WishPriority);
}

/** A detail this long is folded behind a disclosure rather than pushing
 *  the next wish off the screen. */
export const DETAIL_FOLD_CHARS = 200;

export function foldsDetail(detail: string | null): boolean {
  if (!detail) return false;
  return detail.length > DETAIL_FOLD_CHARS || detail.split("\n").length > 3;
}

export type WishSectionId = "open" | "planned" | "building" | "closed";

export interface WishSection {
  id: WishSectionId;
  label: string;
  items: WishItem[];
  /** Folded sections start collapsed — the finished and the refused. */
  folded: boolean;
}

const SECTIONS: { id: WishSectionId; label: string; statuses: WishStatus[]; folded: boolean }[] = [
  { id: "open", label: "Open", statuses: ["open"], folded: false },
  { id: "planned", label: "Planned", statuses: ["planned"], folded: false },
  { id: "building", label: "Building", statuses: ["building"], folded: false },
  { id: "closed", label: "Done & declined", statuses: ["done", "declined"], folded: true },
];

function updatedMs(i: WishItem): number {
  const t = Date.parse(i.updated_at);
  return Number.isFinite(t) ? t : 0;
}

/** Highest priority first, then the wish he came back to most, then freshest. */
function byRank(a: WishItem, b: WishItem): number {
  return a.priority - b.priority || b.times_wanted - a.times_wanted || updatedMs(b) - updatedMs(a);
}

function byFreshness(a: WishItem, b: WishItem): number {
  return updatedMs(b) - updatedMs(a);
}

/** Sections in workflow order; empty sections are left out so the panel
 *  can show one empty state instead of four empty headings. */
export function groupWishes(items: readonly WishItem[]): WishSection[] {
  return SECTIONS.map((s) => ({
    id: s.id,
    label: s.label,
    folded: s.folded,
    items: items.filter((i) => s.statuses.includes(i.status)).sort(s.id === "closed" ? byFreshness : byRank),
  })).filter((s) => s.items.length > 0);
}

/** "wanted 3× · from a chat · 2d ago" */
export function describeWish(item: WishItem, now: Date = new Date()): string {
  const parts: string[] = [];
  if (item.source === "owner") parts.push("your idea");
  else parts.push(item.times_wanted > 1 ? `wanted ${item.times_wanted}×` : "wanted once");
  if (item.conversation_id) parts.push("from a chat");
  parts.push(formatRelative(item.updated_at, { now }));
  return parts.join(" · ");
}
