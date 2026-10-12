// API client for /hub. Cookie auth today; a scoped device token comes later.

import type { CareStatus, HubBoard } from "./types";

export async function fetchBoard(): Promise<HubBoard> {
  const res = await fetch("/api/hub/board", { credentials: "include" });
  if (!res.ok) throw new Error(`/api/hub/board failed: ${res.status}`);
  return (await res.json()) as HubBoard;
}

export async function logCare(
  itemId: string,
  givenBy: string,
  status: CareStatus = "given",
): Promise<void> {
  const res = await fetch(`/api/hub/care/${itemId}/log`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ given_by: givenBy, status, method: "tap" }),
  });
  if (!res.ok) throw new Error(`log ${itemId} failed: ${res.status}`);
}

export async function undoCare(itemId: string): Promise<void> {
  const res = await fetch(`/api/hub/care/${itemId}/log`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`undo ${itemId} failed: ${res.status}`);
}

// ---- Who is at the hub ----

/**
 * Who lives here (`people`, EDWARD_HOUSEHOLD, owner first) and who the
 * server last saw at the hub (`person`, null after 30 minutes idle).
 */
export async function fetchHubWho(): Promise<import("./types").HubWho> {
  const res = await fetch("/api/hub/who", { credentials: "include" });
  if (!res.ok) throw new Error(`/api/hub/who failed: ${res.status}`);
  return (await res.json()) as import("./types").HubWho;
}

/**
 * Tell Edward who just picked themselves on the toggle (2026-09-24): the
 * server remembers it for his pack and his surfaces, and forgets it after
 * 30 minutes idle. Fire-and-forget — it never throws, so an offline kiosk
 * still changes theme on the tap.
 */
export async function postHubWho(person: string): Promise<void> {
  try {
    await fetch("/api/hub/who", {
      method: "POST",
      credentials: "include",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ person }),
      keepalive: true,
    });
  } catch {
    /* the theme is local; the server hears on the next change or dose */
  }
}

// ---- Today ----

export async function fetchCalendar(): Promise<import("./types").CalendarResponse> {
  const res = await fetch("/api/display/calendar", { credentials: "include" });
  if (!res.ok) throw new Error(`calendar failed: ${res.status}`);
  return await res.json();
}

export async function fetchScheduledEvents(): Promise<import("./types").ScheduledEvent[]> {
  const res = await fetch("/api/events?status=pending&sort_order=asc", {
    credentials: "include",
  });
  if (!res.ok) throw new Error(`events failed: ${res.status}`);
  return await res.json();
}

// ---- Lists ----

export async function fetchLists(): Promise<import("./types").ListsResponse> {
  const res = await fetch("/api/hub/lists", { credentials: "include" });
  if (!res.ok) throw new Error(`lists failed: ${res.status}`);
  return await res.json();
}

export async function addListItem(listName: string, text: string, createdBy: string) {
  const res = await fetch(`/api/hub/lists/${encodeURIComponent(listName)}/items`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, created_by: createdBy }),
  });
  if (!res.ok) throw new Error(`add item failed: ${res.status}`);
}

export async function setListItemDone(itemId: string, done: boolean, doneBy: string) {
  const res = await fetch(`/api/hub/lists/items/${itemId}`, {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ done, done_by: doneBy }),
  });
  if (!res.ok) throw new Error(`tick item failed: ${res.status}`);
}

export async function deleteListItem(itemId: string) {
  const res = await fetch(`/api/hub/lists/items/${itemId}`, {
    method: "DELETE",
    credentials: "include",
  });
  if (!res.ok) throw new Error(`delete item failed: ${res.status}`);
}

// ---- Health ----

export async function fetchHealth(): Promise<import("./types").HealthResponse> {
  const res = await fetch("/api/hub/health", { credentials: "include" });
  if (!res.ok) throw new Error(`health failed: ${res.status}`);
  return await res.json();
}

// ---- Vitals & settings ----

export async function fetchVitals(): Promise<import("./types").VitalsResponse> {
  const res = await fetch("/api/display/vitals", { credentials: "include" });
  if (!res.ok) throw new Error(`vitals failed: ${res.status}`);
  return await res.json();
}

export async function fetchSettings(): Promise<import("./types").EdwardSettings> {
  const res = await fetch("/api/settings", { credentials: "include" });
  if (!res.ok) throw new Error(`settings failed: ${res.status}`);
  return await res.json();
}

export async function fetchModels(): Promise<import("./types").ModelsResponse> {
  const res = await fetch("/api/settings/models", { credentials: "include" });
  if (!res.ok) throw new Error(`models failed: ${res.status}`);
  return await res.json();
}

export async function saveSettings(
  patch: Partial<import("./types").EdwardSettings>,
): Promise<void> {
  const res = await fetch("/api/settings", {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`save settings failed: ${res.status}`);
}

// ---- Photo screensaver ----

export async function fetchPhotos(seed: number): Promise<import("./types").PhotosResponse> {
  const res = await fetch(`/api/hub/photos?seed=${seed}`, { credentials: "include" });
  if (!res.ok) throw new Error(`photos failed: ${res.status}`);
  return await res.json();
}

export function photoUrl(name: string): string {
  return `/api/hub/photos/${encodeURIComponent(name)}`;
}

// ---- Screensaver settings ----

export async function fetchHubSettings(): Promise<import("./types").HubSettings> {
  const res = await fetch("/api/hub/settings", { credentials: "include" });
  if (!res.ok) throw new Error(`hub settings failed: ${res.status}`);
  return await res.json();
}

export async function saveHubSettings(
  patch: Partial<import("./types").HubSettings>,
): Promise<import("./types").HubSettings> {
  const res = await fetch("/api/hub/settings", {
    method: "PATCH",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(patch),
  });
  if (!res.ok) throw new Error(`save hub settings failed: ${res.status}`);
  return await res.json();
}


// ---- Canvas: Edward's expression surface ----

export async function fetchCanvas(): Promise<import("./canvas").CanvasContent | null> {
  const res = await fetch("/api/hub/canvas", { credentials: "include" });
  if (!res.ok) throw new Error(`canvas failed: ${res.status}`);
  return ((await res.json()) as { content: import("./canvas").CanvasContent | null }).content;
}

export async function dismissCanvas(): Promise<void> {
  await fetch("/api/hub/canvas?by=tap", { method: "DELETE", credentials: "include" });
}
