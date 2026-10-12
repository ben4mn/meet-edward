// The wakes Edward takes on his own, as the proactive panel shows them.
// Pure: a cron from `scheduler_service.WAKEUP_TICKS` becomes a phrase.

export interface WakeInfo {
  kind: string;
  cron: string;
}

const LABELS: Record<string, string> = {
  // The one tick since the proactivity rebuild (2026-10-11); the rest stay
  // so an old payload still reads.
  nightly: "Nightly reflection",
  morning: "Morning wake",
  evening: "Evening wake",
  reflect: "Sunday reflection",
  reflect_lite: "Nightly reflection",
  free: "Free time",
};

const WEEKDAYS = ["Sundays", "Mondays", "Tuesdays", "Wednesdays", "Thursdays", "Fridays", "Saturdays"];

export function wakeLabel(kind: string): string {
  if (LABELS[kind]) return LABELS[kind];
  const words = kind.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** "0 7 * * *" → "07:00 every day"; "0 9 * * 0" → "09:00 on Sundays"; else the cron itself. */
export function describeWake(cron: string): string {
  const parts = (cron || "").trim().split(/\s+/);
  if (parts.length !== 5) return cron;
  const [min, hour, dom, mon, dow] = parts;
  if (!/^\d{1,2}$/.test(min) || !/^\d{1,2}$/.test(hour) || dom !== "*" || mon !== "*") return cron;
  const time = `${hour.padStart(2, "0")}:${min.padStart(2, "0")}`;
  if (dow === "*") return `${time} every day`;
  if (/^[0-6]$/.test(dow)) return `${time} on ${WEEKDAYS[Number(dow)]}`;
  return cron;
}
