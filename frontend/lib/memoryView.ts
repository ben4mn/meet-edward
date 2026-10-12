// The Memory panel's words (2026-09-26, Jarvis polish). Pure; pinned in
// __tests__/memoryView.test.ts.
//
// A memory card leads with how Edward came by it ("he heard / he read /
// he thought / he did") and whom it is about. Tier and reinforcement are
// one muted meta line; importance and access counts live in "Where this
// came from", not on the card.

import { formatRelative } from "./time";

export type Tone = "neutral" | "edward";

const ORIGIN_LABEL: Record<string, string> = {
  heard: "he heard",
  read: "he read",
  thought: "he thought",
  did: "he did",
};

/** How he came by it. What he thought or did himself is his (emerald);
 *  what he heard or read is neutral. */
export function originChip(origin: string | null | undefined): { label: string; tone: Tone } {
  const key = origin || "heard";
  return {
    label: ORIGIN_LABEL[key] ?? key,
    tone: key === "thought" || key === "did" ? "edward" : "neutral",
  };
}

const ABOUT_LABEL: Record<string, string> = {
  owner: "about you",
  contact: "about someone else",
  self: "about himself",
  world: "about the world",
};

export function aboutLabel(about: string | null | undefined): string {
  const key = about || "owner";
  return ABOUT_LABEL[key] ?? `about ${key}`;
}

export const TYPE_LABEL: Record<string, string> = {
  fact: "Fact",
  preference: "Preference",
  context: "Context",
  instruction: "Instruction",
  intention: "Intention",
};

export const TIER_LABEL: Record<string, string> = {
  knowledge: "Known",
  belief: "Believed",
  observation: "Noticed",
};

export const NATURE_LABEL: Record<string, string> = {
  timeless: "Lasting",
  permanent: "Lasting",
  temporary: "For now",
  evolving: "Changing",
};

function titleCase(s: string): string {
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : s;
}

export function typeLabel(t: string | null | undefined): string {
  return t ? TYPE_LABEL[t] ?? titleCase(t) : "Memory";
}

export function tierLabel(t: string | null | undefined): string {
  return t ? TIER_LABEL[t] ?? titleCase(t) : "Noticed";
}

export function natureLabel(n: string | null | undefined): string {
  return n ? NATURE_LABEL[n] ?? titleCase(n) : "";
}

export interface MetaMemory {
  memory_type: string;
  tier: string;
  reinforcement_count: number;
  created_at: string | null;
}

/** "Fact · Known · reinforced 14× · 1w ago". */
export function memoryMeta(m: MetaMemory, opts: { now?: Date; tz?: string } = {}): string {
  const parts = [typeLabel(m.memory_type), tierLabel(m.tier)];
  if (m.reinforcement_count > 0) parts.push(`reinforced ${m.reinforcement_count}×`);
  if (m.created_at) parts.push(formatRelative(m.created_at, opts));
  return parts.join(" · ");
}

// ------------------------------------------------------------ filters
export const MEMORY_TYPES = ["all", "fact", "preference", "context", "instruction", "intention"] as const;
export type MemoryType = (typeof MEMORY_TYPES)[number];

export const TIERS = ["all", "knowledge", "belief", "observation"] as const;
export type Tier = (typeof TIERS)[number];

export const NATURES = ["all", "timeless", "temporary", "evolving"] as const;
export type Nature = (typeof NATURES)[number];

export type SortMode = "recent" | "reinforced";

export interface MemoryFilters {
  type: MemoryType;
  tier: Tier;
  nature: Nature;
  sort: SortMode;
}

export const DEFAULT_FILTERS: MemoryFilters = { type: "all", tier: "all", nature: "all", sort: "recent" };

/** The Filter chip's badge: what the sheet holds that is not on screen.
 *  The tier is its own segment, always visible, so it does not count. */
export function activeFilterCount(f: MemoryFilters): number {
  return (f.type !== "all" ? 1 : 0) + (f.nature !== "all" ? 1 : 0) + (f.sort !== "recent" ? 1 : 0);
}

export interface TierStats {
  total: number;
  live?: number;
  by_tier?: Record<string, number>;
}

export interface TierOption {
  value: Tier;
  label: string;
  count?: number;
}

/** The tier bar: All, then strongest first. */
export function tierOptions(stats: TierStats | null | undefined): TierOption[] {
  const by = stats?.by_tier;
  return [
    { value: "all", label: "All", count: stats ? stats.live ?? stats.total : undefined },
    { value: "knowledge", label: TIER_LABEL.knowledge, count: by ? by.knowledge ?? 0 : undefined },
    { value: "belief", label: TIER_LABEL.belief, count: by ? by.belief ?? 0 : undefined },
    { value: "observation", label: TIER_LABEL.observation, count: by ? by.observation ?? 0 : undefined },
  ];
}

// ------------------------------------------------------------ small words
export function compactCount(n: number): string {
  if (n < 1000) return String(n);
  const k = n / 1000;
  return `${k >= 10 ? Math.round(k) : Math.round(k * 10) / 10}k`;
}

export function normalizeQuery(q: string): string {
  return q.trim().replace(/\s+/g, " ");
}

export interface StatsLike {
  total: number;
  live?: number;
  created_last_24h?: number;
  unused_live?: number;
}

/** "4,432 memories · 37 new today". */
export function statsLine(stats: StatsLike | null | undefined): string {
  if (!stats) return "";
  const n = stats.live ?? stats.total;
  const parts = [`${n.toLocaleString("en-US")} ${n === 1 ? "memory" : "memories"}`];
  if (stats.created_last_24h) parts.push(`${stats.created_last_24h} new today`);
  return parts.join(" · ");
}

/** Empty states, in his voice. */
export function memoryEmpty(query: string, filtered: boolean): { title: string; body: string } {
  if (query) {
    return { title: `Nothing I remember matches “${query}”.`, body: "Try fewer words, or ask me in chat." };
  }
  if (filtered) {
    return { title: "Nothing I remember fits those filters.", body: "Clear a filter to see more." };
  }
  return { title: "I haven't kept anything yet.", body: "Tell me something worth remembering and it will show up here." };
}
