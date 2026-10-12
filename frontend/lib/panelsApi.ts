// Fetches for the Mind / Attention / Work panels that lib/api.ts does not
// have (the journal, the push ledger, the test push). Kept out of
// lib/api.ts so concurrent edits there do not collide; the auth behaviour
// is the same: credentials included, and a 401 goes to /login once
// (lib/authSession.ts).

import { redirectToLogin } from "./authSession";
import { errorText } from "./apiError";
import type { PushLogEntry } from "./pushPanelModel";

async function call<T>(url: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(url, {
    ...init,
    credentials: "include",
    headers: init.body ? { "Content-Type": "application/json", ...(init.headers ?? {}) } : init.headers,
  });
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }
  if (!response.ok) {
    let detail: unknown = null;
    try {
      detail = (await response.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new Error(errorText(detail, `Request failed (${response.status})`));
  }
  return response.json() as Promise<T>;
}

// The weekly journal -----------------------------------------------------------

export interface JournalObservation {
  id: string;
  kind: string;
  signature: string;
  occurrences: number;
  description: string;
  last_seen_at: string | null;
  status: string;
}

export interface JournalWake {
  id: number;
  at: string | null;
  source: string;
  outcome: string;
  note: string | null;
  tools: string[];
  surfaces: string[];
  delivered: boolean;
}

export interface JournalWeek {
  window_days: number;
  observations: { by_kind: { kind: string; count: number }[]; recent: JournalObservation[]; total_in_window: number };
  wakes: { by_outcome: { outcome: string; count: number }[]; recent: JournalWake[]; total_in_window: number };
}

export function getJournalWeek(): Promise<JournalWeek> {
  return call("/api/journal/week");
}

// The push ledger ----------------------------------------------------------------

export async function getPushLog(hours = 24 * 7, limit = 10): Promise<PushLogEntry[]> {
  const out = await call<{ pushes?: PushLogEntry[] }>(`/api/push/log?hours=${hours}&limit=${limit}`);
  return Array.isArray(out.pushes) ? out.pushes : [];
}

export function sendTestPush(): Promise<{ ok?: boolean; sent?: number; failed?: number; message?: string }> {
  return call("/api/push/test", { method: "POST" });
}
