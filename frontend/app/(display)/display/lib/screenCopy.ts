// Words on Edward's screen — pure (__tests__/screenCopy.test.ts).

import { formatRelative, parseIso } from "@/lib/time";

const ISO_LIKE = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

/** An alert's time for the strip: an ISO string becomes "42m ago"; a
 *  phrase the backend already wrote ("now", "12m ago") is kept. */
export function alertTimeLabel(t: string | null | undefined, now: Date = new Date()): string {
  if (!t) return "";
  if (ISO_LIKE.test(t) && !Number.isNaN(parseIso(t).getTime())) return formatRelative(t, { now });
  return t;
}

export function workersLabel(n: number | null | undefined): string {
  if (n === null || n === undefined) return "—";
  if (n <= 0) return "no workers";
  return n === 1 ? "1 worker" : `${n} workers`;
}

const TRACK_WORDS: Readonly<Record<string, string>> = {
  imessage: "messages",
  calendar: "calendar",
  email: "mail",
  gmail: "gmail",
};

/** "watching messages · calendar" from the heartbeat's tracks. */
export function watchingLabel(tracks: Record<string, { enabled: boolean }> | null | undefined): string {
  if (!tracks) return "—";
  const on = Object.entries(tracks)
    .filter(([, t]) => t?.enabled)
    .map(([name]) => TRACK_WORDS[name] ?? name);
  return on.length ? `watching ${on.join(" · ")}` : "not watching";
}
