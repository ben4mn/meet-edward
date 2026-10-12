// The Edward tab's rooms — settings regrouped by the question you bring
// to him. Pure TS (no JSX) so the mapping is testable under node --test.
// Every slug that exists today lives in exactly one room, and
// /settings/<slug> URLs are unchanged — with one exception: the self-
// evolution panels (`evolution`, `dreams`) were retired in Sep 2026.
// Edward asks for features now (`wishlist`, Work) instead of coding them,
// and memory consolidation moved to Memory as `dreaming`.
//
// 2026-09-26 (the Edward tab's frame): Mind leads with his thoughts and the
// journal beside them; his servers (`mcp`) sit next to his skills — both are
// what he can do; token usage moved next to the Codex quota in Work ("Fuel");
// the timezone went to House with the household. Nothing was removed.

export type RoomId = "mind" | "memory" | "attention" | "work" | "house";

export interface Room {
  id: RoomId;
  label: string;
  question: string;
}

export const ROOMS: readonly Room[] = [
  { id: "mind", label: "Mind", question: "How he thinks" },
  { id: "memory", label: "Memory", question: "What he knows" },
  { id: "attention", label: "Attention", question: "What he watches" },
  { id: "work", label: "Work", question: "What he does on his own" },
  { id: "house", label: "House", question: "The machine and the household" },
] as const;

export const ROOM_SLUGS: Record<RoomId, readonly string[]> = {
  mind: ["thoughts", "journal", "model", "skills", "mcp"],
  memory: ["memories", "documents", "files", "databases", "dreaming"],
  attention: ["heartbeat", "notifications", "proactive", "gmail", "alexa"],
  work: ["events", "orchestrator", "wishlist", "usage"],
  house: ["locale", "hub-photos"],
};

const ROOM_OF: Record<string, RoomId> = Object.fromEntries(
  (Object.keys(ROOM_SLUGS) as RoomId[]).flatMap((room) => ROOM_SLUGS[room].map((slug) => [slug, room])),
) as Record<string, RoomId>;

/** Unknown slugs land in House rather than vanishing from the index. */
export function roomFor(slug: string): RoomId {
  return ROOM_OF[slug] ?? "house";
}

export function roomLabel(id: RoomId): string {
  return ROOMS.find((r) => r.id === id)?.label ?? id;
}

export function isRoomId(id: string): id is RoomId {
  return ROOMS.some((r) => r.id === id);
}

/** Where on the Edward tab a path is — the rail highlights from this. */
export type SettingsLocation =
  | { kind: "home" }
  | { kind: "all" }
  | { kind: "room"; room: RoomId }
  | { kind: "panel"; room: RoomId; slug: string }
  | { kind: "other" };

export function settingsLocation(pathname: string | null | undefined): SettingsLocation {
  if (!pathname) return { kind: "other" };
  const path = pathname.split(/[?#]/)[0].replace(/\/+$/, "");
  if (path === "/settings") return { kind: "home" };
  if (!path.startsWith("/settings/")) return { kind: "other" };
  const parts = path.slice("/settings/".length).split("/");
  if (parts[0] === "all" && parts.length === 1) return { kind: "all" };
  if (parts[0] === "room") {
    const room = parts[1] ?? "";
    return parts.length === 2 && isRoomId(room) ? { kind: "room", room } : { kind: "other" };
  }
  if (parts.length === 1 && parts[0]) return { kind: "panel", room: roomFor(parts[0]), slug: parts[0] };
  return { kind: "other" };
}

/** The A–Z index's second line: the room first, then what the panel is for. */
export function settingDetail(slug: string, description: string): string {
  return `${roomLabel(roomFor(slug))} · ${description}`;
}

export interface SearchableSetting {
  slug: string;
  title: string;
  description: string;
  /** The category the panel lived under before the regroup — searchable, never shown. */
  was: string;
  /** Old or technical names ("heartbeat", "mcp") — searchable, never shown. */
  keywords?: string;
}

export function searchSettings<T extends SearchableSetting>(items: readonly T[], query: string): T[] {
  const q = query.trim().toLowerCase();
  const hit = q
    ? items.filter((i) =>
        `${i.title} ${i.description} ${i.was} ${i.keywords ?? ""} ${roomLabel(roomFor(i.slug))}`
          .toLowerCase()
          .includes(q),
      )
    : [...items];
  return hit.sort((a, b) => a.title.localeCompare(b.title));
}
