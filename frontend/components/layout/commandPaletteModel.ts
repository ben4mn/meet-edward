// The ⌘K palette's list — pure, pinned in __tests__/commandPaletteModel.test.ts.
//
// Nothing typed: recent conversations (five, newest first), then actions.
// A query: every source is matched — actions, conversations, settings
// (goals went with unit G, 2026-10-11) — and the group holding the best hit leads (ties keep that
// order). Arrow keys walk the flattened list and wrap.

export type PaletteGroupId = "recent" | "actions" | "conversations" | "settings";

export interface PaletteItem {
  id: string;
  group: PaletteGroupId;
  label: string;
  detail?: string;
  keywords?: string[];
  /** Navigate here when chosen. */
  href?: string;
  /** Or run this (actions). */
  run?: () => void;
  disabled?: boolean;
}

export interface PaletteGroup {
  id: PaletteGroupId;
  label: string;
  items: PaletteItem[];
}

export const GROUP_LABEL: Record<PaletteGroupId, string> = {
  recent: "Recent",
  actions: "Actions",
  conversations: "Conversations",
  settings: "Settings",
};

const ORDER: PaletteGroupId[] = ["actions", "conversations", "settings"];
const LIMIT: Record<PaletteGroupId, number> = { recent: 5, actions: 12, conversations: 6, settings: 5 };

function wordStart(text: string, q: string): boolean {
  return text.split(/[\s\-_/·,.:'’]+/).some((w) => w.startsWith(q));
}

function scoreWord(item: PaletteItem, word: string): number {
  const label = item.label.toLowerCase();
  if (label.startsWith(word)) return 4;
  if (wordStart(label, word)) return 3;
  if (label.includes(word)) return 2;
  if ((item.keywords ?? []).some((k) => k.toLowerCase().includes(word))) return 1;
  if ((item.detail ?? "").toLowerCase().includes(word)) return 0.5;
  return 0;
}

/** 0 = no match. Every word of the query must match somewhere. */
export function scoreMatch(item: PaletteItem, query: string): number {
  const words = query.toLowerCase().split(/\s+/).filter(Boolean);
  if (words.length === 0) return 1;
  let total = 0;
  for (const w of words) {
    const s = scoreWord(item, w);
    if (s === 0) return 0;
    total += s;
  }
  return total / words.length;
}

export interface PaletteInput {
  query: string;
  actions: PaletteItem[];
  /** Newest first. */
  conversations: PaletteItem[];
  settings: PaletteItem[];
}

export function buildPalette(input: PaletteInput): { groups: PaletteGroup[]; flat: PaletteItem[] } {
  const q = input.query.trim();
  let groups: PaletteGroup[];
  if (!q) {
    groups = [
      { id: "recent", label: GROUP_LABEL.recent, items: input.conversations.slice(0, LIMIT.recent).map((i) => ({ ...i, group: "recent" as const })) },
      { id: "actions", label: GROUP_LABEL.actions, items: input.actions.slice(0, LIMIT.actions) },
    ];
  } else {
    const sources: Record<string, PaletteItem[]> = {
      actions: input.actions,
      conversations: input.conversations,
      settings: input.settings,
    };
    const scored = ORDER.map((id, order) => {
      const hits = sources[id]
        .map((item) => ({ item, s: scoreMatch(item, q) }))
        .filter((h) => h.s > 0)
        .sort((a, b) => b.s - a.s);
      return {
        order,
        best: hits[0]?.s ?? 0,
        group: { id, label: GROUP_LABEL[id], items: hits.slice(0, LIMIT[id]).map((h) => h.item) } as PaletteGroup,
      };
    });
    groups = scored
      .filter((g) => g.group.items.length > 0)
      .sort((a, b) => b.best - a.best || a.order - b.order)
      .map((g) => g.group);
  }
  groups = groups.filter((g) => g.items.length > 0);
  return { groups, flat: groups.flatMap((g) => g.items) };
}

export function moveActive(index: number, delta: number, count: number): number {
  if (count <= 0) return 0;
  return (((index + delta) % count) + count) % count;
}
