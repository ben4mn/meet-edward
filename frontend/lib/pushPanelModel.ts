/**
 * Pure logic for the Notifications panel.
 *
 * 2026-09-14: the panel awaited `navigator.serviceWorker.ready` before it
 * asked the server anything; with no active worker (a freshly re-added iOS
 * app, or a plain tab) that promise never settles, `configured` stayed at
 * its initial false, and the headline blamed "missing VAPID keys" on a
 * server that was fine. The headline now separates "could not reach the
 * server" from "the server says it is not configured", and subscribe
 * errors keep the browser's own error name so the text is diagnostic.
 */

import { formatRelative } from "./time";

export type Tone = "good" | "warn" | "bad";

export interface PushPanelState {
  supported: boolean | null;
  reachable: boolean;
  configured: boolean;
  permission: string;
  subscribed: boolean;
}

export function pushHeadline(s: PushPanelState): { label: string; tone: Tone } {
  if (s.supported === false) return { label: "Notifications are not supported in this browser.", tone: "bad" };
  if (!s.reachable) return { label: "I couldn't reach the Mac to check — try again in a moment.", tone: "warn" };
  if (!s.configured) return { label: "The Mac isn't set up to send notifications (its VAPID keys are missing).", tone: "bad" };
  if (s.permission === "denied") return { label: "Notifications are blocked for this app in your device's settings.", tone: "bad" };
  if (s.subscribed && s.permission === "granted") return { label: "I can reach you on this device.", tone: "good" };
  if (s.permission === "granted") return { label: "Notifications are allowed, but there's no subscription on this device yet.", tone: "warn" };
  return { label: "I can't reach you on this device yet — turn it on below.", tone: "warn" };
}

const HINTS: Record<string, string> = {
  NotAllowedError: "notifications are off for this app — iOS: Settings → Notifications → Edward → Allow, then retry",
  AbortError: "the push service refused the registration — iOS needs the app on the Home Screen and a network connection",
  InvalidAccessError: "the server key the browser received was not a valid P-256 key",
  InvalidStateError: "a subscription for a different server key already exists on this device — Reset (re-register) clears it",
};

export function describeSubscribeError(err: unknown): string {
  if (err instanceof Error) {
    const name = err.name && err.name !== "Error" ? err.name : "";
    const text = name ? `${name}: ${err.message}` : err.message || "Unknown error";
    const hint = name ? HINTS[name] : undefined;
    return hint ? `${text} (${hint})` : text;
  }
  if (typeof err === "string" && err) return err;
  return "Unknown error";
}

// ---- the push ledger (GET /api/push/log) ---------------------------------------

const SOURCE_LABEL: Record<string, string> = {
  mind: "from a wake",
  edward_thought: "from a wake",
  tool: "from a chat",
  chat: "from a chat",
  llm: "from a chat",
  voice: "from a voice chat",
  voice_handoff: "from a voice chat",
  approval: "needs your approval",
  interrupt: "couldn't wait",
  scheduled_event: "a reminder",
  heartbeat: "about your messages",
  codex_limit: "about my fuel",
  contradiction: "a question for you",
  memory_report: "my memory report",
  worker: "from a helper",
  orchestrator_worker: "from a helper",
  test: "a test",
  snooze: "snoozed, again",
  system: "from the Mac",
};

export function pushSourceLabel(source: string | null | undefined): string {
  const s = (source ?? "").trim();
  if (!s) return "";
  return SOURCE_LABEL[s] ?? s.replace(/_/g, " ");
}

export interface PushLogEntry {
  id: string;
  source?: string | null;
  title?: string | null;
  url?: string | null;
  sent_at?: string | null;
  opened_at?: string | null;
  held_reason?: string | null;
  sent?: number | null;
  failed?: number | null;
}

export interface PushLogRow {
  id: string;
  title: string;
  meta: string;
  href: string | null;
  state: "opened" | "not opened" | "held" | "didn't arrive";
}

/** The ten newest pushes, newest first: what, from where, when, and
 *  whether the owner opened it. */
export function pushLogRows(entries: readonly PushLogEntry[] | null | undefined, now: Date = new Date()): PushLogRow[] {
  if (!Array.isArray(entries)) return [];
  return entries
    .filter((e) => e && typeof e.id === "string" && typeof e.sent_at === "string")
    .sort((a, b) => Date.parse(b.sent_at!) - Date.parse(a.sent_at!))
    .slice(0, 10)
    .map((e) => {
      let state: PushLogRow["state"];
      if (e.opened_at) state = "opened";
      else if (e.held_reason) state = "held";
      else if ((e.sent ?? 1) === 0 && (e.failed ?? 0) > 0) state = "didn't arrive";
      else state = "not opened";
      const meta = [pushSourceLabel(e.source), formatRelative(e.sent_at!, { now })].filter(Boolean).join(" · ");
      return { id: e.id, title: (e.title ?? "").trim() || "(no title)", meta, href: e.url || null, state };
    });
}
