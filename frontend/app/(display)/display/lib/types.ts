// Shared types for the /display page. Mirrors backend response shapes.

export type EdwardState =
  | "idle"
  | "thinking"
  | "speaking"
  | "listening"
  | "looking-left"
  | "looking-right"
  | "looking-up";

export type AccentName = "green" | "blue" | "amber" | "violet";
export type TimeOfDay = "auto" | "morning" | "day" | "dusk" | "night";
export type Density = "loose" | "normal" | "dense";
export type AvatarSize = "sm" | "md" | "lg" | "xl";

export const PANEL_KEYS = [
  "clock",
  "alerts",
  "calendar",
  "heartbeat",
  "now_thinking",
  "recents",
  "workers",
  "vitals",
  "memory_ticker",
] as const;
export type PanelKey = (typeof PANEL_KEYS)[number];

export interface DisplaySettings {
  accent: AccentName;
  time_of_day: TimeOfDay;
  density: Density;
  avatar_size: AvatarSize;
  show_panels: Record<PanelKey, boolean>;
  /** His live thinking in the thought lane while a turn runs (2026-09-28). */
  show_thinking: boolean;
  /** Lines he says with `aloud` play through the host Mac's speakers. */
  speak_aloud: boolean;
}

export const ACCENT_HEX: Record<AccentName, string> = {
  green: "#3ecf9e", // Edward's emerald (globals.css --pane-accent), since the stage (2026-09-28)
  blue: "#3b82f6",
  amber: "#f59e0b",
  violet: "#8b5cf6",
};

export const AVATAR_PX: Record<AvatarSize, number> = {
  sm: 180,
  md: 260,
  lg: 340,
  xl: 440,
};

// ---- API response types ----

export interface CalendarEvent {
  id: string;
  title: string;
  start: string;
  end: string;
  where: string;
  kind: "work" | "focus" | "personal";
  /** Set when the calendar source says so; otherwise inferred (lib/calendarDay.ts). */
  all_day?: boolean;
}

export interface CalendarResponse {
  source: string;
  fetched_at: string;
  events: CalendarEvent[];
}

export interface VitalsResponse {
  host: string;
  uptime_seconds: number | null;
  cpu_pct: number | null;
  mem_pct: number | null;
  disk_pct: number | null;
  net_kbps: number | null;
  model: string;
  tokens_in_today: number;
  tokens_out_today: number;
  cost_today: number | null;
  convos_today: number;
  memory_count: number;
  docs_count: number;
}

export interface WorkerStat {
  name: string;
  status: string;
  tone: "ok" | "warn" | "danger" | "mute" | "info";
  last: string;
}

export interface WorkersResponse {
  running_count: number;
  total: number;
  workers: WorkerStat[];
}

export interface RecentItem {
  t: string | null;
  verb: string;
  obj: string;
  status: string;
}

export interface RecentsResponse {
  items: RecentItem[];
}

export interface MemoryTickerResponse {
  total: number;
  items: string[];
}

export interface NowThinkingItem {
  id: string;
  title: string;
  detail: string;
  started_at: string | null;
  elapsed_seconds: number;
  steps: { label: string; state: "done" | "active" | "pending" }[];
  sources: string[];
  confidence: number | null;
}

export interface NowThinkingResponse {
  item: NowThinkingItem | null;
}

export type AlertLevel = "ok" | "warn" | "info" | "danger";

export interface AlertItem {
  level: AlertLevel;
  t: string;
  text: string;
}

export interface AlertsResponse {
  items: AlertItem[];
}

export interface PendingInterrupt {
  delivery_id: string;
  title: string;
  body: string;
  url: string;
  urgency: "low" | "normal" | "high";
  sent_at: string;
}

export interface SignalsResponse {
  pending_interrupt?: PendingInterrupt | null;
  voice_state: "idle" | "listening" | "speaking";
  active_chat_streams: number;
  active_orchestrator_tasks: number;
  recent_alert_at: string | null;
  calendar_near_fire_at: string | null;
  fetched_at: string;
}
