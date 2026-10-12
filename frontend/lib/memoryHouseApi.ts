// Fetches for the Memory and House panels that lib/api.ts does not have
// (one memory by id, recent notes for a flag's question, one stored file,
// Alexa). Kept out of lib/api.ts so concurrent edits there do not collide.
// Same behaviour as the rest of the app: relative URLs in the browser
// (API_URL is "" there — never localhost:8000, which on the phone is the
// phone), credentials included, and a 401 goes to /login once.

import { API_URL, type MemoryItem, type StoredFileItem } from "./api";
import { redirectToLogin } from "./authSession";
import { errorText } from "./apiError";

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
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

// Memories ---------------------------------------------------------------

/** A memory as the API really returns it: origin and about included. */
export interface MemoryRecord extends MemoryItem {
  origin?: string;
  about?: string;
  source_type?: string;
  confidence?: number;
}

export function getMemoryById(id: string): Promise<MemoryRecord> {
  return call<MemoryRecord>(`/api/memories/${encodeURIComponent(id)}`);
}

// Notes (the question behind a flag) -----------------------------------------

export interface NoteLike {
  id: string;
  title?: string;
  body?: string | null;
  href?: string | null;
}

export async function listRecentNotes(hours = 72): Promise<NoteLike[]> {
  const res = await call<{ notes?: NoteLike[] }>(`/api/notes?hours=${hours}&limit=60`);
  return Array.isArray(res.notes) ? res.notes : [];
}

// Files ------------------------------------------------------------------

export function getStoredFile(id: string): Promise<StoredFileItem> {
  return call<StoredFileItem>(`/api/files/${encodeURIComponent(id)}`);
}

// Alexa ------------------------------------------------------------------

export interface AlexaDevice {
  entity_id: string;
  friendly_name: string;
  state: string;
}

export interface AlexaAnnouncement {
  id: string;
  message: string;
  device: string;
  timestamp: string;
  success: boolean;
}

export interface AlexaStatus {
  outbound: { configured: boolean; healthy: boolean; ha_url: string; device_count: number };
  inbound: { configured: boolean; skill_id: string };
  devices: AlexaDevice[];
  recent_announcements?: AlexaAnnouncement[];
}

export function getAlexaStatus(): Promise<AlexaStatus> {
  return call<AlexaStatus>("/api/alexa/status");
}

export function testAlexaAnnouncement(
  message: string,
  device?: string,
): Promise<{ success: boolean; message?: string; error?: string }> {
  return call("/api/alexa/test", { method: "POST", body: JSON.stringify({ message, device: device || undefined }) });
}
