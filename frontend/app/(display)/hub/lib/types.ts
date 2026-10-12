// Payload shape from GET /api/hub/board. Mirrors services/hub_pet_service.py.

export type CareStatus =
  | "due"
  | "given"
  | "overdue"
  | "missed"
  | "refused"
  | "skipped";

export type CareWindow = "am" | "pm";

export interface FeedingEntry {
  item_id: string | null;
  done: boolean;
  by: string | null;
  at_local: string | null;
  /** Structurally always false — feeding is windowed and never overdue. */
  overdue: boolean;
  /** e.g. "+P" for a probiotic powder that rides in a cat's PM bowl. */
  marker: string | null;
  bundled_item_ids: string[];
}

export interface MedEntry {
  id: string;
  name: string;
  dose: string | null;
  kind: string;
  window: CareWindow | null;
  at_time: string | null;
  status: CareStatus;
  by: string | null;
  at_local: string | null;
  needs_review: boolean;
  minutes_late: number | null;
}

export interface HubPet {
  id: string;
  name: string;
  species: string | null;
  photo_url: string | null;
  feedings: Record<CareWindow, FeedingEntry>;
  meds: MedEntry[];
}

export interface HubAlert {
  pet: string;
  item: string;
  minutes_late: number;
}

export interface HubBoard {
  care_day: string;
  window: CareWindow;
  pets: HubPet[];
  alerts: HubAlert[];
}

// ---- Who is at the hub (GET /api/hub/who) ----

export interface HubWho {
  /** Who was last at the hub; null after 30 minutes idle. */
  person: string | null;
  since: string | null;
  last_seen: string | null;
  /** The household, owner first (EDWARD_HOUSEHOLD). */
  people: string[];
}

// ---- Today ----

export interface CalendarEvent {
  title: string;
  start: string;
  end?: string | null;
  all_day?: boolean;
  kind?: string;
}

export interface CalendarResponse {
  source: string;
  fetched_at: string;
  events: CalendarEvent[];
}

export interface ScheduledEvent {
  id: string;
  description: string;
  next_fire_at: string;
  recurrence_pattern: string | null;
  status: string;
}

// ---- Lists ----

export interface ListItem {
  id: string;
  list_name: string;
  text: string;
  done: boolean;
  created_by: string | null;
  done_by: string | null;
}

export interface ListsResponse {
  lists: Record<string, ListItem[]>;
  names: string[];
}

// ---- Health ----

export type HealthStatus = "healthy" | "repaired" | "stale" | "unknown" | "problem";

export interface HealthComponent {
  label: string;
  /** A LaunchAgent's service name without the host's prefix ("backend"). */
  name?: string;
  status: HealthStatus;
  detail?: string | null;
  pid?: number | null;
  exit?: number | null;
  pct?: number | null;
}

export interface WatchdogState {
  status: HealthStatus;
  stale: boolean;
  age_minutes: number | null;
  checked_at: string | null;
  problems: string[];
  repairs: string[];
}

export interface HealthResponse {
  overall: HealthStatus;
  watchdog: WatchdogState;
  agents: HealthComponent[];
  components: HealthComponent[];
  /** The LaunchAgent label prefix (EDWARD_LAUNCHD_PREFIX). */
  label_prefix?: string;
  host: string;
  checked_at: string;
}

// ---- Vitals & settings ----

export interface VitalsResponse {
  host: string;
  uptime_seconds: number;
  cpu_pct: number;
  mem_pct: number;
  disk_pct: number;
  model: string;
  tokens_in_today: number;
  tokens_out_today: number;
  cost_today: number;
  convos_today: number;
  memory_count: number;
  docs_count: number;
}

export interface EdwardSettings {
  name: string;
  provider: string;
  model: string;
  temperature: number;
  debug_streaming?: boolean;
  timezone?: string;
}

export interface ModelOption {
  id: string;
  name: string;
}

export interface ModelsResponse {
  providers: Record<string, { available: boolean; models: ModelOption[] }>;
}

// ---- Photo screensaver ----

export interface PhotosResponse {
  photos: string[];
  skipped_unsupported: number;
  dir: string;
  dir_present: boolean;
}

// ---- Screensaver settings ----

export type SlideTransition = "crossfade" | "kenburns" | "slide";

export interface HubSettings {
  slide_seconds: number;
  transition: SlideTransition;
  edward_presents: boolean;
}
