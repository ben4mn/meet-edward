/** Pure helpers for Edward's screen on the host Mac (/screen). Tested in __tests__/screenModel.test.ts.
 *
 * The screen is a stage since 2026-09-28 (lib/stageModel.ts draws it); this
 * file keeps the snapshot's shape, the legacy tile keys an old backend still
 * sends, and how to draw what Edward shows on top.
 */

import { contentKey, isExpired, rendererFor, type CanvasContent, type Renderer } from "../../hub/lib/canvas";
import type { AskedHold, SpokenLine, StageGrid, StageScene } from "./stageModel";

export type TileType =
  | "clock" | "edward" | "alerts" | "calendar" | "heartbeat" | "recents" | "workers" | "vitals" | "memory_ticker"
  | "text" | "image" | "html" | "list" | "stat" | "countdown";

export type TileSize = "s" | "m" | "l" | "wide" | "tall" | "big" | "hero" | "full";

export interface ListItem {
  label: string;
  detail: string | null;
}

/** A tile of the old 6×4 layout (the snapshot's legacy keys, for one release). */
export interface PlacedTile {
  type: TileType;
  size: TileSize;
  col: number;
  row: number;
  w: number;
  h: number;
  title?: string;
  body?: string;
  source?: string;
  items?: unknown;
  value?: string;
  detail?: string;
  target?: string;
}

/** What Edward puts over the tiles: the household kinds. Websites are not
 *  drawn in this page any more — he opens them in the owner's Chrome, full screen
 *  (2026-09-07, second pass). */
export type ShowKind = CanvasContent["kind"];

export type ShowContent = CanvasContent;

export interface ScreenSnapshot {
  /** The stage (2026-09-28). Optional: an older backend sends only the tiles. */
  stage?: StageGrid;
  scene?: StageScene;
  asked?: AskedHold | null;
  lines?: SpokenLine[];
  owner_at_mini?: boolean;
  show: ShowContent | null;
  now: string;
  // Legacy tile keys, kept one release so an open old tab keeps drawing.
  grid?: { cols: number; rows: number };
  layout?: PlacedTile[];
  layout_source?: "edward" | "default";
  layout_note?: string | null;
  layout_updated_at?: string | null;
  layout_updated_by?: string | null;
}

export type ShowRenderer = Renderer;

export function showRenderer(s: ShowContent): ShowRenderer {
  return rendererFor(s);
}

export function showKey(s: ShowContent | null): string {
  return s ? `${s.kind}|${contentKey(s as CanvasContent)}` : "";
}

export function isShowExpired(s: ShowContent, nowMs: number): boolean {
  return isExpired(s as CanvasContent, nowMs);
}

/** "2d 2h" / "3h 12m" / "5m" / "now" / "—" */
export function countdownLabel(targetIso: string, nowMs: number): string {
  const t = Date.parse(targetIso);
  if (!Number.isFinite(t)) return "—";
  const diff = t - nowMs;
  if (diff <= 0) return "now";
  const day = 86_400_000;
  const hour = 3_600_000;
  const days = Math.floor(diff / day);
  const hours = Math.floor((diff % day) / hour);
  const mins = Math.floor((diff % hour) / 60_000);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

export function listItems(raw: unknown): ListItem[] {
  if (!Array.isArray(raw)) return [];
  const out: ListItem[] = [];
  for (const it of raw) {
    if (typeof it === "string") {
      if (it.trim()) out.push({ label: it, detail: null });
    } else if (it && typeof it === "object") {
      const o = it as { label?: unknown; detail?: unknown };
      const label = typeof o.label === "string" ? o.label.trim() : "";
      if (!label) continue;
      out.push({ label, detail: typeof o.detail === "string" && o.detail ? o.detail : null });
    }
  }
  return out;
}
