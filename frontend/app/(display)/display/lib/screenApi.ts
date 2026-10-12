import type { ScreenSnapshot } from "./screenModel";
import { EMPTY_LIVE, type ScreenLive } from "./thinkingModel";
import { redirectToLogin } from "@/lib/authSession";

export async function fetchScreen(): Promise<ScreenSnapshot> {
  const res = await fetch("/api/screen", { credentials: "include" });
  if (res.status === 401) redirectToLogin(); // a dead session: stop polling, go log in
  if (!res.ok) throw new Error(`/api/screen failed: ${res.status}`);
  return (await res.json()) as ScreenSnapshot;
}

export async function dismissShow(by = "tap"): Promise<void> {
  await fetch(`/api/screen/show?by=${encodeURIComponent(by)}`, { method: "DELETE", credentials: "include" });
}

/** His thinking while a mind or surfaces turn runs, and his recent thoughts (services/screen_thoughts.py). */
export const LIVE_URL = "/api/screen/live";
/** Every 2 s: the stream should feel live, and the read is in-process (no DB). */
export const LIVE_POLL_MS = 2_000;

/** An older backend without the route is no thinking, not a failure; anything else throws so the poll counts it. */
export async function fetchLive(): Promise<ScreenLive> {
  const res = await fetch(LIVE_URL, { credentials: "include" });
  if (res.status === 404) return EMPTY_LIVE;
  if (!res.ok) throw new Error(`${LIVE_URL} failed: ${res.status}`);
  const body = (await res.json()) as Partial<ScreenLive> | null;
  return {
    thinking: body?.thinking ?? null,
    folded: Array.isArray(body?.folded) ? body!.folded : [],
    recent: Array.isArray(body?.recent) ? body!.recent : [],
    now: typeof body?.now === "string" ? body.now : "",
  };
}
