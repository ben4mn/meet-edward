// API client for the /display page. All endpoints use cookie auth so we
// just need to forward credentials. Errors bubble up — the polling hook
// keeps stale data on screen if a fetch fails. A 401 is different: the
// session is gone, so the tab goes to /login instead of polling a dead
// cookie once a second for a day (85,000 401s on 2026-09-20/21).

import { redirectToLogin } from "@/lib/authSession";

import type {
  AlertsResponse,
  CalendarResponse,
  DisplaySettings,
  MemoryTickerResponse,
  NowThinkingResponse,
  RecentsResponse,
  SignalsResponse,
  VitalsResponse,
  WorkersResponse,
} from "./types";

async function get<T>(path: string): Promise<T> {
  const res = await fetch(path, { credentials: "include" });
  if (res.status === 401) redirectToLogin();
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return (await res.json()) as T;
}

export const fetchCalendar = () => get<CalendarResponse>("/api/display/calendar");
export const fetchVitals = () => get<VitalsResponse>("/api/display/vitals");
export const fetchWorkers = () => get<WorkersResponse>("/api/display/workers");
export const fetchRecents = () => get<RecentsResponse>("/api/display/recents");
export const fetchMemoryTicker = () =>
  get<MemoryTickerResponse>("/api/display/memory_ticker");
export const fetchNowThinking = () =>
  get<NowThinkingResponse>("/api/display/now_thinking");
export const fetchAlerts = () => get<AlertsResponse>("/api/display/alerts");
export const fetchSignals = () => get<SignalsResponse>("/api/display/signals");
export const fetchSettings = () =>
  get<DisplaySettings>("/api/display/settings");

export async function patchSettings(
  patch: Partial<DisplaySettings>,
): Promise<DisplaySettings> {
  const res = await fetch("/api/display/settings", {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (res.status === 401) redirectToLogin();
  if (!res.ok) throw new Error(`PATCH /api/display/settings failed: ${res.status}`);
  return (await res.json()) as DisplaySettings;
}

// Heartbeat is shared with the rest of the app — pull from the existing
// endpoint shape but keep the fetch local so this page can poll independently.
export interface HeartbeatTrack {
  enabled: boolean;
  status: string;
  poll_seconds: number;
  lookahead_minutes?: number;
}

export interface HeartbeatStatusResponse {
  running: boolean;
  enabled: boolean;
  pending_count: number;
  listener_status: string;
  tracks: Record<string, HeartbeatTrack>;
}

export interface HeartbeatEvent {
  id: string;
  source: string;
  event_type: string;
  sender: string | null;
  contact_name: string | null;
  summary: string | null;
  is_from_user: boolean;
  created_at: string;
  triage_status: string;
}

export const fetchHeartbeatStatus = () =>
  get<HeartbeatStatusResponse>("/api/heartbeat/status");

export const fetchHeartbeatEvents = (limit = 20) =>
  get<HeartbeatEvent[]>(`/api/heartbeat/events?limit=${limit}`);
